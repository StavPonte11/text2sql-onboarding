import json
import uuid
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from agent.graph import agent_graph
from langgraph.types import Command
from sqlmodel import Session, select
from core.db.engine import engine
from core.models.models import Table, HttpExtractor, ExtractorStatus

mcp = FastMCP("Text2SQL Agent")

@mcp.tool()
async def chat_with_agent(
    query: str | None = None,
    thread_id: str | None = None,
    resume_value: str | dict | None = None,
    allowed_tables: list[str] | None = None,
    allowed_statuses: list[str] | None = None,
    extractors: list[str] | None = None,
) -> str:
    """Run the Text2SQL agent to answer database queries."""
    thread_id = thread_id or str(uuid.uuid4())
    config = {
        "configurable": {"thread_id": thread_id},
    }

    if resume_value is not None:
        state_snapshot = await agent_graph.aget_state(config)
        if not state_snapshot.values:
            return json.dumps({"error": f"Thread ID '{thread_id}' not found or has no active session."})
            
        result = await agent_graph.ainvoke(
            Command(resume=resume_value),
            config=config
        )
    else:
        if not query:
            return json.dumps({"error": "Query is required for new chat session."})
            
        if allowed_tables:
            with Session(engine) as session:
                all_tables = session.exec(select(Table)).all()
                for allowed in allowed_tables:
                    exists = False
                    for t in all_tables:
                        if (t.id == allowed or t.name == allowed or f"{t.schema_name}.{t.name}" == allowed):
                            exists = True
                            break
                    if not exists:
                        return json.dumps({"error": f"Table '{allowed}' does not exist."})

        active_extractors = []
        with Session(engine) as session:
            if extractors:
                for ext_name_or_id in extractors:
                    ext = session.exec(select(HttpExtractor).where(
                        (HttpExtractor.id == ext_name_or_id) | (HttpExtractor.name == ext_name_or_id)
                    )).first()
                    if not ext:
                        return json.dumps({"error": f"Extractor '{ext_name_or_id}' does not exist."})
                    active_extractors.append({"name": ext.name, "url": ext.url})
            else:
                prod_extractors = session.exec(select(HttpExtractor).where(HttpExtractor.status == ExtractorStatus.production)).all()
                for ext in prod_extractors:
                    active_extractors.append({"name": ext.name, "url": ext.url})

        result = await agent_graph.ainvoke(
            {
                "user_query": query, 
                "allowed_tables": allowed_tables,
                "allowed_statuses": allowed_statuses,
                "active_extractors": active_extractors
            },
            config=config
        )

    final_state = await agent_graph.aget_state(config)
    if final_state.interrupts:
        interrupt_val = final_state.interrupts[-1].value
        return json.dumps({
            "thread_id": thread_id,
            "status": "interrupted",
            "interrupt_details": interrupt_val,
            "schema_plan": final_state.values.get("schema_plan"),
            "sql_query": final_state.values.get("sql_query")
        })

    return json.dumps({
        "thread_id": thread_id,
        "status": "completed",
        "summary": result.get("summary", ""),
        "raw_data_ref": result.get("raw_data_ref"),
        "sql_query": result.get("sql_query"),
        "sql_explanation": result.get("sql_explanation"),
        "schema_plan": result.get("schema_plan")
    })

if __name__ == "__main__":
    mcp.run()
