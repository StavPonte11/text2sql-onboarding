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
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from agent.config import settings

logger = logging.getLogger(__name__)


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
    if not result.content:
        return {}

    first = result.content[0]
    text = getattr(first, "text", None)
    if text is None:
        return {}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


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
        self, query: str, table_name: str | None = None, column_name: str | None = None
    ) -> list[str]:
        """
        Looks up real values a column contains using semantic and keyword matching via MCP.
        """
        if not self.is_configured:
            logger.warning("JeenMetadataClient is not configured. Returning empty search results.")
            return []
            
        args: dict[str, Any] = {
            "connection_id": self._connection_id,
            "query": query,
            "limit": self._search_limit,
        }
        if table_name:
            args["table"] = table_name
        if column_name:
            args["column"] = column_name

        try:
            payload = await self._call("search_column_values", args)
            if isinstance(payload, dict) and "values" in payload:
                results = []
                for v in payload["values"]:
                    if isinstance(v, dict) and "value" in v:
                        results.append(str(v["value"]))
                    else:
                        results.append(str(v))
                return results
            return []
        except Exception as exc:
            logger.error("JeenMetadataClient.search_column_values failed: %s", exc, exc_info=True)
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

            # Normalise column list into the shape the agent already consumes
            columns = []
            for col in columns_raw:
                entry: dict[str, Any] = {
                    "name": col.get("columnName") or col.get("column_name") or col.get("name", ""),
                    "type": col.get("dataType") or col.get("data_type") or col.get("type", ""),
                    "null_rate": round(float(col.get("nullRate") or col.get("null_rate") or 0.0), 4),
                    "distinct_count": int(col.get("distinctCount") or col.get("distinct_count") or 0),
                    "semantic_type": col.get("semantic_type") or "unknown",
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
