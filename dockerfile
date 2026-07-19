FROM python:3.12.13-slim-bookworm

# Copy a pinned uv executable from Astral's image.
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Keep the container environment outside /app.
# This prevents the development bind mount ".:/app" from hiding it.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy dependency metadata first for Docker layer caching.
COPY pyproject.toml uv.lock ./

# Install only production dependencies and refuse lockfile changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync \
        --locked \
        --no-dev \
        --no-install-project

# Copy application source after dependencies.
COPY app ./app
COPY scripts ./scripts

EXPOSE 8000

CMD ["uvicorn","app.main:app","--host","0.0.0.0","--port","8000"]
