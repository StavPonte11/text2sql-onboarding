"""
jeen_metadata_client.py
=======================
MCP client adapter for jeen-metadata.

Replaces the local Postgres hybrid_search_tables() + get_table_profile()
calls with equivalent calls to the jeen-metadata MCP server.

MCP tools used
--------------
- ``get_catalog_prompt``            → table discovery (replaces hybrid_search_tables)
- ``get_table_profile`` → per-table column stats (replaces get_table_profile tool)
- ``list_tables_rich``  → fallback full-table list with row counts

Why we use the MCP SDK instead of raw httpx
--------------------------------------------
jeen-metadata uses WebStandardStreamableHTTPServerTransport from the
@modelcontextprotocol/sdk (TypeScript).  That transport requires a proper
MCP handshake before any tool call:

  1. POST  initialize      → 200 + JSON  (server sends InitializeResult)
  2. POST  notifications/initialized → 202 + empty body  ← raw httpx dies here
  3. POST  tools/call      → 200 + JSON or SSE stream

The Python MCP SDK's streamablehttp_client + ClientSession handles all three
steps automatically and knows how to read both JSON and SSE responses.
Bypassing it with plain httpx would require re-implementing the entire
handshake and SSE framing logic — and that's exactly what caused the
"Expecting value: line 1 column 1 (char 0)" error.

Configuration (all in AgentSettings / .env)
-------------------------------------------
JEEN_METADATA_MCP_URL          Base URL of the MCP endpoint,
                                e.g. https://jeen-metadata.example.com/api/mcp
JEEN_METADATA_MCP_KEY          Bearer token / API key issued by jeen-metadata's
                                key-management UI (/api/mcp/keys).
JEEN_METADATA_CONNECTION_ID    Numeric service ID returned by list_connections.
JEEN_METADATA_SEARCH_LIMIT     Max tables returned by the search tool (default 10).
JEEN_METADATA_PROFILE_TIMEOUT  Per-call timeout in seconds (default 15).
"""

from __future__ import annotations

import json
import logging
from typing import Any, NoReturn

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from agent.config import settings

logger = logging.getLogger(__name__)


class JeenMetadataError(RuntimeError):
    """The MCP server reported an error, or returned a payload of an unexpected shape."""


# ---------------------------------------------------------------------------
# Low-level MCP call helper
# ---------------------------------------------------------------------------

async def _call_mcp_tool(
    url: str,
    api_key: str,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = 15.0,
) -> Any:
    """
    Open an MCP session, run the full initialization handshake, call one
    tool, and return the parsed payload from the first text content block.

    Uses the official Python MCP SDK so the
    initialize → notifications/initialized → tools/call sequence is handled
    automatically.  Returns parsed JSON (dict/list) or a raw string if the
    content block is not JSON.
    """
    headers = {"Authorization": f"Bearer {api_key}"}

    async with streamablehttp_client(
        url,
        headers=headers,
        timeout=timeout,
        # SSE read timeout slightly longer than the overall timeout so a
        # streaming tool call has time to produce its first event.
        sse_read_timeout=timeout + 30,
    ) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool(tool_name, arguments)

    # result.content is a list of ContentBlock objects.
    # We expect the first (and only) block to be a TextContent.
    first = result.content[0] if result.content else None
    text = getattr(first, "text", None)

    if getattr(result, "isError", False):
        raise JeenMetadataError(f"MCP tool '{tool_name}' failed: {text or 'no error details'}")
    if text is None:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _reraise_as_metadata_error(exc: Exception) -> NoReturn:
    if isinstance(exc, JeenMetadataError):
        raise exc
    raise JeenMetadataError(str(exc)) from exc


def _expect_list(payload: Any, key: str, method: str) -> list[dict[str, Any]]:
    """Unwrap a list payload (bare or under `key`); anything else is an error, never an empty result."""
    if isinstance(payload, dict) and key in payload:
        payload = payload[key]
    if isinstance(payload, list):
        return payload
    raise JeenMetadataError(f"{method} returned unexpected payload: {str(payload)[:300]}")


# ---------------------------------------------------------------------------
# Public client façade
# ---------------------------------------------------------------------------

class JeenMetadataClient:
    """
    High-level async client for jeen-metadata's MCP server.

    Methods map 1-to-1 to what schema_explorer_node needs:
    - get_catalog_prompt()     get the big catalog prompt about the whole database we work with
    - get_table_profile() replaces the @tool get_table_profile()
    - list_tables_rich()  fallback when search returns nothing
    """

    def __init__(self) -> None:
        self._mcp_url = getattr(settings, "JEEN_METADATA_MCP_URL", "")
        self._mcp_key = getattr(settings, "JEEN_METADATA_MCP_KEY", "")
        self._connection_id: int = int(getattr(settings, "JEEN_METADATA_CONNECTION_ID", 0))
        self._search_limit: int = int(
            getattr(settings, "JEEN_METADATA_SEARCH_LIMIT", settings.HYBRID_SEARCH_MAX_TABLES)
        )
        self._timeout: float = float(getattr(settings, "JEEN_METADATA_PROFILE_TIMEOUT", 15.0))

        self.is_configured: bool = bool(
            self._mcp_url and self._mcp_key and self._connection_id
        )

        if not self.is_configured:
            logger.info(
                "JeenMetadataClient is not fully configured "
                "(JEEN_METADATA_MCP_URL / JEEN_METADATA_MCP_KEY / "
                "JEEN_METADATA_CONNECTION_ID missing). "
                "Schema explorer will fall back to local DB."
            )

    # ── internal helper ────────────────────────────────────────────────────

    def _cid(self, connection_id: int | None) -> int:
        """Per-call connection override, falling back to the configured default."""
        return connection_id if connection_id is not None else self._connection_id

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Thin wrapper so individual methods don't repeat the URL/key/timeout."""
        return await _call_mcp_tool(
            self._mcp_url,
            self._mcp_key,
            tool_name,
            arguments,
            self._timeout,
        )

    # ------------------------------------------------------------------
    # Search Column Values
    # ------------------------------------------------------------------

    async def search_column_values(
        self,
        search_term: str,
        table: str | None = None,
        column: str | None = None,
        limit: int | None = None,
        raw: bool = False,
        connection_id: int | None = None,
    ) -> list[str] | dict[str, Any]:
        """
        Looks up real values a column contains using semantic and keyword matching via MCP.
        Uses exact MCP parameter names: search_term, table, column, limit.
        When raw=True, returns the full payload dictionary from the MCP server.
        """
        if not self.is_configured:
            logger.warning("JeenMetadataClient is not configured. Returning empty search results.")
            return [] if not raw else {"error": "JeenMetadataClient is not configured."}

        lim = limit if limit is not None else self._search_limit
        args: dict[str, Any] = {
            "connection_id": self._cid(connection_id),
            "search_term": search_term,
            "limit": lim,
        }
        if table:
            args["table"] = table
        if column:
            args["column"] = column

        try:
            payload = await self._call("search_column_values", args)
            if raw:
                if isinstance(payload, dict):
                    return payload
                if isinstance(payload, list):
                    return {"values": payload}
                raise JeenMetadataError(f"search_column_values returned unexpected payload: {str(payload)[:300]}")

            if isinstance(payload, dict) and "values" in payload:
                results = []
                for v in payload["values"]:
                    if isinstance(v, dict) and "value" in v:
                        results.append(str(v["value"]))
                    else:
                        results.append(str(v))
                return results
            elif isinstance(payload, list):
                return [str(x) for x in payload]
            return []
        except Exception as exc:
            logger.error("JeenMetadataClient.search_column_values failed: %s", exc, exc_info=True)
            if raw:
                _reraise_as_metadata_error(exc)
            return []

    # ------------------------------------------------------------------
    # Glossary / Context
    # ------------------------------------------------------------------

    async def get_catalog_prompt(self, connection_id: int | None = None) -> str:
        """
        Fetch the entire catalog context prompt for the connection using the
        MCP `get_catalog_prompt` tool. This returns a large markdown string
        describing all tables, columns, relationships, and business terms.
        """
        try:
            cid = connection_id if connection_id is not None else self._connection_id
            payload = await self._call(
                "get_catalog_prompt",
                {
                    "connection_id": cid,
                },
            )
            # The MCP tool returns { "content": [{ "type": "text", "text": "..." }] }
            # but our _call helper returns the parsed JSON or the raw text block.
            # In get_catalog_prompt's case, the content block is the prompt string directly.
            
            if isinstance(payload, str):
                logger.info("JeenMetadataClient.get_catalog_prompt → fetched successfully.")
                return payload
            elif isinstance(payload, dict) and "prompt" in payload:
                # Just in case the MCP returned a JSON string that we parsed
                return payload.get("prompt", "")
            else:
                logger.warning("JeenMetadataClient.get_catalog_prompt → unexpected payload type %s", type(payload))
                return str(payload)

        except Exception as exc:
            logger.error(
                "JeenMetadataClient.get_catalog_prompt failed: %s", exc, exc_info=True
            )
            raise RuntimeError(
                f"Failed to connect to Jeen MCP at {self._mcp_url} (Connection ID: {self._connection_id}): {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Table profile (columns + stats)
    # ------------------------------------------------------------------

    async def get_table_profile(self, table_name: str, connection_id: int | None = None) -> dict[str, Any] | None:
        """
        Fetch the latest stored column stats for *table_name* from jeen-metadata.

        Returns a dict shaped identically to the lightweight dict built by
        the local ``get_table_profile`` @tool so the rest of
        schema_explorer_node is unchanged:

            {
                "table_id":    str,
                "table_name":  str,       # fully qualified
                "description": str,
                "row_count":   int | None,
                "columns": [
                    {"name": str, "type": str, "null_rate": float,
                     "distinct_count": int, ...},
                    ...
                ]
            }
        """
        try:
            cid = connection_id if connection_id is not None else self._connection_id
            payload = await self._call(
                "get_table_profile",
                {
                    "connection_id": cid,
                    "table_name": table_name,
                },
            )

            table_profile: dict = payload.get("table_profile") or {} if isinstance(payload, dict) else {}
            columns_raw: list[dict] = payload.get("columns") or [] if isinstance(payload, dict) else []

            # Normalise column list into the shape the agent consumes
            columns = []
            for col in columns_raw:
                null_val = col.get("nullRatio") if col.get("nullRatio") is not None else (col.get("nullRate") or col.get("null_rate"))
                distinct_val = col.get("distinctCount") if col.get("distinctCount") is not None else col.get("distinct_count")
                sem_type = col.get("semanticType") or col.get("semantic_type") or col.get("columnRole") or "unknown"

                entry: dict[str, Any] = {
                    "name": col.get("columnName") or col.get("column_name") or col.get("name", ""),
                    "type": col.get("dataType") or col.get("data_type") or col.get("type", ""),
                    "null_rate": round(float(null_val or 0.0), 4),
                    "distinct_count": int(distinct_val or 0),
                    "semantic_type": sem_type,
                    "domain_is_complete": bool(col.get("domainIsComplete") or col.get("domain_is_complete", False)),
                    "example_values": col.get("exampleValues") or col.get("example_values") or [],
                }
                # Propagate optional stats if available
                for src_key, dst_key in [
                    ("minValue", "min"), ("min_value", "min"),
                    ("maxValue", "max"), ("max_value", "max"),
                    ("avgValue", "mean"), ("avg_value", "mean"),
                ]:
                    val = col.get(src_key)
                    if val is not None:
                        entry[dst_key] = val
                sample = col.get("sampleValues") or col.get("sample_values")
                if sample:
                    entry["sample_values"] = sample

                columns.append(entry)

            result = {
                "table_id": table_name,
                "table_name": f"{payload.get('table_name', table_name)}" if isinstance(payload, dict) else table_name,
                "description": table_profile.get("tableDescription") or "",
                "row_count": table_profile.get("rowCount"),
                "columns": columns,
            }

            logger.info(
                "JeenMetadataClient.get_table_profile: table=%r → %d column(s)",
                table_name,
                len(columns),
            )
            return result

        except Exception as exc:
            logger.error(
                "JeenMetadataClient.get_table_profile(%r) failed: %s",
                table_name,
                exc,
                exc_info=True,
            )
            return None

    async def get_column_profile(
        self, table_name: str, column_name: str, connection_id: int | None = None
    ) -> dict[str, Any] | None:
        """
        Returns the latest stored statistics for a single column: role, null ratio, distinct count, min/max, and example values.
        """
        try:
            res = await self._call(
                "get_column_profile",
                {
                    "connection_id": self._cid(connection_id),
                    "table_name": table_name,
                    "column_name": column_name,
                },
            )
        except Exception as exc:
            logger.error("JeenMetadataClient.get_column_profile failed: %s", exc)
            _reraise_as_metadata_error(exc)
        if isinstance(res, dict):
            return res.get("profile") or res
        if isinstance(res, str) and res.strip():
            raise JeenMetadataError(f"get_column_profile returned unexpected payload: {res[:300]}")
        return None

    # ------------------------------------------------------------------
    # Tables, Columns & Relationships Structure
    # ------------------------------------------------------------------

    async def get_tables(self, connection_id: int | None = None) -> list[dict[str, Any]]:
        """
        Returns the list of tables for a connection with business descriptions and quoted identifiers.
        """
        cid = connection_id if connection_id is not None else self._connection_id
        res = await self._call("get_tables", {"connection_id": cid})
        return _expect_list(res, "tables", "get_tables")

    async def get_columns(self, table_name: str | None = None, connection_id: int | None = None) -> list[dict[str, Any]]:
        """
        Returns columns for connection (data type, primary key, description, example values, profiling stats).
        """
        cid = connection_id if connection_id is not None else self._connection_id
        args: dict[str, Any] = {"connection_id": cid}
        if table_name:
            args["table_name"] = table_name
        res = await self._call("get_columns", args)
        return _expect_list(res, "columns", "get_columns")

    async def get_relationships(self, connection_id: int | None = None) -> list[dict[str, Any]]:
        """
        Returns source-scoped foreign-key and semantic-model joins for a connection.
        """
        cid = connection_id if connection_id is not None else self._connection_id
        res = await self._call("get_relationships", {"connection_id": cid})
        return _expect_list(res, "relationships", "get_relationships")

    async def get_source_statistics(self, connection_id: int | None = None) -> dict[str, Any]:
        """
        Stored source profile summary: latest profile run, table types, column roles.
        """
        cid = connection_id if connection_id is not None else self._connection_id
        return await self._call("get_source_statistics", {"connection_id": cid})

    # ------------------------------------------------------------------
    # Query Execution via MCP
    # ------------------------------------------------------------------

    async def run_query(self, query: str, max_rows: int = 100, connection_id: int | None = None) -> dict[str, Any]:
        """
        Executes a read-only SQL query via the MCP server on connections where can_execute=True.
        """
        cid = connection_id if connection_id is not None else self._connection_id
        return await self._call("run_query", {"connection_id": cid, "query": query, "max_rows": max_rows})

    # ------------------------------------------------------------------
    # Full table listing (fallback when search returns nothing)
    # ------------------------------------------------------------------

    async def list_tables_rich(self, connection_id: int | None = None) -> list[dict[str, Any]]:
        """
        Return ALL tables for the configured connection via ``list_tables_rich``.
        Used as a fallback when the search tool returns no results.
        """
        try:
            cid = connection_id if connection_id is not None else self._connection_id
            rows = await self._call(
                "list_tables_rich",
                {"connection_id": cid},
            )
            if not isinstance(rows, list):
                rows = []
            tables = []
            for row in rows:
                name = row.get("name", "")
                tables.append(
                    {
                        "id": name,
                        "name": name,
                        "schema_name": "",
                        "catalog": "",
                        "description": row.get("description") or "",
                    }
                )
            logger.info(
                "JeenMetadataClient.list_tables_rich → %d table(s)", len(tables)
            )
            return tables
        except Exception as exc:
            logger.error(
                "JeenMetadataClient.list_tables_rich failed: %s", exc, exc_info=True
            )
            return []

    # ------------------------------------------------------------------
    # MCP Search Tools
    # ------------------------------------------------------------------

    async def search(self, query: str, limit: int = 10, connection_id: int | None = None) -> Any:
        """Search metadata for a connection across tables, columns, etc."""
        try:
            return await self._call("search", {"connection_id": self._cid(connection_id), "query": query, "limit": limit})
        except Exception as exc:
            logger.error("search failed: %s", exc)
            _reraise_as_metadata_error(exc)

    async def search_business_terms(self, query: str, limit: int = 10, connection_id: int | None = None) -> Any:
        """Finds business glossary terms relevant to a question."""
        try:
            return await self._call("search_business_terms", {"connection_id": self._cid(connection_id), "query": query, "limit": limit})
        except Exception as exc:
            logger.error("search_business_terms failed: %s", exc)
            _reraise_as_metadata_error(exc)

    async def search_knowledge_pairs(self, query: str, limit: int = 10, connection_id: int | None = None) -> Any:
        """Finds saved question-and-query examples."""
        try:
            return await self._call("search_knowledge_pairs", {"connection_id": self._cid(connection_id), "query": query, "limit": limit})
        except Exception as exc:
            logger.error("search_knowledge_pairs failed: %s", exc)
            _reraise_as_metadata_error(exc)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-initialised, safe to import at module load)
# ---------------------------------------------------------------------------

_client: JeenMetadataClient | None = None


def get_jeen_metadata_client() -> JeenMetadataClient:
    """Return the shared JeenMetadataClient singleton."""
    global _client
    if _client is None:
        _client = JeenMetadataClient()
    return _client
