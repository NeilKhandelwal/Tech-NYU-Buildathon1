# syntax=docker/dockerfile:1
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    DATABASE_URL=sqlite:////data/cases.db

WORKDIR /app

# deps first so a source edit doesn't reinstall them (the project itself declares no
# build-system, so `uv sync` only ever installs dependencies)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# migrate on every boot, then seed -- seed is a no-op unless the cases table is empty,
# so a restart never touches an existing database
RUN mkdir -p /data && printf '%s\n' \
    '#!/bin/sh' \
    'set -e' \
    'alembic upgrade head' \
    'python -m scripts.seed' \
    'exec "$@"' > /entrypoint.sh && chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
