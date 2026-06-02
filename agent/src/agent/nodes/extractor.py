from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from agent.state import AgentState

from agent.config import settings

# TODO: Support openai as well as ollama
llm = ChatOllama(model=settings.OLLAMA_MODEL, base_url=settings.OLLAMA_URL, temperature=0)

# TODO: add location extractor, open plug for future custom extractors

def extractor_node(state: AgentState):
    """Extract entities and query intent."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", "Extract entities, tables, and intent from the user query. Output in structured JSON."),
        ("human", "{user_query}")
    ])
    chain = prompt | llm
    response = chain.invoke({"user_query": state["user_query"]})
    return {"extracted_entities": {"raw_response": response.content}}
