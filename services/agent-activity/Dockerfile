# syntax=docker/dockerfile:1
# Stdlib only: the image is just Python and this package.
FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache .
RUN useradd --system --uid 10001 --no-create-home app
USER 10001
EXPOSE 8113
CMD ["agent-activity"]
