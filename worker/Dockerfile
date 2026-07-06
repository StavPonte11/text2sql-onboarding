FROM python:3.12-slim

# Install uv, git and openssh-client
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client && rm -rf /var/lib/apt/lists/*

# Configure git to use ssh for github.com
RUN git config --global url."git@github.com:".insteadOf "https://github.com/"

WORKDIR /app

# Copy workspace dependencies
COPY core /app/core
COPY worker /app/worker

WORKDIR /app/worker

# Mount SSH deploy key secret and sync dependencies
RUN --mount=type=secret,id=deploy_key,target=/root/.ssh/id_rsa,mode=0600 \
    mkdir -p -m 0700 /root/.ssh && \
    ssh-keyscan github.com >> /root/.ssh/known_hosts && \
    uv sync --no-dev --no-install-project

# Add virtualenv bin to PATH
ENV PATH="/app/worker/.venv/bin:$PATH"

CMD ["python", "-m", "app.main"]
