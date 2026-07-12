"""
hybrid_searcher.py - Retrieves canonical database matching values.

Executes vector similarity search on pgvector and lexical fallback pattern matching 
in PostgreSQL concurrently using asyncio workflows.
"""

import logging
import asyncio
import re
from typing import List, Dict, Any, Tuple, Optional
from sqlmodel import Session, select
from sqlalchemy import text
from core.db.engine import engine
from core.models.models import Table
from core.embeddings import get_embedding
from agent.config import settings
from agent.services.enrichment_models import SQLFilterParams, AgentSQLTable

logger = logging.getLogger(__name__)


def get_query_embedding(text_val: str) -> Optional[List[float]]:
    """
    Generate 768-dimensional embedding from nomic-embed-text.

    Args:
        text_val: Raw text search pattern.

    Returns:
        List of floats representing the embedding vector, or None if the request failed.
    """
    emb: Optional[List[float]] = get_embedding(
        text=text_val,
        embedder_url=settings.EMBEDDER_URL,
        embedder_model=settings.EMBEDDER_MODEL,
        embedder_key=settings.EMBEDDER_KEY,
    )
    if emb is None:
        logger.error(f"Error getting query embedding for text: {text_val}")
        return None
    return emb


def find_table_id(source_table: str) -> Optional[str]:
    """
    Look up the Table row in DB to resolve the table's UUID.

    Args:
        source_table: Simple or qualified table name (e.g. schema.table).

    Returns:
        The table ID string if found in database, else None.
    """
    parts: List[str] = source_table.split(".")
    with Session(engine) as session:
        stmt = select(Table)
        if len(parts) == 3:
            stmt = stmt.where(Table.catalog == parts[0], Table.schema_name == parts[1], Table.name == parts[2])
        elif len(parts) == 2:
            stmt = stmt.where(Table.schema_name == parts[0], Table.name == parts[1])
        else:
            stmt = stmt.where(Table.name == source_table)
        table_row = session.exec(stmt).first()
        return table_row.id if table_row else None


def query_db_exact(table_id: str, col_name: str, value: str) -> List[str]:
    """
    Checks for a case-insensitive exact match in the database table.

    Args:
        table_id: Database table UUID.
        col_name: Database column name.
        value: Clean filter string literal.

    Returns:
        A list of matching database values.
    """
    with Session(engine) as session:
        stmt = text(
            """
            SELECT value_text FROM large_category_values
            WHERE table_id = :table_id AND column_name = :col_name
              AND LOWER(value_text) = LOWER(:val)
            LIMIT 5
            """
        )
        res = session.execute(stmt, {
            "table_id": table_id,
            "col_name": col_name,
            "val": value,
        }).fetchall()
        return [row[0] for row in res]


def query_db_semantic(table_id: str, col_name: str, emb: List[float]) -> List[str]:
    """
    Queries candidate categorical database values using pgvector cosine distance.

    Args:
        table_id: The resolved target table ID.
        col_name: The target column name.
        emb: The embedding query vector list.

    Returns:
        A list of matching database categorical values sorted by similarity.
    """
    with Session(engine) as session:
        stmt = text(
            """
            SELECT value_text FROM large_category_values
            WHERE table_id = :table_id AND column_name = :col_name
            ORDER BY embedding <=> :emb
            LIMIT 10
            """
        )
        res = session.execute(stmt, {
            "table_id": table_id,
            "col_name": col_name,
            "emb": str(emb),
        }).fetchall()
        return [row[0] for row in res]


def query_db_trigram(table_id: str, col_name: str, value: str) -> List[str]:
    """
    Queries candidate categorical database values using trigram similarity ordering.

    Args:
        table_id: The resolved target table ID.
        col_name: The target column name.
        value: Clean filter string literal.

    Returns:
        A list of matching database values ordered by trigram similarity.
    """
    with Session(engine) as session:
        stmt = text(
            """
            SELECT value_text FROM large_category_values
            WHERE table_id = :table_id AND column_name = :col_name
            ORDER BY similarity(value_text, :val) DESC
            LIMIT 10
            """
        )
        res = session.execute(stmt, {
            "table_id": table_id,
            "col_name": col_name,
            "val": value,
        }).fetchall()
        return [row[0] for row in res]


def query_db_digits_match(table_id: str, col_name: str, digits_list: List[str]) -> List[str]:
    """
    Queries database for values where value_text contains the exact digits sequence.

    Args:
        table_id: The resolved target table ID.
        col_name: The target column name.
        digits_list: Digits sequence list to search.

    Returns:
        A list of matching database values.
    """
    if not digits_list:
        return []
        
    with Session(engine) as session:
        # Build a dynamic AND clause for every number found
        clauses = " AND ".join([f"value_text LIKE :p_{i}" for i in range(len(digits_list))])
        params = {"table_id": table_id, "col_name": col_name}
        
        for i, d in enumerate(digits_list):
            params[f"p_{i}"] = f"%{d}%"
            
        stmt = text(f"""
            SELECT value_text FROM large_category_values
            WHERE table_id = :table_id AND column_name = :col_name
              AND {clauses}
            LIMIT 20
        """)
        res = session.execute(stmt, params).fetchall()
        return [row[0] for row in res]


def reciprocal_rank_fusion(sem_list: List[str], lex_list: List[str], k: int = 60) -> List[str]:
    """
    Employs Reciprocal Rank Fusion (RRF) to merge semantic and lexical result lists.

    Args:
        sem_list: List of semantic candidate values.
        lex_list: List of lexical candidate values.
        k: Constant ranking parameter (defaults to 60).

    Returns:
        Merged list of candidates sorted descending by RRF score.
    """
    scores: Dict[str, float] = {}
    for rank, item in enumerate(sem_list, start=1):
        scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    for rank, item in enumerate(lex_list, start=1):
        scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    
    sorted_items = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return sorted_items


def rerank_candidates(query: str, candidates: List[str]) -> List[str]:
    """
    Reranks candidates using a placeholder Cross-Encoder model.
    Currently returns the top 5 candidates.

    To implement full cross-encoder reranking:
    1. Load a pre-trained Cross-Encoder model (e.g. sentence-transformers CrossEncoder).
    2. Score pairs: pairs = [[query, candidate] for candidate in candidates].
    3. Sort candidates descending by scores and return the top 5.
    """
    return candidates[:5]


async def search_workflow(table_id: str, col_name: str, value: str, use_rrf: bool = True) -> List[str]:
    """
    Executes an enterprise-grade retrieval pipeline for large_category columns.

    Args:
        table_id: Database table UUID.
        col_name: Database column name.
        value: Search string literal.
        use_rrf: Enable Reciprocal Rank Fusion merging.

    Returns:
        Deduplicated list of matching candidate strings.
    """
    try:
        # 1. Fast-Path Exact Match
        exact_matches: List[str] = await asyncio.to_thread(query_db_exact, table_id, col_name, value)
        if exact_matches:
            logger.info(f"Fast-path exact match hit for {col_name}={value}: {exact_matches}")
            return exact_matches
            
        # 2. Get embedding vector asynchronously in a thread
        emb: Optional[List[float]] = await asyncio.to_thread(get_query_embedding, value)
        if not emb:
            lex_results = await asyncio.to_thread(query_db_trigram, table_id, col_name, value)
            return rerank_candidates(value, lex_results)
            
        sem_task = asyncio.to_thread(query_db_semantic, table_id, col_name, emb)
        lex_task = asyncio.to_thread(query_db_trigram, table_id, col_name, value)
        
        sem_res, lex_res = await asyncio.gather(sem_task, lex_task, return_exceptions=True)
        
        sem_list: List[str] = sem_res if not isinstance(sem_res, BaseException) else []
        lex_list: List[str] = lex_res if not isinstance(lex_res, BaseException) else []
        
        # 3. Merge employing RRF Reranking
        if use_rrf:
            merged_list = reciprocal_rank_fusion(sem_list, lex_list)
        else:
            merged_list = list(dict.fromkeys(sem_list + lex_list))
            
        # 4. Cross-Encoder Reranking
        final_list = rerank_candidates(value, merged_list)
        return final_list
    except Exception as e:
        logger.error(f"Search workflow failed for {col_name}={value}: {e}", exc_info=True)
        return []


async def unit_id_workflow(table_id: str, col_name: str, value: str, use_rrf: bool = True) -> List[str]:
    """
    Executes retrieval pipeline for large_unit_id numeric semantic columns.
    Uses Reciprocal Rank Fusion to balance exact numeric strictness with semantic flexibility.

    Args:
        table_id: Database table UUID.
        col_name: Database column name.
        value: Search string literal.

    Returns:
        A list of matching database values.
    """
    try:
        # A. Regex digits extraction (Keep as a list!)
        digits_match: List[str] = re.findall(r"\d+", value)
        
        # B. Exact/Numeric Match Priority (Acts as our "Lexical" list)
        exact_numeric_results: List[str] = []
        if digits_match:
            # We fetch up to 50 so RRF has enough data to do the math
            exact_numeric_results = await asyncio.to_thread(query_db_digits_match, table_id, col_name, digits_match)
            
        # C. Semantic lookup (Acts as our "Meaning" list)
        emb: Optional[List[float]] = await asyncio.to_thread(get_query_embedding, value)
        semantic_raw_results: List[str] = []
        if emb:
            semantic_raw_results = await asyncio.to_thread(query_db_semantic, table_id, col_name, emb)
            
        # Filter out semantic results that do not contain the extracted exact numbers
        filtered_semantic: List[str] = []
        has_digit_matches = False
        
        if exact_numeric_results:
            has_digit_matches = True
            
        if digits_match:
            for item in semantic_raw_results:
                if all(d in item for d in digits_match):
                    has_digit_matches = True
                    filtered_semantic.append(item)
            # If no candidates containing the digits were found anywhere, fallback to raw semantic list
            if not has_digit_matches:
                filtered_semantic = semantic_raw_results
        else:
            filtered_semantic = semantic_raw_results
            
        # D. The Balanced Merge
        if use_rrf and (exact_numeric_results or filtered_semantic):
            # RRF beautifully balances this. Exact numbers get boosted, semantic meaning gets preserved.
            # Garbage semantic matches drop to the bottom.
            combined = reciprocal_rank_fusion(filtered_semantic, exact_numeric_results)
        else:
            # Fallback if RRF is disabled
            combined = list(dict.fromkeys(exact_numeric_results + filtered_semantic))
            
        # Safely cap the list so we don't overwhelm the LLM context window
        return combined
        
    except Exception as e:
        logger.error(f"Unit ID workflow failed for {col_name}={value}: {e}", exc_info=True)
        return []


class HybridSearcher:
    """
    Handles candidate retrieval workflows across multi-model databases 
    (Semantic Vector store + PostgreSQL relational tables).
    """

    @staticmethod
    async def search(filters: List[SQLFilterParams], tables: List[AgentSQLTable]) -> Dict[str, List[str]]:
        """
        Executes parallel lookup requests for all categorical filter parameters.

        Args:
            filters: List of SQLFilterParams extracted from draft query.
            tables: List of schemas containing targeted column configurations.

        Returns:
            A mapping from "column#@#value" to lists of candidate database values.
        """
        results: Dict[str, List[str]] = {}
        local_cache: Dict[str, List[str]] = {}
        table_id_cache: Dict[str, str] = {}
        
        tasks = []
        task_keys: List[str] = []
        
        def find_agent_table(tbl_name: str) -> Optional[AgentSQLTable]:
            tbl_lower: str = tbl_name.lower()
            for t in tables:
                t_lower: str = t.name.lower()
                if t_lower == tbl_lower or t_lower.endswith("." + tbl_lower):
                    return t
            return None
            
        for param in filters:
            agent_tbl: Optional[AgentSQLTable] = find_agent_table(param.source_table)
            if not agent_tbl:
                continue
                
            col_info = agent_tbl.columns.get(param.source_column) or agent_tbl.columns.get(param.source_column.lower())
            if not col_info:
                continue
                
            col_type: str = col_info.get("column_type", "") if isinstance(col_info, dict) else str(col_info)
            col_type_lower = col_type.lower()
            if col_type_lower not in ("large_category", "large_categorical", "large_unit_id"):
                continue
                
            # Parse targets to process
            search_vals: List[str] = []
            if isinstance(param.value, list):
                for val in param.value:
                    if val is not None:
                        search_vals.append(str(val))
            elif param.value is not None:
                val_str: str = str(param.value)
                if param.operator.upper() == "LIKE":
                    val_str = val_str.replace("%", "").strip()
                search_vals.append(val_str)
                
            # Resolve table ID once per filter parameter using the cache
            if param.source_table not in table_id_cache:
                t_id = await asyncio.to_thread(find_table_id, param.source_table)
                if t_id:
                    table_id_cache[param.source_table] = t_id

            table_id = table_id_cache.get(param.source_table)
            if not table_id:
                logger.warning(f"Could not resolve table_id for {param.source_table}")
                continue # Skip this entire column if the table doesn't exist

            # Queue lookups for uncached items
            for s_val in search_vals:
                key: str = f"{param.source_column}#@#{s_val}"
                if key in local_cache or key in task_keys:
                    continue
                    
                if col_type_lower == "large_unit_id":
                    tasks.append(unit_id_workflow(table_id, param.source_column, s_val))
                else:
                    tasks.append(search_workflow(table_id, param.source_column, s_val))
                task_keys.append(key) 
           
        if tasks:
            search_results = await asyncio.gather(*tasks, return_exceptions=True)
            for key, res in zip(task_keys, search_results, strict=False):
                if isinstance(res, BaseException):
                    logger.error(f"Search failed for {key}: {res}")
                    local_cache[key] = []
                else:
                    local_cache[key] = res
                    
        # Construct output dictionary mapping
        for param in filters:
            if isinstance(param.value, list):
                for val in param.value:
                    if val is not None:
                        val_str = str(val)
                        s_val = val_str.replace("%", "").strip() if param.operator.upper() == "LIKE" else val_str
                        cache_key: str = f"{param.source_column}#@#{s_val}"
                        if cache_key in local_cache:
                            results[f"{param.source_column}#@#{val_str}"] = local_cache[cache_key]
            elif param.value is not None:
                val_str = str(param.value)
                s_val = val_str.replace("%", "").strip() if param.operator.upper() == "LIKE" else val_str
                cache_key = f"{param.source_column}#@#{s_val}"
                if cache_key in local_cache:
                    results[f"{param.source_column}#@#{val_str}"] = local_cache[cache_key]
                    
        return results
