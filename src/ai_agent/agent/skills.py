"""Assistant Skills management: loading, catalog formatting, and load_skill tool."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

LOAD_SKILL_TOOL = "load_skill"
READ_ONLY = {"readOnlyHint": True}

# Built-in skills location
_DEFAULT_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
RECORDS_COMMENT_PATTERN = re.compile(r"<!--\s*records:\s*\n(.*?)\s*-->", re.DOTALL)

# Sensitive DocTypes where any modification carries always-ask risk
ALWAYS_ASK_DOCTYPES = frozenset(
    {
        "Client Script",
        "User",
        "Role Profile",
        "User Permission",
        "Custom DocPerm",
    }
)


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    risk: str
    content: str
    source: str = "bundled"
    records: dict[str, list[str]] = field(default_factory=dict)


def _parse_yaml_simple(raw: str) -> dict[str, str]:
    """Basic YAML key-value parser for frontmatter without external dependency."""
    out: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        out[key.strip()] = val.strip().strip("\"'")
    return out


def parse_skill_text(content: str, source: str = "bundled") -> Skill | None:
    """Parse a SKILL.md content into a Skill object."""
    fm_match = FRONTMATTER_PATTERN.match(content)
    if not fm_match:
        return None
    fm = _parse_yaml_simple(fm_match.group(1))
    name = fm.get("name", "").strip()
    desc = fm.get("description", "").strip()
    risk = fm.get("risk", "writes").strip()
    if not name or not desc:
        return None
    body = content[fm_match.end() :].strip()
    return Skill(
        name=name,
        description=desc,
        risk=risk,
        content=body,
        source=source,
    )


def load_bundled_skills(skills_dir: Path | None = None) -> dict[str, Skill]:
    """Scan and load all bundled skills from disk."""
    path = skills_dir or _DEFAULT_SKILLS_DIR
    if not path.is_dir():
        logger.debug("bundled_skills_dir_not_found", path=str(path))
        return {}

    skills: dict[str, Skill] = {}
    for item in sorted(path.iterdir()):
        if not item.is_dir() or item.name.startswith("."):
            continue
        skill_file = item / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            raw = skill_file.read_text(encoding="utf-8")
            skill = parse_skill_text(raw, source="bundled")
            if skill and skill.name == item.name:
                skills[skill.name] = skill
        except Exception as exc:
            logger.warning("failed_to_load_skill", skill=item.name, error=str(exc))
    return skills


def build_skills_catalog(skills: dict[str, Skill]) -> str:
    """Render a compact catalog of available skills for the system prompt."""
    if not skills:
        return ""
    lines = [
        "# Assistant Skills",
        "",
        "When a user's request matches one of these specialized skills, call load_skill to read its full instructions before taking action:",
    ]
    for name in sorted(skills):
        skill = skills[name]
        lines.append(f"- {name}: {skill.description}")
    return "\n".join(lines)


class _LoadSkillArgs(BaseModel):
    name: str = Field(description="The name of the skill to load from the catalog")


def build_skill_tool(
    skills: dict[str, Skill],
    fetch_site_skill: Callable[[str], Awaitable[Skill | None]] | None = None,
) -> BaseTool:
    """Build the load_skill LangChain tool."""

    async def _load_skill(name: str) -> str:
        name = name.strip()
        # 1. Check for site skill override if loader provided
        if fetch_site_skill is not None:
            try:
                site_skill = await fetch_site_skill(name)
                if site_skill is not None:
                    return site_skill.content
            except Exception as exc:
                logger.warning("site_skill_fetch_failed", skill=name, error=str(exc))

        # 2. Shipped skill
        if name in skills:
            return skills[name].content

        return f"error: skill {name!r} not found; available: {sorted(skills)}"

    return StructuredTool(
        name=LOAD_SKILL_TOOL,
        description=(
            "Load the full instructions for a specialized assistant skill by name. "
            "Call this when the user's task matches a skill in the Assistant Skills catalog."
        ),
        args_schema=_LoadSkillArgs,
        coroutine=_load_skill,
        metadata=READ_ONLY,
    )


def assess_risk(name: str, args: dict[str, Any]) -> str | None:
    """Determine risk level of a tool call. Returns 'always-ask' for high-privilege operations."""
    doctype = str(args.get("doctype") or "").strip()
    if doctype in ALWAYS_ASK_DOCTYPES:
        return "always-ask"
    # Also check if target record is Client Script or User via name or dt
    dt = str(args.get("dt") or "").strip()
    if dt in ALWAYS_ASK_DOCTYPES:
        return "always-ask"
    return None
