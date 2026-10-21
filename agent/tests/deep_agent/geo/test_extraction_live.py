"""
Live evaluation of the location-analyst's extraction on the spec examples (real LLM; nothing mocked).

    GEO_LIVE_LLM=1 uv run pytest tests/deep_agent/geo/test_extraction_live.py -v

Runs the real location-analyst subagent (its prompt, tools, structured output and validation
middleware) with the configured model (LLM_BASE_URL / LLM_MODEL, or DEEP_AGENT_SUBAGENT_MODEL) and
compares the validated `locations` of its LocationPlan with the expected ones. The question is
sent without candidate tables, so only stage 1 is evaluated. Skipped unless GEO_LIVE_LLM=1.
"""

import os

import pytest
from langchain.agents import create_agent

from deep_agent.geo.extract import to_snake_case
from deep_agent.subagents import build_subagents
from tests.deep_agent.geo.extraction_cases import ADDITIONAL, REGRESSION

pytestmark = pytest.mark.skipif(os.getenv("GEO_LIVE_LLM") != "1", reason="set GEO_LIVE_LLM=1 to run against a real LLM")


@pytest.fixture(scope="module")
def analyst():
    from deep_agent.agent import _default_llm

    spec = next(s for s in build_subagents(_default_llm()) if s["name"] == "location-analyst")
    return create_agent(model=spec.get("model") or _default_llm(), tools=spec["tools"],
                        system_prompt=spec["system_prompt"], middleware=spec["middleware"],
                        response_format=spec["response_format"])


async def locations(analyst, question):
    state = await analyst.ainvoke({"messages": [("user", question)]})
    return state["structured_response"].locations


@pytest.mark.asyncio
@pytest.mark.parametrize("query, expected", REGRESSION.values(), ids=REGRESSION.keys())
async def test_spec_examples(analyst, query, expected):
    found = await locations(analyst, query)
    assert [(x.canonical_hebrew, x.english_name) for x in found] == expected, [x.model_dump() for x in found]


@pytest.mark.asyncio
@pytest.mark.parametrize("query, expected", ADDITIONAL.values(), ids=ADDITIONAL.keys())
async def test_additional_cases(analyst, query, expected):
    found = await locations(analyst, query)
    assert [x.canonical_hebrew for x in found] == expected, [x.model_dump() for x in found]
    assert all(x.english_name and to_snake_case(x.english_name) == x.english_name for x in found)
