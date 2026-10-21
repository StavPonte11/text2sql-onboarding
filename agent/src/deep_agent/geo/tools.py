"""
Tools and middleware of the location-analyst subagent, and geometry placeholder substitution.
"""

import re
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent.utils.sql import resolve_wkt_polygons
from deep_agent.context import DeepAgentContext, geo_registry
from deep_agent.envelope import fail, ok
from deep_agent.geo.models import LocationPlan
from deep_agent.geo.plan import finalize_plan
from deep_agent.geo.resolve import GATEWAY_GEOCODER, GatewayGeocoder, Geocoder, NominatimGeocoder, resolve

Runtime = ToolRuntime[DeepAgentContext]

PLACEHOLDER = re.compile(r"@([a-z0-9_]+_wkt)@")
# Tool results whose text counts as evidence for column names and stored values.
EVIDENCE_TOOLS = frozenset({
    "get_table_columns", "get_column_profile", "get_table_profile", "search_column_values",
    "get_column_distinct_values", "get_sample_rows", "search_metadata", "get_database_tables",
})


class UnknownPlaceholder(ValueError):
    pass


def substitute_geometries(sql: str, registry: dict[str, Any]) -> str:
    """Replace '@<name>_wkt@' placeholders with their registry WKT (quoted or not in the SQL)."""
    tokens = set(PLACEHOLDER.findall(sql or ""))
    if not tokens:
        return sql
    geometries = (registry or {}).get("geometries") or {}
    missing = sorted(t for t in tokens if t not in geometries)
    if missing:
        raise UnknownPlaceholder(f"unknown geometry placeholder(s): {', '.join('@' + t + '@' for t in missing)}")
    return resolve_wkt_polygons(sql, {"coords": {t: geometries[t]["wkt"] for t in tokens}})


class ResolveLocationArgs(BaseModel):
    canonical_hebrew: str = Field(description="The location's canonical_hebrew from your extraction.")
    geometry: Literal["point", "line", "area", "any"] = Field(
        description="What the SQL operation needs: 'point' for a center / radius, 'area' for containment or "
                    "boundary distance, 'line' for rivers / roads, 'any' if unsure. Do not call this at all when a "
                    "categorical column answers the question.")
    entity_type: str | None = Field(default=None, description="The location's entity_type from your extraction.")
    english_name: str | None = Field(default=None, description="The location's english_name from your extraction.")
    provider_id: str | None = Field(
        default=None, description="Pick one candidate from a previous LOCATION_AMBIGUOUS result (no new provider call).")


def _lookup_key(args: ResolveLocationArgs) -> str:
    return "|".join([args.geometry, args.canonical_hebrew.strip(), args.entity_type or "", args.provider_id or ""])


def make_resolve_location_tool(geocoders: list[Geocoder]) -> BaseTool:
    """resolve_location bound to the geocoders available at build time (gateway first, then in-repo)."""
    names = [g.name for g in geocoders]

    async def _resolve(
        canonical_hebrew: str, geometry: str, entity_type: str | None = None, english_name: str | None = None,
        provider_id: str | None = None, runtime: Runtime = None,
    ) -> str | Command:
        args = ResolveLocationArgs(canonical_hebrew=canonical_hebrew, geometry=geometry, entity_type=entity_type,
                                   english_name=english_name, provider_id=provider_id)
        registry = geo_registry(runtime)
        key = _lookup_key(args)
        cached = (registry.get("lookups") or {}).get(key)
        if cached is not None:
            if cached.get("error"):
                category, message = cached["error"]
                return fail(category, message, alternatives=cached.get("alternatives") or None, cached=True)
            feature = (registry.get("features") or {}).get(cached["feature_ref"])
            if feature is not None:
                return ok({**_view(feature, registry, cached.get("limitation")), "cached": True})

        result = await resolve(args.canonical_hebrew, args.geometry, args.entity_type, args.english_name, geocoders,
                               args.provider_id)
        if result.error and result.error[0] == "LOCATION_NOT_FOUND" and args.english_name and not args.provider_id \
                and not result.alternatives:
            retry = await resolve(args.english_name.replace("_", " "), args.geometry, args.entity_type,
                                  args.english_name, geocoders)
            if retry.feature is not None:
                retry.feature.notes.append(f"no match for '{args.canonical_hebrew}'; resolved by the English name")
                result = retry

        update: dict[str, Any]
        if result.feature is None:
            category, message = result.error
            update = {"lookups": {key: {"error": [category, message], "alternatives": result.alternatives}}}
            content = fail(category, message, alternatives=result.alternatives or None)
        else:
            feature = result.feature.model_dump()
            update = {"features": {feature["feature_ref"]: feature},
                      "lookups": {key: {"feature_ref": feature["feature_ref"], "limitation": result.limitation}}}
            if result.geometry:
                update["geometries"] = {result.geometry["token"]: result.geometry}
            merged = {"features": {**(registry.get("features") or {}), **update["features"]},
                      "geometries": {**(registry.get("geometries") or {}), **update.get("geometries", {})}}
            content = ok(_view(feature, merged, result.limitation))
        if runtime is None or not runtime.tool_call_id:
            return content
        return Command(update={
            "geo_registry": update,
            "messages": [ToolMessage(content=content, tool_call_id=runtime.tool_call_id, name="resolve_location")],
        })

    return StructuredTool.from_function(
        coroutine=_resolve,
        name="resolve_location",
        description=(
            "Stage 2: resolve one extracted location to a real-world feature and fetch only the geometry the SQL "
            f"needs (geocoders: {', '.join(names)}). Returns feature_ref, display_name, provider_id, "
            "feature_class, geometry_type (what the provider really has — a point is never reported as an area), "
            "center_lon_lat, bbox (an extent, NOT a boundary), notes and limitation. Geometry stays in the registry; "
            "refer to it by feature_ref in the plan. Errors: LOCATION_NOT_FOUND, LOCATION_AMBIGUOUS (with "
            "alternatives — re-call with provider_id), GEOCODER_UNAVAILABLE, MALFORMED_GEOMETRY, "
            "GEOMETRY_TOO_COMPLEX. A repeated identical call returns the cached result; do not retry failures."
        ),
        args_schema=ResolveLocationArgs,
    )


def _view(feature: dict, registry: dict, limitation: str | None) -> dict:
    geometries = registry.get("geometries") or {}
    return {
        "feature_ref": feature["feature_ref"], "display_name": feature.get("display_name"),
        "provider": feature.get("provider"), "provider_id": feature.get("provider_id"),
        "feature_class": feature.get("feature_class"),
        "geometry_types": sorted(feature.get("geometries") or {}),
        "center_lon_lat": feature.get("center"), "bbox": feature.get("bbox"),
        "geometry_chars": {t: len(geometries[tok]["wkt"]) for t, tok in (feature.get("geometries") or {}).items()
                           if tok in geometries},
        "notes": feature.get("notes") or [], "limitation": limitation,
    }


def build_geocoders(gateway_tools: list[BaseTool]) -> list[Geocoder]:
    gateway = [GatewayGeocoder(t) for t in gateway_tools if t.name == GATEWAY_GEOCODER]
    return [*gateway, NominatimGeocoder()]


def _observed_text(messages: list) -> str:
    return "\n".join(m.text for m in messages if isinstance(m, ToolMessage) and m.name in EVIDENCE_TOOLS)


def _question(messages: list) -> str:
    """The delegation message (the parent passes the user's question verbatim in it)."""
    return next((m.text for m in messages if isinstance(m, HumanMessage)), "")


class LocationPlanMiddleware(AgentMiddleware):
    """Finalises the location-analyst's LocationPlan: evidence checks, SQL rendering, derived geometries."""

    def after_agent(self, state, runtime) -> dict[str, Any] | None:
        plan = state.get("structured_response")
        if not isinstance(plan, LocationPlan):
            return None
        messages = state.get("messages") or []
        finalized, new_geometries = finalize_plan(plan, state.get("geo_registry") or {}, _observed_text(messages),
                                                  query=_question(messages))
        update: dict[str, Any] = {"structured_response": finalized}
        if new_geometries:
            update["geo_registry"] = {"geometries": new_geometries}
        return update

    async def aafter_agent(self, state, runtime) -> dict[str, Any] | None:
        return self.after_agent(state, runtime)
