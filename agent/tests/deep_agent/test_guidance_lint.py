"""
Lint for the Deep Agent's guidance (system prompt, skills, tool docstrings).

Catches drift: references to tools that don't exist, known contradictory/incorrect
statements creeping back, catalog-specific examples in what must be general text-to-SQL
guidance, and skill SQL that isn't valid Trino.
"""

import re

import pytest
import sqlglot

from deep_agent.agent import SYSTEM_PROMPT
from deep_agent.prompt_registry import local_prompt
from deep_agent.resources import DEFAULT_SKILLS_DIR, EXPECTED_SKILLS
from deep_agent.tools import DEEP_AGENT_TOOLS

DOMAIN_TOOLS = {t.name for t in DEEP_AGENT_TOOLS}
HARNESS_TOOLS = {"task", "write_todos", "read_file", "ls", "submit_answer", "submit_verdict"}

DOCS: dict[str, str] = {
    "system prompt": SYSTEM_PROMPT,
    "validator prompt": local_prompt("validator"),
    **{f"skill {p.parent.name}": p.read_text(encoding="utf-8") for p in sorted(DEFAULT_SKILLS_DIR.glob("*/SKILL.md"))},
    **{f"docstring {t.name}": t.description for t in DEEP_AGENT_TOOLS},
}

# Backticked identifiers that look like tool names (verb_object).
_TOOL_REF = re.compile(r"`((?:get|search|execute|validate|enrich|resolve)_[a-z_]+)`")

# Statements that were wrong, contradictory or removed, and must not come back.
BANNED = [
    (r"Every categorical filter value must be verified — use `search_column_values`", "categorical values are not indexed"),
    (r"`categorical` — small finite value set; use search_column_values", "categorical values are not indexed"),
    (r"Use `search_column_values` with the user's term", "only for large_categorical / large_unit_id"),
    (r"`search_column_values` on any string filter", "only for large_categorical / large_unit_id"),
    (r"Using `NOW\(\)` — Trino uses", "now() is valid Trino"),
    (r"Using `DATE_FORMAT` — Trino uses", "date_format() is valid Trino"),
    (r'"השבוע".*date_trunc\(\'week\'', "Trino weeks start Monday; Israeli weeks start Sunday"),
    (r"returns a `POINT", "resolve_location_to_wkt returns area geometries only"),
    (r"@location_name_wkt@|@[a-z_]+_wkt@", "WKT placeholders are a legacy-graph mechanism"),
    (r"Call (it|this) before every `execute_trino_query`|Always call before `execute_trino_query`", "validation is optional (#42)"),
    (r"NEVER use Hebrew as SQL identifiers", "Hebrew catalog columns are valid (double-quoted)"),
    (r"jeen_catalog \(str\)", "enrich_sql_filters takes only sql (#36)"),
    (r"prefix_pattern", "search_column_values returns all matches; no prefix guessing (#45)"),
    (r"sql_literal", "resolve_location_to_wkt returns data.wkt (#45)"),
    (r"error\.category|error\.hint|error_category", "SQL errors carry Trino's own error.name / error.type (#39)"),
    (r"validate_sql_syntax", "the tool is validate_sql (#39)"),
    (r"needs_clarification", "clarifications are off by default; only the flag adds them to the prompt (#41)"),
    (r"AGENTS\.md|agent_memory", "there is no AGENTS.md memory; always-on guidance is the system prompt (#40)"),
    (r"prefix pattern", "use every matching stored value, never a guessed prefix (#45)"),
]

# Catalog-specific names: guidance must stay general text-to-SQL knowledge, not one deployment's data.
CATALOG_SPECIFIC = re.compile(
    r"flights?_table|flight_alerts|flight_id|city_info|launch_country|mahoz|Tel Aviv \d|'France'|\"postgres\"\.\"public\"",
    re.IGNORECASE,
)


@pytest.mark.parametrize("doc", DOCS, ids=list(DOCS))
def test_referenced_tools_exist(doc):
    unknown = set(_TOOL_REF.findall(DOCS[doc])) - DOMAIN_TOOLS - HARNESS_TOOLS
    assert not unknown, f"{doc} references unknown tools: {sorted(unknown)}"


@pytest.mark.parametrize("pattern, reason", BANNED, ids=[r for _, r in BANNED])
def test_banned_statements_are_absent(pattern, reason):
    hits = [name for name, text in DOCS.items() if re.search(pattern, text)]
    assert not hits, f"{reason}: found in {hits}"


@pytest.mark.parametrize("doc", DOCS, ids=list(DOCS))
def test_no_catalog_specific_examples(doc):
    hits = sorted(set(CATALOG_SPECIFIC.findall(DOCS[doc])))
    assert not hits, f"{doc} contains catalog-specific examples: {hits}"


def test_value_resolution_policy_has_one_owner():
    owners = [name for name, text in DOCS.items() if "the only column types it indexes" in text]
    assert owners == ["system prompt"]


def test_system_prompt_names_every_skill():
    for skill in EXPECTED_SKILLS:
        assert f"`{skill}`" in SYSTEM_PROMPT


def _sql_blocks(name: str, text: str) -> list[tuple[str, str]]:
    blocks = []
    for i, block in enumerate(re.findall(r"```sql\n(.*?)```", text, re.DOTALL)):
        # Split into statements on blank lines; keep complete SELECT / WITH statements only.
        for j, chunk in enumerate(re.split(r"\n\s*\n", block)):
            body = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
            if re.match(r"(?is)^(SELECT|WITH)\b", body):
                blocks.append((f"{name}#{i}.{j}", body))
    return blocks


SQL_BLOCKS = [b for name, text in DOCS.items() if not name.startswith("docstring") for b in _sql_blocks(name, text)]


def test_guidance_contains_sql_examples():
    # The parse check below must not pass vacuously: the system prompt and every skill carry checked SQL.
    sources = {name.split("#")[0] for name, _ in SQL_BLOCKS}
    assert {"system prompt", *(f"skill {s}" for s in EXPECTED_SKILLS)} <= sources


@pytest.mark.parametrize("name, sql", SQL_BLOCKS, ids=[n for n, _ in SQL_BLOCKS])
def test_guidance_sql_parses_as_trino(name, sql):
    # Template placeholders like <filter> stand for a boolean expression.
    sql = re.sub(r"<[A-Za-z_][\w ]*>", "1 = 1", sql)  # placeholders only — not `a <= x AND b >= y`
    sqlglot.parse_one(sql, read="trino")
