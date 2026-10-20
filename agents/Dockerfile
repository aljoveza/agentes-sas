# syntax=docker/dockerfile:1
# One image for every employee: Hermes plus the agent runtime (event adapter).
# Who the employee is comes from the environment (AGENT_ID, prompt location, queue).
FROM nousresearch/hermes-agent@sha256:444b42594e7f467425872fc35e077a227d377f0fe0726bc818706d62175fad8b

COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# The runtime gets its own virtualenv so it never disturbs Hermes' dependencies.
WORKDIR /opt/agent-runtime
COPY runtime/pyproject.toml runtime/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --python /usr/bin/python3
COPY runtime/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --python /usr/bin/python3

# The Company MCP's signing proxy, in its own virtualenv. In ECS Express Mode (one container
# per task) the runtime starts it on 127.0.0.1; locally it runs as a separate container.
# Build with: docker build --build-context company-mcp=services/company-mcp agents
WORKDIR /opt/mcp-proxy
COPY --from=company-mcp pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --python /usr/bin/python3
COPY --from=company-mcp src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --python /usr/bin/python3

WORKDIR /opt/agent-runtime
# Hermes plugins the runtime installs into HERMES_HOME when enabled (agent activity publisher).
COPY plugins ./plugins

WORKDIR /opt/data
# The Hermes entrypoint runs this as the unprivileged hermes user with HERMES_HOME=/opt/data.
CMD ["/opt/agent-runtime/.venv/bin/agent-runtime"]
