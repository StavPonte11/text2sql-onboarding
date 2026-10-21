"""
Seed gazetteer: canonical Hebrew names, English identifiers, types and known surface forms.

This is the deterministic source of canonical names for places the agent meets often. It is
not a complete geographic dictionary — the location-analyst model proposes names it does not
cover, and those are marked `source="model_hint"`. English identifiers are names, not feature
ids: resolution decides which real feature is meant.

Surface-form kinds (recorded in normalization_notes when a form other than the canonical matched):
- abbreviation   ת"א → תל אביב
- variant        spelling / transliteration variant (בינת ג'ביל → בינת ג'בייל)
- alias          a different name for the same feature (ג'בל שייח' → הר חרמון)
- person_risk    a bare form that is also a common personal name (גולן, שרון); only extracted with
                 a locative prefix or a geographic head word
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GazetteerEntry:
    canonical_hebrew: str
    english_name: str
    entity_type: str
    forms: dict[str, str] = field(default_factory=dict)  # surface form -> kind
    ambiguity: str | None = None


def _e(canonical: str, english: str, entity_type: str, ambiguity: str | None = None, **kinds: tuple[str, ...]):
    forms = {form: kind for kind, values in kinds.items() for form in values}
    return GazetteerEntry(canonical, english, entity_type, forms, ambiguity)


ENTRIES: tuple[GazetteerEntry, ...] = (
    # Countries and geopolitical regions
    _e("ישראל", "israel", "country"),
    _e("לבנון", "lebanon", "country"),
    _e("סוריה", "syria", "country"),
    _e("ירדן", "jordan", "country", person_risk=("ירדן",)),
    _e("מצרים", "egypt", "country"),
    _e("איראן", "iran", "country"),
    _e("ארצות הברית", "united_states", "country", abbreviation=('ארה"ב',)),
    _e("יהודה ושומרון", "judea_and_samaria", "region", abbreviation=('איו"ש', 'יו"ש')),
    _e("דרום לבנון", "south_lebanon", "region"),
    _e("מזרח ירושלים", "east_jerusalem", "region"),
    _e("רצועת עזה", "gaza_strip", "region", variant=("רצועת-עזה",)),
    _e("עוטף עזה", "gaza_envelope", "region"),
    _e("גוש דן", "gush_dan", "region"),
    _e("רמת הגולן", "golan_heights", "region", alias=("הגולן",), person_risk=("גולן",)),
    _e("השרון", "sharon_plain", "region", person_risk=("שרון",)),
    _e("הכרמל", "mount_carmel", "mountain", alias=("הר הכרמל",), person_risk=("כרמל",)),
    _e("הגליל", "galilee", "region"),
    _e("הגליל העליון", "upper_galilee", "region"),
    _e("הנגב", "negev", "region"),
    _e("בקעת הירדן", "jordan_valley", "valley"),
    # Cities and towns
    _e("תל אביב", "tel_aviv", "city", abbreviation=('ת"א',), variant=("תל-אביב", "תל אביב-יפו", "תל אביב יפו")),
    _e("ירושלים", "jerusalem", "city", abbreviation=("י-ם",)),
    _e("חיפה", "haifa", "city"),
    _e("באר שבע", "beer_sheva", "city", abbreviation=('ב"ש',), variant=("באר-שבע",)),
    _e("פתח תקווה", "petah_tikva", "city", abbreviation=('פ"ת',), variant=("פתח תקוה",)),
    _e("ראשון לציון", "rishon_lezion", "city", abbreviation=('ראשל"צ',)),
    _e("רמת גן", "ramat_gan", "city", abbreviation=('ר"ג',)),
    _e("קריית שמונה", "kiryat_shmona", "city", abbreviation=('ק"ש',), variant=("קרית שמונה",)),
    _e("בני ברק", "bnei_brak", "city"),
    _e("נתניה", "netanya", "city"),
    _e("אשדוד", "ashdod", "city"),
    _e("אשקלון", "ashkelon", "city"),
    _e("שדרות", "sderot", "city"),
    _e("מטולה", "metula", "town"),
    _e("נהריה", "nahariya", "city"),
    _e("עכו", "acre", "city"),
    _e("צפת", "safed", "city"),
    _e("טבריה", "tiberias", "city"),
    _e("אילת", "eilat", "city"),
    _e("לוד", "lod", "city"),
    _e("רמלה", "ramla", "city"),
    _e("נצרת", "nazareth", "city"),
    _e("אום אל-פחם", "umm_al_fahm", "city", variant=("אום אל פחם",)),
    _e("כפר קאסם", "kafr_qasim", "city", variant=("כפר קסם",)),
    _e("כפר כנא", "kafr_kanna", "town", variant=("כפר כנה",)),
    _e("אוסלו", "oslo", "city"),
    # Gaza and Judea & Samaria
    _e("עזה", "gaza", "city", ambiguity="may mean Gaza City or the whole Gaza Strip"),
    _e("חאן יונס", "khan_yunis", "city", variant=("ח'אן יונס", "חאן יונוס")),
    _e("רפיח", "rafah", "city"),
    _e("שכם", "nablus", "city"),
    _e("ג'נין", "jenin", "city"),
    _e("טול כרם", "tulkarm", "city", variant=("טולכרם",)),
    _e("קלקיליה", "qalqilya", "city"),
    _e("רמאללה", "ramallah", "city"),
    _e("חברון", "hebron", "city"),
    _e("בית לחם", "bethlehem", "city"),
    _e("מחנה הפליטים ג'נין", "jenin_refugee_camp", "refugee_camp",
       variant=("מחנה פליטים ג'נין", "מחנה ג'נין")),
    _e("מחנה בלאטה", "balata_refugee_camp", "refugee_camp",
       variant=("מחנה הפליטים בלאטה", "מחנה בלטא", "בלאטה", "בלטא")),
    _e("מחנה נור א-שמס", "nur_shams_refugee_camp", "refugee_camp",
       variant=("מחנה נור שמס", "נור א-שמס", "נור שמס")),
    # Southern Lebanon
    _e("בינת ג'בייל", "bint_jbeil", "town", variant=("בינת ג'ביל", "בינת ג'בל", "בינת גבייל")),
    _e("מארון א-ראס", "maroun_al_ras", "village",
       variant=("מארון אר-ראס", "מארון אל-ראס", "מארון א ראס", "מרון א-ראס", "מארון אר ראס")),
    _e("אל-ח'יאם", "al_khiam", "town", variant=("אל ח'יאם", "אלח'יאם", "אל-חיאם", "ח'יאם", "חיאם")),
    # Military installations
    _e("מחנה נחשונים", "nahshonim_camp", "military_base"),
    # Water and natural features
    _e("נהר הליטני", "litani_river", "river", variant=("הליטני", "ליטני", "נהר ליטני")),
    _e("נחל קישון", "kishon_river", "river", variant=("הקישון", "נחל הקישון")),
    _e("נחל הירקון", "yarkon_river", "river", variant=("הירקון",)),
    _e("ואדי עארה", "wadi_ara", "valley", variant=("ואדי ערה", "ואדי עארא")),
    _e("ים המלח", "dead_sea", "lake"),
    _e("הכנרת", "sea_of_galilee", "lake", alias=("ים כנרת",), person_risk=("כנרת",)),
    _e("הר דב", "har_dov", "mountain"),
    _e("הר חרמון", "mount_hermon", "mountain",
       alias=("החרמון", "חרמון", "ג'בל שייח'", "ג'בל א-שייח'", "ג'בל אל-שייח'", "ג'בל א שייח'")),
)

# Abbreviations whose expansion depends on context — never expanded speculatively.
AMBIGUOUS_ABBREVIATIONS: dict[str, tuple[str, ...]] = {
    'ר"ה': ("ראש העין", "ראש השנה (holiday, not a place)"),
    'ק"ג': ("קריית גת", "קילוגרם (unit, not a place)"),
}

# Directional words that are dropped unless they are part of an established name (דרום לבנון).
DIRECTION_WORDS = frozenset({"צפון", "דרום", "מזרח", "מערב"})
# Generic operational / area words in front of a name (גזרת עוטף עזה → עוטף עזה).
OPERATIONAL_WORDS = frozenset({"גזרת", "גזרה", "אזור", "מרחב", "שטח"})
# Words that turn a following place name into part of an organisation name.
TEAM_WORDS = frozenset({"מכבי", "הפועל", 'בית"ר', "ביתר", "בני", "עירוני", "הכח", "אחי"})
VENUE_WORDS = frozenset({"מסעדת", "מסעדה", "מלון", "קפה", "בר", "פאב", "חנות", "קניון", "רחוב"})
# A bare person_risk form counts as a place only after one of these head words.
GEOGRAPHIC_HEADS = frozenset({"אזור", "רמת", "עמק", "גבול", "צפון", "דרום", "מזרח", "מערב"})
