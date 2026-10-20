import os
import shutil

import pytest
from langchain_core.messages import AIMessage

from deep_agent.agent import create_deep_agent
from deep_agent.resources import (
    DEFAULT_MEMORY_DIR,
    DEFAULT_SKILLS_DIR,
    EXPECTED_SKILLS,
    DeepAgentResourceError,
    _read_frontmatter,
    build_resources,
    discover_skills,
)
from tests.deep_agent.conftest import system_text


def test_packaged_skills_and_memory_are_valid():
    resources = build_resources()
    assert resources.skill_names == EXPECTED_SKILLS
    assert resources.skills == ["/skills/"]
    assert resources.memory == ["/memory/AGENTS.md"]


def test_no_user_specific_paths_in_deep_agent_sources():
    for path in DEFAULT_SKILLS_DIR.parent.rglob("*.py"):
        assert "/Users/" not in path.read_text(encoding="utf-8"), path


def test_resources_resolve_independently_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert build_resources().skill_names == EXPECTED_SKILLS


@pytest.fixture
def resource_copy(tmp_path):
    skills = tmp_path / "skills"
    memory = tmp_path / "memory"
    shutil.copytree(DEFAULT_SKILLS_DIR, skills)
    shutil.copytree(DEFAULT_MEMORY_DIR, memory)
    return skills, memory


def test_missing_skills_dir_fails_fast(tmp_path):
    with pytest.raises(DeepAgentResourceError, match="Skills directory not found"):
        build_resources(skills_dir=tmp_path / "nope", memory_dir=DEFAULT_MEMORY_DIR)


def test_missing_required_skill_fails_fast(resource_copy):
    skills, memory = resource_copy
    shutil.rmtree(skills / "temporal-analyst")
    with pytest.raises(DeepAgentResourceError, match="temporal-analyst"):
        build_resources(skills_dir=skills, memory_dir=memory)


def test_empty_memory_fails_fast(resource_copy):
    skills, memory = resource_copy
    (memory / "AGENTS.md").write_text("  \n", encoding="utf-8")
    with pytest.raises(DeepAgentResourceError, match="Memory file missing or empty"):
        build_resources(skills_dir=skills, memory_dir=memory)


def test_skill_without_frontmatter_fails_fast(resource_copy):
    skills, _ = resource_copy
    (skills / "trino-expert" / "SKILL.md").write_text("# no frontmatter", encoding="utf-8")
    with pytest.raises(DeepAgentResourceError, match="frontmatter"):
        discover_skills(skills)


def test_skill_name_must_match_directory(resource_copy):
    skills, _ = resource_copy
    os.rename(skills / "trino-expert", skills / "trino")
    with pytest.raises(DeepAgentResourceError, match="must match its directory"):
        discover_skills(skills)


@pytest.mark.asyncio
async def test_skills_and_memory_reach_the_model_prompt(scripted_model):
    model = scripted_model(AIMessage("done"))
    agent = create_deep_agent(model=model)
    await agent.ainvoke({"messages": [("user", "hi")]}, {"configurable": {"thread_id": "t-prompt"}})

    prompt = system_text(model.seen[0])
    # Skill *names* also appear in source labels even when nothing loads, so assert on the
    # frontmatter descriptions, which are only rendered for skills that were actually loaded.
    assert "No skills available" not in prompt
    for skill_md in sorted(DEFAULT_SKILLS_DIR.glob("*/SKILL.md")):
        description = _read_frontmatter(skill_md)["description"]
        assert description[:60] in prompt, f"skill {skill_md.parent.name} not loaded"
    assert "Deep SQL Analyst — Core Memory" in prompt, "AGENTS.md memory not injected"


@pytest.mark.asyncio
async def test_agent_cannot_write_skills_or_memory(scripted_model):
    write = AIMessage("", tool_calls=[
        {"name": "write_file", "args": {"file_path": "/skills/evil/SKILL.md", "content": "x"}, "id": "w1"},
        {"name": "edit_file", "args": {"file_path": "/memory/AGENTS.md", "old_string": "Deep", "new_string": "Shallow"}, "id": "w2"},
    ])
    model = scripted_model(write, AIMessage("done"))
    agent = create_deep_agent(model=model)
    result = await agent.ainvoke({"messages": [("user", "hi")]}, {"configurable": {"thread_id": "t-write"}})

    tool_results = [m for m in result["messages"] if m.type == "tool"]
    assert len(tool_results) == 2
    for msg in tool_results:
        assert "denied" in msg.text.lower() or "permission" in msg.text.lower(), msg.text
    assert not (DEFAULT_SKILLS_DIR / "evil").exists()
    assert "Deep SQL Analyst" in (DEFAULT_MEMORY_DIR / "AGENTS.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_can_read_a_skill_file(scripted_model):
    read = AIMessage("", tool_calls=[
        {"name": "read_file", "args": {"file_path": "/skills/trino-expert/SKILL.md"}, "id": "r1"},
    ])
    model = scripted_model(read, AIMessage("done"))
    agent = create_deep_agent(model=model)
    result = await agent.ainvoke({"messages": [("user", "hi")]}, {"configurable": {"thread_id": "t-read"}})

    tool_msg = next(m for m in result["messages"] if m.type == "tool")
    assert "Trino SQL Expert Reference" in tool_msg.text
