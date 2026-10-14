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

# Records that carry code or privilege: a change to one is never covered by an auto-approve mode,
# and the sidebar shows its full content. A client or server script runs for every user, a webhook
# sends data out, a role or permission changes who can do what.
ALWAYS_ASK_DOCTYPES = frozenset(
    {
        "Client Script",
        "Server Script",
        "Webhook",
        "Scheduled Job Type",
        "Custom HTML Block",
        "Website Theme",
        "Website Script",
        "User",
        "Role",
        "Role Profile",
        "User Permission",
        "DocPerm",
        "Custom DocPerm",
        "Workflow",
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
    # Writes this skill is the instruction for: a record type ("Print Format") or a tool and a
    # record type ("create_document:User"). A write the model makes without having read it is
    # sent back with the skill.
    governs: tuple[str, ...] = ()


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


def _parse_records(block: str) -> dict[str, list[str]]:
    """The `records:` comment (`DocType:` then `- field` lines) as {doctype: [fields]}."""
    records: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.startswith((" ", "\t")):
            if current is not None:
                current.append(line.strip().removeprefix("-").strip())
        else:
            current = records.setdefault(line.strip().rstrip(":").strip(), [])
    return records


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
    # the records comment is for the field check in CI; the model has no use for it
    records_match = RECORDS_COMMENT_PATTERN.search(body)
    records = _parse_records(records_match.group(1)) if records_match else {}
    if records_match:
        body = (body[: records_match.start()] + body[records_match.end() :]).strip()
    governs = tuple(g.strip() for g in fm.get("governs", "").split(",") if g.strip())
    return Skill(
        name=name,
        description=desc,
        risk=risk,
        content=body,
        source=source,
        records=records,
        governs=governs,
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
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("failed_to_load_skill", skill=item.name, error=str(exc))
    return skills


def build_skills_catalog(skills: dict[str, Skill]) -> str:
    """Render a compact catalog of available skills for the system prompt."""
    if not skills:
        return ""
    lines = [
        "# Assistant Skills",
        "",
        "When a user's request matches one of these specialized skills, call load_skill to read "
        "its full instructions before taking action:",
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
            except Exception as exc:  # noqa: BLE001 - a site's skill failing must not fail the turn
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


def skill_for_write(skills: dict[str, Skill], tool: str, args: dict[str, Any]) -> Skill | None:
    """The skill that governs this call, if there is one: by record type, or by report name."""
    target = str(args.get("doctype") or args.get("report_name") or "").strip()
    if not target:
        return None
    for skill in skills.values():
        if target in skill.governs or f"{tool}:{target}" in skill.governs:
            return skill
    return None
