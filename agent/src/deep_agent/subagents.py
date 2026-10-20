"""
Specialised subagents the Deep Agent delegates to with the `task` tool.

Each has a narrow tool allow-list (fewer schemas per call, no off-task tools), its own
Langfuse-managed prompt, the request-context middleware (connection, date), a tool-call cap,
and optionally a smaller model (DEEP_AGENT_SUBAGENT_MODEL). They return compact briefs, so
the main agent's context holds conclusions instead of raw catalog / profile dumps.
"""

from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain_core.language_models import BaseChatModel

from agent.config import settings
from deep_agent import tools as t
from deep_agent.context import DeepAgentContextMiddleware
from deep_agent.limits import BuiltinToolFilterMiddleware
from deep_agent.prompt_registry import PromptRefreshMiddleware, load_prompt
from deep_agent.resources import SKILLS_ROUTE

SUBAGENT_TOOLS = {
    "schema-scout": [
        t.search_metadata, t.get_database_tables, t.get_table_columns, t.get_table_profile,
        t.get_column_profile, t.get_table_relationships,
    ],
    "value-resolver": [
        t.search_column_values, t.get_column_distinct_values, t.get_column_profile, t.search_business_terms,
        t.resolve_location_to_wkt, t.get_sample_rows,
    ],
    "result-verifier": [t.execute_trino_query, t.get_sample_rows],
}

SUBAGENT_DESCRIPTIONS = {
    "schema-scout": (
        "Finds the tables, columns, join keys and date-column types relevant to a question across a large catalog. "
        "Give it the user's question (and candidate tables if known). Returns a compact brief, never raw dumps."
    ),
    "value-resolver": (
        "Resolves several filter values at once to exact stored values (by semantic type), place names to WKT "
        "literals and relative dates to concrete bounds. Give it the question and the list of (table, column, term)."
    ),
    "result-verifier": (
        "Independently checks a finished JOIN/aggregation query for grain, fan-out, completeness and magnitude. "
        "Give it the question, the SQL and a summary of the result. Returns PASS/FAIL with evidence."
    ),
}


def _subagent_model(main_model: BaseChatModel) -> BaseChatModel | None:
    if not settings.DEEP_AGENT_SUBAGENT_MODEL:
        return None  # inherit the main agent's model
    return main_model.model_copy(update={"model_name": settings.DEEP_AGENT_SUBAGENT_MODEL})


def build_subagents(main_model: BaseChatModel) -> list[dict]:
    model = _subagent_model(main_model)
    subagents = []
    for name, tools in SUBAGENT_TOOLS.items():
        prompt = load_prompt(name).text
        spec = {
            "name": name,
            "description": SUBAGENT_DESCRIPTIONS[name],
            "system_prompt": prompt,
            "tools": tools,
            "skills": [SKILLS_ROUTE],
            "middleware": [
                DeepAgentContextMiddleware(),
                PromptRefreshMiddleware(name, prompt),
                ToolCallLimitMiddleware(run_limit=settings.DEEP_AGENT_SUBAGENT_MAX_TOOL_CALLS),
                BuiltinToolFilterMiddleware(),
            ],
        }
        if model is not None:
            spec["model"] = model
        subagents.append(spec)
    return subagents
