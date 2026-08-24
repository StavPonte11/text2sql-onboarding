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
        client = get_jeen_metadata_client()
        
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
                
                # Strip wildcards before sending to Jeen MCP to ensure clean semantic matching
                clean_val = val.replace("%", "")
                
                # Schedule search task directly via MCP client
                # The MCP backend handles semantic_type routing automatically
                task = client.search_column_values(
                    query=clean_val, 
                    table_name=table_lower or None, 
                    column_name=col_lower or None
                )
                    
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
                # Deduplicate while preserving the custom ranking order
                seen = set()
                deduped = []
                for x in res:
                    if x not in seen:
                        seen.add(x)
                        deduped.append(x)
                final_dict[key] = deduped
                
        return final_dict
