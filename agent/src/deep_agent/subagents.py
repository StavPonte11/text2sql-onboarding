"""
Specialised subagents the Deep Agent delegates to with the `task` tool.

Each has a narrow tool allow-list (fewer schemas per call, no off-task tools), its own
Langfuse-managed prompt, the request-context middleware (connection, date), a tool-call cap,
and optionally a smaller model (DEEP_AGENT_SUBAGENT_MODEL). They return compact briefs, so
the main agent's context holds conclusions instead of raw catalog / profile dumps.
"""

from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from agent.config import settings
from deep_agent import tools as t
from deep_agent.context import DeepAgentContextMiddleware
from deep_agent.geo import tools as geo
from deep_agent.geo.models import LocationPlan
from deep_agent.geo.resolve import GATEWAY_GEOCODER
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
        t.get_sample_rows,
    ],
    "result-verifier": [t.execute_trino_query, t.get_sample_rows],
    # + resolve_location, bound per build to the available geocoders (see build_subagents)
    "location-analyst": [
        geo.extract_locations, t.search_metadata, t.get_table_columns, t.get_column_profile,
        t.search_column_values, t.get_column_distinct_values, t.get_sample_rows,
    ],
}

SUBAGENT_DESCRIPTIONS = {
    "schema-scout": (
        "Finds the tables, columns, join keys and date-column types relevant to a question across a large catalog. "
        "Give it the user's question (and candidate tables if known). Returns a compact brief, never raw dumps."
    ),
    "value-resolver": (
        "Resolves several filter values at once to exact stored values (by semantic type) and relative dates to "
        "concrete bounds. Give it the question and the list of (table, column, term). Not for places."
    ),
    "result-verifier": (
        "Independently checks a finished JOIN/aggregation query for grain, fan-out, completeness and magnitude. "
        "Give it the question, the SQL and a summary of the result. Returns PASS/FAIL with evidence."
    ),
    "location-analyst": (
        "Handles every place / geography in a question: extracts and normalises Hebrew place names, decides per "
        "place whether a verified categorical column answers it or geometry is needed, resolves geometry only then, "
        "and returns a JSON LocationPlan with ready predicate_sql fragments (geometry as '@..._wkt@' placeholders "
        "that execute_trino_query substitutes). Give it the user's question verbatim and the candidate table(s)."
    ),
}

# Subagents that finish with a structured response, and the middleware that finalises it.
SUBAGENT_RESPONSE = {"location-analyst": (LocationPlan, geo.LocationPlanMiddleware)}


def _subagent_model(main_model: BaseChatModel) -> BaseChatModel | None:
    if not settings.DEEP_AGENT_SUBAGENT_MODEL:
        return None  # inherit the main agent's model
    return main_model.model_copy(update={"model_name": settings.DEEP_AGENT_SUBAGENT_MODEL})


def build_subagents(main_model: BaseChatModel, extra_tools: dict[str, list] | None = None) -> list[dict]:
    """Subagent specs; `extra_tools` maps subagent name -> additional tools (e.g. Utils-Server)."""
    model = _subagent_model(main_model)
    extra_tools = extra_tools or {}
    subagents = []
    for name, tools in SUBAGENT_TOOLS.items():
        prompt = load_prompt(name).text
        extras = extra_tools.get(name, [])
        if name == "location-analyst":
            # The gateway geocoder is consumed inside resolve_location (geometry stays out of the context).
            geocoder_tools = [x for x in extras if x.name == GATEWAY_GEOCODER]
            extras = [x for x in extras if x.name != GATEWAY_GEOCODER]
            tools = [*tools, geo.make_resolve_location_tool(geo.build_geocoders(geocoder_tools))]
        spec = {
            "name": name,
            "description": SUBAGENT_DESCRIPTIONS[name],
            "system_prompt": prompt,
            "tools": [*tools, *extras],
            "skills": [SKILLS_ROUTE],
            "middleware": [
                DeepAgentContextMiddleware(),
                PromptRefreshMiddleware(name, prompt),
                ToolCallLimitMiddleware(run_limit=settings.DEEP_AGENT_SUBAGENT_MAX_TOOL_CALLS),
                BuiltinToolFilterMiddleware(),
            ],
        }
        if name in SUBAGENT_RESPONSE:
            schema, finalizer = SUBAGENT_RESPONSE[name]
            spec["response_format"] = ToolStrategy(schema)
            spec["middleware"].append(finalizer())
        if model is not None:
            spec["model"] = model
        subagents.append(spec)
    return subagents
