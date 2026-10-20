"""IAM caller identity for MCP requests (the EKS / Vault "aws-iam" pattern).

The caller presigns an STS GetCallerIdentity request with its own credentials and sends
the URL as a bearer token. The MCP executes that request against STS: STS verifies the
signature and answers with the caller's role ARN, which maps to an agent id. No shared
secrets, and the model never sees credentials — the agent's signing proxy adds the token.

The presigned request covers an audience header, so a token minted for this MCP cannot
be replayed against another service that uses the same pattern, and vice versa.
"""

import base64
import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import parse_qs, urlsplit

from botocore.auth import SigV4QueryAuth
from botocore.awsrequest import AWSRequest

from . import policy

AUTH_SCHEME = "Company-STS"
AUDIENCE_HEADER = "x-company-mcp-audience"
TOKEN_PREFIX = "sts-v1."
TOKEN_LIFETIME = timedelta(minutes=15)
CLOCK_SKEW = timedelta(minutes=5)

_ALLOWED_PARAMS = {
    "Action",
    "Version",
    "X-Amz-Algorithm",
    "X-Amz-Credential",
    "X-Amz-Date",
    "X-Amz-Expires",
    "X-Amz-SignedHeaders",
    "X-Amz-Signature",
    "X-Amz-Security-Token",
}
_ASSUMED_ROLE = re.compile(r"^arn:aws:sts::(\d{12}):assumed-role/([\w+=,.@-]+)/[\w+=,.@-]+$")


class Unauthenticated(Exception):
    """The request carries no valid IAM identity."""


# --------------------------------------------------------------------------- signing (caller side)


class TokenSigner:
    """Mints tokens from a botocore session; credentials refresh automatically on ECS."""

    def __init__(self, session: Any, region: str, audience: str, refresh_after: float = 300) -> None:
        self._session = session
        self._region = region
        self._audience = audience
        self._refresh_after = refresh_after
        self._lock = threading.Lock()
        self._token: str | None = None
        self._minted_at = 0.0

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() - self._minted_at > self._refresh_after:
                credentials = self._session.get_credentials()
                if credentials is None:
                    raise Unauthenticated("No AWS credentials available to sign MCP requests.")
                self._token = mint_token(credentials.get_frozen_credentials(), self._region, self._audience)
                self._minted_at = time.monotonic()
            return self._token

    def headers(self) -> dict[str, str]:
        return {"authorization": f"{AUTH_SCHEME} {self.token()}"}


class AgentIdHeader:
    """Local development only: pairs with HeaderAuthenticator on the MCP side."""

    def __init__(self, agent_id: str) -> None:
        self._agent_id = agent_id

    def headers(self) -> dict[str, str]:
        return {"x-agent-id": self._agent_id}


def mint_token(credentials: Any, region: str, audience: str) -> str:
    request = AWSRequest(
        method="GET",
        url=f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15",
        headers={AUDIENCE_HEADER: audience},
    )
    SigV4QueryAuth(credentials, "sts", region, expires=int(TOKEN_LIFETIME.total_seconds())).add_auth(request)
    return TOKEN_PREFIX + base64.urlsafe_b64encode(request.url.encode()).decode().rstrip("=")


# --------------------------------------------------------------------------- verification (MCP side)


class Authenticator(Protocol):
    def identify(self, headers: Mapping[str, str]) -> str:
        """Return the calling agent id or raise Unauthenticated."""
        ...


class HeaderAuthenticator:
    """Local development only: trusts X-Agent-Id."""

    def identify(self, headers: Mapping[str, str]) -> str:
        agent = headers.get("x-agent-id", "").strip()
        if not policy.is_known(agent):
            raise Unauthenticated("Unknown or missing X-Agent-Id.")
        return agent


@dataclass(frozen=True)
class StsResponse:
    status: int
    body: bytes


def fetch_sts(url: str, headers: Mapping[str, str]) -> StsResponse:
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 - URL host is validated
            return StsResponse(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return StsResponse(exc.code, exc.read())


class StsAuthenticator:
    """Verifies presigned GetCallerIdentity tokens and maps <role_prefix><agent-id> roles to agents."""

    def __init__(
        self,
        *,
        region: str,
        audience: str,
        role_prefix: str,
        account_id: str,
        fetch: Callable[[str, Mapping[str, str]], StsResponse] = fetch_sts,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._host = f"sts.{region}.amazonaws.com"
        self._audience = audience
        self._role_prefix = role_prefix
        self._account_id = account_id
        self._fetch = fetch
        self._now = now
        self._cache: dict[str, tuple[str, datetime]] = {}
        self._lock = threading.Lock()

    def identify(self, headers: Mapping[str, str]) -> str:
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme != AUTH_SCHEME or not token:
            raise Unauthenticated(f"Expected 'Authorization: {AUTH_SCHEME} <token>'.")

        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
        if cached and cached[1] > self._now():
            return cached[0]

        url, expires_at = self._validate_token(token)
        agent = self._agent_for(self._caller_arn(url))
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if v[1] > self._now()}
            self._cache[key] = (agent, expires_at)
        return agent

    def _validate_token(self, token: str) -> tuple[str, datetime]:
        if not token.startswith(TOKEN_PREFIX):
            raise Unauthenticated("Malformed token.")
        encoded = token[len(TOKEN_PREFIX) :]
        try:
            url = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        except ValueError:
            raise Unauthenticated("Malformed token.") from None

        # Only ever call the regional STS endpoint: the token must not steer our request elsewhere.
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc != self._host or parts.path not in ("", "/") or parts.fragment:
            raise Unauthenticated("Token does not target this region's STS endpoint.")
        try:
            raw = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise Unauthenticated("Malformed token.") from None
        if any(len(values) != 1 for values in raw.values()) or not set(raw) <= _ALLOWED_PARAMS:
            raise Unauthenticated("Unexpected token parameters.")
        params = {name: values[0] for name, values in raw.items()}

        if params.get("Action") != "GetCallerIdentity" or params.get("Version") != "2011-06-15":
            raise Unauthenticated("Token is not a GetCallerIdentity request.")
        if AUDIENCE_HEADER not in params.get("X-Amz-SignedHeaders", "").split(";"):
            raise Unauthenticated("Token is not bound to this audience.")
        try:
            signed_at = datetime.strptime(params["X-Amz-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except (KeyError, ValueError):
            raise Unauthenticated("Token has no valid signing date.") from None
        now = self._now()
        expires_at = signed_at + TOKEN_LIFETIME
        if signed_at > now + CLOCK_SKEW or expires_at <= now:
            raise Unauthenticated("Token expired.")
        return url, expires_at

    def _caller_arn(self, url: str) -> str:
        try:
            response = self._fetch(url, {AUDIENCE_HEADER: self._audience, "accept": "application/json"})
        except OSError as exc:
            raise Unauthenticated(f"STS unreachable: {exc}") from exc
        if response.status != 200:
            raise Unauthenticated(f"STS rejected the token (HTTP {response.status}).")
        try:
            result = json.loads(response.body)["GetCallerIdentityResponse"]["GetCallerIdentityResult"]
            return result["Arn"]
        except (ValueError, KeyError, TypeError):
            raise Unauthenticated("Unexpected STS response.") from None

    def _agent_for(self, arn: str) -> str:
        match = _ASSUMED_ROLE.match(arn)
        if not match:
            raise Unauthenticated("Caller is not an assumed IAM role.")
        account, role = match.groups()
        if account != self._account_id:
            raise Unauthenticated("Caller belongs to another AWS account.")
        agent = role.removeprefix(self._role_prefix)
        if not role.startswith(self._role_prefix) or not policy.is_known(agent):
            raise Unauthenticated(f"Role {role} is not a company agent.")
        return agent
