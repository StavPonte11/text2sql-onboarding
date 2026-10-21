"""
Stage 1 checks — validation of the locations the location-analyst model extracted.

Extraction itself is the location-analyst's reasoning (its prompt carries the guidelines and
examples) and arrives as LocationPlan.locations / .excluded. This module never decides what is a
place. It only checks the model's output before anyone relies on it:
- every original_mention must occur in the question (a hallucinated mention is moved to
  `excluded` with the reason);
- english_name is coerced to lowercase snake_case; entity_type must be a known type;
- duplicates (same canonical Hebrew + English name) are merged.
"""

import logging
import re

from deep_agent.geo.models import EntityType, ExcludedMention, LocationEntity

logger = logging.getLogger(__name__)

_ENTITY_TYPES = frozenset(EntityType.__args__)
# One-to-one character substitutions (Hebrew geresh/gershayim, maqaf, typographic quotes), so a
# mention typed with ״ matches a question typed with ".
_PUNCT = str.maketrans({"״": '"', "“": '"', "”": '"', "׳": "'", "‘": "'", "’": "'", "`": "'",
                        "־": "-", "–": "-", "—": "-"})


def normalize_punctuation(text: str) -> str:
    return (text or "").translate(_PUNCT)


def squash(text: str) -> str:
    """Punctuation-normalised, whitespace-collapsed form used to compare names."""
    return re.sub(r"\s+", " ", normalize_punctuation(text)).strip()


def to_snake_case(name: str | None) -> str | None:
    """lowercase ASCII snake_case, or None when the name has no ASCII letters (e.g. Hebrew)."""
    if not name:
        return None
    snake = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.strip().lower())).strip("_")
    return snake if re.search(r"[a-z]", snake) else None


def mention_span(query: str, mention: str) -> tuple[int, int] | None:
    """Span of `mention` in `query`, tolerant only of punctuation variants and whitespace runs."""
    if not mention.strip():
        return None
    parts = [re.escape(p) for p in squash(mention).split(" ")]
    m = re.search(r"\s+".join(parts), normalize_punctuation(query))
    return m.span() if m else None


def validate_locations(
    query: str, locations: list[LocationEntity], excluded: list[ExcludedMention],
) -> tuple[list[LocationEntity], list[ExcludedMention]]:
    """Check the model's extraction against the question; never adds or reinterprets places."""
    valid: list[LocationEntity] = []
    rejected = list(excluded)
    seen: set[tuple[str, str | None]] = set()
    for loc in locations:
        span = mention_span(query, loc.original_mention)
        if span is None:
            logger.warning("location-analyst reported %r, which is not in the question", loc.original_mention)
            rejected.append(ExcludedMention(mention=loc.original_mention,
                                            reason="reported as a location but not found in the question — dropped"))
            continue
        english = to_snake_case(loc.english_name)
        notes = list(loc.normalization_notes)
        if english and loc.english_name and english != loc.english_name:
            notes.append(f"english name normalised to snake_case ({loc.english_name!r} → {english!r})")
        entity_type = loc.entity_type if loc.entity_type in _ENTITY_TYPES else None
        if loc.entity_type and entity_type is None:
            notes.append(f"unknown entity_type {loc.entity_type!r} dropped")
        key = (squash(loc.canonical_hebrew), english)
        if key in seen:
            continue
        seen.add(key)
        valid.append(LocationEntity(
            original_mention=query[span[0]:span[1]], canonical_hebrew=squash(loc.canonical_hebrew),
            english_name=english, entity_type=entity_type,
            mention_context=loc.mention_context or query[max(0, span[0] - 25):span[1] + 25].strip(),
            normalization_notes=notes, ambiguity=loc.ambiguity or (None if english else "no English name given"),
        ))
    return valid, rejected
