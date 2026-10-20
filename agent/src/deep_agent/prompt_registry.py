"""
Prompt management for the Deep Agent.

Prompts are Langfuse text prompts named `deep-agent/<key>` (system, schema-scout,
value-resolver, result-verifier), fetched by label (DEEP_AGENT_PROMPT_LABEL, default
"production") with the SDK's TTL cache. The packaged `prompts/<key>.md` files are the
fallback when Langfuse is unreachable or the prompt doesn't exist, and the only source when
DEEP_AGENT_LANGFUSE_PROMPTS is off (tests, offline dev).

PromptRefreshMiddleware swaps in the current Langfuse version on every model call, so moving
a label in Langfuse changes behaviour without a redeploy. Push the packaged prompts with
`uv run python scripts/sync_deep_agent_prompts.py --label staging`.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from agent.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
PROMPT_KEYS = ("system", "schema-scout", "value-resolver", "result-verifier")


def langfuse_name(key: str) -> str:
    return f"deep-agent/{key}"


def local_prompt(key: str) -> str:
    return (PROMPTS_DIR / f"{key}.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class LoadedPrompt:
    key: str
    text: str
    source: str               # "langfuse" | "local"
    version: int | None = None
    client: Any = None        # langfuse TextPromptClient (links generations to the prompt version)


# key -> monotonic deadline until which Langfuse is not asked again (prompt missing / unreachable).
# The SDK caches successful fetches but not misses, and load_prompt runs on every model call.
_misses: dict[str, float] = {}


def _miss(key: str, local: str) -> LoadedPrompt:
    _misses[key] = time.monotonic() + max(settings.DEEP_AGENT_PROMPT_CACHE_TTL_S, 1)
    return LoadedPrompt(key, local, "local")


def load_prompt(key: str) -> LoadedPrompt:
    local = local_prompt(key)
    if not settings.DEEP_AGENT_LANGFUSE_PROMPTS:
        return LoadedPrompt(key, local, "local")
    if _misses.get(key, 0.0) > time.monotonic():
        return LoadedPrompt(key, local, "local")
    try:
        from agent.langfuse_client import langfuse_client

        client = langfuse_client.get_prompt(
            langfuse_name(key),
            label=settings.DEEP_AGENT_PROMPT_LABEL,
            type="text",
            fallback=local,
            cache_ttl_seconds=settings.DEEP_AGENT_PROMPT_CACHE_TTL_S,
            fetch_timeout_seconds=5,
            max_retries=1,
        )
    except Exception as exc:
        logger.warning("Langfuse prompt %s unavailable (%s); using packaged prompt", langfuse_name(key), exc)
        return _miss(key, local)
    if getattr(client, "is_fallback", False):
        return _miss(key, local)
    _misses.pop(key, None)
    return LoadedPrompt(key, client.prompt, "langfuse", getattr(client, "version", None), client)


def _system_text(message: SystemMessage | None) -> str:
    if message is None:
        return ""
    content = message.content
    if isinstance(content, str):
        return content
    first = content[0] if content else None
    return first.get("text", "") if isinstance(first, dict) else str(first or "")


class PromptRefreshMiddleware(AgentMiddleware):
    """Replace the prompt the agent was built with by the current version of `key`, per model call.

    deepagents composes the system message as <our prompt> + harness/skills/memory sections, so
    only that leading part is swapped; if it isn't found verbatim the request is left untouched.
    """

    def __init__(self, key: str, built_with: str) -> None:
        super().__init__()
        self.key = key
        self.built_with = built_with

    def _refresh(self, request: ModelRequest) -> ModelRequest:
        current = load_prompt(self.key).text
        if current == self.built_with or request.system_message is None:
            return request
        message = request.system_message
        if isinstance(message.content, str):
            if not message.content.startswith(self.built_with):
                return request
            new_content: Any = current + message.content[len(self.built_with):]
        else:
            text = _system_text(message)
            if not text.startswith(self.built_with):
                return request
            first = {**message.content[0], "text": current + text[len(self.built_with):]}
            new_content = [first, *message.content[1:]]
        return request.override(system_message=SystemMessage(content=new_content))

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        return handler(self._refresh(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        return await handler(self._refresh(request))
