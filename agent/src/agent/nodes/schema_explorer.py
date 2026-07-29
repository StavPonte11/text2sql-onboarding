from __future__ import annotations
import asyncio
from langgraph.types import interrupt
import json
import re
import urllib.request
from agent.utils.redis_publisher import publish_node_event
from agent.utils.jeen_metadata_client import get_jeen_metadata_client
from core.trino import execute_query_sync

IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

from typing import Any, List, Optional
from pydantic import BaseModel, Field

import logging
from agent.state import AgentState

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

from langchain_core.tools import tool
from sqlalchemy import text
from core.db.engine import engine
from core.models.models import Table, TableProfile, ColumnProfile, EnrichmentVersion
from sqlmodel import Session, select
from agent.config import settings
from agent.langfuse_client import langfuse_client
from agent.llm import get_llm

from core.cache import get_cache_service
from core.embeddings import get_embedding
from core.trino import execute_query_sync


# Initialize LLM
llm = get_llm("schema_explorer")
logger = logging.getLogger(__name__)

# Cache singleton
_cache = get_cache_service()

# G2-02 limits
MAX_SCHEMA_RETRIES = 3


def _build_column_context(cp: "ColumnProfile") -> dict:
    """Build a rich column context dict from a ColumnProfile ORM row.

    Returns all fields the LLM needs to write accurate SQL:
    - name, type, semantic_type
    - null_rate, distinct_count
    - sample_values (top values for categorical/text, or sample values for continuous)
    - min, max, mean for numeric/time columns
    """
    top_vals = cp.top_values or []
    sample_values = [
        v.get("value") for v in top_vals[:20] if v.get("value") is not None
    ]
    stats = cp.stats_json or {}

    col: dict = {
        "name": cp.column_name,
        "type": cp.data_type,
        "semantic_type": cp.semantic_type or "unknown",
        "null_rate": round(cp.null_rate or 0.0, 4),
        "distinct_count": cp.distinct_count or 0,
    }

    if cp.is_categorical:
        col["sample_values"] = sample_values
    else:
        # For numeric/time columns expose range and sample values
        if cp.min_value is not None:
            col["min"] = cp.min_value
        if cp.max_value is not None:
            col["max"] = cp.max_value
        if cp.avg_value is not None:
            col["mean"] = round(float(cp.avg_value), 4)
        # Pull any sample_values stored in stats_json (continuous columns)
        stored_samples = stats.get("sample_values", [])
        if stored_samples:
            col["sample_values"] = [str(v) for v in stored_samples[:10]]
        elif sample_values:
            col["sample_values"] = sample_values[:10]

    return col


# Define standardized Schema Explorer Output Type
class SchemaExplorerOutput(BaseModel):
    schema_plan: Optional[Any] = Field(
        default=None,
        description="Detailed query plan describing tables, columns, and joins.",
    )
    ambiguity_detected: bool = Field(
        default=False, description="Set to true if there is table selection ambiguity."
    )
    ambiguity_message: str = Field(
        default="",
        description="A question to ask the user to clarify/select the right table(s). Must be empty if ambiguity_detected is false.",
    )
    candidate_options: List[str] = Field(
        default_factory=list,
        description="List of strings (table names or options) for the user to choose from. Must be empty if ambiguity_detected is false.",
    )
    tables_used: List[str] = Field(
        default_factory=list,
        description="List of fully qualified table names (catalog.schema.name) used in the plan.",
    )


def get_query_embedding(text: str) -> list[float]:
    """Generate 768-dimensional embedding from nomic-embed-text."""
    emb = get_embedding(
        text=text,
        embedder_url=settings.EMBEDDER_URL,
        embedder_model=settings.EMBEDDER_MODEL,
        embedder_key=settings.EMBEDDER_KEY,
    )
    if emb is None:
        print("Error getting query embedding for text")
        return [0.0] * 768
    return emb


def hybrid_search_tables(
    query: str,
    query_embedding: list[float],
    session: Session,
    allowed_tables: list[str] | None = None,
    allowed_statuses: list[str] | None = None,
    scoping_mode: str = "hybrid",
) -> list[Table]:
    """Hybrid search combining pgvector cosine distance and keyword matching.

    G2-01: In strict mode, allowed_tables is a hard allowlist — allowed_statuses
    is ignored.  In hybrid mode, the union of both filters applies.
    """
    stmt_all = select(Table)
    all_tables = session.exec(stmt_all).all()

    allowed = allowed_tables or []
    statuses = allowed_statuses or ["production"]
    allowed_tables_set = []
    allowed_ids = set()

    for table in all_tables:
        if scoping_mode == "strict":
            # Hard allowlist: only tables explicitly named in allowed_tables
            is_allowed = bool(
                allowed
                and (
                    table.id in allowed
                    or table.name in allowed
                    or f"{table.schema_name}.{table.name}" in allowed
                    or f"{table.catalog}.{table.schema_name}.{table.name}" in allowed
                )
            )
        else:
            # Hybrid: production/status union OR explicit allowed list
            is_allowed = table.status in statuses or (
                allowed
                and (
                    table.id in allowed
                    or table.name in allowed
                    or f"{table.schema_name}.{table.name}" in allowed
                    or f"{table.catalog}.{table.schema_name}.{table.name}" in allowed
                )
            )

        if is_allowed:
            allowed_tables_set.append(table)
            allowed_ids.add(table.id)

    # Vector Search
    if allowed_ids:
        stmt = text(
            """
            SELECT id FROM tables
            WHERE id = ANY(:allowed_ids)
            ORDER BY embedding <=> :emb
            LIMIT :limit
        """
        )
        try:
            vec_ids = [
                row[0]
                for row in session.execute(
                    stmt,
                    {
                        "emb": str(query_embedding),
                        "allowed_ids": list(allowed_ids),
                        "limit": settings.HYBRID_SEARCH_MAX_TABLES,
                    },
                ).fetchall()
            ]
        except Exception as e:
            print(f"Vector search failed: {e}")
            vec_ids = []
    else:
        vec_ids = []

    # Keyword Search
    keyword_matches = []
    query_words = query.lower().split()
    for table in allowed_tables_set:
        enrichment = session.exec(
            select(EnrichmentVersion)
            .where(EnrichmentVersion.table_id == table.id)
            .order_by(EnrichmentVersion.version.desc())
        ).first()

        desc = (
            enrichment.data.get("table_description", "")
            if enrichment and enrichment.data
            else ""
        )

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
    kw_ids = [x[0] for x in keyword_matches[: settings.HYBRID_SEARCH_MAX_TABLES]]

    combined_ids = list(dict.fromkeys(vec_ids + kw_ids))[
        : settings.HYBRID_SEARCH_MAX_TABLES
    ]

    # If scoping_mode == "strict" or allowed_tables was specified,
    # ensure explicitly allowed tables are never dropped due to low keyword/vector scores
    if allowed_tables_set:
        for table in allowed_tables_set:
            if table.id not in combined_ids:
                combined_ids.append(table.id)

    result_tables = []
    for tid in combined_ids:
        t = session.get(Table, tid)
        if t:
            result_tables.append(t)
    return result_tables


# Define Tools


@tool
async def get_table_profile(table_id: str) -> str:
    """Get the lightweight column names/types for a table. Use this before planning a query."""
    cache_hit = False

    with Session(engine) as session:
        table = session.get(Table, table_id)
        if not table:
            return json.dumps({"error": f"Table ID {table_id} not found."})

        profile = session.exec(
            select(TableProfile)
            .where(
                TableProfile.table_id == table_id, TableProfile.status == "completed"
            )
            .order_by(TableProfile.created_at.desc())
        ).first()

        if not profile:
            # Fallback to querying Trino directly if no static profile exists in DB
            try:
                trino_res = await asyncio.to_thread(
                    execute_query_sync,
                    f"DESCRIBE {table.catalog}.{table.schema_name}.{table.name}",
                )
                if trino_res.success and trino_res.rows:
                    cols = [
                        {
                            "name": row[0],
                            "type": row[1],
                            "sample_values": [],
                            "null_count": 0,
                        }
                        for row in trino_res.rows
                    ]
                    res = {
                        "table_id": table_id,
                        "table_name": f"{table.catalog}.{table.schema_name}.{table.name}",
                        "row_count": 0,
                        "columns": cols,
                        "description": "",
                    }
                    return json.dumps(res)
            except Exception as e:
                print(f"Trino DESCRIBE fallback failed for {table.name}: {e}")

            return json.dumps(
                {
                    "error": f"No completed profile found for Table ID {table_id}. Make sure to trigger profiling first."
                }
            )

        # ── G2-05: Redis cache lookup ─────────────────────────────────────────
        cache_key = _cache.profile_key(table_id, profile.id)
        cached = await _cache.get_json(cache_key)
        if cached is not None:
            cache_hit = True
            # Lightweight wrapper returned from cache
            return json.dumps(cached)

        columns = session.exec(
            select(ColumnProfile).where(ColumnProfile.profile_id == profile.id)
        ).all()

        # ── Fetch table description from EnrichmentVersion ─────────────────────
        table_description = ""
        enrichment = session.exec(
            select(EnrichmentVersion)
            .where(EnrichmentVersion.table_id == table_id)
            .order_by(EnrichmentVersion.version.desc())
        ).first()
        if enrichment and enrichment.data:
            # Prefer human annotation, fall back to AI summary
            table_description = enrichment.data.get(
                "table_description", ""
            ) or enrichment.data.get("ai_summary", "")

        # Lightweight response to cache and return to LLM
        lightweight = {
            "table_id": table_id,
            "table_name": f"{table.catalog}.{table.schema_name}.{table.name}",
            "description": table_description,
            "row_count": profile.row_count,
            "columns": [_build_column_context(cp) for cp in columns],
        }

        # ── G2-05: Populate cache ─────────────────────────────────────────────
        await _cache.set_json(cache_key, lightweight, settings.PROFILE_CACHE_TTL)

        return json.dumps(lightweight, indent=2)


async def schema_explorer_node(state: AgentState, config: RunnableConfig | None = None):
    """Schema Explorer node — just fetches the full catalog prompt from MCP."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""

    await publish_node_event(thread_id, "schema_explorer")

    _jeen = get_jeen_metadata_client()
    if not _jeen.is_configured:
        error_msg = "Jeen is not configured. Jeen must be configured for the schema explorer to work."
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    logger.info("Fetching full catalog prompt from Jeen MCP.")
    try:
        catalog_prompt = await _jeen.get_catalog_prompt()
        if not catalog_prompt:
            raise ValueError("Received empty catalog prompt from Jeen.")
    except Exception as exc:
        error_msg = f"There was a problem getting the catalog_prompt from Jeen: {exc}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from exc

    # ── Langfuse trace metadata ───────────────────────────────────────────────
    try:
        trace_id = langfuse_client.get_current_trace_id()
        if trace_id:
            langfuse_client.update_current_span(
                metadata={
                    "schema_explorer_mode": "mcp_catalog_only",
                },
            )
    except Exception as exc:
        logger.warning("Langfuse trace update failed in schema_explorer: %s", exc)

    result_state: dict = {
        "jeen_catalog": catalog_prompt,
        "execution_path": ["schema_explorer"],
        "schema_explorer_retry_count": 0,
    }

    return result_state
