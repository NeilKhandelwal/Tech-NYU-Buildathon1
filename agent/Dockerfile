# syntax=docker/dockerfile:1
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# the project is a setuptools package rooted at src/, so src/ has to be present to sync
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN uv sync --frozen --no-dev

COPY . .

# turn-detector / VAD model weights, baked in so the container starts without a download
RUN python src/agent.py download-files

# LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET / BACKEND_URL come from the environment
CMD ["python", "src/agent.py", "dev"]
