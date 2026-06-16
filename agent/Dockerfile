FROM python:3.12-slim

RUN pip install uv
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy workspace
COPY core /app/core
COPY agent /app/agent

WORKDIR /app/agent

# Sync dependencies via HTTPS
RUN uv sync --no-dev

# Add virtualenv bin to PATH to run uvicorn
ENV PATH="/app/agent/.venv/bin:$PATH"

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8001"]
