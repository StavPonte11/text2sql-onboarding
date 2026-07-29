import json
import uuid
from mcp.server.fastmcp import FastMCP

from agent.graph import agent_graph
from langgraph.types import Command
from sqlmodel import Session, select
from core.db.engine import engine
from core.models.models import Table, HttpExtractor, ExtractorStatus
from langfuse import observe

mcp = FastMCP("Text2SQL Agent")


@mcp.tool()
@observe()
async def chat_with_agent(
    query: str | None = None,
    thread_id: str | None = None,
    resume_value: str | dict | None = None,
    allowed_tables: list[str] | None = None,
    allowed_statuses: list[str] | None = None,
    extractors: list[str] | None = None,
    active_skills: list[str] | None = None,
    execution_mode: str | None = None,
    hitl_enabled: bool = True,
) -> str:
    """Run the Text2SQL agent to answer database queries.

    Args:
        query:          The natural language question to answer.
        thread_id:      Optional thread ID for session continuity.
        resume_value:   HITL resume payload (pass after receiving an interrupt).
        allowed_tables: Restrict the agent to specific tables.
        allowed_statuses: Filter tables by status.
        extractors:     List of extractor names/IDs to use.
        active_skills:  List of Jeen skill UUIDs to inject.
        execution_mode: Named configuration preset (e.g. 'cost_saving',
                        'high_quality', 'benchmark'). Overrides flag defaults
                        for this invocation only.
        hitl_enabled:   If False, skip all human-in-the-loop interrupts.
    """
    thread_id = thread_id or str(uuid.uuid4())
    
    langfuse_handler = None
    try:
        from core.langfuse import get_langfuse_handler
        langfuse_handler = get_langfuse_handler()
        callbacks = [langfuse_handler] if langfuse_handler else []
    except Exception:
        callbacks = []

    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": callbacks,
    }

    if resume_value is not None:
        state_snapshot = await agent_graph.aget_state(config)
        if not state_snapshot.values:
            return json.dumps(
                {
                    "error": f"Thread ID '{thread_id}' not found or has no active session."
                }
            )

        update_dict = {
            "last_error": None,
            "trino_error": None,
            "escalated": None,
            "escalation_reason": None,
            "refinement_count": 0,
        }

        # If paused at a node breakpoint (interrupt_before), manually map the resume
        # value into the state, since Command(resume=) only targets dynamic interrupt()
        if state_snapshot.next:
            if isinstance(resume_value, str):
                update_dict["feedback"] = resume_value
            elif isinstance(resume_value, dict) and "feedback" in resume_value:
                update_dict["feedback"] = resume_value["feedback"]

        result = await agent_graph.ainvoke(Command(
            update=update_dict,
            resume=resume_value
        ), config=config)
    else:
        if not query:
            return json.dumps({"error": "Query is required for new chat session."})

        if allowed_tables:
            with Session(engine) as session:
                all_tables = session.exec(select(Table)).all()
                for allowed in allowed_tables:
                    exists = False
                    for t in all_tables:
                        if (
                            t.id == allowed
                            or t.name == allowed
                            or f"{t.schema_name}.{t.name}" == allowed
                        ):
                            exists = True
                            break
                    if not exists:
                        return json.dumps(
                            {"error": f"Table '{allowed}' does not exist."}
                        )

        active_extractors = []
        with Session(engine) as session:
            if extractors:
                for ext_name_or_id in extractors:
                    ext = session.exec(
                        select(HttpExtractor).where(
                            (HttpExtractor.id == ext_name_or_id)
                            | (HttpExtractor.name == ext_name_or_id)
                        )
                    ).first()
                    if not ext:
                        return json.dumps(
                            {"error": f"Extractor '{ext_name_or_id}' does not exist."}
                        )
                    active_extractors.append({"name": ext.name, "url": ext.url})
            else:
                prod_extractors = session.exec(
                    select(HttpExtractor).where(
                        HttpExtractor.status == ExtractorStatus.production
                    )
                ).all()
                for ext in prod_extractors:
                    active_extractors.append({"name": ext.name, "url": ext.url})

        result = await agent_graph.ainvoke(
            {
                "user_query": query,
                "allowed_tables": allowed_tables,
                "allowed_statuses": allowed_statuses,
                "active_extractors": active_extractors,
                "active_skills": active_skills,
                "execution_mode": execution_mode,
                "non_interactive": not hitl_enabled,
            },
            config=config,
        )

    # Get trace_id from the Langfuse handler if available
    trace_id = getattr(langfuse_handler, "last_trace_id", None) if langfuse_handler else None
    
    if langfuse_handler and hasattr(langfuse_handler, "flush"):
        langfuse_handler.flush()

    from agent.langfuse_client import langfuse_client
    langfuse_client.flush()

    final_state = await agent_graph.aget_state(config)
    
    # Check if interrupted by `interrupt()` function
    if final_state.interrupts:
        interrupt_val = final_state.interrupts[-1].value
        return json.dumps(
            {
                "thread_id": thread_id,
                "status": "interrupted",
                "interrupt_details": interrupt_val,
                "sql_query": final_state.values.get("sql_query") or (interrupt_val.get("sql_query") if isinstance(interrupt_val, dict) else None),
                "sql_explanation": interrupt_val.get("sql_explanation") if isinstance(interrupt_val, dict) else None,
                "trace_id": trace_id,
                "execution_path": final_state.values.get("execution_path", []),
            }
        )
        
    # Check if interrupted by `interrupt_before` breakpoint
    if final_state.next:
        next_node = final_state.next[0]
        # Build an artificial interrupt detail based on state
        interrupt_val = {
            "type": "hitl_escalation",
            "reason": final_state.values.get("escalation_reason", f"Paused before {next_node}"),
        }
        return json.dumps(
            {
                "thread_id": thread_id,
                "status": "interrupted",
                "interrupt_details": interrupt_val,
                "sql_query": final_state.values.get("sql_query"),
                "trace_id": trace_id,
                "execution_path": final_state.values.get("execution_path", []),
            }
        )

    is_unans = False
    if final_state.values.get("ambiguity_type") == "unanswerable" or final_state.values.get("failure_reason"):
        is_unans = True

    return json.dumps(
        {
            "thread_id": thread_id,
            "status": "completed",
            "summary": result.get("summary", ""),
            "raw_data_ref": result.get("raw_data_ref"),
            "sql_query": result.get("sql_query"),
            "sql_explanation": result.get("sql_explanation"),
            "trace_id": trace_id,
            "execution_path": result.get("execution_path", []),
            "is_unanswerable": is_unans,
        }
    )

@mcp.tool()
async def suggest_fixes(thread_id: str, category: str) -> str:
    """Generate quick fix suggestions during an interruption."""
    from agent.llm import get_llm
    from pydantic import BaseModel, Field
    
    state_snapshot = await agent_graph.aget_state({"configurable": {"thread_id": thread_id}})
    if not state_snapshot.values:
        return "[]"
    
    sql_query = state_snapshot.values.get("sql_query", "")
    jeen_catalog = state_snapshot.values.get("jeen_catalog", "")
    user_query = state_snapshot.values.get("user_query", "")
    runtime_flags = state_snapshot.values.get("runtime_flags", {})
    
    llm = get_llm(node="routing", runtime_flags=runtime_flags)
    
    class Fixes(BaseModel):
        suggestions: list[str] = Field(description="2-3 short, actionable suggested fixes (under 60 chars each)")
        
    prompt = f"""
    The user rejected the agent's Text2SQL output with category '{category}'.
    User Query: {user_query}
    Current SQL: {sql_query}
    Current Plan: {jeen_catalog}
    
    Provide 2-3 short, distinct button labels for the user to quickly apply a fix.
    For example: "GROUP BY date instead of month", "Include cancelled orders", "Filter by region".
    """
    
    structured_llm = llm.with_structured_output(Fixes, method="json_schema")
    try:
        res = await structured_llm.ainvoke(prompt)
        if isinstance(res, dict):
            suggestions = res.get("suggestions", [])
        else:
            suggestions = res.suggestions
        return json.dumps(suggestions)
    except Exception as e:
        return json.dumps([])

if __name__ == "__main__":
    mcp.run(transport="stdio")
