from python_core_utils import setup_logging
from agent.mcp_server import mcp

# Set up logging with correlation ID
setup_logging()

# FastMCP SSE app is a full Starlette app.
# We expose it directly so its lifespan is triggered properly by Uvicorn.
# It exposes the endpoint at /sse.
app = mcp.sse_app()
