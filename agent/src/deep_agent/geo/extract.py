"""
Stage 1 — Hebrew location extraction and normalisation, by the model.

Which spans are places, and how each is normalised, is a contextual judgement (a team named
after a city, a personal name that is also a region, a prefix letter that belongs to the name),
so it is made by an LLM following the `location-extractor` prompt (guidelines + examples,
Langfuse-managed as deep-agent/location-extractor). The model first lists every candidate it
considered with its decision, then the entities.

This module never decides what is a place. It only checks the model's output before anyone
relies on it:
- every original_mention must occur in the query (a hallucinated mention is moved to
  `excluded` with the reason);
- english_name is coerced to lowercase snake_case; entity_type must be a known type;
- duplicates (same canonical Hebrew + English name) are merged;
- malformed output gets one corrective retry, then a clear failure.
"""

import json
import logging
import re
from typing import Any

from json_repair import repair_json
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, Field, ValidationError

from deep_agent.geo.models import EntityType, ExcludedMention, ExtractionResult, LocationEntity
from deep_agent.prompt_registry import load_prompt

logger = logging.getLogger(__name__)

PROMPT_KEY = "location-extractor"
_ENTITY_TYPES = frozenset(EntityType.__args__)
# One-to-one character substitutions (Hebrew geresh/gershayim, maqaf, typographic quotes), so a
# mention typed with ״ matches a query typed with ".
_PUNCT = str.maketrans({"״": '"', "“": '"', "”": '"', "׳": "'", "‘": "'", "’": "'", "`": "'",
                        "־": "-", "–": "-", "—": "-"})


class CandidateDecision(BaseModel):
    mention: str = Field(description="A span of the query you considered.")
    is_geographic: bool = Field(description="True only if it is used as a geographic reference here.")
    reason: str = Field(description="Short factual reason (e.g. 'sports team', 'date', 'city where it happened').")


class ExtractedLocation(BaseModel):
    original_mention: str = Field(description="Exact substring of the query, including any prefix.")
    canonical_hebrew: str
    english_name: str | None = Field(default=None, description="lowercase snake_case English name")
    entity_type: str | None = None
    mention_context: str = ""
    normalization_notes: list[str] = Field(default_factory=list)
    ambiguity: str | None = None


class LocationExtraction(BaseModel):
    """Submit the geographic entities of the query. Call exactly once."""

    candidates: list[CandidateDecision] = Field(
        default_factory=list, description="Every span considered, places and look-alikes, with the decision. Fill first.")
    locations: list[ExtractedLocation] = Field(default_factory=list)


class ExtractionError(RuntimeError):
    pass


def normalize_punctuation(text: str) -> str:
    return (text or "").translate(_PUNCT)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_punctuation(text)).strip()


def to_snake_case(name: str | None) -> str | None:
    """lowercase ASCII snake_case, or None when the name has no ASCII letters (e.g. Hebrew)."""
    if not name:
        return None
    snake = re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.strip().lower())).strip("_")
    return snake if re.search(r"[a-z]", snake) else None


def _mention_span(query: str, mention: str) -> tuple[int, int] | None:
    """Span of `mention` in `query`, tolerant only of punctuation variants and whitespace runs."""
    if not mention.strip():
        return None
    parts = [re.escape(p) for p in _squash(mention).split(" ")]
    m = re.search(r"\s+".join(parts), normalize_punctuation(query))
    return m.span() if m else None


def validate_extraction(query: str, raw: LocationExtraction) -> ExtractionResult:
    """Check the model's extraction against the query; never adds or reinterprets places."""
    locations: list[LocationEntity] = []
    excluded = [ExcludedMention(mention=c.mention, reason=c.reason) for c in raw.candidates if not c.is_geographic]
    seen: set[tuple[str, str | None]] = set()
    for loc in raw.locations:
        span = _mention_span(query, loc.original_mention)
        if span is None:
            logger.warning("Location extractor reported %r, which is not in the query", loc.original_mention)
            excluded.append(ExcludedMention(mention=loc.original_mention,
                                            reason="reported by the extractor but not found in the query — dropped"))
            continue
        english = to_snake_case(loc.english_name)
        notes = list(loc.normalization_notes)
        if english and loc.english_name and english != loc.english_name:
            notes.append(f"english name normalised to snake_case ({loc.english_name!r} → {english!r})")
        entity_type = loc.entity_type if loc.entity_type in _ENTITY_TYPES else None
        if loc.entity_type and entity_type is None:
            notes.append(f"unknown entity_type {loc.entity_type!r} dropped")
        ambiguity = loc.ambiguity or (None if english else "no English name given")
        key = (_squash(loc.canonical_hebrew), english)
        if key in seen:
            continue
        seen.add(key)
        locations.append(LocationEntity(
            original_mention=query[span[0]:span[1]], canonical_hebrew=_squash(loc.canonical_hebrew),
            english_name=english, entity_type=entity_type,
            mention_context=loc.mention_context or query[max(0, span[0] - 25):span[1] + 25].strip(),
            normalization_notes=notes, ambiguity=ambiguity,
        ))
    return ExtractionResult(locations=locations, excluded=excluded)


def _parse(message: AIMessage) -> LocationExtraction:
    for call in message.tool_calls or []:
        if call.get("name") == LocationExtraction.__name__:
            return LocationExtraction.model_validate(call.get("args") or {})
    text = message.text if isinstance(message.text, str) else str(message.content)
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("no LocationExtraction tool call and no JSON object in the reply")
    return LocationExtraction.model_validate(json.loads(repair_json(match.group(0))))


async def extract_locations(
    query: str, model: BaseChatModel, note: str | None = None, config: dict[str, Any] | None = None,
) -> ExtractionResult:
    """Run the model extractor on `query` (one corrective retry on malformed output)."""
    if not query or not query.strip():
        return ExtractionResult()
    bound = model.bind_tools([LocationExtraction])
    content = query if not note else f"{query}\n\n[Reviewer note: {note}]"
    messages: list = [SystemMessage(load_prompt(PROMPT_KEY).text), HumanMessage(content)]
    last_error: Exception | None = None
    for _ in range(2):
        reply = await bound.ainvoke(messages, config=config)
        try:
            return validate_extraction(query, _parse(reply))
        except (ValueError, ValidationError) as exc:
            last_error = exc
            logger.warning("Location extractor returned malformed output: %s", exc)
            call_id = next((c.get("id") for c in reply.tool_calls or []), None)
            messages.append(reply)
            feedback = f"Invalid output: {exc}. Call LocationExtraction again with valid arguments."
            messages.append(ToolMessage(feedback, tool_call_id=call_id) if call_id else HumanMessage(feedback))
    raise ExtractionError(f"location extractor output invalid after retry: {last_error}")
