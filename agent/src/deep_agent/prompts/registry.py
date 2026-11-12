"""
Prompt management for the Deep Agent.

Prompts are Langfuse text prompts in the `text2sql/deep_agent/` folder (`system`, `validator`), next to the
structured agent's `text2sql/` prompts,, fetched by label (DEEP_AGENT_PROMPT_LABEL, default
"production") with the SDK's TTL cache. The packaged `prompts/<key>.md` files are the
fallback when Langfuse is unreachable or the prompt doesn't exist, and the only source when
DEEP_AGENT_LANGFUSE_PROMPTS is off (tests, offline dev).

The service rebuilds the agent when a prompt text changes (deep_agent.service.get_deep_agent),
so moving a label in Langfuse changes behaviour from the next request, without a redeploy; a run
keeps the prompt it started with. Push the packaged prompts with
`uv run python scripts/sync_deep_agent_prompts.py --label staging`.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.runnables import RunnableConfig, RunnableLambda
from langchain_core.runnables.config import ensure_config

from agent.config import settings

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent  # the .md files sit next to this module
PROMPT_KEYS = ("system", "validator")
LANGFUSE_FOLDER = "text2sql/deep_agent"  # Langfuse shows "/" as folders


def langfuse_name(key: str) -> str:
    return f"{LANGFUSE_FOLDER}/{key}"


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
# The SDK caches successful fetches but not misses, and load_prompt runs on every request.
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


class PromptLinkMiddleware(AgentMiddleware):
    """Link this agent's model calls to the Langfuse prompt version it was built with.

    Langfuse's LangChain handler takes a prompt from the `langfuse_prompt` metadata of a *chain* run and gives it to
    the nearest generation below. So each model call runs inside a small chain step carrying this agent's prompt —
    the reviewer's calls get the validator prompt even though they run inside the main agent's run.
    """

    def __init__(self, prompt: LoadedPrompt) -> None:
        super().__init__()
        self.prompt = prompt

    def _config(self) -> RunnableConfig:
        metadata = {**(ensure_config().get("metadata") or {}), "langfuse_prompt": self.prompt.client,
                    "prompt_source": self.prompt.source, "prompt_version": self.prompt.version}
        return {"metadata": metadata, "run_name": f"prompt:{self.prompt.key}"}

    def wrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]) -> ModelResponse:
        if self.prompt.client is None:  # packaged prompt: nothing in Langfuse to link to
            return handler(request)
        return RunnableLambda(lambda _: handler(request)).invoke(None, self._config())

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        if self.prompt.client is None:
            return await handler(request)

        async def call(_):
            return await handler(request)

        return await RunnableLambda(call).ainvoke(None, self._config())
