import time
from contextlib import AbstractContextManager, AbstractAsyncContextManager
from typing import Optional, Type
from types import TracebackType
from prometheus_client import CollectorRegistry, Counter, Histogram

# Create an isolated Prometheus collector registry to prevent global state contamination
# and ensure deterministic, isolated tests.
PROMETHEUS_REGISTRY = CollectorRegistry()

# ---------------------------------------------------------
# Metric Definitions
# ---------------------------------------------------------

# API Layer Metrics
text2sql_requests_total = Counter(
    name="text2sql_requests_total",
    documentation="Total number of HTTP requests processed by the Text-to-SQL API layer",
    labelnames=["method", "route", "status_code"],
    registry=PROMETHEUS_REGISTRY
)

text2sql_request_duration_seconds = Histogram(
    name="text2sql_request_duration_seconds",
    documentation="Latency of HTTP requests processed by the Text-to-SQL API layer in seconds",
    labelnames=["method", "route", "status_code"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0),
    registry=PROMETHEUS_REGISTRY
)

# LangGraph Node Metrics
agent_node_duration_seconds = Histogram(
    name="agent_node_duration_seconds",
    documentation="Latency of individual LangGraph node executions in seconds",
    labelnames=["node_name"],
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=PROMETHEUS_REGISTRY
)

agent_node_errors_total = Counter(
    name="agent_node_errors_total",
    documentation="Total number of execution errors within individual LangGraph nodes",
    labelnames=["node_name", "error_type"],
    registry=PROMETHEUS_REGISTRY
)

# Refiner Specific Metrics
refiner_iterations_total = Counter(
    name="refiner_iterations_total",
    documentation="Total number of refinement iterations executed in the SQL generation workflow",
    registry=PROMETHEUS_REGISTRY
)

refiner_max_loop_fallbacks_total = Counter(
    name="refiner_max_loop_fallbacks_total",
    documentation="Total times refinement loop hit maximum iterations and fell back to safety defaults",
    registry=PROMETHEUS_REGISTRY
)

# Accuracy & Quality Metrics
schema_hallucinated_tables_total = Counter(
    name="schema_hallucinated_tables_total",
    documentation="Total count of queries attempting to access non-existent tables (schema hallucinations)",
    labelnames=["table_name"],
    registry=PROMETHEUS_REGISTRY
)

# ESCA Storage Metrics
esca_write_failures_total = Counter(
    name="esca_write_failures_total",
    documentation="Total count of payload write failures to the ESCA tiered storage backend",
    labelnames=["failure_type"],
    registry=PROMETHEUS_REGISTRY
)

# Trino Query Metrics
trino_query_duration_seconds = Histogram(
    name="trino_query_duration_seconds",
    documentation="Latency of database query executions via Trino in seconds",
    labelnames=["catalog", "schema"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=PROMETHEUS_REGISTRY
)

# LLM Token Metrics
llm_tokens_total = Counter(
    name="llm_tokens_total",
    documentation="Total count of LLM tokens consumed by provider, model, and token type (prompt, completion, total)",
    labelnames=["provider", "model", "token_type"],
    registry=PROMETHEUS_REGISTRY
)


# ---------------------------------------------------------
# Reusable Context Managers for LangGraph Instrumentation
# ---------------------------------------------------------

class NodeExecutionTracker(AbstractContextManager["NodeExecutionTracker"], AbstractAsyncContextManager["NodeExecutionTracker"]):
    """
    Context manager and async context manager that tracks execution duration and errors
    for a specific LangGraph node. Uses time.perf_counter for high precision.
    """
    def __init__(self, node_name: str, track_errors: bool = True) -> None:
        self.node_name = node_name
        self.track_errors = track_errors
        self._start_time: float = 0.0

    def __enter__(self) -> "NodeExecutionTracker":
        self._start_time = time.perf_counter()
        return self
    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType]
    ) -> None:
        duration = time.perf_counter() - self._start_time
        agent_node_duration_seconds.labels(node_name=self.node_name).observe(duration)
        
        if self.track_errors and exc_type is not None:
            error_type = exc_type.__name__
            agent_node_errors_total.labels(node_name=self.node_name, error_type=error_type).inc()

    async def __aenter__(self) -> "NodeExecutionTracker":
        self._start_time = time.perf_counter()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType]
    ) -> None:
        self.__exit__(exc_type, exc_value, traceback)
def track_node_duration(node_name: str) -> NodeExecutionTracker:
    """
    Returns a context manager that only measures the execution duration of the node.
    Usage:
        with track_node_duration("sql_generator"):
            ...
        async with track_node_duration("async_generator"):
            ...
    """
    return NodeExecutionTracker(node_name, track_errors=False)


def track_node_execution(node_name: str) -> NodeExecutionTracker:
    """
    Returns a context manager that measures duration and automatically tracks any
    raised exceptions as node errors.
    Usage:
        with track_node_execution("sql_generator"):
            ...
        async with track_node_execution("async_generator"):
            ...
    """
    return NodeExecutionTracker(node_name, track_errors=True)
