from typing import List
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from agent.state import AgentState
from agent.config import settings
from agent.langfuse_client import langfuse_client

# TODO: Support openai as well as ollama
llm = ChatOpenAI(model=settings.LLM_MODEL, base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY, temperature=0)


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


# TODO: add location extractor, open plug for future custom extractors

def extractor_node(state: AgentState):
    """Enrich the user query with additional context to help downstream phases."""
    user_query = state["user_query"]

    langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_EXTRACTOR)
    prompt = ChatPromptTemplate.from_messages(langfuse_prompt.get_langchain_prompt())

    structured_llm = llm.with_structured_output(ExtractorOutput, method="json_schema")
    chain = prompt | structured_llm

    try:
        data = chain.invoke({"user_query": user_query})
    except Exception as e:
        print(f"Structured output parsing failed in extractor: {e}")
        data = ExtractorOutput(enrichments=[])

    return {"query_enrichments": [item.model_dump() for item in data.enrichments]}
