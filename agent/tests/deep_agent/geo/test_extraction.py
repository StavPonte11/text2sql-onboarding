"""
Stage 1 pipeline with a scripted model: the prompt the model gets, and how its output is checked.

What counts as a place is the model's judgement — that is evaluated against the spec examples
on a real LLM in test_extraction_live.py. These tests only cover the code around the model.
"""

import json

import pytest
from langchain_core.messages import AIMessage

from deep_agent.geo.extract import (
    ExtractionError, LocationExtraction, extract_locations, normalize_punctuation, to_snake_case, validate_extraction,
)
from deep_agent.geo.tools import make_extract_locations_tool
from deep_agent.prompt_registry import local_prompt
from tests.deep_agent.conftest import system_text
from tests.deep_agent.geo.extraction_cases import REGRESSION

QUERY_E = REGRESSION["E arabic-derived names and a region"][0]


def reply(**args) -> AIMessage:
    return AIMessage("", tool_calls=[{"name": "LocationExtraction", "id": "e1", "args": args}])


def loc(mention, canonical, english, entity_type="city", **kw):
    return {"original_mention": mention, "canonical_hebrew": canonical, "english_name": english,
            "entity_type": entity_type, **kw}


def test_prompt_carries_the_guidelines_and_all_examples():
    prompt = local_prompt("location-extractor")
    for topic in ("Grammatical prefixes", "Abbreviations", "directional modifiers", "Spelling", "snake_case",
                  "candidates", "not a list to match against"):
        assert topic in prompt
    for query, _ in REGRESSION.values():
        assert query in prompt


@pytest.mark.asyncio
async def test_model_output_is_returned_after_validation(scripted_model):
    model = scripted_model(reply(
        candidates=[{"mention": "בליילה", "is_geographic": False, "reason": "time of day"},
                    {"mention": "בבינת ג'ביל", "is_geographic": True, "reason": "town where forces operated"}],
        locations=[loc("בבינת ג'ביל", "בינת ג'בייל", "bint_jbeil", "town",
                       normalization_notes=["stripped ב", "spelling ג'ביל → ג'בייל"]),
                   loc("בדרום לבנון", "דרום לבנון", "South Lebanon", "region")]))
    result = await extract_locations(QUERY_E, model)

    # The model saw the extractor prompt and the query verbatim.
    assert "You are location-extractor" in system_text(model.seen[0])
    assert model.seen[0][-1].content == QUERY_E
    assert model.bound_tool_names[0] == ["LocationExtraction"]

    assert [(x.canonical_hebrew, x.english_name) for x in result.locations] == [
        ("בינת ג'בייל", "bint_jbeil"), ("דרום לבנון", "south_lebanon")]
    south = result.locations[1]
    assert "snake_case" in south.normalization_notes[-1] and south.mention_context
    assert [(e.mention, e.reason) for e in result.excluded] == [("בליילה", "time of day")]


def test_hallucinated_mentions_are_dropped_not_trusted():
    raw = LocationExtraction(locations=[loc("בעזה", "עזה", "gaza"), loc("ברפיח", "רפיח", "rafah")])
    result = validate_extraction("כמה חיילים נפצעו ברפיח", raw)
    assert [x.english_name for x in result.locations] == ["rafah"]
    assert result.excluded[0].mention == "בעזה" and "not found in the query" in result.excluded[0].reason


def test_mentions_match_across_punctuation_variants():
    query = "הגענו למזרח ת״א ואז המשכנו לאיו״ש"
    raw = LocationExtraction(locations=[loc('ת"א', "תל אביב", "tel_aviv"),
                                        loc("לאיו''ש", "יהודה ושומרון", "judea_and_samaria", "region")])
    result = validate_extraction(query, raw)
    assert [x.original_mention for x in result.locations] == ["ת״א"]  # exact query substring is reported
    raw = LocationExtraction(locations=[loc("לאיו״ש", "יהודה ושומרון", "judea_and_samaria", "region")])
    assert validate_extraction(query, raw).locations[0].original_mention == "לאיו״ש"


def test_types_names_and_duplicates_are_checked():
    query = "ג'בל שייח' והחרמון וגם בעיר החדשה"
    raw = LocationExtraction(locations=[
        loc("ג'בל שייח'", "הר חרמון", "Mount Hermon", "mountain"),
        loc("והחרמון", "הר חרמון", "mount_hermon", "mountain"),          # same entity twice
        loc("בעיר החדשה", "העיר החדשה", "העיר", "spaceship"),            # Hebrew english name, unknown type
    ])
    result = validate_extraction(query, raw)
    assert [x.english_name for x in result.locations] == ["mount_hermon", None]
    odd = result.locations[1]
    assert odd.entity_type is None and odd.ambiguity == "no English name given"
    assert any("unknown entity_type" in n for n in odd.normalization_notes)


@pytest.mark.asyncio
async def test_empty_query_makes_no_model_call(scripted_model):
    model = scripted_model()
    assert (await extract_locations("  ", model)).locations == []
    assert model.seen == []


@pytest.mark.asyncio
async def test_no_locations_is_valid(scripted_model):
    model = scripted_model(reply(candidates=[{"mention": "גולן", "is_geographic": False, "reason": "person"}],
                                 locations=[]))
    result = await extract_locations("גולן ושרון קבעו פגישה במסעדת אוסלו", model)
    assert result.locations == [] and result.excluded[0].reason == "person"


@pytest.mark.asyncio
async def test_malformed_output_gets_one_corrective_retry(scripted_model):
    model = scripted_model(reply(locations=[{"canonical_hebrew": "רפיח"}]),       # missing original_mention
                           reply(locations=[loc("ברפיח", "רפיח", "rafah")]))
    result = await extract_locations("נפצעו ברפיח", model)
    assert [x.english_name for x in result.locations] == ["rafah"]
    retry_messages = model.seen[1]
    assert retry_messages[-1].type == "tool" and "Invalid output" in retry_messages[-1].text


@pytest.mark.asyncio
async def test_persistent_malformed_output_fails_clearly(scripted_model):
    model = scripted_model(AIMessage("I think it's Rafah"), AIMessage("still no json"))
    with pytest.raises(ExtractionError):
        await extract_locations("נפצעו ברפיח", model)


@pytest.mark.asyncio
async def test_json_text_reply_is_accepted(scripted_model):
    model = scripted_model(AIMessage('```json\n{"locations": [{"original_mention": "ברפיח", '
                                     '"canonical_hebrew": "רפיח", "english_name": "rafah"}]}\n```'))
    result = await extract_locations("נפצעו ברפיח", model)
    assert result.locations[0].english_name == "rafah"


@pytest.mark.asyncio
async def test_tool_envelope_note_and_failures(scripted_model):
    model = scripted_model(reply(locations=[loc("ברפיח", "רפיח", "rafah")]), AIMessage("x"), AIMessage("y"))
    tool = make_extract_locations_tool(model)
    out = json.loads(await tool.ainvoke({"query": "נפצעו ברפיח", "note": "check רפיח"}))
    assert out["ok"] and out["data"]["locations"][0]["english_name"] == "rafah"
    assert "[Reviewer note: check רפיח]" in model.seen[0][-1].content
    failed = json.loads(await tool.ainvoke({"query": "נפצעו ברפיח"}))
    assert failed["ok"] is False and failed["error"]["category"] == "EXTRACTION_FAILED"


def test_snake_case_and_punctuation_helpers():
    assert to_snake_case("Maroun al-Ras") == "maroun_al_ras"
    assert to_snake_case("  Tel  Aviv ") == "tel_aviv"
    assert to_snake_case("חיפה") is None
    assert normalize_punctuation("ת״א ג׳נין") == "ת\"א ג'נין"
