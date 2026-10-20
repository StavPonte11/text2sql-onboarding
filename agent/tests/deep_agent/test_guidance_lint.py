"""
Lint for the Deep Agent's guidance (system prompt, AGENTS.md memory, skills, tool docstrings).

Catches the drift found in issue #40: references to tools that don't exist, known
contradictory/incorrect statements creeping back, and skill SQL that isn't valid Trino.
"""

import re
from pathlib import Path

import pytest
import sqlglot

from deep_agent.agent import SYSTEM_PROMPT
from deep_agent.resources import DEFAULT_MEMORY_DIR, DEFAULT_SKILLS_DIR, EXPECTED_SKILLS
from deep_agent.tools import DEEP_AGENT_TOOLS

DOMAIN_TOOLS = {t.name for t in DEEP_AGENT_TOOLS}
HARNESS_TOOLS = {"task", "write_todos", "read_file", "ls", "DeepAgentAnswer"}

DOCS: dict[str, str] = {
    "system prompt": SYSTEM_PROMPT,
    "AGENTS.md": (DEFAULT_MEMORY_DIR / "AGENTS.md").read_text(encoding="utf-8"),
    **{f"skill {p.parent.name}": p.read_text(encoding="utf-8") for p in sorted(DEFAULT_SKILLS_DIR.glob("*/SKILL.md"))},
    **{f"docstring {t.name}": t.description for t in DEEP_AGENT_TOOLS},
}

# Backticked identifiers that look like tool names (verb_object).
_TOOL_REF = re.compile(r"`((?:get|search|execute|validate|enrich|resolve)_[a-z_]+)`")

# Statements that were wrong or contradictory before #40 and must not come back.
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
]


@pytest.mark.parametrize("doc", DOCS, ids=list(DOCS))
def test_referenced_tools_exist(doc):
    unknown = set(_TOOL_REF.findall(DOCS[doc])) - DOMAIN_TOOLS - HARNESS_TOOLS
    assert not unknown, f"{doc} references unknown tools: {sorted(unknown)}"


@pytest.mark.parametrize("pattern, reason", BANNED, ids=[r for _, r in BANNED])
def test_banned_statements_are_absent(pattern, reason):
    hits = [name for name, text in DOCS.items() if re.search(pattern, text)]
    assert not hits, f"{reason}: found in {hits}"


def test_value_resolution_policy_has_one_owner():
    owners = [name for name, text in DOCS.items() if "the only column types it indexes" in text]
    assert owners == ["system prompt"]


def test_system_prompt_names_every_skill():
    for skill in EXPECTED_SKILLS:
        assert f"`{skill}`" in SYSTEM_PROMPT


def _skill_sql_blocks() -> list[tuple[str, str]]:
    blocks = []
    for path in sorted(DEFAULT_SKILLS_DIR.glob("*/SKILL.md")):
        for i, block in enumerate(re.findall(r"```sql\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL)):
            # Split into statements on blank lines; keep complete SELECT / WITH statements only.
            for j, chunk in enumerate(re.split(r"\n\s*\n", block)):
                body = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
                if re.match(r"(?is)^(SELECT|WITH)\b", body):
                    blocks.append((f"{path.parent.name}#{i}.{j}", body))
    return blocks


SQL_BLOCKS = _skill_sql_blocks()


def test_skills_contain_sql_examples():
    assert len(SQL_BLOCKS) >= 15


@pytest.mark.parametrize("name, sql", SQL_BLOCKS, ids=[n for n, _ in SQL_BLOCKS])
def test_skill_sql_parses_as_trino(name, sql):
    # Template placeholders like <filter> stand for a boolean expression.
    sql = re.sub(r"<[^<>]+>", "1 = 1", sql)
    sqlglot.parse_one(sql, read="trino")


def test_guidance_token_budget():
    """System prompt + memory must stay well under the pre-#40 size (4,901 tokens)."""
    import tiktoken

    enc = tiktoken.get_encoding("o200k_base")
    total = len(enc.encode(DOCS["system prompt"])) + len(enc.encode(DOCS["AGENTS.md"]))
    assert total <= 0.7 * 4901, total
