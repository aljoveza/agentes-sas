"""IAM caller identity: presigned GetCallerIdentity tokens, verified via STS (faked here)."""

import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import pytest
from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from company_mcp.iam import (
    AUDIENCE_HEADER,
    AUTH_SCHEME,
    TOKEN_PREFIX,
    StsAuthenticator,
    StsResponse,
    TokenSigner,
    Unauthenticated,
    mint_token,
)
from company_mcp.proxy import create_proxy
from company_mcp.server import create_server
from conftest import serve

ACCOUNT = "123456789012"
AUDIENCE = "agentes-sas-dev-company-mcp"
PREFIX = "agentes-sas-dev-"


def role_arn(role: str, account: str = ACCOUNT) -> str:
    return f"arn:aws:sts::{account}:assumed-role/{role}/ecs-task"


# Access key id -> the ARN STS would report for it.
IDENTITIES = {
    "AKID-ACCOUNTANT": role_arn("agentes-sas-dev-accountant"),
    "AKID-CEO": role_arn("agentes-sas-dev-ceo"),
    "AKID-OTHER-ACCOUNT": role_arn("agentes-sas-dev-ceo", "999999999999"),
    "AKID-OTHER-ENV": role_arn("agentes-sas-demo-ceo"),
    "AKID-UNKNOWN-ROLE": role_arn("agentes-sas-dev-intern"),
    "AKID-IAM-USER": f"arn:aws:iam::{ACCOUNT}:user/alice",
}


SECRET = "secret"


def signature_matches(url: str, headers: dict) -> bool:
    """Recompute the SigV4 query signature over the headers the verifier actually sent, like STS does."""
    parts = urlsplit(url)
    params = dict(parse_qsl(parts.query, keep_blank_values=True))
    key_id, _, region, service, _ = params["X-Amz-Credential"].split("/")
    unsigned = {k: v for k, v in params.items() if k != "X-Amz-Signature"}
    request = AWSRequest(
        method="GET",
        url=f"https://{parts.netloc}/?" + urlencode(unsigned, quote_via=quote),
        headers={AUDIENCE_HEADER: headers.get(AUDIENCE_HEADER, "")},
    )
    request.context["timestamp"] = params["X-Amz-Date"]
    auth = SigV4QueryAuth(Credentials(key_id, SECRET, params.get("X-Amz-Security-Token")), service, region)
    expected = auth.signature(auth.string_to_sign(request, auth.canonical_request(request)), request)
    return expected == params["X-Amz-Signature"]


class FakeSts:
    """Stands in for STS: verifies the signature and answers with the identity of the signing key."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = 200

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        key_id = dict(parse_qsl(urlsplit(url).query))["X-Amz-Credential"].split("/")[0]
        if self.status != 200 or not signature_matches(url, headers):
            return StsResponse(403, b"SignatureDoesNotMatch")
        body = {"GetCallerIdentityResponse": {"GetCallerIdentityResult": {"Arn": IDENTITIES[key_id], "Account": ACCOUNT}}}
        return StsResponse(200, json.dumps(body).encode())


def creds(key_id: str) -> Credentials:
    return Credentials(key_id, SECRET, "session-token")


def auth_header(key_id: str, region: str = "us-east-1", audience: str = AUDIENCE) -> dict:
    return {"authorization": f"{AUTH_SCHEME} {mint_token(creds(key_id), region, audience)}"}


@pytest.fixture
def sts() -> FakeSts:
    return FakeSts()


@pytest.fixture
def authenticator(sts) -> StsAuthenticator:
    return StsAuthenticator(region="us-east-1", audience=AUDIENCE, role_prefix=PREFIX, account_id=ACCOUNT, fetch=sts)


def test_role_maps_to_agent(authenticator, sts):
    assert authenticator.identify(auth_header("AKID-ACCOUNTANT")) == "accountant"
    url, headers = sts.calls[0]
    assert urlsplit(url).netloc == "sts.us-east-1.amazonaws.com"
    assert headers[AUDIENCE_HEADER] == AUDIENCE


def test_verified_tokens_are_cached(authenticator, sts):
    headers = auth_header("AKID-CEO")
    for _ in range(3):
        assert authenticator.identify(headers) == "ceo"
    assert len(sts.calls) == 1


@pytest.mark.parametrize(
    "key_id",
    ["AKID-OTHER-ACCOUNT", "AKID-OTHER-ENV", "AKID-UNKNOWN-ROLE", "AKID-IAM-USER"],
)
def test_callers_that_are_not_agents_of_this_company(authenticator, key_id):
    with pytest.raises(Unauthenticated):
        authenticator.identify(auth_header(key_id))


def test_token_for_another_audience_is_rejected_by_sts(authenticator):
    with pytest.raises(Unauthenticated, match="STS rejected"):
        authenticator.identify(auth_header("AKID-CEO", audience="some-other-service"))


def test_sts_rejection(authenticator, sts):
    sts.status = 403
    with pytest.raises(Unauthenticated):
        authenticator.identify(auth_header("AKID-CEO"))


def test_token_for_another_region_is_not_fetched(authenticator, sts):
    with pytest.raises(Unauthenticated, match="STS endpoint"):
        authenticator.identify(auth_header("AKID-CEO", region="us-west-2"))
    assert sts.calls == []


def encode(url: str) -> str:
    return f"{AUTH_SCHEME} {TOKEN_PREFIX}" + base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def test_token_cannot_point_the_mcp_at_another_host(authenticator, sts):
    evil = "https://attacker.example/?Action=GetCallerIdentity&Version=2011-06-15"
    with pytest.raises(Unauthenticated):
        authenticator.identify({"authorization": encode(evil)})
    assert sts.calls == []


def test_token_must_sign_the_audience_header(authenticator, sts):
    token = mint_token(creds("AKID-CEO"), "us-east-1", AUDIENCE)
    url = base64.urlsafe_b64decode(token[len(TOKEN_PREFIX):] + "==").decode()
    unbound = url.replace("host%3Bx-company-mcp-audience", "host")
    with pytest.raises(Unauthenticated, match="audience"):
        authenticator.identify({"authorization": encode(unbound)})
    assert sts.calls == []


def test_expired_token(sts):
    later = StsAuthenticator(
        region="us-east-1", audience=AUDIENCE, role_prefix=PREFIX, account_id=ACCOUNT, fetch=sts,
        now=lambda: datetime.now(UTC) + timedelta(minutes=16),
    )
    with pytest.raises(Unauthenticated, match="expired"):
        later.identify(auth_header("AKID-CEO"))


@pytest.mark.parametrize("headers", [{}, {"authorization": "Bearer abc"}, {"x-agent-id": "ceo"}])
def test_missing_or_foreign_credentials(authenticator, headers):
    with pytest.raises(Unauthenticated):
        authenticator.identify(headers)


class FakeSession:
    def __init__(self, key_id: str) -> None:
        self._credentials = creds(key_id)

    def get_credentials(self):
        return self._credentials


async def test_agent_cannot_impersonate_another_agent(service, authenticator):
    """Through the sidecar proxy, identity comes from the task role, whatever headers the agent sends."""
    mcp = create_server(service, authenticator).http_app(path="/mcp", stateless_http=True)
    with serve(mcp) as mcp_url:
        proxy = create_proxy(mcp_url, TokenSigner(FakeSession("AKID-ACCOUNTANT"), "us-east-1", AUDIENCE))
        with serve(proxy) as proxy_url:
            spoofed = {"X-Agent-Id": "ceo", "Authorization": "Company-STS forged"}
            async with Client(StreamableHttpTransport(f"{proxy_url}/mcp", headers=spoofed)) as agent:
                identity = (await agent.call_tool("whoami")).structured_content
                denied = await agent.call_tool("list_pending_decisions", raise_on_error=False)

            # Skipping the proxy leaves the caller unidentified.
            async with Client(StreamableHttpTransport(f"{mcp_url}/mcp", headers={"X-Agent-Id": "ceo"})) as direct:
                assert await direct.list_tools() == []

    assert identity["agentId"] == "accountant"
    assert json.loads(denied.content[0].text)["errorCode"] == "FORBIDDEN"


async def test_local_proxy_mode_also_ignores_agent_supplied_identity(service):
    """SIGNING_MODE=header: the proxy, not the agent, decides which X-Agent-Id is sent."""
    from company_mcp.iam import AgentIdHeader, HeaderAuthenticator

    mcp = create_server(service, HeaderAuthenticator()).http_app(path="/mcp", stateless_http=True)
    with serve(mcp) as mcp_url, serve(create_proxy(mcp_url, AgentIdHeader("accountant"))) as proxy_url:
        async with Client(StreamableHttpTransport(f"{proxy_url}/mcp", headers={"X-Agent-Id": "ceo"})) as agent:
            identity = (await agent.call_tool("whoami")).structured_content
    assert identity["agentId"] == "accountant"
