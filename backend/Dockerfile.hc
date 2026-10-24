# syntax=docker/dockerfile:1
# Heat Check API (app/hc.py). Unlike ./Dockerfile there is no migrate/seed entrypoint: the
# schema lives in Supabase (supabase/schema.sql) and this service never alters it.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.hc:app", "--host", "0.0.0.0", "--port", "8000"]
