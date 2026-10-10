FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.5 /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/
RUN uv sync --locked --no-dev --no-editable

FROM python:3.13-slim AS runtime
WORKDIR /app
RUN useradd --create-home --uid 10001 botfluencer
COPY --from=builder /app/.venv /app/.venv
COPY fixtures/ ./fixtures/
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER botfluencer
EXPOSE 8000
CMD ["botfluencer", "serve"]
