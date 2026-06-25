import time
from typing import Dict, Any
from starlette.types import ASGIApp, Scope, Receive, Send, Message
from starlette.routing import Match
from core.metrics import text2sql_requests_total, text2sql_request_duration_seconds


class PrometheusMiddleware:
    """
    Pure ASGI Middleware that hooks into HTTP request flows to measure request duration
    and count processed requests. Completely safe for streaming/SSE responses.
    Uses route templates (e.g. '/query/{query_id}') to avoid cardinality explosion.
    """
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        
        # Identify route template path
        route = "unknown"
        method = scope.get("method", "UNKNOWN")
        
        app = scope.get("app")
        if app is not None and hasattr(app, "routes"):
            for r in app.routes:
                match_result, _ = r.matches(scope)
                if match_result == Match.FULL:
                    route = r.path
                    break
                elif match_result == Match.PARTIAL and route == "unknown":
                    route = r.path
        
        if route == "unknown":
            route = scope.get("path", "unknown")

        status_code = [500]

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code[0] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            status_code[0] = 500
            raise exc
        finally:
            duration = time.perf_counter() - start_time
            
            # Observe request status in metrics registry
            try:
                text2sql_requests_total.labels(
                    method=method,
                    route=route,
                    status_code=str(status_code[0])
                ).inc()
                
                text2sql_request_duration_seconds.labels(
                    method=method,
                    route=route,
                    status_code=str(status_code[0])
                ).observe(duration)
            except Exception:
                # Shield telemetry failures from interrupting the request lifecycle
                pass
