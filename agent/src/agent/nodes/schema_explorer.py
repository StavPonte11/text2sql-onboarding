from __future__ import annotations
import asyncio
import json
import re
import urllib.request
from agent.utils.redis_publisher import publish_node_event
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
from agent.utils.schema_enrichment import (
    run_semantic_typing,
    run_join_graph,
)
from core.cache import get_cache_service
from core.embeddings import get_embedding

# Initialize LLM
llm = get_llm("schema_explorer")
logger = logging.getLogger(__name__)

# Cache singleton
_cache = get_cache_service()

# Skill Registry
from agent.utils.skill_registry import SkillRegistry
from python_core_utils.redis import get_redis_client

_skill_registry = SkillRegistry()

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
# Ambiguity detection is handled exclusively by the detect_ambiguity node in the
# refiner subgraph — schema_explorer only produces a query plan and table list.
class SchemaExplorerOutput(BaseModel):
    schema_plan: str = Field(
        default="",
        description="Detailed query plan describing tables, columns, and joins. Must be a detailed string explanation.",
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


async def schema_explorer_node(state: AgentState, config: RunnableConfig = None):
    """RAG Schema Explorer sub-agent node — with G2-01 scoping, G2-03 enrichment, G2-05 caching."""
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""

    await publish_node_event(thread_id, "schema_explorer")

    user_query = state.get("user_query")
    enrichments = state.get("query_enrichments", [])
    allowed_tables = state.get("allowed_tables")
    allowed_statuses = state.get("allowed_statuses")
    feedback = state.get("feedback")
    runtime_flags = state.get("runtime_flags") or {}

    # Resolve all flag-tunable parameters for this invocation
    profile_fetch_concurrency = int(
        runtime_flags.get(
            "PROFILE_FETCH_CONCURRENCY", settings.PROFILE_FETCH_CONCURRENCY
        )
    )
    max_profiles_to_fetch = int(
        runtime_flags.get("MAX_PROFILES_TO_FETCH", settings.MAX_PROFILES_TO_FETCH)
    )
    def _parse_bool_flag(value) -> bool:
        """Parse a flag value that may be a bool or a string like 'true'/'false'/'0'/'1'."""
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1")

    schema_semantic_typing = _parse_bool_flag(
        runtime_flags.get("SCHEMA_SEMANTIC_TYPING", settings.ENABLE_SEMANTIC_TYPING)
    )
    schema_join_graph = _parse_bool_flag(
        runtime_flags.get("SCHEMA_JOIN_GRAPH", settings.ENABLE_JOIN_GRAPH)
    )
    schema_summarization = _parse_bool_flag(
        runtime_flags.get("SCHEMA_SUMMARIZATION", settings.ENABLE_SCHEMA_SUMMARIZATION)
    )
    schema_skill_injection = _parse_bool_flag(
        runtime_flags.get("SCHEMA_SKILL_INJECTION", settings.ENABLE_SKILL_INJECTION)
    )
    scoping_mode_flag = runtime_flags.get(
        "DEFAULT_TABLE_SCOPING_MODE", settings.DEFAULT_TABLE_SCOPING_MODE
    )

    # Per-invocation LLM (supports model switching via execution mode)
    _llm = get_llm("schema_explorer", runtime_flags=runtime_flags)

    # ── G2-01: Resolve scoping mode (state > runtime_flag > env default) ─────────
    scoping_mode: str = state.get("scoping_mode") or scoping_mode_flag

    # ── G2-05: Cache hit/miss counters (pushed to Langfuse at end) ────────────
    cache_hit_count = 0
    cache_miss_count = 0

    # 1. Automatically run hybrid search to find candidates
    emb = get_query_embedding(user_query)
    with Session(engine) as session:
        candidate_tables = hybrid_search_tables(
            user_query, emb, session, allowed_tables, allowed_statuses, scoping_mode
        )

    tables_info = []
    profile_details = []

    # 2. Get profiles for top candidate tables (G2-05 cache-aware)
    import asyncio

    sem = asyncio.Semaphore(profile_fetch_concurrency)

    async def fetch_profile(t_id, t_name):
        nonlocal cache_hit_count, cache_miss_count
        async with sem:
            try:
                # Quick cache check at this level for hit/miss accounting
                with Session(engine) as s:
                    profile_row = s.exec(
                        select(TableProfile)
                        .where(
                            TableProfile.table_id == t_id,
                            TableProfile.status == "completed",
                        )
                        .order_by(TableProfile.created_at.desc())
                    ).first()
                    if profile_row:
                        ck = _cache.profile_key(t_id, profile_row.id)
                        hit = await _cache.get(ck)
                        if hit is not None:
                            cache_hit_count += 1
                        else:
                            cache_miss_count += 1

                profile_res = await get_table_profile.ainvoke({"table_id": t_id})
                return json.loads(profile_res)
            except Exception as e:
                print(f"Error fetching profile for {t_name}: {e}")
                return None

    fetch_tasks = []
    for i, t in enumerate(candidate_tables):
        tables_info.append(
            {
                "id": t.id,
                "name": f"{t.catalog}.{t.schema_name}.{t.name}",
                "description": "",
            }
        )
        if i < max_profiles_to_fetch:
            fetch_tasks.append(fetch_profile(t.id, t.name))

    if fetch_tasks:
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        for res in results:
            if res and not isinstance(res, Exception):
                profile_details.append(res)

    # ── G2-03: Advanced Schema Enrichment phases ──────────────────────────────
    active_phases: list[str] = []
    table_ids = [t.id for t in candidate_tables]

    # human_message = (
    #     f"Question: {user_query}\n"
    #     f"Query Enrichments (extra context for ambiguous terms): {json.dumps(enrichments)}"
    # )
    human_message = user_query
    if feedback:
        human_message += f"\nUser Feedback on previous plan/query: {feedback}"

    # G2-01 strict mode prompt injection
    if scoping_mode == "strict":
        human_message += (
            "\n\n[STRICT MODE] Only use tables from the approved list. "
            "Do not suggest alternatives.\n"
            f"Approved tables: {json.dumps(allowed_tables)}"
        )

    # Phase A: Semantic Typing
    if schema_semantic_typing and profile_details:
        try:
            profile_details = await run_semantic_typing(profile_details, _llm)
            active_phases.append("SCHEMA_SEMANTIC_TYPING")
        except Exception as exc:
            logger.warning("SCHEMA_SEMANTIC_TYPING phase failed: %s", exc)

    # Phase B: Join Graph
    if schema_join_graph and len(table_ids) >= 2:
        try:
            join_paths_json = await run_join_graph(table_ids)
            if join_paths_json:
                human_message += (
                    "\n\n[JOIN GRAPH] Shortest join paths between candidate tables:\n"
                    + join_paths_json
                )
                active_phases.append("SCHEMA_JOIN_GRAPH")
        except Exception as exc:
            logger.warning("SCHEMA_JOIN_GRAPH phase failed: %s", exc)

    # ── G3: Skill Injection ───────────────────────────────────────────────────
    loaded_skills = state.get("loaded_skills")
    if schema_skill_injection and loaded_skills:
        try:
            skill_prompts = _skill_registry.build_system_prompt_addition(loaded_skills)
            if skill_prompts:
                human_message += f"\n\n[APPLIED SKILLS]{skill_prompts}"
        except Exception as e:
            logger.warning(f"Failed to inject skills: {e}")

    # Phase C: Schema Summarization (replaces profiles_json in prompt)
    profiles_json_str = json.dumps(profile_details, indent=2)
    if schema_summarization and profile_details:
        try:
            summaries = [
                f"[{p.get('table_name', 'unknown')}] {p.get('description', '') or '(no description available)'}"
                for p in profile_details
            ]
            profiles_json_str = "\n".join(summaries)
            active_phases.append("SCHEMA_SUMMARIZATION")
        except Exception as exc:
            logger.warning("SCHEMA_SUMMARIZATION phase failed: %s", exc)


    # ── Langfuse trace metadata ───────────────────────────────────────────────
    try:
        trace_id = langfuse_client.get_current_trace_id()
        if trace_id:
            langfuse_client._create_trace_tags_via_ingestion(
                trace_id=trace_id, tags=[f"scoping_mode={scoping_mode}"]
            )
            langfuse_client.update_current_span(
                metadata={
                    "active_schema_phases": active_phases,
                    "cache_hit_count": cache_hit_count,
                    "cache_miss_count": cache_miss_count,
                },
            )
    except Exception as exc:
        logger.warning("Langfuse trace update failed in schema_explorer: %s", exc)

    # 3. Present all metadata to the LLM to construct a query plan
    langfuse_prompt = langfuse_client.get_prompt(
        settings.LANGFUSE_PROMPT_SCHEMA_EXPLORER
    )
    prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())

    structured_llm = _llm.with_structured_output(
        SchemaExplorerOutput, method="json_schema"
    )
    chain = prompt | structured_llm

    try:
        data = await chain.ainvoke(
            {
                "tables_json": json.dumps(tables_info, indent=2),
                "profiles_json": profiles_json_str,
                "human_message": human_message,
            }
        )
    except Exception as e:
        print(f"Structured output parsing failed in schema explorer: {e}")
        data = SchemaExplorerOutput(schema_plan="")

    plan = data.schema_plan or ""

    tables_used = getattr(data, "tables_used", [])

    result_state: dict = {"schema_plan": plan, "tables_used": tables_used}
    result_state["execution_path"] = ["schema_explorer"]
    # Store enriched profiles for downstream nodes (refiner re-uses without re-fetch)
    result_state["table_profiles"] = profile_details if profile_details else None

    return result_state

async def sql_static_validations_node(state: AgentState, config: RunnableConfig = None) -> dict:
    """
    Check if tables_used actually exist.
    """
    tables_used = state.get("tables_used") or []
    hallucinated = []
    
    if tables_used:
        try:
            redis_client = get_redis_client()
            for t_name in tables_used:
                cache_key = f"table_exists:{t_name}"
                exists = await redis_client.get(cache_key)
                if exists is None:
                    parts = t_name.split(".")
                    if len(parts) == 3:
                        cat, sch, tbl = parts
                        if not IDENT_RE.fullmatch(cat):
                            hallucinated.append(t_name)
                            continue
                        sql = f'SELECT 1 FROM "{cat}".information_schema.tables WHERE table_schema = ? AND table_name = ?'
                        params = [sch, tbl]
                    elif len(parts) == 2:
                        sch, tbl = parts
                        sql = "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?"
                        params = [sch, tbl]
                    elif len(parts) == 1:
                        tbl = parts[0]
                        sql = "SELECT 1 FROM information_schema.tables WHERE table_name = ?"
                        params = [tbl]
                    else:
                        hallucinated.append(t_name)
                        continue

                    try:
                        res = await asyncio.to_thread(
                            execute_query_sync, sql, "", params
                        )
                        if res.success and len(res.rows) > 0:
                            await redis_client.setex(cache_key, 3600, "1")
                        else:
                            await redis_client.setex(cache_key, 3600, "0")
                            hallucinated.append(t_name)
                    except Exception as e:
                        logger.error(
                            f"Information schema check failed for {t_name}: {e}"
                        )
                        # Do not mark as hallucinated on infrastructure failures
                elif exists == b"0":
                    hallucinated.append(t_name)
        except Exception as e:
            logger.error(f"Error during Redis/Trino table verification: {e}")
            # Do not mark as hallucinated on infrastructure failures

    retry_count = state.get("schema_explorer_retry_count", 0) or 0
    result_state: dict = {
        "schema_explorer_retry_count": retry_count,
    }
    
    if hallucinated:
        new_retry = retry_count + 1
        result_state["hallucinated_tables"] = hallucinated
        result_state["feedback"] = (
            f"Do not use these tables, they do not exist: {', '.join(hallucinated)}"
        )
        result_state["last_error"] = (
            f"Hallucinated tables detected: {', '.join(hallucinated)}"
        )
        result_state["schema_explorer_retry_count"] = new_retry

        # G2-02: set escalation_reason when approaching the limit
        if new_retry >= MAX_SCHEMA_RETRIES:
            result_state["escalation_reason"] = (
                f"Schema explorer failed {new_retry} times due to hallucinated tables: "
                f"{', '.join(hallucinated)}"
            )
    else:
        result_state["hallucinated_tables"] = None
        result_state["feedback"] = None
        result_state["last_error"] = None
        result_state["schema_explorer_retry_count"] = 0

    result_state["execution_path"] = ["sql_static_validations"]
    return result_state
