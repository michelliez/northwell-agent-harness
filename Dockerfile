# syntax=docker/dockerfile:1
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project --no-editable --group ui --group dense

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --group ui --group dense

FROM python:3.14-slim

WORKDIR /app

RUN useradd -m -u 1000 app

COPY --from=builder --chown=app:app /app /app

RUN chmod u+w /app && mkdir -p /app/.files && chmod -R u+w /app/.files

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

USER app

EXPOSE 8000 8501
