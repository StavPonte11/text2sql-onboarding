"""
Skills wiring for the Deep Agent.

Skills live on disk inside this package and are exposed to deepagents through a CompositeBackend:

    /skills/<name>/SKILL.md   -> read-only FilesystemBackend over the skills dir
    everything else           -> ephemeral StateBackend (agent scratch files)

Writes to /skills/ are denied via FilesystemPermission, so the agent can read its references
but never modify them. (There is no AGENTS.md memory: always-on guidance lives in the system
prompt, deep_agent/prompts/system.md.)
"""

from dataclasses import dataclass
from pathlib import Path

import yaml
from deepagents import FilesystemPermission
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

from agent.config import settings

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_SKILLS_DIR = PACKAGE_DIR / "skills"

SKILLS_ROUTE = "/skills/"

EXPECTED_SKILLS = frozenset({"temporal-analyst", "spatial-analyst"})


class DeepAgentResourceError(RuntimeError):
    """Skills are missing or malformed; the agent must not start without them."""


@dataclass(frozen=True)
class DeepAgentResources:
    backend: CompositeBackend
    skills: list[str]
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
    expected_skills: frozenset[str] = EXPECTED_SKILLS,
) -> DeepAgentResources:
    """Validate skills on disk and build the backend + create_deep_agent arguments.

    Fails fast (DeepAgentResourceError) instead of letting deepagents silently skip
    missing files.
    """
    skills_dir = Path(skills_dir or settings.DEEP_AGENT_SKILLS_DIR or DEFAULT_SKILLS_DIR).resolve()

    skill_names = discover_skills(skills_dir)
    missing = expected_skills - skill_names
    if missing:
        raise DeepAgentResourceError(f"Missing required skills in {skills_dir}: {sorted(missing)}")

    backend = CompositeBackend(
        default=StateBackend(),
        routes={SKILLS_ROUTE: FilesystemBackend(root_dir=skills_dir, virtual_mode=True)},
    )
    permissions = [FilesystemPermission(operations=["write"], paths=[f"{SKILLS_ROUTE}**"], mode="deny")]
    return DeepAgentResources(
        backend=backend,
        skills=[SKILLS_ROUTE],
        permissions=permissions,
        skill_names=skill_names,
    )
