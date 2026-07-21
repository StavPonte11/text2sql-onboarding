"""
extractor_base.py - Shared base classes for all extractors.

Extracted to a standalone module to avoid circular imports between
agent.nodes.extractor and agent.services.location_extractor.
"""

import abc
from typing import List
from pydantic import BaseModel, Field


class ContextEntry(BaseModel):
    term: str = Field(
        description="The exact term or phrase from the user query that is being enriched."
    )
    context: str = Field(
        description=(
            "Additional context, resolved meaning, or clarification for the term. "
            "Examples: resolving an abbreviation ('MDA' → 'Magen David Adom'), "
            "a relative date ('last quarter' → 'Q1 2025, Jan 1 - Mar 31 2025'), "
            "or a geographic clarification ('Jordan' → 'country in the Middle East, not a person name')."
        )
    )


class BaseExtractor(abc.ABC):
    def __init__(self, runtime_flags: dict | None = None):
        self.runtime_flags = runtime_flags or {}

    @abc.abstractmethod
    def extract(self, query: str) -> List[ContextEntry]:
        """Run extraction and return enrichment entries."""
        pass

    def state_update(self) -> dict:
        """Return any extra AgentState fields this extractor wants to set.
        Called after extract(). Defaults to no extra state.
        Override in subclasses that need to write state beyond query_enrichments.
        """
        return {}
