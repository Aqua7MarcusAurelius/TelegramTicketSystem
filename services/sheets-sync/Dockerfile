# syntax=docker/dockerfile:1.6
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY shared/ ./shared/
COPY services/sheets-sync/ ./services/sheets-sync/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package sheets-sync

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/services/sheets-sync/src ./src
COPY --from=builder /app/services/sheets-sync/migrations ./migrations
COPY --from=builder /app/services/sheets-sync/alembic.ini ./alembic.ini
COPY --from=builder /app/shared/src ./shared_src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:/app/shared_src" \
    PYTHONUNBUFFERED=1

CMD ["python", "-m", "sheets_sync.main"]
