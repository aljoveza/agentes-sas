"""Signing proxy that runs next to each agent (sidecar in the same ECS task).

Hermes talks plain HTTP to http://127.0.0.1:<port>/mcp; the proxy adds an IAM token
minted from the task role and forwards to the Company MCP. The model never handles
credentials, and whatever it puts in headers cannot change who it is.

SIGNING_MODE=header (local only) sends X-Agent-Id=$AGENT_ID instead of a token, so the
same Hermes configuration works against a Company MCP running with AUTH_MODE=header.
"""

import os
from contextlib import asynccontextmanager
from typing import Protocol

import anyio
import boto3
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .iam import AgentIdHeader, TokenSigner

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}
# Stripped from agent requests: identity comes only from the signature.
_CALLER_CONTROLLED = {"authorization", "x-agent-id"}


class IdentityHeaders(Protocol):
    def headers(self) -> dict[str, str]: ...


def create_proxy(upstream: str, signer: IdentityHeaders, transport: httpx.AsyncBaseTransport | None = None) -> Starlette:
    upstream = upstream.rstrip("/")
    client: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def lifespan(app):
        nonlocal client
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, read=300.0), transport=transport
        ) as http:
            client = http
            yield

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def forward(request: Request) -> StreamingResponse:
        assert client is not None
        headers = [
            (name, value)
            for name, value in request.headers.items()
            if name not in _HOP_BY_HOP and name not in _CALLER_CONTROLLED
        ]
        headers.extend((await anyio.to_thread.run_sync(signer.headers)).items())
        url = upstream + request.url.path + (f"?{request.url.query}" if request.url.query else "")
        outbound = client.build_request(request.method, url, headers=headers, content=await request.body())
        response = await client.send(outbound, stream=True)
        return StreamingResponse(
            response.aiter_raw(),
            status_code=response.status_code,
            headers={name: value for name, value in response.headers.items() if name.lower() not in _HOP_BY_HOP},
            background=BackgroundTask(response.aclose),
        )

    methods = ["GET", "POST", "DELETE", "OPTIONS", "HEAD", "PUT", "PATCH"]
    return Starlette(
        routes=[Route("/proxy-health", health), Route("/{path:path}", forward, methods=methods)],
        lifespan=lifespan,
    )


def main() -> None:
    if os.environ.get("SIGNING_MODE", "iam") == "header":
        signer: IdentityHeaders = AgentIdHeader(os.environ["AGENT_ID"])
    else:
        region = os.environ["AWS_REGION"]
        signer = TokenSigner(boto3.Session(region_name=region), region, os.environ["MCP_AUDIENCE"])
    app = create_proxy(os.environ["UPSTREAM_URL"], signer)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("LISTEN_PORT", "9000")), log_level="warning")


if __name__ == "__main__":
    main()
