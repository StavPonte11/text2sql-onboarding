FROM python:3.12-slim

# Install uv, git and openssh-client
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*

# Configure git to use ssh for github.com
RUN git config --global url."git@github.com:".insteadOf "https://github.com/"

WORKDIR /app

# Copy workspace dependencies
COPY core /app/core
COPY backend /app/backend

WORKDIR /app/backend

# Mount SSH deploy key secret and sync dependencies
RUN --mount=type=secret,id=deploy_key,target=/root/.ssh/id_rsa,mode=0600 \
    mkdir -p -m 0700 /root/.ssh && \
    ssh-keyscan github.com >> /root/.ssh/known_hosts && \
    uv sync --no-dev --no-install-project

# Add virtualenv bin to PATH to run uvicorn
ENV PATH="/app/backend/.venv/bin:$PATH"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]