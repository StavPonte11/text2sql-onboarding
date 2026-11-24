import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_jeen_mcp_session():
    """Context manager for an MCP ClientSession connecting to the Jeen metadata server."""
    url = settings.JEEN_METADATA_MCP_URL
    headers = (
        {"Authorization": f"Bearer {settings.JEEN_METADATA_MCP_KEY}"}
        if settings.JEEN_METADATA_MCP_KEY
        else {}
    )
    async with streamablehttp_client(url, headers=headers) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


# list_prompt_tests returns at most this many tests per call
_PROMPT_TESTS_PAGE = 200


def _tool_json(result: Any, tool: str) -> Any:
    """The JSON a Jeen MCP tool answered with; raises when the tool reported an error."""
    text = next((c.text for c in result.content or [] if c.type == "text"), None)
    if getattr(result, "isError", False):
        raise RuntimeError(f"Jeen {tool} failed: {text or 'no details'}")
    return json.loads(text) if text else None


async def fetch_jeen_sql_pairs(connection_id: int) -> list[dict[str, Any]]:
    """
    The connection's golden questions: the tests on Jeen's Prompt tab
    (list_prompt_tests, the connection's own "override" set), each as
    {"question", "query" (its reference query), "prompt_test_id"}.

    Not the knowledge pairs (get_sql_pairs): those are the few-shot examples
    the agent itself is given, so they can't also judge it.
    """
    tests: list[dict[str, Any]] = []
    offset: int | None = 0
    try:
        async with get_jeen_mcp_session() as session:
            while offset is not None:
                result = await session.call_tool(
                    "list_prompt_tests",
                    {
                        "scope": "override",
                        "connection_id": connection_id,
                        "limit": _PROMPT_TESTS_PAGE,
                        "offset": offset,
                    },
                )
                data = _tool_json(result, "list_prompt_tests") or {}
                page = data.get("tests") or []
                tests.extend(page)
                next_offset = data.get("next_offset")
                # stop on an empty page too, so a server that keeps sending
                # next_offset can't loop forever
                offset = next_offset if page and next_offset is not None else None
    except Exception as e:
        logger.error(
            f"[JeenMetadata] Failed to fetch prompt tests for connection {connection_id}: {e}",
            exc_info=True,
        )
        raise
    return [
        {
            "question": test.get("question"),
            "query": test.get("reference_query"),
            "prompt_test_id": test.get("id"),
        }
        for test in tests
    ]


async def fetch_jeen_knowledge_questions(
    connection_id: int,
) -> list[dict[str, Any]]:
    """Fetch knowledge questions, categories, and tags for a connection from Jeen metadata MCP."""
    try:
        async with get_jeen_mcp_session() as session:
            result = await session.call_tool(
                "list_knowledge_questions", {"connection_id": connection_id}
            )
            if not result.content:
                return []
            first = result.content[0]
            if first.type != "text":
                return []
            data = json.loads(first.text)
            if isinstance(data, dict):
                return data.get("questions") or data.get("knowledge_questions") or []
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(
            f"[JeenMetadata] Failed to fetch knowledge questions for connection {connection_id}: {e}",
            exc_info=True,
        )
        raise


def _normalized_tags(raw: Any) -> list[str]:
    """Tags as a list of names, whether Jeen sent names, objects or a comma list."""
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        return []
    names = [
        (tag.get("name") or tag.get("tag") or tag.get("label"))
        if isinstance(tag, dict)
        else tag
        for tag in raw
    ]
    return list(dict.fromkeys(str(n).strip() for n in names if n and str(n).strip()))


def _question_key(item: dict[str, Any]) -> str:
    return (item.get("question") or "").strip()


async def fetch_jeen_sql_pairs_with_tags(connection_id: int) -> list[dict[str, Any]]:
    """
    The connection's golden questions (its Prompt-tab tests), each with a
    normalized "tags" list. Prompt tests have no tags, so a test takes the tags
    of the knowledge question with the same text (list_knowledge_questions);
    if that fails the questions are still returned, without tags.
    """
    pairs = await fetch_jeen_sql_pairs(connection_id)
    knowledge_tags: dict[str, list[str]] = {}
    if pairs and any(pair.get("tags") is None for pair in pairs):
        try:
            knowledge_tags = {
                _question_key(item): _normalized_tags(item.get("tags"))
                for item in await fetch_jeen_knowledge_questions(connection_id)
            }
        except Exception as e:
            logger.warning(
                f"[JeenMetadata] No knowledge-question tags for connection {connection_id}: {e}"
            )
    return [
        {
            **pair,
            "tags": knowledge_tags.get(_question_key(pair), [])
            if pair.get("tags") is None
            else _normalized_tags(pair["tags"]),
        }
        for pair in pairs
    ]


async def save_jeen_sql_pair(
    connection_id: int,
    question: str,
    query: str,
    category: str | None = "General",
    tags: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create or update a question -> SQL query knowledge pair in Jeen metadata MCP."""
    try:
        arguments: dict[str, Any] = {
            "connection_id": connection_id,
            "question": question,
            "query": query,
            "category": category or "General",
            "tags": tags if tags is not None else [],
            "dry_run": dry_run,
        }
        async with get_jeen_mcp_session() as session:
            result = await session.call_tool("save_knowledge_pair", arguments=arguments)
            if not result.content:
                return {"status": "ok"}
            first = result.content[0]
            if first.type == "text":
                try:
                    return json.loads(first.text)
                except json.JSONDecodeError:
                    return {"text": first.text}
            return {"status": "ok"}
    except Exception as e:
        logger.error(
            f"[JeenMetadata] Failed to save knowledge pair for connection {connection_id}: {e}",
            exc_info=True,
        )
        raise
