"""
location_extractor.py - Extracts and resolves location polygons from queries.
"""

import logging
from typing import Dict, Optional, List
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from agent.services.extractor_base import BaseExtractor, ContextEntry
from agent.services import geo_utils

from agent.langfuse_client import langfuse_client
from agent.config import settings

import asyncio
import json
import re
from json_repair import repair_json

logger = logging.getLogger(__name__)


class LocationMapping(BaseModel):
    hebrew_name: str 
    english_name: str  # Standardized ID, e.g., "khan_yunis"
    wkt_polygon: Optional[str] = None  # The quoted WKT string: "'POLYGON(...)'"
    error_message: Optional[str] = None


class LocationExtractionResult(BaseModel):
    """Final output of the extractor."""
    valid_locations: List[LocationMapping] = Field(default_factory=list)
    location_wkt_instruction: str = ""
    raw_locations_dict: Dict[str, str] = Field(default_factory=dict)
    locations_coords_dict: Dict[str, str] = Field(default_factory=dict)


def _make_var_name(english_name: str) -> str:
    """Convert an LLM-produced English name into a safe Python/SQL identifier.

    Steps:
    1. Lowercase.
    2. Replace every non-alphanumeric/underscore character with '_'.
    3. Collapse consecutive underscores and strip leading/trailing ones.
    4. Prefix with 'loc_' when the result starts with a digit or is empty.
    5. Append '_wkt' suffix.
    """
    name = english_name.lower()
    name = re.sub(r'[^a-z0-9_]', '_', name)  # replace punctuation / spaces
    name = re.sub(r'_+', '_', name)            # collapse runs
    name = name.strip('_')                     # strip edges
    if not name or name[0].isdigit():
        name = f"loc_{name}" if name else "unknown"
    return f"{name}_wkt"


class LocationExtractorAgent(BaseExtractor):
    def __init__(self, llm_client, max_wkt_length: int | None = None, api_token: Optional[str] = None, runtime_flags: dict | None = None):
        super().__init__(runtime_flags)
        self.llm = llm_client
        self.max_wkt_length = max_wkt_length if max_wkt_length is not None else settings.LOCATION_MAX_WKT_LENGTH
        self.prompt_template = self._build_prompt()
        self._last_result: LocationExtractionResult | None = None

    def _process_locations(self, locations_map: Dict[str, str]) -> LocationExtractionResult:
        """
        Processes Hebrew locations to geocoded simplified WKT polygons.
        """
        valid_locations = []
        for heb_name, eng_name in locations_map.items():
            wkt = None
            error = None
            try:
                geojson = geo_utils.get_geojson_polygon(heb_name)
                if geojson:
                    wkt = geo_utils.geojson_to_simplified_wkt(geojson, self.max_wkt_length)
                    if not wkt:
                        error = "Geometry too complex to fit in max length limit"
                else:
                    error = "No geometry found from API"
            except Exception as e:
                error = f"Processing error: {str(e)}"

            valid_locations.append(LocationMapping(
                hebrew_name=heb_name,
                english_name=eng_name,
                wkt_polygon=wkt,
                error_message=error
            ))

        # Build instruction string & dictionaries
        successful = [loc for loc in valid_locations if loc.wkt_polygon]
        instruction_parts = []
        coords_dict = {}
        names_dict = {}
        seen_ids: set = set()
        for loc in successful:
            names_dict[loc.hebrew_name] = loc.english_name  # always populated
            var_name = _make_var_name(loc.english_name)
            if var_name in seen_ids:
                logger.warning(
                    "Duplicate identifier '%s' for location '%s'; skipping coords/instruction entry.",
                    var_name, loc.hebrew_name,
                )
                continue
            seen_ids.add(var_name)
            instruction_parts.append(f"{var_name} = {loc.wkt_polygon}")
            coords_dict[var_name] = loc.wkt_polygon

        instruction_text = "\n".join(instruction_parts) if instruction_parts else ""

        return LocationExtractionResult(
            valid_locations=valid_locations,
            location_wkt_instruction=instruction_text,
            raw_locations_dict=names_dict,
            locations_coords_dict=coords_dict
        )

    def extract(self, query: str) -> List[ContextEntry]:
        """
        Synchronous extraction to satisfy the BaseExtractor interface.
        Also stores the full result on self._last_result for callers that
        need location-specific state (coords dict, WKT instruction).
        """
        messages = self.prompt_template.format_messages(user_query=query)
        response = self.llm.invoke(messages)
        locations_map = self._parse_llm_json(response.content)
        result = self._process_locations(locations_map)
        self._last_result = result

        entries = []
        for loc in result.valid_locations:
            if loc.wkt_polygon:
                entries.append(ContextEntry(
                    term=loc.hebrew_name,
                    context=f"Location '{loc.hebrew_name}' translated to '{loc.english_name}' with polygon: {loc.wkt_polygon}"
                ))
        return entries

    def _build_prompt(self) -> ChatPromptTemplate:
        langfuse_prompt = langfuse_client.get_prompt(settings.LANGFUSE_PROMPT_LOC_EXTRACTOR)
        if langfuse_prompt is None:
            raise RuntimeError(
                f"Langfuse prompt '{settings.LANGFUSE_PROMPT_LOC_EXTRACTOR}' could not be retrieved."
            )
        return ChatPromptTemplate.from_messages(
            langfuse_prompt.get_langchain_prompt()
        )

    def _parse_llm_json(self, text: str) -> Dict[str, str]:
        # Strip markdown code blocks if present
        clean_text = re.sub(r'```(?:json)?\s*([\s\S]*?)\s*```', r'\1', text)
        clean_text = clean_text.strip()

        try:
            data = json.loads(clean_text)
            if not isinstance(data, dict):
                return {}
            return {k: str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        except json.JSONDecodeError:
            try:
                fixed = repair_json(clean_text)
                data = json.loads(fixed)
                if isinstance(data, dict):
                    return {k: str(v) for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
            except Exception:
                pass
            return {}

    async def run(self, user_request: str) -> LocationExtractionResult:
        # Step 1: LLM Call
        messages = self.prompt_template.format_messages(user_query=user_request)
        response = await self.llm.ainvoke(messages)
        content = response.content

        # Step 2: Robust JSON Parsing
        locations_map = self._parse_llm_json(content)

        # Step 3 & 4: Process locations in a worker thread to avoid blocking
        # the event loop with network I/O and rate-limiter sleeps.
        return await asyncio.to_thread(self._process_locations, locations_map)
