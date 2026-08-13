import json
import logging
import datetime
from typing import Optional
from pydantic import Field
from langchain_core.tools import tool
from langchain_core.prompts import PromptTemplate

from agent.utils.jeen_metadata_client import get_jeen_metadata_client
from core.trino import execute_query_sync
from agent.services.geo_utils import get_geojson_polygon, geojson_to_simplified_wkt
from agent.llm import get_llm

logger = logging.getLogger(__name__)

@tool
async def get_database_catalog() -> str:
    """
    Retrieves the complete metadata catalog and structural overview for the database.
    
    Use this FIRST when starting any new query to discover what tables exist, how they relate, and to see column profiling statistics.
    Do NOT use this to fetch live data rows; this only retrieves metadata and schema definitions.
    
    The output payload includes Column/Table Names, Descriptions, Types, Universal Base Metrics (Null Rate, Distinct Count) and specific Profiling Data Payloads.
    """
    client = get_jeen_metadata_client()
    try:
        catalog_prompt = await client.get_catalog_prompt()
        return catalog_prompt
    except Exception as e:
        return f"Error fetching catalog: {str(e)}"

@tool
async def get_table_profile(table_name: str) -> str:
    """
    Retrieves the latest stored schema and statistics for one specific table. 
    
    Use this to understand a single table's row counts, column roles, null ratios, distinct counts, and min/max values.
    Do NOT use this to fetch raw data rows; use `get_sample_rows` or `execute_trino_query` instead.
    
    Args:
        table_name (str): The fully qualified, exact 3-part name of the table, e.g., '"catalog"."schema"."table"'.
    """
    client = get_jeen_metadata_client()
    try:
        profile = await client.get_table_profile(table_name)
        if not profile:
            return f"Error: Schema not found for table {table_name}"
        return json.dumps(profile, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error fetching table schema: {str(e)}"

@tool
async def get_column_profile(table_name: str, column_name: str) -> str:
    """
    Retrieves the latest stored statistics for a single specific column.
    
    Use this before writing a WHERE clause to learn exactly how values are formatted in the database.
    Do NOT use this for live dynamic querying of values; this only reads pre-computed metadata.
    
    If the output shows 'domainIsComplete: true', the listed values are the entire value set, so any other value can be safely ruled out. The 'semanticType' field indicates how the column should be searched (e.g., categorical, geo).
    
    Args:
        table_name (str): The fully qualified exact name of the table, e.g., '"catalog"."schema"."table"'.
        column_name (str): The exact name of the column to profile.
    """
    client = get_jeen_metadata_client()
    try:
        profile = await client.get_column_profile(table_name, column_name)
        if not profile:
            return f"Error: Column profile not found for {table_name}.{column_name}"
        return json.dumps(profile, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error fetching column profile: {str(e)}"

@tool
def execute_trino_query(sql: str, limit: int = 50) -> str:
    """
    Executes a read-only Trino SQL query and returns the resulting rows. 
    
    Use this to test SQL queries against the actual database and perform exploratory data analysis.
    Do NOT use this for queries returning millions of rows; it will crash the context. Always use LIMIT.
    
    Args:
        sql (str): The exact Trino SQL query string to execute.
        limit (int): The maximum number of rows to return (from 1 to 100). Defaults to 50.
    """
    sql_clean = sql.strip().rstrip(';')
    if "limit" not in sql_clean.lower():
        sql_clean = f"{sql_clean} LIMIT {limit}"
        
    try:
        res = execute_query_sync(sql_clean)
        if not res.success:
            return f"Error executing query: {res.error_message}"
        
        result_dict = {
            "columns": res.columns,
            "row_count": res.row_count,
            "rows": res.rows
        }
        return json.dumps(result_dict, ensure_ascii=False, default=str)
    except Exception as e:
        return f"Execution failed: {str(e)}"

@tool
def get_sample_rows(table_name: str, limit: int = 5) -> str:
    """
    Retrieves a small number of raw data rows from a table using a live Trino query.
    
    Use this only when schema profiles do not provide enough context and you must see exact raw data formats.
    Do NOT use this for counting or aggregating data.
    
    Args:
        table_name (str): The fully qualified exact name of the table.
        limit (int): The number of rows to sample (from 1 to 10). Defaults to 5.
    """
    safe_table = table_name.split()[0].rstrip(';')
    sql = f"SELECT * FROM {safe_table} LIMIT {limit}"
    return execute_trino_query.invoke({"sql": sql, "limit": limit})

@tool
def get_column_distinct_values(table_name: str, column_name: str, limit: int = 50) -> str:
    """
    Executes a live Trino query to fetch unique values for a specific column.
    
    Use this to see exact existing strings in the database before writing an exact-match WHERE clause.
    Do NOT use this if the catalog or `get_column_profile` already provided the exhaustive list of categorical values.
    
    Args:
        table_name (str): The fully qualified exact name of the table.
        column_name (str): The exact name of the column.
        limit (int): Maximum distinct values to return (from 1 to 100). Defaults to 50.
    """
    safe_table = table_name.split()[0].rstrip(';')
    safe_col = column_name.split()[0].rstrip(';')
    sql = f"SELECT DISTINCT {safe_col} FROM {safe_table} LIMIT {limit}"
    return execute_trino_query.invoke({"sql": sql, "limit": limit})

@tool
async def search_metadata(query: str, limit: int = 10) -> str:
    """
    Searches the metadata catalog across all tables, columns, relationships, and business terms.
    
    Use this to quickly discover relevant schema or glossary context when you aren't sure which table to look at.
    Do NOT use this to search for specific data row values (e.g., 'John Doe'); use `search_column_values` instead.
    
    Args:
        query (str): The search text (multilingual/Hebrew support) to match against metadata names and descriptions.
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    client = get_jeen_metadata_client()
    res = await client.search(query, limit)
    return json.dumps(res, indent=2, ensure_ascii=False)

@tool
async def search_business_terms(query: str, limit: int = 10) -> str:
    """
    Searches the business glossary for terms relevant to a natural language question.
    
    Use this to bridge the gap between user vocabulary and database terminology (e.g., matching 'sales' to 'Revenue').
    Do NOT use this to search for database tables or column names directly.
    
    The search is multilingual and ranks by meaning rather than exact keywords.
    
    Args:
        query (str): A natural language question or phrase (multilingual support).
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    client = get_jeen_metadata_client()
    res = await client.search_business_terms(query, limit)
    return json.dumps(res, indent=2, ensure_ascii=False)

@tool
async def search_knowledge_pairs(query: str, limit: int = 10) -> str:
    """
    Searches saved question-and-query examples that are semantically similar to the user's question.
    
    Use this to find few-shot examples of how similar questions were solved previously before writing your own query.
    Even if you think you know how to write the query, searching for similar past questions often reveals domain-specific SQL conventions, exact JOIN paths, or edge cases you haven't considered.

    Do NOT pass SQL keywords as the query; pass the raw natural language question exactly as the user wrote it (in Hebrew).
    
    Only the stored question is matched (never the query text), so it works best with full natural language sentences.
    
    Args:
        query (str): The natural language user question (multilingual/Hebrew support).
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    client = get_jeen_metadata_client()
    res = await client.search_knowledge_pairs(query, limit)
    return json.dumps(res, indent=2, ensure_ascii=False)

@tool
async def search_column_values(query: str, table: Optional[str] = None, column: Optional[str] = None, limit: int = 10) -> str:
    """
    Searches for exact real values contained within text columns across the database.
    
    Use this before writing a WHERE clause on a text column when you only have a partial or loosely spelled value (e.g., finding 'Tel Aviv' from 'tel aviv').
    Do NOT use this on numeric or date columns.
    
    Matching is multilingual for descriptive values and substring-based for codes and identifiers, ensuring you find the exact database spelling.
    
    Args:
        query (str): The specific value to look for (partial or full string).
        table (str, optional): Restrict to one table. Omit to search all tables.
        column (str, optional): Restrict to one column. Omit to search all columns.
        limit (int): Maximum number of results to return. Defaults to 10.
    """
    client = get_jeen_metadata_client()
    res = await client.search_column_values(query, table, column, limit)
    return json.dumps(res, indent=2, ensure_ascii=False)


@tool
def resolve_location_to_wkt(location_name: str) -> str:
    """
    Resolves a geographic location name to its WKT (Well-Known Text) polygon representation.
    
    Use this to get the exact WKT coordinates when you need to filter geographically using `ST_GeometryFromText()` and only have a city/region name.
    Do NOT use this for exact addresses; it is designed for cities, regions, and countries.
    
    Args:
        location_name (str): The name of the geographic location to resolve (e.g., 'Paris, France').
    """
    geojson = get_geojson_polygon(location_name)
    if geojson:
        wkt = geojson_to_simplified_wkt(geojson)
        if wkt:
            return wkt
    return f"Error: Could not resolve location '{location_name}' to WKT."

@tool
def validate_sql_syntax(sql: str) -> str:
    """
    Validates the syntax of a Trino SQL query without executing it against data.
    
    Use this to dry-run a query and instantly catch syntax errors (like missing CASTs or typos).
    Do NOT use this to check if a query returns 0 rows; it only checks syntax validity.
    
    Args:
        sql (str): The exact Trino SQL query string to validate.
    """
    sql_clean = sql.strip().rstrip(';')
    try:
        res = execute_query_sync(f"EXPLAIN (TYPE VALIDATE) {sql_clean}")
        if res.success:
            return "Syntax is VALID."
        else:
            return f"Syntax Error: {res.error_message}"
    except Exception as e:
        return f"Validation failed: {str(e)}"
