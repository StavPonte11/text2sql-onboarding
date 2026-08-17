"""
hybrid_searcher.py - Retrieves canonical database matching values.

Delegates semantic and lexical pattern matching to the Jeen-Metadata MCP API.
"""

import logging
import asyncio
import re
from typing import List, Dict, Tuple, Optional
from agent.services.enrichment_models import SQLFilterParams, AgentSQLTable
from agent.utils.jeen_metadata_client import get_jeen_metadata_client
from agent.config import settings

logger = logging.getLogger(__name__)


async def search_workflow(table_id: str, col_name: str, value: str, use_rrf: bool = True) -> List[str]:
    """
    Executes an enterprise-grade retrieval pipeline for large_category columns.

    Args:
        table_id: Database table name or UUID.
        col_name: Database column name.
        value: Search string literal.
        use_rrf: (Deprecated) Kept for compatibility.

    Returns:
        Deduplicated list of matching candidate strings.
    """
    try:
        client = get_jeen_metadata_client()
        # Jeen-Metadata MCP API expects fully qualified table name if we have it, 
        # but the MCP tool accepts bare table names too. 
        results = await client.search_column_values(query=value, table_name=table_id, column_name=col_name)
        return results
    except Exception as e:
        logger.error(f"Search workflow failed for {col_name}={value}: {e}", exc_info=True)
        return []


async def unit_id_workflow(table_id: str, col_name: str, value: str, use_rrf: bool = True) -> List[str]:
    """
    Executes retrieval pipeline for large_unit_id numeric semantic columns.

    Args:
        table_id: Database table name or UUID.
        col_name: Database column name.
        value: Search string literal.

    Returns:
        A list of matching database values.
    """
    try:
        # A. Regex digits extraction to find numeric parts
        digits_match: List[str] = re.findall(r"\d+", value)
        query = value
        if digits_match:
            # Maybe search specifically for the digits if that's what matters for an ID
            # but usually the MCP search_column_values handles partial substrings well.
            pass
            
        client = get_jeen_metadata_client()
        results = await client.search_column_values(query=query, table_name=table_id, column_name=col_name)
        return results
    except Exception as e:
        logger.error(f"Unit ID workflow failed for {col_name}={value}: {e}", exc_info=True)
        return []


class HybridSearcher:
    """
    Handles candidate retrieval workflows across multi-model databases 
    by delegating to Jeen-Metadata MCP API.
    """

    @staticmethod
    async def search(filters: List[SQLFilterParams], tables: List[AgentSQLTable]) -> Dict[str, List[str]]:
        """
        Executes parallel lookup requests for all categorical filter parameters.

        Args:
            filters: List of SQLFilterParams extracted from draft query.
            tables: List of schemas containing targeted column configurations.

        Returns:
            Dict mapping "column{DELIMITER}value" -> List of candidate strings
        """
        # Build map of (table_name, column_name) -> semantic_type
        col_type_map = {}
        for table in tables:
            t_name = table.name.lower()
            for col_name, col_meta in table.columns.items():
                c_name = col_name.lower()
                sem_type = col_meta.get("semantic_type", "categorical") if isinstance(col_meta, dict) else "categorical"
                col_type_map[(t_name, c_name)] = sem_type
                # also store without table prefix
                if c_name not in col_type_map:
                    col_type_map[c_name] = sem_type
                    
        tasks = []
        task_keys = []
        
        delimiter = settings.CACHE_KEY_DELIMITER
        
        for f in filters:
            col_lower = f.source_column.lower()
            table_lower = f.source_table.lower() if f.source_table else ""
            
            # Lookup type
            sem_type = "categorical"
            if table_lower and (table_lower, col_lower) in col_type_map:
                sem_type = col_type_map[(table_lower, col_lower)]
            elif col_lower in col_type_map:
                sem_type = col_type_map[col_lower]
                
            if sem_type not in ("large_categorical", "large_unit_id"):
                continue
                
            values_to_search = []
            if isinstance(f.value, list):
                values_to_search = [str(v) for v in f.value]
            elif f.value is not None:
                values_to_search = [str(f.value)]
                
            for val in values_to_search:
                if not val.strip():
                    continue
                # Schedule tasks based on semantic type
                if sem_type == "large_unit_id":
                    task = unit_id_workflow(table_lower, col_lower, val)
                else:
                    task = search_workflow(table_lower, col_lower, val)
                    
                tasks.append(task)
                task_keys.append(f"{f.source_column}{delimiter}{val}")
                
        if not tasks:
            return {}
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        final_dict: Dict[str, List[str]] = {}
        for key, res in zip(task_keys, results):
            if isinstance(res, Exception):
                logger.error(f"Search task failed for {key}: {res}")
                continue
            if res:
                # Deduplicate and sort
                final_dict[key] = sorted(list(set(res)))
                
        return final_dict
