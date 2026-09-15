"""
try_location_extractor.py - Run location extractor on custom input queries.

Usage:
  uv run python -m agent.scratch.try_location_extractor "אירועים בחאן יונס וברפיח"
  uv run python -m agent.scratch.try_location_extractor (starts interactive loop)
"""

import sys
import asyncio
from agent.llm import get_llm
from agent.services.location_extractor import LocationExtractorAgent


async def run_extraction(user_query: str):
    print("=" * 80)
    print(f"🔍 INPUT QUERY: {user_query}")
    print("=" * 80)

    llm = get_llm("location_extractor")
    agent = LocationExtractorAgent(llm_client=llm)

    print("\n⏳ Extracting locations and fetching geocoded WKT polygons...\n")
    result = await agent.run(user_query)

    print("🧠 STEP-BY-STEP REASONING (_analysis):")
    print("-" * 60)
    print(result.analysis if result.analysis else "(No reasoning returned or flat format)")

    print("\n🗺️ RAW LOCATION MAPPING (Hebrew -> Standardized English):")
    print("-" * 60)
    if result.raw_locations_dict:
        for heb, eng in result.raw_locations_dict.items():
            print(f"  • {heb} -> {eng}")
    else:
        print("  (No location entities detected)")

    print("\n📍 VALIDATED LOCATIONS & WKT POLYGONS:")
    print("-" * 60)
    if result.valid_locations:
        for loc in result.valid_locations:
            print(f"  • Hebrew: {loc.hebrew_name}")
            print(f"    English: {loc.english_name}")
            if loc.wkt_polygon:
                polygon_preview = (
                    loc.wkt_polygon[:80] + "..." if len(loc.wkt_polygon) > 80 else loc.wkt_polygon
                )
                print(f"    WKT: {polygon_preview}")
            if loc.error_message:
                print(f"    Error: {loc.error_message}")
            print()
    else:
        print("  (No valid location polygons resolved)")

    print("\n📜 GENERATED WKT INSTRUCTION FOR QUERY BUILDER:")
    print("-" * 60)
    print(result.location_wkt_instruction if result.location_wkt_instruction else "(Empty instruction)")
    print("=" * 80)


async def interactive_loop():
    print("🌍 Location Extractor Interactive CLI")
    print("Type a query (or 'exit' / 'q' to quit):\n")
    while True:
        try:
            query = input("Query > ").strip()
            if not query:
                continue
            if query.lower() in ("exit", "q", "quit"):
                break
            await run_extraction(query)
            print()
        except (KeyboardInterrupt, EOFError):
            break


def main():
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        asyncio.run(run_extraction(query))
    else:
        asyncio.run(interactive_loop())


if __name__ == "__main__":
    main()
