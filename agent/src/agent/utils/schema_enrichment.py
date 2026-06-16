"""
G2-03: Advanced Schema Explorer — Enrichment Phases
====================================================
Four independently feature-gated async functions that enrich schema context
before the LLM planning call in schema_explorer_node.

Phase constants (used in Langfuse trace metadata):
    SCHEMA_SEMANTIC_TYPING
    SCHEMA_JOIN_GRAPH
    SCHEMA_SUMMARIZATION
    SCHEMA_AMBIGUITY_DETECT

Join-graph algorithm
--------------------
Uses networkx.DiGraph populated from:
  • ForeignKeyMapping rows (explicit FK declarations)
  • CrossTableProfile rows (auto-detected join suggestions)
BFS shortest path (nx.shortest_path) is run between every pair of
candidate table_ids.  If networkx is unavailable the function falls
back to a pure-Python BFS implementation so the phase never hard-fails.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from core.db.engine import engine
from core.models.models import CrossTableProfile, ForeignKeyMapping, Table

logger = logging.getLogger(__name__)


# ─── Pydantic schemas for structured LLM calls ────────────────────────────────


class SemanticAnnotation(BaseModel):
    table_column: str = Field(description="The full table_name.column_name identifier")
    semantic_type: str = Field(description="Must be one of: id | timestamp | category | metric | text | geo | unknown")


class SemanticTypingOutput(BaseModel):
    """Maps table_name.column_name → semantic type."""

    annotations: list[SemanticAnnotation] = Field(
        default_factory=list,
        description="List of column annotations."
    )


class SummarizationOutput(BaseModel):
    summary: str = Field(
        description="≤3-sentence plain-English description of the table's purpose and key columns."
    )


class AmbiguityOutput(BaseModel):
    ambiguity_notes: list[str] = Field(
        default_factory=list,
        description=(
            "List of ambiguity notes for column/table names relative to the user query. "
            "Empty list if nothing is ambiguous."
        ),
    )


class ColumnCoverageOutput(BaseModel):
    """Used by G2-04 satisfaction check (imported from here for reuse)."""

    satisfies_question: bool = Field(
        description="True if the SQL column headers conceptually answer the user's question."
    )
    reason: str = Field(default="", description="Brief rationale.")


class SemanticAlignmentOutput(BaseModel):
    """Used by G2-04 satisfaction check."""

    alignment_score: float = Field(
        ge=0.0,
        le=1.0,
        description="0–1 score of how well the query output schema matches the question intent.",
    )
    reason: str = Field(default="")


# ─── Phase A: Semantic Typing ─────────────────────────────────────────────────


async def run_semantic_typing(
    profiles: list[dict[str, Any]],
    llm: Any,
) -> list[dict[str, Any]]:
    """
    Classify each column in *profiles* with a semantic type via a single
    structured LLM call.  Returns the mutated profiles list.
    """
    if not profiles:
        return profiles

    # Build a compact column list for the prompt
    col_list = []
    for p in profiles:
        tname = p.get("table_name", "unknown")
        for col in p.get("columns", []):
            col_list.append(f"{tname}.{col['name']} ({col.get('type', '?')})")

    prompt_text = (
        "Classify each column with one of: id | timestamp | category | metric | text | geo | unknown.\n"
        "Columns:\n" + "\n".join(col_list)
    )

    try:
        structured = llm.with_structured_output(SemanticTypingOutput, method="json_schema")
        result = await structured.ainvoke(prompt_text)
        
        # Handle both Pydantic model and raw dict responses (some LLM integrations return dicts when method="json_schema")
        annotations = getattr(result, "annotations", []) if not isinstance(result, dict) else result.get("annotations", [])
        
        lookup = {}
        for item in annotations:
            # Item could be a dict or a SemanticAnnotation model
            if isinstance(item, dict):
                col = item.get("table_column")
                sem = item.get("semantic_type")
            else:
                col = getattr(item, "table_column", None)
                sem = getattr(item, "semantic_type", None)
                
            if col and sem:
                lookup[col] = sem

        for p in profiles:
            tname = p.get("table_name", "unknown")
            for col in p.get("columns", []):
                key = f"{tname}.{col['name']}"
                if key in lookup:
                    col["semantic_type"] = lookup[key]
    except Exception as exc:
        logger.warning("run_semantic_typing failed: %s", exc, exc_info=True)

    return profiles


# ─── Phase B: Join Graph (BFS via networkx + FK/CrossTableProfile data) ───────


def _bfs_shortest_path(
    graph: dict[str, list[str]], source: str, target: str
) -> list[str] | None:
    """Pure-Python BFS fallback returning the shortest path or None."""
    from collections import deque

    visited = {source}
    queue: deque[list[str]] = deque([[source]])
    while queue:
        path = queue.popleft()
        node = path[-1]
        if node == target:
            return path
        for neighbour in graph.get(node, []):
            if neighbour not in visited:
                visited.add(neighbour)
                queue.append(path + [neighbour])
    return None


async def run_join_graph(
    table_ids: list[str],
    session: Session | None = None,
) -> str:
    """
    Build a directed join graph from ForeignKeyMapping + CrossTableProfile rows,
    then compute BFS shortest paths between all candidate table pairs.
    Returns a JSON string suitable for appending to human_message.
    """
    if not table_ids or len(table_ids) < 2:
        return ""

    own_session = session is None
    if own_session:
        session = Session(engine)

    try:
        # Load FK mappings touching our candidate tables
        fk_rows = session.exec(
            select(ForeignKeyMapping).where(
                ForeignKeyMapping.table_id.in_(table_ids)  # type: ignore[attr-defined]
            )
        ).all()

        # Load cross-table profile suggestions touching our candidates
        ctp_rows = session.exec(
            select(CrossTableProfile).where(
                CrossTableProfile.source_table_id.in_(table_ids)  # type: ignore[attr-defined]
            )
        ).all()

        # Resolve table_id → qualified name
        id_to_name: dict[str, str] = {}
        all_related_ids = (
            table_ids
            + [fk.target_table_id for fk in fk_rows]
            + [ctp.target_table_id for ctp in ctp_rows]
        )
        for t in session.exec(
            select(Table).where(Table.id.in_(list(set(all_related_ids))))  # type: ignore[attr-defined]
        ).all():
            id_to_name[t.id] = f"{t.catalog}.{t.schema_name}.{t.name}"

    finally:
        if own_session:
            session.close()

    # Build graph (prefer networkx, fall back to adjacency dict)
    try:
        import networkx as nx  # type: ignore[import-untyped]

        G: nx.DiGraph = nx.DiGraph()
        for fk in fk_rows:
            src = id_to_name.get(fk.table_id, fk.table_id)
            tgt = id_to_name.get(fk.target_table_id, fk.target_table_id)
            G.add_edge(
                src,
                tgt,
                via=f"{fk.source_column} = {fk.target_column}",
                weight=1,
            )
        for ctp in ctp_rows:
            src = id_to_name.get(ctp.source_table_id, ctp.source_table_id)
            tgt = id_to_name.get(ctp.target_table_id, ctp.target_table_id)
            weight = 1 if ctp.match_strength == "strong" else 2
            G.add_edge(
                src,
                tgt,
                via=ctp.join_suggestion or "inferred",
                weight=weight,
            )

        paths: list[dict[str, Any]] = []
        node_names = [id_to_name.get(tid, tid) for tid in table_ids]
        for i, a in enumerate(node_names):
            for b in node_names[i + 1 :]:
                try:
                    path_nodes = nx.shortest_path(G, source=a, target=b, weight="weight")
                    edge_labels = []
                    for u, v in zip(path_nodes, path_nodes[1:]):
                        edge_labels.append(G[u][v].get("via", ""))
                    paths.append({"from": a, "to": b, "path": path_nodes, "joins": edge_labels})
                except nx.NetworkXNoPath:
                    pass
                except nx.NodeNotFound:
                    pass

    except ImportError:
        # Fallback: adjacency dict + pure-Python BFS
        adj: dict[str, list[str]] = {}
        for fk in fk_rows:
            src = id_to_name.get(fk.table_id, fk.table_id)
            tgt = id_to_name.get(fk.target_table_id, fk.target_table_id)
            adj.setdefault(src, []).append(tgt)
        for ctp in ctp_rows:
            src = id_to_name.get(ctp.source_table_id, ctp.source_table_id)
            tgt = id_to_name.get(ctp.target_table_id, ctp.target_table_id)
            adj.setdefault(src, []).append(tgt)

        paths = []
        node_names = [id_to_name.get(tid, tid) for tid in table_ids]
        for i, a in enumerate(node_names):
            for b in node_names[i + 1 :]:
                p = _bfs_shortest_path(adj, a, b)
                if p:
                    paths.append({"from": a, "to": b, "path": p})

    if not paths:
        return ""

    return json.dumps(paths, indent=2)


# ─── Phase C: Schema Summarization ───────────────────────────────────────────


async def run_schema_summarization(
    profiles: list[dict[str, Any]],
    llm: Any,
) -> list[str]:
    """
    Produce a ≤3-sentence plain-English summary for each table profile
    via independent LLM calls.  Returns a list of summary strings
    (one per profile, same order).
    """
    import asyncio

    summaries: list[str] = []
    # Limit concurrency to 1 to prevent local models like Ollama from crashing
    sem = asyncio.Semaphore(1)

    async def _summarize_one(p: dict[str, Any]) -> str:
        async with sem:
            tname = p.get("table_name", "unknown")
            columns = p.get("columns", [])
            col_summary = ", ".join(
                f"{c['name']} ({c.get('type', '?')})" for c in columns[:20]
            )
            prompt = (
                f"Table: {tname}\n"
                f"Row count: {p.get('row_count', 'unknown')}\n"
                f"Columns: {col_summary}\n\n"
                "Write a ≤3-sentence description of this table's purpose and most important columns."
            )
            try:
                structured = llm.with_structured_output(SummarizationOutput, method="json_schema")
                # Handle both Pydantic model and raw dict responses gracefully
                result = await structured.ainvoke(prompt)
                
                # Check if it's a dict or object
                if isinstance(result, dict):
                    summary_text = result.get("summary", "(summarization unavailable)")
                else:
                    summary_text = getattr(result, "summary", "(summarization unavailable)")
                    
                return f"[{tname}] {summary_text}"
            except Exception as exc:
                logger.warning("run_schema_summarization failed for %s: %s", tname, exc)
                return f"[{tname}] (summarization unavailable)"

    tasks = [_summarize_one(p) for p in profiles]
    summaries = list(await asyncio.gather(*tasks))
    return summaries


# ─── Phase D: Ambiguity Detection ────────────────────────────────────────────


async def run_ambiguity_detection(
    profiles: list[dict[str, Any]],
    user_query: str,
    llm: Any,
) -> list[str]:
    """
    Identify any column or table name ambiguities relative to the user query.
    Returns a list of human-readable ambiguity notes (may be empty).
    """
    if not profiles:
        return []

    col_names = []
    for p in profiles:
        for col in p.get("columns", []):
            col_names.append(f"{p.get('table_name','')}.{col['name']}")

    prompt = (
        f"User question: {user_query}\n"
        f"Available columns: {', '.join(col_names[:80])}\n\n"
        "List any ambiguous column or table names that could be misinterpreted for this question. "
        "Return an empty list if nothing is ambiguous."
    )
    try:
        structured = llm.with_structured_output(AmbiguityOutput, method="json_schema")
        result: AmbiguityOutput = await structured.ainvoke(prompt)
        return result.ambiguity_notes
    except Exception as exc:
        logger.warning("run_ambiguity_detection failed: %s", exc)
        return []
