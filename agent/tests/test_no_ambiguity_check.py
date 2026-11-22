"""The structured agent runs no ambiguity check (and so never stops early on one) until that work is designed."""
from agent.graph import route_query_builder


def test_query_builder_never_routes_to_the_ambiguity_check_even_with_the_flag_on():
    for flag in (True, "true", False, None):
        assert route_query_builder({"runtime_flags": {"SCHEMA_AMBIGUITY_DETECT": flag}}) == "hitl_query_approval"
    assert route_query_builder({}) == "hitl_query_approval"
