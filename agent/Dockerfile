FROM python:3.12-slim

RUN pip install uv
RUN apt-get update && apt-get install -y git openssh-client

# Configure git to use ssh for github.com
RUN git config --global url."git@github.com:".insteadOf "https://github.com/"

WORKDIR /app

# Copy workspace
COPY core /app/core
COPY agent /app/agent

WORKDIR /app/agent

# Mount SSH deploy key secret and sync dependencies
RUN --mount=type=secret,id=deploy_key,target=/root/.ssh/id_rsa,mode=0600 \
    mkdir -p -m 0700 /root/.ssh && \
    ssh-keyscan github.com >> /root/.ssh/known_hosts && \
    uv sync

# Add virtualenv bin to PATH to run uvicorn
ENV PATH="/app/agent/.venv/bin:$PATH"

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8001"]