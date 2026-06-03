from langgraph.types import interrupt
import json
import urllib.request
from typing import Any, List, Optional
from pydantic import BaseModel, Field
from esca_sdk import EscaClient
        
from agent.state import AgentState
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from sqlalchemy import text
from core.db.engine import engine
from core.models.models import Table, TableProfile, ColumnProfile, EnrichmentVersion
from sqlmodel import Session, select
from agent.config import settings

# Initialize LLM
llm = ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_URL, temperature=0)

# Define standardized Schema Explorer Output Type
class SchemaExplorerOutput(BaseModel):
    schema_plan: Optional[Any] = Field(
        default=None,
        description="Detailed query plan describing tables, columns, and joins."
    )
    ambiguity_detected: bool = Field(
        default=False,
        description="Set to true if there is table selection ambiguity."
    )
    ambiguity_message: str = Field(
        default="",
        description="A question to ask the user to clarify/select the right table(s). Must be empty if ambiguity_detected is false."
    )
    candidate_options: List[str] = Field(
        default_factory=list,
        description="List of strings (table names or options) for the user to choose from. Must be empty if ambiguity_detected is false."
    )

def get_query_embedding(text: str) -> list[float]:
    """Generate 768-dimensional embedding from nomic-embed-text."""
    # TODO: support secret
    url = f"{settings.EMBEDDER_URL}/api/embeddings"
    data = json.dumps({"model": settings.EMBEDDER_MODEL, "prompt": text}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode())["embedding"]
    except Exception as e:
        print(f"Error getting query embedding: {e}")
        return [0.0] * 768

def hybrid_search_tables(query: str, query_embedding: list[float], session: Session, allowed_tables: list[str] | None = None) -> list[Table]:
    """Hybrid search combining pgvector cosine distance and keyword matching."""
    # 1. Get all allowed tables
    stmt_all = select(Table)
    all_tables = session.exec(stmt_all).all()
    
    allowed = allowed_tables or []
    allowed_tables_set = []
    allowed_ids = set()
    for table in all_tables:
        is_allowed = (
            table.status == "production" or
            (allowed and (
                table.id in allowed or
                table.name in allowed or
                f"{table.schema_name}.{table.name}" in allowed
            ))
        )
        if is_allowed:
            allowed_tables_set.append(table)
            allowed_ids.add(table.id)

    # 2. Vector Search
    if allowed_ids:
        stmt = text("""
            SELECT id FROM tables
            WHERE id = ANY(:allowed_ids)
            ORDER BY embedding <=> :emb
            LIMIT 10
        """)
        try:
            vec_ids = [row[0] for row in session.execute(stmt, {"emb": str(query_embedding), "allowed_ids": list(allowed_ids)}).fetchall()]
        except Exception as e:
            print(f"Vector search failed: {e}")
            vec_ids = []
    else:
        vec_ids = []

    # 3. Keyword Search
    keyword_matches = []
    query_words = query.lower().split()
    for table in allowed_tables_set:
        enrichment = session.exec(
            select(EnrichmentVersion)
            .where(EnrichmentVersion.table_id == table.id)
            .order_by(EnrichmentVersion.version.desc())
        ).first()
        
        desc = enrichment.data.get("table_description", "") if enrichment and enrichment.data else ""
        
        score = 0
        for word in query_words:
            if word in table.name.lower():
                score += 10
            if word in table.schema_name.lower():
                score += 5
            if word in desc.lower():
                score += 2
        
        if score > 0:
            keyword_matches.append((table.id, score))
            
    keyword_matches.sort(key=lambda x: x[1], reverse=True)
    kw_ids = [x[0] for x in keyword_matches[:10]]
    
    # 4. Combine and limit to 8-12 tables
    combined_ids = list(dict.fromkeys(vec_ids + kw_ids))[:12]
    
    result_tables = []
    for tid in combined_ids:
        t = session.get(Table, tid)
        if t:
            result_tables.append(t)
    return result_tables

# Define Tools
@tool
def search_tables(query: str) -> str:
    """Search for tables in the catalog using keywords or semantic query. Returns the top relevant tables."""
    emb = get_query_embedding(query)
    with Session(engine) as session:
        tables = hybrid_search_tables(query, emb, session)
        if not tables:
            return "No tables found matching query."
        
        results = []
        for t in tables:
            enrichment = session.exec(
                select(EnrichmentVersion)
                .where(EnrichmentVersion.table_id == t.id)
                .order_by(EnrichmentVersion.version.desc())
            ).first()
            desc = enrichment.data.get("table_description", "") if enrichment and enrichment.data else ""
            results.append({
                "id": t.id,
                "name": f"{t.schema_name}.{t.name}",
                "catalog": t.catalog,
                "description": desc
            })
        return json.dumps(results, indent=2)

@tool
async def get_table_profile(table_id: str) -> str:
    """Get the lightweight column names/types for a table, and the Esca reference ID for the full profiling statistics. Use this before planning a query."""
    with Session(engine) as session:
        table = session.get(Table, table_id)
        if not table:
            return json.dumps({"error": f"Table ID {table_id} not found."})
            
        profile = session.exec(
            select(TableProfile)
            .where(TableProfile.table_id == table_id, TableProfile.status == "completed")
            .order_by(TableProfile.created_at.desc())
        ).first()
        
        if not profile:
            return json.dumps({"error": f"No completed profile found for Table ID {table_id}. Make sure to trigger profiling first."})
            
        columns = session.exec(
            select(ColumnProfile).where(ColumnProfile.profile_id == profile.id)
        ).all()
        
        # Heavy data to pass by reference to Esca
        profile_data = {
            "table_id": table_id,
            "table_name": f"{table.schema_name}.{table.name}",
            "row_count": profile.row_count,
            "sample_data": profile.sample_data,
            "auto_insights": profile.auto_insights,
            "profile_json": profile.profile_json,
            "columns": [
                {
                    "name": cp.column_name,
                    "type": cp.data_type,
                    "null_rate": cp.null_rate,
                    "distinct_count": cp.distinct_count,
                    "top_values": cp.top_values
                }
                for cp in columns
            ]
        }
        
        # Save heavy data in Esca
        esca_payload = json.dumps(profile_data).encode()
        
        client = EscaClient(api_key=settings.ESCA_API_KEY, base_url=settings.ESCA_URL)
        try:
            res = await client.save_data(esca_payload)
            esca_id = res.get("esca_id")
        except Exception as e:
            esca_id = None
            # TODO: handle error
        finally:
            await client.close()
        
        # Return only lightweight metadata to LLM
        return json.dumps({
            "table_id": table_id,
            "table_name": f"{table.schema_name}.{table.name}",
            "row_count": profile.row_count,
            "columns": [
                {"name": cp.column_name, "type": cp.data_type}
                for cp in columns
            ],
            "esca_reference_id": esca_id
        }, indent=2)

async def schema_explorer_node(state: AgentState):
    """RAG Schema Explorer sub-agent node, pausing for table selection if ambiguous."""
    user_query = state["user_query"]
    enrichments = state.get("query_enrichments", [])
    allowed_tables = state.get("allowed_tables")
    feedback = state.get("feedback")
    
    # 1. Automatically run hybrid search to find candidates
    emb = get_query_embedding(user_query)
    with Session(engine) as session:
        candidate_tables = hybrid_search_tables(user_query, emb, session, allowed_tables)
        
    tables_info = []
    profile_details = []
    
    # 2. Automatically get profiles for the top candidate tables (up to 3) to seed the prompt
    for i, t in enumerate(candidate_tables):
        tables_info.append({
            "id": t.id,
            "name": f"{t.schema_name}.{t.name}",
            "catalog": t.catalog,
            "description": ""
        })
        
        # Fetch profile for the top 3 tables
        if i < 3:
            try:
                profile_res = await get_table_profile.ainvoke({"table_id": t.id})
                profile_details.append(json.loads(profile_res))
            except Exception as e:
                print(f"Error fetching profile for {t.name}: {e}")
    
    # TODO: Make more dynamic - allow LLM to search other tables if the first pass is not enough
    # TODO: Support multi-turn conversation
    # TODO: Support async simultanious profile fetching for top K tables 
         
    # 3. Present all metadata to the LLM to construct a query plan
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a Schema Explorer sub-agent. Your goal is to identify the most relevant tables "
            "and inspect their column details to form a query plan for the user's question.\n\n"
            "Candidate Tables found:\n{tables_json}\n\n"
            "Detailed Profiles for top tables (with Esca Reference IDs):\n{profiles_json}\n\n"
            "## Decision-Making Rules\n\n"
            "You MUST make all planning decisions autonomously. This includes:\n"
            "- Join strategy: If multiple tables are needed to answer the query, decide which tables to join and on which keys — do NOT ask the user.\n"
            "- Column selection: Choose the most appropriate columns yourself.\n"
            "- Filter strategy: Infer filters from the user's question.\n"
            "- Table selection when one is clearly more appropriate: Pick the best match and proceed.\n\n"
            "## When to Flag Ambiguity (ONLY these cases)\n\n"
            "Set ambiguity_detected=true ONLY IF:\n"
            "1. Two or more completely independent tables could each independently and fully answer the user's question, "
            "and you have no way to determine which one the user wants (e.g., 'orders' vs 'orders_archive' with no "
            "indication of time range, or two fact tables from different business domains that both seem equally relevant).\n"
            "2. There is no table in the catalog that can answer the query at all.\n\n"
            "Do NOT flag ambiguity for: join decisions, column choices, filter logic, or any decision you can make yourself.\n\n"
            "## Output Format\n\n"
            "Output MUST be a valid JSON object with the following keys:\n"
            "- schema_plan: detailed query plan describing tables, columns, joins, and Esca reference IDs (empty string if ambiguity_detected is true)\n"
            "- ambiguity_detected: boolean, true ONLY in the hard-blocker cases described above\n"
            "- ambiguity_message: a concise question to ask the user to resolve the blocker (empty string if ambiguity_detected is false)\n"
            "- candidate_options: list of strings (table names or options) for the user to choose from (empty list if ambiguity_detected is false)\n"
            "Return only the raw JSON, no markdown formatting (no ```json code blocks)."
        )),
        ("human", "{human_message}")
    ])
    
    human_message = f"Question: {user_query}\nQuery Enrichments (extra context for ambiguous terms): {json.dumps(enrichments)}"
    if feedback:
        human_message += f"\nUser Feedback on previous plan/query: {feedback}"
        
    structured_llm = llm.with_structured_output(SchemaExplorerOutput, method="json_schema")
    chain = prompt | structured_llm
    
    try:
        data = await chain.ainvoke({
            "tables_json": json.dumps(tables_info, indent=2),
            "profiles_json": json.dumps(profile_details, indent=2),
            "human_message": human_message
        })
    except Exception as e:
        print(f"Structured output parsing failed in schema explorer: {e}")
        data = SchemaExplorerOutput(
            schema_plan=None,
            ambiguity_detected=False,
            ambiguity_message="",
            candidate_options=[]
        )
        
    if data.ambiguity_detected and data.ambiguity_message and not state.get("non_interactive"):
        user_choice = interrupt({
            "type": "schema_explorer_ambiguity",
            "message": data.ambiguity_message,
            "options": data.candidate_options
        })
        
        clarified_message = f"{human_message}\nSelected table/option: {user_choice}"
        try:
            data = await chain.ainvoke({
                "tables_json": json.dumps(tables_info, indent=2),
                "profiles_json": json.dumps(profile_details, indent=2),
                "human_message": clarified_message
            })
        except Exception as e:
            print(f"Structured output parsing failed in schema explorer after clarification: {e}")
            data = SchemaExplorerOutput(
                schema_plan=None,
                ambiguity_detected=False,
                ambiguity_message="",
                candidate_options=[]
            )
            
    plan = data.schema_plan
    if plan is not None and not isinstance(plan, str):
        plan = json.dumps(plan)
    elif plan is None:
        plan = ""
        
    return {"schema_plan": plan}


