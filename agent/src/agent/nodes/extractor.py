from typing import List
from pydantic import BaseModel, Field
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from agent.state import AgentState
from agent.config import settings

# TODO: Support openai as well as ollama
llm = ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_URL, temperature=0)


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

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "You are a query enrichment assistant for a text-to-SQL system.\n\n"
            "Your job is to read the user's natural-language query and add context that "
            "makes ambiguous or implicit terms clearer for downstream processing.\n\n"
            "Add enrichment entries for things like:\n"
            "  • Abbreviations or acronyms that have a specific meaning "
            "(e.g. 'MDA' → 'Magen David Adom')\n"
            "  • Relative time expressions that can be resolved to absolute dates "
            "(e.g. 'last quarter' → 'Q1 2025, Jan 1 – Mar 31 2025')\n"
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

    structured_llm = llm.with_structured_output(ExtractorOutput, method="json_schema")
    chain = prompt | structured_llm

    try:
        data = chain.invoke({"user_query": user_query})
    except Exception as e:
        print(f"Structured output parsing failed in extractor: {e}")
        data = ExtractorOutput(enrichments=[])

    return {"query_enrichments": [item.model_dump() for item in data.enrichments]}
