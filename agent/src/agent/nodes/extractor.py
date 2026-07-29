"""
extractor.py - LangGraph extractor node.

Runs all extractors in parallel to enrich the user query with additional
context before downstream reasoning phases.

Extractor classes:
  - TimeExtractor    : anchors current datetime
  - LLMExtractor     : uses an LLM to resolve query terms (abbreviations, etc.)
  - LocationExtractor: extracts Hebrew place names, resolves WKT polygons
  - HTTPExtractor    : calls an external HTTP enrichment service
"""

import concurrent.futures
import datetime
import requests
from typing import List

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables.config import RunnableConfig

from agent.config import settings
from agent.langfuse_client import langfuse_client
from agent.llm import get_llm
from agent.services.extractor_base import BaseExtractor, ContextEntry
from agent.services.location_extractor import LocationExtractorAgent
from agent.state import AgentState
from agent.utils.redis_publisher import publish_node_event_sync


# ── Structured output schema for LLMExtractor ────────────────────────────────


class ExtractorOutput(BaseModel):
    enrichments: List[ContextEntry] = Field(
        default_factory=list,
        description=(
            "Context entries that enrich the user query with additional information. "
            "Only include entries where extra context genuinely helps downstream processing. "
            "If the query is fully clear and self-contained, return an empty list."
        ),
    )


# ── Extractor implementations ─────────────────────────────────────────────────


class TimeExtractor(BaseExtractor):
    """Always injects the current datetime so downstream nodes have a time anchor."""

    def extract(self, query: str) -> List[ContextEntry]:
        now = datetime.datetime.now()
        # TODO: add relative time resolution (e.g. "last quarter" → date range)
        return [
            ContextEntry(
                term="current_time",
                context=f"The current time is {now.isoformat()}",
            )
        ]


class LLMExtractor(BaseExtractor):
    """Uses an LLM to resolve abbreviations, ambiguous terms, and context clues."""

    def __init__(self, runtime_flags: dict | None = None):
        super().__init__(runtime_flags)
        self.llm = get_llm("extractor", runtime_flags=runtime_flags)
        langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_EXTRACTOR)
        prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())
        self.chain = prompt | self.llm.with_structured_output(
            ExtractorOutput, method="json_schema"
        )

    def extract(self, query: str) -> List[ContextEntry]:
        try:
            data = self.chain.invoke({"user_query": query})
            return data.enrichments
        except Exception as e:
            print(f"LLMExtractor failed: {e}")
            return []


class LocationExtractor(BaseExtractor):
    """Extracts Hebrew place names from the query, resolves each to a WKT polygon
    via the Nominatim geocoding API, and surfaces location state for the graph."""

    def __init__(self, runtime_flags: dict | None = None):
        super().__init__(runtime_flags)
        llm_client = get_llm("location_extractor", runtime_flags=runtime_flags)
        self._agent = LocationExtractorAgent(llm_client=llm_client, max_wkt_length=settings.LOCATION_MAX_WKT_LENGTH)

    def extract(self, query: str) -> List[ContextEntry]:
        return self._agent.extract(query)

    def state_update(self) -> dict:
        result = self._agent._last_result
        if result is None:
            return {}
        return {
            "locations_dict": {
                "names": result.raw_locations_dict,
                "coords": result.locations_coords_dict,
            },
            "location_wkt_instruction": result.location_wkt_instruction,
        }


class HTTPExtractor(BaseExtractor):
    """Calls an external HTTP enrichment service registered via active_extractors."""

    def __init__(self, url: str, name: str, runtime_flags: dict | None = None):
        super().__init__(runtime_flags)
        self.url = url
        self.name = name

    def extract(self, query: str) -> List[ContextEntry]:
        try:
            payload = {"query": query, "runtime_flags": self.runtime_flags}
            res = requests.post(self.url, json=payload, timeout=50)
            res.raise_for_status()
            data = res.json()
            return [ContextEntry(**item) for item in data.get("enrichments", [])]
        except Exception as e:
            print(f"HTTPExtractor ({self.name} at {self.url}) failed: {e}")
            return []


# ── Node ──────────────────────────────────────────────────────────────────────


def extractor_node(state: AgentState, config: RunnableConfig | None = None) -> dict:
    """Enrich the user query with additional context to help downstream phases.

    Runs all extractors concurrently via a thread pool:
      - TimeExtractor and LLMExtractor run for every request.
      - LocationExtractor resolves Hebrew place names to WKT polygons.
      - HTTPExtractor instances are added for each entry in active_extractors.

    Each extractor may also contribute extra AgentState fields via state_update().
    """
    user_query = state["user_query"]
    active_extractors = state.get("active_extractors") or []
    runtime_flags = state.get("runtime_flags") or {}

    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""
    publish_node_event_sync(thread_id, "extractor")

    # Build each extractor independently so a single construction failure
    # (e.g. Langfuse unavailable for LocationExtractor) does not abort the
    # whole list and lose TimeExtractor / LLMExtractor results.
    _extractor_specs: list = [
        (TimeExtractor,    {"runtime_flags": runtime_flags}),
        (LLMExtractor,     {"runtime_flags": runtime_flags}),
        (LocationExtractor, {"runtime_flags": runtime_flags}),
        *[
            (HTTPExtractor, {"url": e["url"], "name": e["name"], "runtime_flags": runtime_flags})
            for e in active_extractors
        ],
    ]

    extractors: List[BaseExtractor] = []
    for factory, kwargs in _extractor_specs:
        try:
            extractors.append(factory(**kwargs))
        except Exception as exc:
            print(f"Extractor {factory.__name__} failed to initialise, skipping: {exc}")

    all_enrichments: List[dict] = []

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {executor.submit(ext.extract, user_query): ext for ext in extractors}
        for future in concurrent.futures.as_completed(futures):
            try:
                all_enrichments.extend([e.model_dump() for e in future.result()])
            except Exception as e:
                print(f"Extractor {type(futures[future]).__name__} failed: {e}")

    # Merge extra state contributions from each extractor (e.g. locations_dict)
    extra_state: dict = {}
    for ext in extractors:
        extra_state.update(ext.state_update())

    return {
        "query_enrichments": all_enrichments,
        **extra_state,
        "execution_path": ["extractor"],
    }
