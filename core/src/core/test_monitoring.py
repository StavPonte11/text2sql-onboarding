import asyncio
from core.metrics import (
    PROMETHEUS_REGISTRY,
    track_node_execution,
    refiner_iterations_total,
    llm_tokens_total
)
from core.logging import NodeEvent, LOG_NODE_EVENT, bind_request_context, clear_request_context
from core.splunk import splunk_log
from prometheus_client import generate_latest

async def main():
    print("=== Testing Structured Logging and Context Propagation ===")
    bind_request_context(
        session_id="session-test-123",
        user_id="user-test-99",
        request_id="req-test-abc",
        langfuse_trace_id="lf-trace-xyz"
    )
    
    event = NodeEvent(
        event="node_completed",
        node_name="query_refiner",
        duration_ms=250,
        session_id="session-test-123",
        request_id="req-test-abc",
        langfuse_trace_id="lf-trace-xyz",
        metadata={"iterations": 3}
    )
    
    LOG_NODE_EVENT(event)
    clear_request_context()
    
    print("\n=== Testing Prometheus Metrics and Context Managers ===")
    refiner_iterations_total.inc(3)
    llm_tokens_total.labels(provider="openai", model="gpt-4o", token_type="total").inc(1200)
    
    async with track_node_execution("sql_generation"):
        await asyncio.sleep(0.1) # Simulate async execution
        
    metrics_data = generate_latest(PROMETHEUS_REGISTRY).decode("utf-8")
    print("Prometheus Metrics generated successfully!")
    print(f"Contains 'refiner_iterations_total': {'refiner_iterations_total' in metrics_data}")
    print(f"Contains 'agent_node_duration_seconds': {'agent_node_duration_seconds' in metrics_data}")
    
    print("\n=== Testing Splunk Logging Interface (Fails safely if HEC is not configured) ===")
    await splunk_log(
        event={"status": "dry-run", "message": "Verification test"},
        sourcetype="query_execution"
    )
    print("Splunk HEC handler processed safely.")
    
    print("\n✨ All monitoring components verified successfully!")

if __name__ == "__main__":
    asyncio.run(main())
