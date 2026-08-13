"""
Agent logic and LangGraph harness for the Deep Agent.
"""

from langchain_core.messages import SystemMessage
from deepagents import create_deep_agent as create_harness_agent
from langgraph.checkpoint.memory import MemorySaver

from deep_agent.tools import (
    get_database_catalog,
    get_table_profile,
    execute_trino_query,
    get_sample_rows,
    get_column_distinct_values,
    search_metadata,
    search_business_terms,
    search_knowledge_pairs,
    search_column_values,
    get_column_profile,
    resolve_location_to_wkt,
    validate_sql_syntax,
)
import datetime
from agent.llm import get_llm


SYSTEM_PROMPT = """You are an elite, highly autonomous Deep SQL Analyst. You are decisive, analytical, and follow exploratory methodology without deviation.

Your goal is to answer complex user questions (written in Hebrew) by thoroughly exploring the Trino database, discovering the correct schema, writing precise SQL, and returning the exact answer.

**Current Context**: 
* Today's date is: {today}. Use this to natively resolve relative time expressions (e.g., 'last month', 'yesterday') when building queries.

You have access to a suite of highly specialized tools. You are free to determine the best path to solve the user's request. 
If you need to perform an extensive catalog search, profile many columns, or run complex exploratory tasks that will bloat your context, you can autonomously use your `task` tool to spawn a subagent to do the heavy lifting for you!

**CRITICAL REQUIREMENT FOR FINAL RESPONSE**:
Even if a subagent executed the query for you, YOUR final response to the user MUST contain the exact successful SQL query enclosed in a markdown block (```sql ... ```) at the very bottom. Do not forget this!
"""

def create_deep_agent():
    """
    Creates and returns a compiled LangGraph ReAct agent equipped with our deep exploration tools.
    """
    llm = get_llm()
    
    tools = [
        get_database_catalog,
        get_table_profile,
        get_column_profile,
        execute_trino_query,
        validate_sql_syntax,
        get_sample_rows,
        get_column_distinct_values,
        search_metadata,
        search_business_terms,
        search_knowledge_pairs,
        search_column_values,
        resolve_location_to_wkt,
    ]
    
    # We use a simple MemorySaver for ephemeral memory per thread
    memory = MemorySaver()
    
    today = datetime.date.today().isoformat()
    
    agent = create_harness_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT.format(today=today),
        checkpointer=memory,
        memory=["/Users/benben/Desktop/code/text2sql-onboarding/agent/AGENTS.md"],
        skills=["/Users/benben/Desktop/code/text2sql-onboarding/agent/skills/trino-expert", "/Users/benben/Desktop/code/text2sql-onboarding/agent/skills/spatial-analyst"],
    )
    
    return agent
