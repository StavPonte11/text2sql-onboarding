import abc
import datetime
import requests
from typing import List
from pydantic import BaseModel, Field

from langchain_core.prompts import ChatPromptTemplate
from agent.state import AgentState
from agent.config import settings
from agent.langfuse_client import langfuse_client
from agent.llm import get_llm


# A single piece of enriched context added to the query
class ContextEntry(BaseModel):
    term: str = Field(
        description="The exact term or phrase from the user query that is being enriched."
    )
    context: str = Field(
        description=(
            "Additional context, resolved meaning, or clarification for the term. "
            "Examples: resolving an abbreviation ('MDA' → 'Magen David Adom'), "
            "a relative date ('last quarter' → 'Q1 2025, Jan 1 - Mar 31 2025'), "
            "or a geographic clarification ('Jordan' → 'country in the Middle East, not a person name')."
        )
    )


# The enriched output produced by the extractor phase
class ExtractorOutput(BaseModel):
    enrichments: List[ContextEntry] = Field(
        default_factory=list,
        description=(
            "Context entries that enrich the user query with additional information. "
            "Only include entries where extra context genuinely helps downstream processing. "
            "If the query is fully clear and self-contained, return an empty list."
        ),
    )


class BaseExtractor(abc.ABC):
    @abc.abstractmethod
    def extract(self, query: str) -> List[ContextEntry]:
        pass


class LLMExtractor(BaseExtractor):
    def __init__(self):
        self.llm = get_llm("extractor")

        langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_EXTRACTOR)
        self.prompt = ChatPromptTemplate.from_messages(
            langfuse_prompt.get_langchain_prompt()
        )

        self.chain = self.prompt | self.llm.with_structured_output(
            ExtractorOutput, method="json_schema"
        )

    def extract(self, query: str) -> List[ContextEntry]:
        try:
            data = self.chain.invoke({"user_query": query})
            return data.enrichments
        except Exception as e:
            print(f"Structured output parsing failed in LLMExtractor: {e}")
            return []


class TimeExtractor(BaseExtractor):
    def extract(self, query: str) -> List[ContextEntry]:
        enrichments = []
        now = datetime.datetime.now()
        # Always anchor current time
        enrichments.append(
            ContextEntry(
                term="current_time", context=f"The current time is {now.isoformat()}"
            )
        )

        # TODO: add relative time values handling

        return enrichments


class HTTPExtractor(BaseExtractor):
    def __init__(self, url: str, name: str):
        self.url = url
        self.name = name

    def extract(self, query: str) -> List[ContextEntry]:
        try:
            res = requests.post(self.url, json={"query": query}, timeout=50)
            res.raise_for_status()
            data = res.json()
            return [ContextEntry(**item) for item in data.get("enrichments", [])]
        except Exception as e:
            print(f"HTTPExtractor ({self.name} at {self.url}) failed: {e}")
            return []


def extractor_node(state: AgentState):
    """Enrich the user query with additional context to help downstream phases."""
    user_query = state["user_query"]
    active_extractors = state.get("active_extractors") or []

    import concurrent.futures

    extractors: List[BaseExtractor] = [TimeExtractor(), LLMExtractor()]

    for ext_info in active_extractors:
        extractors.append(HTTPExtractor(ext_info["url"], ext_info["name"]))

    all_enrichments = []

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {executor.submit(ext.extract, user_query): ext for ext in extractors}
        for future in concurrent.futures.as_completed(futures):
            try:
                entries = future.result()
                all_enrichments.extend([e.model_dump() for e in entries])
            except Exception as e:
                ext = futures[future]
                print(f"Extractor {type(ext).__name__} failed: {e}")

    return {"query_enrichments": all_enrichments}
