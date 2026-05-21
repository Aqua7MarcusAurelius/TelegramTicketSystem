# syntax=docker/dockerfile:1.6
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Корневой манифест и lockfile нужны uv для разрешения workspace.
COPY pyproject.toml uv.lock* ./
COPY shared/ ./shared/
COPY services/gateway-tg/ ./services/gateway-tg/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package gateway-tg

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/gateway-tg/src ./src
COPY --from=builder /app/shared/src ./shared_src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:/app/shared_src" \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "gateway_tg.main"]
