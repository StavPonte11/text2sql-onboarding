import json

import pytest

from deep_agent.geo.extract import MentionHint, extract_locations, normalize_punctuation, to_snake_case
from deep_agent.geo.gazetteer import ENTRIES
from deep_agent.geo.tools import extract_locations as extract_tool


def pairs(query, hints=None):
    return [(loc.canonical_hebrew, loc.english_name) for loc in extract_locations(query, hints).locations]


def excluded(query):
    return {e.mention: e.reason for e in extract_locations(query).excluded}


# --- Section 2.7 regression examples ---------------------------------------------------------------

REGRESSION = {
    "A two cities and a date": (
        "כמה חיילים נפצעו בחאן יונס וברפיח במאי 2025",
        [("חאן יונס", "khan_yunis"), ("רפיח", "rafah")],
    ),
    "B generic direction and acronyms": (
        "הגענו למזרח ת״א ואז המשכנו לאיו״ש",
        [("תל אביב", "tel_aviv"), ("יהודה ושומרון", "judea_and_samaria")],
    ),
    "C sports teams vs geography": (
        "משחק של מכבי חיפה נגד הפועל ירושלים נדחה בגלל מזג האוויר בחיפה",
        [("חיפה", "haifa")],
    ),
    "D personal names and a restaurant": (
        "גולן ושרון קבעו פגישה במסעדת אוסלו",
        [],
    ),
    "E arabic-derived names and a region": (
        "כוחות צה״ל פעלו בליילה בבינת ג'ביל, במארון אר-ראס ובאל-ח'יאם בדרום לבנון",
        [("בינת ג'בייל", "bint_jbeil"), ("מארון א-ראס", "maroun_al_ras"), ("אל-ח'יאם", "al_khiam"),
         ("דרום לבנון", "south_lebanon")],
    ),
    "F settlements and natural features": (
        "הם נסעו מכפר קאסם לכפר כנא דרך ואדי עארה ועצרו ליד נחל קישון",
        [("כפר קאסם", "kafr_qasim"), ("כפר כנא", "kafr_kanna"), ("ואדי עארה", "wadi_ara"),
         ("נחל קישון", "kishon_river")],
    ),
    "G refugee camps, a city and a military camp": (
        "פעילות במחנה הפליטים ג'נין ובלטא ליד שכם, וגם במחנה נחשונים",
        [("מחנה הפליטים ג'נין", "jenin_refugee_camp"), ("מחנה בלאטה", "balata_refugee_camp"), ("שכם", "nablus"),
         ("מחנה נחשונים", "nahshonim_camp")],
    ),
    "H river, mountain and alias": (
        "זיהו שיגורים מאזור נהר הליטני לכיוון הר דב וג'בל שייח'",
        [("נהר הליטני", "litani_river"), ("הר דב", "har_dov"), ("הר חרמון", "mount_hermon")],
    ),
    "I operational sector vs named region": (
        "כוחותינו הוקפצו לגזרה הצפונית ולגזרת עוטף עזה",
        [("עוטף עזה", "gaza_envelope")],
    ),
}


@pytest.mark.parametrize("query, expected", REGRESSION.values(), ids=REGRESSION.keys())
def test_regression_examples(query, expected):
    assert pairs(query) == expected


def test_original_mentions_are_exact_substrings_without_prefixes():
    query = REGRESSION["E arabic-derived names and a region"][0]
    result = extract_locations(query)
    assert [loc.original_mention for loc in result.locations] == ["בינת ג'ביל", "מארון אר-ראס", "אל-ח'יאם", "דרום לבנון"]
    assert all(loc.original_mention in query for loc in result.locations)


def test_normalization_notes_record_each_decision():
    b = {loc.english_name: loc.normalization_notes for loc in extract_locations(REGRESSION["B generic direction and acronyms"][0]).locations}
    assert any("abbreviation" in n for n in b["tel_aviv"])
    assert any("directional modifier 'מזרח'" in n for n in b["tel_aviv"])
    assert any("ל (to)" in n for n in b["judea_and_samaria"])
    h = {loc.english_name: loc.normalization_notes for loc in extract_locations(REGRESSION["H river, mountain and alias"][0]).locations}
    assert any("alias" in n and "הר חרמון" in n for n in h["mount_hermon"])
    i = extract_locations(REGRESSION["I operational sector vs named region"][0]).locations[0]
    assert any("'גזרת'" in n for n in i.normalization_notes)


def test_exclusion_reasons():
    c = excluded(REGRESSION["C sports teams vs geography"][0])
    assert "sports team" in c["חיפה"] and "sports team" in c["ירושלים"]
    d = excluded(REGRESSION["D personal names and a restaurant"][0])
    assert set(d) == {"גולן", "שרון", "אוסלו"}
    assert "venue" in d["אוסלו"] and "personal name" in d["גולן"]


def test_entity_types_and_distinct_camp_vs_city():
    locs = {loc.english_name: loc for loc in extract_locations(REGRESSION["G refugee camps, a city and a military camp"][0]).locations}
    assert locs["jenin_refugee_camp"].entity_type == "refugee_camp"
    assert "jenin" not in locs  # the camp is not collapsed into the city
    assert locs["nahshonim_camp"].entity_type == "military_base"
    assert locs["nablus"].entity_type == "city"


# --- Additional cases (section 7.1) ----------------------------------------------------------------

@pytest.mark.parametrize("query, expected", [
    ("ומבאר שבע הגיעו", [("באר שבע", "beer_sheva")]),                     # ו + מ
    ("כשבחיפה ירד גשם", [("חיפה", "haifa")]),                              # כש + ב
    ("נסענו ללוד ומשם ללוד", [("לוד", "lod")]),                            # name starting with ל is not stripped
    ("ההגעה לירושלים ולת''א", [("ירושלים", "jerusalem"), ("תל אביב", "tel_aviv")]),  # '' as gershayim
    ("הכוחות פעלו בג'נין ובמחנה הפליטים ג'נין", [("ג'נין", "jenin"), ("מחנה הפליטים ג'נין", "jenin_refugee_camp")]),
    ("בין חיפה לעכו", [("חיפה", "haifa"), ("עכו", "acre")]),
    ("אירועים במזרח ירושלים", [("מזרח ירושלים", "east_jerusalem")]),        # established directional name kept
    ("אירועים בצפון ישראל", [("ישראל", "israel")]),                         # generic direction dropped
    ("סיור ברמת הגולן ובגולן", [("רמת הגולן", "golan_heights")]),           # person-risk form with a locative prefix
    ("מלון ירושלים בתל אביב", [("תל אביב", "tel_aviv")]),                  # venue name
    ("ג'בל א-שייח' והחרמון", [("הר חרמון", "mount_hermon")]),               # two aliases, one entity
    ("ביוני 2024 נמכרו 30 יחידות", []),                                    # dates / numbers
    ("כמה הזמנות היו אתמול", []),
    ("", []),
])
def test_additional_cases(query, expected):
    assert pairs(query) == expected


def test_ambiguous_abbreviation_is_not_expanded():
    [loc] = extract_locations("ההפגנה בר\"ה").locations
    assert loc.canonical_hebrew == 'ר"ה' and loc.english_name is None
    assert "ראש העין" in loc.ambiguity and "ראש השנה" in loc.ambiguity


def test_gaza_city_vs_strip_ambiguity_is_recorded():
    [loc] = extract_locations("ירי מעזה").locations
    assert loc.english_name == "gaza" and "Gaza Strip" in loc.ambiguity


def test_model_hints_are_validated():
    query = "כמה תקריות היו בבית שאן ובחיפה"
    result = extract_locations(query, [
        MentionHint(mention="בבית שאן", canonical_hebrew="בית שאן", english_name="Beit She'an", entity_type="city"),
        MentionHint(mention="בחיפה", canonical_hebrew="חיפה", english_name="haifa"),   # already covered
        MentionHint(mention="בנצרת", canonical_hebrew="נצרת"),                          # not in the query
        MentionHint(mention="הגזרה הצפונית"),                                            # not in the query either
    ])
    locs = {loc.canonical_hebrew: loc for loc in result.locations}
    assert list(locs) == ["בית שאן", "חיפה"]
    hint = locs["בית שאן"]
    assert hint.source == "model_hint" and hint.english_name == "beit_she_an" and hint.entity_type == "city"
    assert hint.original_mention == "בבית שאן"
    assert {e.mention for e in result.excluded} == {"בנצרת", "הגזרה הצפונית"}


def test_hint_generic_sector_and_unknown_type_are_rejected():
    result = extract_locations("הוקפצו לגזרה הצפונית ולמעלה", [
        MentionHint(mention="לגזרה הצפונית"), MentionHint(mention="ולמעלה", entity_type="spaceship")])
    assert [e.reason for e in result.excluded] == ["generic operational sector, not a named place"]
    [loc] = result.locations
    assert loc.entity_type is None and loc.english_name is None and loc.ambiguity


def test_hint_for_known_variant_uses_the_gazetteer():
    result = extract_locations("פעילות בבלאטה", [MentionHint(mention="בבלאטה", english_name="balata")])
    [loc] = result.locations
    assert loc.english_name == "balata_refugee_camp" and loc.source == "gazetteer"


def test_snake_case_and_punctuation():
    assert to_snake_case("Maroun al-Ras") == "maroun_al_ras"
    assert to_snake_case("  Tel  Aviv ") == "tel_aviv"
    assert to_snake_case("חיפה") is None
    assert normalize_punctuation("ת״א ג׳נין") == "ת\"א ג'נין"


def test_gazetteer_identifiers_are_unique_snake_case():
    names = [e.english_name for e in ENTRIES]
    assert len(names) == len(set(names))
    assert all(to_snake_case(n) == n for n in names)


def test_tool_returns_envelope():
    payload = json.loads(extract_tool.invoke({"query": REGRESSION["A two cities and a date"][0]}))
    assert payload["ok"] is True
    assert [loc["english_name"] for loc in payload["data"]["locations"]] == ["khan_yunis", "rafah"]
    empty = json.loads(extract_tool.invoke({"query": "כמה הזמנות"}))
    assert empty["data"] == {"locations": [], "excluded": []}
