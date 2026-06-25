from python_core_utils import setup_logging
from agent.mcp_server import mcp
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response
from core.metrics import PROMETHEUS_REGISTRY
from core.metrics_middleware import PrometheusMiddleware

# Set up logging with correlation ID
setup_logging()

# FastMCP SSE app is a full Starlette app.
# We expose it directly so its lifespan is triggered properly by Uvicorn.
# It exposes the endpoint at /sse.
app = mcp.sse_app()

# Register Prometheus middleware for tracking agent request volumes and latencies
app.add_middleware(PrometheusMiddleware)

async def metrics(request) -> Response:
    """
    Exposes Prometheus metrics gathered from the custom isolated collector registry for the agent app.
    """
    return Response(
        content=generate_latest(PROMETHEUS_REGISTRY),
        media_type=CONTENT_TYPE_LATEST
    )

app.add_route("/metrics", metrics)
app = mcp.streamable_http_app()
