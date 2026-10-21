"""
Stage 1 — Hebrew location extraction and normalisation (deterministic).

Gazetteer forms are matched in the query with an optional Hebrew prefix chain (ו, ש/כש, ב/ל/מ/כ,
ה) in front of them, longest form first so that "מחנה הפליטים ג'נין" wins over "ג'נין" and
"דרום לבנון" over "לבנון". Each match is then checked against its context: sports teams,
venue names and personal names are excluded; generic directional / operational words in front
are dropped with a note.

Mentions the gazetteer does not know are proposed by the location-analyst model as hints and
normalised here: they must occur in the query, prefixes are stripped only when the remainder
is a known form (a mechanical strip would corrupt names such as לוד), and the English name is
validated as snake_case.
"""

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field

from deep_agent.geo.gazetteer import (
    AMBIGUOUS_ABBREVIATIONS, DIRECTION_WORDS, ENTRIES, GEOGRAPHIC_HEADS, OPERATIONAL_WORDS, TEAM_WORDS, VENUE_WORDS,
    GazetteerEntry,
)
from deep_agent.geo.models import EntityType, ExcludedMention, ExtractionResult, LocationEntity

# One-to-one character substitutions, so spans in the normalised text are spans in the query.
_PUNCT = str.maketrans({"״": '"', "“": '"', "”": '"', "׳": "'", "‘": "'", "’": "'", "`": "'",
                        "־": "-", "–": "-", "—": "-"})
_PREFIX = r"(?:ו)?(?:ש|כש)?(?:[בלמכ])?(?:ה)?"
_PREFIX_RE = re.compile(_PREFIX)
_LOCATIVE = re.compile(r"^ו?(?:ש|כש)?[בלמ]")
_PREFIX_NAMES = {"ו": "ו (and)", "ב": "ב (in)", "ל": "ל (to)", "מ": "מ (from)", "כ": "כ (as)", "ש": "ש (that)",
                 "ה": "ה (the)"}
_GENERIC_SECTOR = re.compile(r"^(?:ו?[בלמ]?)?ה?גזרה(?:\s+ה\S+)?$")
_CONTEXT_CHARS = 25


def normalize_punctuation(text: str) -> str:
    return (text or "").translate(_PUNCT)


def to_snake_case(name: str | None) -> str | None:
    """lowercase ASCII snake_case, or None when the name has no ASCII letters (e.g. Hebrew)."""
    if not name:
        return None
    snake = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.strip().lower())).strip("_")
    return snake if re.search(r"[a-z]", snake) else None


def _form_regex(form: str) -> str:
    parts = []
    for ch in normalize_punctuation(form):
        if ch in " -":
            parts.append(r"[\s-]")
        elif ch == '"':
            parts.append(r"(?:\"|'')")
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


@dataclass(frozen=True)
class _Form:
    text: str
    kind: str          # canonical | abbreviation | variant | alias | person_risk | ambiguous_abbreviation
    entry: GazetteerEntry | None
    pattern: re.Pattern


def _compile(text: str, kind: str, entry: GazetteerEntry | None) -> _Form:
    pattern = re.compile(rf"(?<![\w\"'])(?P<prefix>{_PREFIX})(?P<name>{_form_regex(text)})(?![\w])")
    return _Form(text, kind, entry, pattern)


def _build_forms() -> list[_Form]:
    forms = []
    for entry in ENTRIES:
        forms.append(_compile(entry.canonical_hebrew, "canonical", entry))
        forms.extend(_compile(text, kind, entry) for text, kind in entry.forms.items())
    forms.extend(_compile(abbr, "ambiguous_abbreviation", None) for abbr in AMBIGUOUS_ABBREVIATIONS)
    return sorted(forms, key=lambda f: len(f.text), reverse=True)


_FORMS = _build_forms()
_BY_FORM = {normalize_punctuation(f.text): f for f in reversed(_FORMS)}


def _strip_word_prefix(word: str, vocab: frozenset[str]) -> str | None:
    """The vocabulary word under an optional prefix chain ('למזרח' -> 'מזרח'), else None."""
    for i in range(0, min(4, len(word))):
        if _PREFIX_RE.fullmatch(word[:i]) and word[i:] in vocab:
            return word[i:]
    return None


def _previous_word(text: str, start: int) -> str:
    words = re.findall(r"[\w\"'-]+", text[:start])
    return words[-1] if words else ""


def _context(query: str, start: int, end: int) -> str:
    return query[max(0, start - _CONTEXT_CHARS):end + _CONTEXT_CHARS].strip()


def _prefix_note(prefix: str) -> str:
    return "stripped prefix " + " + ".join(_PREFIX_NAMES.get(ch, ch) for ch in prefix)


def _exclusion(form: _Form, prefix: str, previous: str) -> str | None:
    if not prefix:
        if _strip_word_prefix(previous, TEAM_WORDS):
            return f"part of a sports team / organisation name ('{previous} …')"
        if _strip_word_prefix(previous, VENUE_WORDS):
            return f"part of a venue or street name ('{previous} …')"
    if form.kind == "person_risk" and not _LOCATIVE.match(prefix) and not _strip_word_prefix(previous, GEOGRAPHIC_HEADS):
        return "common personal name without a locative prefix or geographic head word"
    return None


def _entity_from_match(query: str, form: _Form, prefix: str, start: int, end: int, previous: str) -> LocationEntity:
    notes = [_prefix_note(prefix)] if prefix else []
    mention = query[start:end]
    if form.entry is None:  # ambiguous abbreviation: keep the mention, do not expand speculatively
        options = AMBIGUOUS_ABBREVIATIONS[form.text]
        return LocationEntity(
            original_mention=mention, canonical_hebrew=normalize_punctuation(mention), english_name=None,
            mention_context=_context(query, start, end), normalization_notes=notes,
            ambiguity=f"ambiguous abbreviation; could be: {', '.join(options)}",
        )
    entry = form.entry
    if form.kind != "canonical":
        label = {"person_risk": "alias"}.get(form.kind, form.kind)
        notes.append(f"{label} '{form.text}' → '{entry.canonical_hebrew}'")
    direction = _strip_word_prefix(previous, DIRECTION_WORDS)
    if direction and not entry.canonical_hebrew.startswith(direction):
        notes.append(f"dropped generic directional modifier '{direction}'")
    operational = _strip_word_prefix(previous, OPERATIONAL_WORDS)
    if operational:
        notes.append(f"dropped generic area word '{operational}'")
    return LocationEntity(
        original_mention=mention, canonical_hebrew=entry.canonical_hebrew, english_name=entry.english_name,
        entity_type=entry.entity_type, mention_context=_context(query, start, end), normalization_notes=notes,
        ambiguity=entry.ambiguity,
    )


class MentionHint(BaseModel):
    """A place the model found in the query that the gazetteer may not know."""

    mention: str = Field(description="Exact substring of the query, as written (with any prefix).")
    canonical_hebrew: str | None = Field(default=None, description="Normalised Hebrew name, without grammatical prefixes.")
    english_name: str | None = Field(default=None, description="English name; converted to snake_case.")
    entity_type: str | None = None


def _lookup(text: str) -> tuple[_Form, str] | None:
    """Gazetteer form for `text`, allowing a prefix chain in front: (form, prefix)."""
    norm = normalize_punctuation(text).strip()
    for i in range(0, min(4, len(norm))):
        if _PREFIX_RE.fullmatch(norm[:i]) and norm[i:] in _BY_FORM:
            return _BY_FORM[norm[i:]], norm[:i]
    return None


def _from_hint(query: str, hint: MentionHint) -> LocationEntity | ExcludedMention:
    mention = hint.mention.strip()
    norm_query = normalize_punctuation(query)
    start = norm_query.find(normalize_punctuation(mention))
    if not mention or start < 0:
        return ExcludedMention(mention=mention, reason="not found in the query")
    end = start + len(mention)
    if _GENERIC_SECTOR.match(normalize_punctuation(mention)):
        return ExcludedMention(mention=mention, reason="generic operational sector, not a named place")

    known = _lookup(mention) or (_lookup(hint.canonical_hebrew) if hint.canonical_hebrew else None)
    if known and known[0].entry is not None:
        form, prefix = known
        entity = _entity_from_match(query, form, prefix, start, end, _previous_word(norm_query, start))
        return entity.model_copy(update={"original_mention": query[start:end]})

    allowed_types = set(EntityType.__args__)
    canonical = normalize_punctuation(hint.canonical_hebrew or mention).strip()
    notes = ["not in the gazetteer — model-proposed name, unverified until resolved"]
    if canonical != normalize_punctuation(mention):
        notes.append(f"model normalised '{mention}' → '{canonical}'")
    english = to_snake_case(hint.english_name)
    entity_type = hint.entity_type if hint.entity_type in allowed_types else None
    return LocationEntity(
        original_mention=query[start:end], canonical_hebrew=canonical, english_name=english,
        entity_type=entity_type, mention_context=_context(query, start, end), normalization_notes=notes,
        ambiguity=None if english else "no canonical English name available", source="model_hint",
    )


def extract_locations(query: str, hints: list[MentionHint] | None = None) -> ExtractionResult:
    """Extract and normalise the geographic entities referenced in a Hebrew query."""
    text = normalize_punctuation(query)
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, LocationEntity]] = []
    excluded: list[ExcludedMention] = []

    for form in _FORMS:
        for m in form.pattern.finditer(text):
            start, end = m.span("name")
            if any(start < e and s < end for s, e in taken):
                continue
            taken.append((start, end))
            previous = _previous_word(text, m.start("prefix"))
            reason = _exclusion(form, m.group("prefix"), previous)
            if reason:
                excluded.append(ExcludedMention(mention=query[start:end], reason=reason))
                continue
            found.append((start, _entity_from_match(query, form, m.group("prefix"), start, end, previous)))

    for hint in hints or []:
        start = text.find(normalize_punctuation(hint.mention.strip())) if hint.mention.strip() else -1
        if start >= 0 and any(start < e and s < start + len(hint.mention.strip()) for s, e in taken):
            continue  # already handled by the gazetteer (extracted or excluded)
        result = _from_hint(query, hint)
        if isinstance(result, ExcludedMention):
            excluded.append(result)
        else:
            found.append((start, result))

    locations: list[LocationEntity] = []
    seen: set[tuple[str, str | None]] = set()
    for _, entity in sorted(found, key=lambda item: item[0]):
        key = (entity.canonical_hebrew, entity.english_name)
        if key not in seen:
            seen.add(key)
            locations.append(entity)
    return ExtractionResult(locations=locations, excluded=excluded)
