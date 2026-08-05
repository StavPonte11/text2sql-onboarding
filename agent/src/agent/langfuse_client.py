import warnings
import urllib3
from langfuse import Langfuse
from opentelemetry import trace as otel_trace_api
from agent.config import settings

# Suppress unverified HTTPS warnings for dev internal endpoints
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

langfuse_client = Langfuse(
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    secret_key=settings.LANGFUSE_SECRET_KEY,
    host=settings.LANGFUSE_BASE_URL,
)

# Patch update_current_span and get_current_trace_id to safely no-op when running without an active OpenTelemetry span context
_orig_update_current_span = langfuse_client.update_current_span
_orig_get_current_trace_id = langfuse_client.get_current_trace_id


def _safe_update_current_span(*args, **kwargs):
    current_span = otel_trace_api.get_current_span()
    if current_span is otel_trace_api.INVALID_SPAN:
        return
    return _orig_update_current_span(*args, **kwargs)


def _safe_get_current_trace_id(*args, **kwargs):
    current_span = otel_trace_api.get_current_span()
    if current_span is otel_trace_api.INVALID_SPAN:
        return None
    return _orig_get_current_trace_id(*args, **kwargs)


langfuse_client.update_current_span = _safe_update_current_span
langfuse_client.get_current_trace_id = _safe_get_current_trace_id


