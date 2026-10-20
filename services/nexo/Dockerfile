# syntax=docker/dockerfile:1
# Nexo for ECS Express Mode. It reuses the order simulator (and, through it, the Company MCP's
# IAM signer) and reads the demo scenarios, so those come in as named build contexts:
#   docker build --build-context order-simulator=services/order-simulator \
#                --build-context company-mcp=services/company-mcp \
#                --build-context scenarios=demo/scenarios services/nexo
# The layout mirrors the repository, so the path dependencies in pyproject.toml resolve.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
COPY --from=company-mcp . /app/services/company-mcp
COPY --from=order-simulator . /app/services/order-simulator
COPY --from=scenarios . /app/demo/scenarios
WORKDIR /app/services/nexo
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable
COPY server.py ./
COPY static ./static
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
ENV NEXO_HOST=0.0.0.0 NEXO_PORT=8090
EXPOSE 8090
CMD ["/app/services/nexo/.venv/bin/python", "server.py"]
