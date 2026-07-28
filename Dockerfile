FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer, cached independently of application code changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Application code.
COPY agents/ agents/
COPY orchestrator/ orchestrator/
COPY gateway/ gateway/
COPY shared/ shared/
COPY scripts/ scripts/
COPY policies/ policies/
COPY alembic/ alembic/
COPY alembic.ini config.yaml ./
RUN uv sync --frozen
