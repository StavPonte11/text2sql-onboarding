"""
Extraction evaluation set: the nine spec examples (section 2.7) plus additional cases (section 7.1).

Each case: (query, expected [(canonical_hebrew, english_name), ...] in order of appearance).
Used by the prompt check (the nine examples are in the extractor prompt) and the live LLM evaluation.
"""

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

# Section 7.1 additions (not in the prompt, so they test generalisation rather than recall).
# Expected canonical Hebrew names only: the spec fixes English names just for its own examples.
ADDITIONAL = {
    "stacked prefixes": ("ומבאר שבע המשיכו לדימונה", ["באר שבע", "דימונה"]),
    "names starting with a prefix letter": ("כמה משלוחים יצאו ללוד ומבית שמש", ["לוד", "בית שמש"]),
    "nested camp vs city": ("הכוחות פעלו בג'נין ובמחנה הפליטים ג'נין", ["ג'נין", "מחנה הפליטים ג'נין"]),
    "established vs generic direction": ("אירועים במזרח ירושלים ובצפון חיפה", ["מזרח ירושלים", "חיפה"]),
    "two places in one phrase": ("כמה רכבות נסעו בין חיפה לעכו", ["חיפה", "עכו"]),
    "team and restaurant": ("אוהדי בית\"ר ירושלים חגגו במסעדת טוקיו בנתניה", ["נתניה"]),
    "date and numbers only": ("ביוני 2024 נמכרו 30 יחידות", []),
    "no geography": ("כמה הזמנות היו אתמול", []),
    "multiple prefixes on arabic names": ("ומשכם עברו לטול כרם ולקלקיליה", ["שכם", "טול כרם", "קלקיליה"]),
}
