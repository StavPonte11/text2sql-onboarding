"""
Stage 1 checks around the location-analyst's own extraction.

Which spans are places is the model's judgement, evaluated on a real LLM in
test_extraction_live.py. These tests cover the prompt it reasons from and the validation of
its output (LocationPlan.locations / .excluded).
"""

import pytest

from deep_agent.geo.extract import normalize_punctuation, to_snake_case, validate_locations
from deep_agent.geo.models import ExcludedMention, LocationEntity, LocationPlan, LocationUsage
from deep_agent.geo.plan import finalize_plan
from deep_agent.prompt_registry import local_prompt
from tests.deep_agent.geo.extraction_cases import REGRESSION


def loc(mention, canonical, english, entity_type="city", **kw) -> LocationEntity:
    return LocationEntity(original_mention=mention, canonical_hebrew=canonical, english_name=english,
                          entity_type=entity_type, **kw)


def test_prompt_carries_the_guidelines_and_all_examples():
    prompt = local_prompt("location-analyst")
    for topic in ("What counts", "Grammatical prefixes", "Abbreviations", "directional modifiers", "Spelling",
                  "snake_case", "not a list to match against", "Apply the same judgement"):
        assert topic in prompt
    for query, expected in REGRESSION.values():
        assert query in prompt
        assert all(f"`{english}`" in prompt for _, english in expected)
    # Extraction happens in the subagent's own reasoning: no extraction tool to call.
    assert "extract_locations" not in prompt


def test_valid_locations_pass_with_exact_mentions():
    query = REGRESSION["E arabic-derived names and a region"][0]
    valid, excluded = validate_locations(query, [
        loc("בבינת ג'ביל", "בינת ג'בייל", "bint_jbeil", "town", normalization_notes=["stripped ב"]),
        loc("בדרום לבנון", "דרום לבנון", "South Lebanon", "region"),
    ], [ExcludedMention(mention="בליילה", reason="time of day")])
    assert [(x.canonical_hebrew, x.english_name) for x in valid] == [
        ("בינת ג'בייל", "bint_jbeil"), ("דרום לבנון", "south_lebanon")]
    assert "snake_case" in valid[1].normalization_notes[-1] and valid[1].mention_context
    assert [e.mention for e in excluded] == ["בליילה"]


def test_hallucinated_mentions_are_dropped_not_trusted():
    valid, excluded = validate_locations("כמה חיילים נפצעו ברפיח", [loc("בעזה", "עזה", "gaza"), loc("ברפיח", "רפיח", "rafah")], [])
    assert [x.english_name for x in valid] == ["rafah"]
    assert excluded[0].mention == "בעזה" and "not found in the question" in excluded[0].reason


def test_mentions_match_across_punctuation_and_whitespace_variants():
    query = "הגענו למזרח ת״א ואז  המשכנו לאיו״ש"
    valid, _ = validate_locations(query, [loc('ת"א', "תל אביב", "tel_aviv"),
                                          loc("לאיו״ש", "יהודה ושומרון", "judea_and_samaria", "region")], [])
    assert [x.original_mention for x in valid] == ["ת״א", "לאיו״ש"]  # reported as written in the question


def test_types_names_and_duplicates_are_checked():
    query = "ג'בל שייח' והחרמון וגם בעיר החדשה"
    valid, _ = validate_locations(query, [
        loc("ג'בל שייח'", "הר חרמון", "Mount Hermon", "mountain"),
        loc("והחרמון", "הר חרמון", "mount_hermon", "mountain"),       # same entity twice
        loc("בעיר החדשה", "העיר החדשה", "העיר", "spaceship"),         # Hebrew english name, unknown type
    ], [])
    assert [x.english_name for x in valid] == ["mount_hermon", None]
    assert valid[1].entity_type is None and valid[1].ambiguity == "no English name given"
    assert any("unknown entity_type" in n for n in valid[1].normalization_notes)


def test_usages_must_refer_to_an_extracted_location():
    query = "כמה אירועים היו בחיפה"
    usage = dict(intent="x", operation="none")
    plan = LocationPlan(
        locations=[loc("בחיפה", "חיפה", "haifa"), loc("בעכו", "עכו", "acre")],
        usages=[LocationUsage(original_mention="בחיפה", canonical_hebrew="חיפה", **usage),
                LocationUsage(original_mention="בעכו", canonical_hebrew="עכו", **usage)])
    finalized, _ = finalize_plan(plan, {}, "", query=query)
    assert [x.canonical_hebrew for x in finalized.locations] == ["חיפה"]
    ok_usage, orphan = finalized.usages
    assert ok_usage.status == "ready"
    assert orphan.status == "unresolved" and "not one of the extracted locations" in orphan.issues[0]


def test_empty_extraction_is_valid():
    finalized, _ = finalize_plan(LocationPlan(excluded=[ExcludedMention(mention="גולן", reason="person")]), {}, "",
                                 query="גולן ושרון קבעו פגישה במסעדת אוסלו")
    assert finalized.locations == [] and finalized.usages == [] and finalized.excluded[0].reason == "person"


@pytest.mark.parametrize("name, expected", [("Maroun al-Ras", "maroun_al_ras"), ("  Tel  Aviv ", "tel_aviv"),
                                            ("חיפה", None), (None, None)])
def test_snake_case(name, expected):
    assert to_snake_case(name) == expected


def test_punctuation_normalisation_is_one_to_one():
    assert normalize_punctuation("ת״א ג׳נין") == "ת\"א ג'נין"
    assert len(normalize_punctuation("ת״א ג׳נין־")) == len("ת״א ג׳נין־")
