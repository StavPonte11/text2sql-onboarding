"""
Live evaluation of the location extractor on the spec examples (real LLM; nothing is mocked).

    GEO_LIVE_LLM=1 uv run pytest tests/deep_agent/geo/test_extraction_live.py -v

Uses the configured model (LLM_BASE_URL / LLM_MODEL, or DEEP_AGENT_SUBAGENT_MODEL) and the packaged
location-extractor prompt. Skipped unless GEO_LIVE_LLM=1.
"""

import os

import pytest

from deep_agent.geo.extract import extract_locations, to_snake_case
from tests.deep_agent.geo.extraction_cases import ADDITIONAL, REGRESSION

pytestmark = pytest.mark.skipif(os.getenv("GEO_LIVE_LLM") != "1", reason="set GEO_LIVE_LLM=1 to run against a real LLM")


@pytest.fixture(scope="module")
def model():
    from agent.config import settings
    from deep_agent.agent import _default_llm

    llm = _default_llm()
    if settings.DEEP_AGENT_SUBAGENT_MODEL:
        llm = llm.model_copy(update={"model_name": settings.DEEP_AGENT_SUBAGENT_MODEL})
    return llm


@pytest.mark.asyncio
@pytest.mark.parametrize("query, expected", REGRESSION.values(), ids=REGRESSION.keys())
async def test_spec_examples(model, query, expected):
    result = await extract_locations(query, model)
    assert [(x.canonical_hebrew, x.english_name) for x in result.locations] == expected, result.model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("query, expected", ADDITIONAL.values(), ids=ADDITIONAL.keys())
async def test_additional_cases(model, query, expected):
    result = await extract_locations(query, model)
    assert [x.canonical_hebrew for x in result.locations] == expected, result.model_dump()
    assert all(x.english_name and to_snake_case(x.english_name) == x.english_name for x in result.locations)
