from python_core_utils import setup_logging
from agent.mcp_server import mcp

# Set up logging with correlation ID
setup_logging()

app = mcp.streamable_http_app()
