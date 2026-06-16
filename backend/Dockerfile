FROM python:3.12-slim

# Install uv and git
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy workspace dependencies
COPY core /app/core
COPY backend /app/backend
COPY python-core-utils /app/python-core-utils

WORKDIR /app/backend

# Sync dependencies via HTTPS
RUN uv sync --no-dev --no-install-project

# Add virtualenv bin to PATH to run uvicorn
ENV PATH="/app/backend/.venv/bin:$PATH"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
