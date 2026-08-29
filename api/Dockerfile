FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

COPY pyproject.toml README.md ./
RUN uv pip install --system -e ".[dev]" 2>/dev/null || \
    (mkdir -p src/recon && touch src/recon/__init__.py && uv pip install --system -e ".[dev]")

COPY . .

EXPOSE 8000
CMD ["uvicorn", "recon.app:app", "--host", "0.0.0.0", "--port", "8000"]
