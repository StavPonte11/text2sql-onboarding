"""
Skills and memory (AGENTS.md) wiring for the Deep Agent.

Skills and memory live on disk inside this package and are exposed to deepagents
through a CompositeBackend:

    /skills/<name>/SKILL.md   -> read-only FilesystemBackend over the skills dir
    /memory/AGENTS.md         -> read-only FilesystemBackend over the memory dir
    everything else           -> ephemeral StateBackend (agent scratch files)

Writes to /skills/ and /memory/ are denied via FilesystemPermission, so the agent
can read its references but never modify them.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml
from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

from agent.config import settings

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_SKILLS_DIR = PACKAGE_DIR / "skills"
DEFAULT_MEMORY_DIR = PACKAGE_DIR / "memory"

SKILLS_ROUTE = "/skills/"
MEMORY_ROUTE = "/memory/"
MEMORY_FILE = "AGENTS.md"

EXPECTED_SKILLS = frozenset({"trino-expert", "temporal-analyst", "spatial-analyst", "semantic-validation"})


class DeepAgentResourceError(RuntimeError):
    """Skills or memory are missing or malformed; the agent must not start without them."""


@dataclass(frozen=True)
class DeepAgentResources:
    backend: CompositeBackend
    skills: list[str]
    memory: list[str]
    permissions: list[FilesystemPermission]
    skill_names: frozenset[str]


def _read_frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise DeepAgentResourceError(f"{skill_md}: missing YAML frontmatter")
    try:
        _, frontmatter, _ = text.split("---", 2)
        data = yaml.safe_load(frontmatter)
    except (ValueError, yaml.YAMLError) as exc:
        raise DeepAgentResourceError(f"{skill_md}: invalid YAML frontmatter ({exc})") from exc
    if not isinstance(data, dict) or not data.get("name") or not data.get("description"):
        raise DeepAgentResourceError(f"{skill_md}: frontmatter needs 'name' and 'description'")
    return data


def discover_skills(skills_dir: Path) -> frozenset[str]:
    """Validate the skills layout (<dir>/<name>/SKILL.md) and return the skill names."""
    if not skills_dir.is_dir():
        raise DeepAgentResourceError(f"Skills directory not found: {skills_dir}")

    names = set()
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name = _read_frontmatter(skill_md)["name"]
        if name != skill_md.parent.name:
            raise DeepAgentResourceError(
                f"{skill_md}: frontmatter name '{name}' must match its directory '{skill_md.parent.name}'"
            )
        names.add(name)

    if not names:
        raise DeepAgentResourceError(f"No skills found under {skills_dir} (expected <name>/SKILL.md)")
    return frozenset(names)


def build_resources(
    skills_dir: Path | None = None,
    memory_dir: Path | None = None,
    expected_skills: frozenset[str] = EXPECTED_SKILLS,
) -> DeepAgentResources:
    """Validate skills/memory on disk and build the backend + create_deep_agent arguments.

    Fails fast (DeepAgentResourceError) instead of letting deepagents silently skip
    missing files.
    """
    skills_dir = Path(skills_dir or settings.DEEP_AGENT_SKILLS_DIR or DEFAULT_SKILLS_DIR).resolve()
    memory_dir = Path(memory_dir or settings.DEEP_AGENT_MEMORY_DIR or DEFAULT_MEMORY_DIR).resolve()

    skill_names = discover_skills(skills_dir)
    missing = expected_skills - skill_names
    if missing:
        raise DeepAgentResourceError(f"Missing required skills in {skills_dir}: {sorted(missing)}")

    memory_file = memory_dir / MEMORY_FILE
    if not memory_file.is_file() or not memory_file.read_text(encoding="utf-8").strip():
        raise DeepAgentResourceError(f"Memory file missing or empty: {memory_file}")

    backend = CompositeBackend(
        default=StateBackend(),
        routes={
            SKILLS_ROUTE: FilesystemBackend(root_dir=skills_dir, virtual_mode=True),
            MEMORY_ROUTE: FilesystemBackend(root_dir=memory_dir, virtual_mode=True),
        },
    )
    permissions = [
        FilesystemPermission(
            operations=["write"],
            paths=[f"{SKILLS_ROUTE}**", f"{MEMORY_ROUTE}**"],
            mode="deny",
        ),
    ]
    return DeepAgentResources(
        backend=backend,
        skills=[SKILLS_ROUTE],
        memory=[f"{MEMORY_ROUTE}{MEMORY_FILE}"],
        permissions=permissions,
        skill_names=skill_names,
    )
