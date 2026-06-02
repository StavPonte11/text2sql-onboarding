import json
import urllib.request
from typing import Any
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

def hybrid_search_tables(query: str, query_embedding: list[float], session: Session) -> list[Table]:
    """Hybrid search combining pgvector cosine distance and keyword matching."""
    # 1. Vector Search
    stmt = text("""
        SELECT id FROM tables
        ORDER BY embedding <=> :emb
        LIMIT 10
    """)
    try:
        vec_ids = [row[0] for row in session.execute(stmt, {"emb": str(query_embedding)}).fetchall()]
    except Exception as e:
        print(f"Vector search failed: {e}")
        vec_ids = []

    # 2. Keyword Search
    stmt_all = select(Table)
    all_tables = session.exec(stmt_all).all()
    
    keyword_matches = []
    query_words = query.lower().split()
    for table in all_tables:
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
    
    # 3. Combine and limit to 8-12 tables
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
    """RAG Schema Explorer sub-agent node."""
    user_query = state["user_query"]
    extracted = state.get("extracted_entities", {})
    
    # 1. Automatically run hybrid search to find candidates
    emb = get_query_embedding(user_query)
    with Session(engine) as session:
        candidate_tables = hybrid_search_tables(user_query, emb, session)
        
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
                
    # 3. Present all metadata to the LLM to construct a query plan
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a Schema Explorer sub-agent. Your goal is to identify the most relevant tables "
            "and inspect their column details to form a query plan for the user's question.\n\n"
            "Candidate Tables found:\n{tables_json}\n\n"
            "Detailed Profiles for top tables (with Esca Reference IDs):\n{profiles_json}\n\n"
            "Formulate a detailed query plan describing which tables to query, how to join them, "
            "their columns, and include the exact Esca reference IDs for their profiles. Do not guess columns."
        )),
        ("human", "Question: {query}\nExtracted Entities: {extracted}")
    ])

    # TODO: Make more dynamic - allow LLM to search other tables if the first pass is not enough
    # TODO: Support multi-turn conversation
    # TODO: Support async simultanious profile fetching for top K tables
    
    chain = prompt | llm
    response = await chain.ainvoke({
        "tables_json": json.dumps(tables_info, indent=2),
        "profiles_json": json.dumps(profile_details, indent=2),
        "query": user_query,
        "extracted": json.dumps(extracted)
    })
    
    return {"schema_plan": response.content.strip()}
