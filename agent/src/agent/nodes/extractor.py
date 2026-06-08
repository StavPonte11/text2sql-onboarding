import abc
import datetime
import requests
from typing import List
from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from agent.state import AgentState
from agent.config import settings


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
        )
    )

class BaseExtractor(abc.ABC):
    @abc.abstractmethod
    def extract(self, query: str) -> List[ContextEntry]:
        pass

class LLMExtractor(BaseExtractor):
    def __init__(self):
        # TODO: Support openai as well as ollama
        self.llm = ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_URL, temperature=0)
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", (
                "You are a query enrichment assistant for a text-to-SQL system.\n\n"
                "Your job is to read the user's natural-language query and add context that "
                "makes ambiguous or implicit terms clearer for downstream processing.\n\n"
                "Add enrichment entries for things like:\n"
                "  • Abbreviations or acronyms that have a specific meaning "
                "(e.g. 'MDA' → 'Magen David Adom')\n"
                "  • Ambiguous proper nouns where context helps "
                "(e.g. 'Jordan' used as a country vs. a person's name)\n"
                "  • Domain-specific shorthand the downstream system may not know\n\n"
                "Do NOT try to identify which database table or column to use — that is handled by a "
                "separate schema exploration phase.\n"
                "Do NOT add enrichments for terms that are already fully clear from the query.\n"
                "If the query needs no enrichment, return an empty enrichments list."
            )),
            ("human", "{user_query}")
        ])
        self.chain = self.prompt | self.llm.with_structured_output(ExtractorOutput, method="json_schema")

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
        enrichments.append(ContextEntry(
            term="current_time",
            context=f"The current time is {now.isoformat()}"
        ))
        
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

    extractors: List[BaseExtractor] = [
        TimeExtractor(),
        LLMExtractor()
    ]
    
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
