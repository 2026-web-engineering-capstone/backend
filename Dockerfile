# ── Stage 1: build ──────────────────────────────────────────
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

COPY app/ ./app/

# ── Stage 2: runtime ───────────────────────────────────────
FROM python:3.13-slim-bookworm

RUN groupadd --system appuser && \
    useradd --system --gid appuser --create-home appuser

WORKDIR /app

RUN mkdir -p /app/data && chown appuser:appuser /app/data

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/app /app/app

ENV PATH="/app/.venv/bin:$PATH"

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
