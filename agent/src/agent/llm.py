import logging
from langchain_openai import ChatOpenAI
from agent.config import settings
from typing import Optional


# We could dynamically configure settings based on the node name.
# For now, it delegates to agent.config settings.
def get_llm(node: str = "default", temperature: Optional[float] = 0.0) -> ChatOpenAI:
    """
    Create a ChatOpenAI client configured from application settings.
    
    Parameters:
    	node (str): Name used for debug logging.
    	temperature (Optional[float]): Sampling temperature to pass to the model.
    
    Returns:
    	ChatOpenAI: A configured chat model client.
    """
    logging.debug(f"Instantiating LLM for node: {node}")
    return ChatOpenAI(
        model=settings.LLM_MODEL,
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        temperature=temperature,
    )
