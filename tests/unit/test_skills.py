"""Tests for the Assistant Skills subsystem: loader, catalog, tool, and risk."""

from __future__ import annotations

import pytest

from ai_agent.agent.prompts import build_system_prompt
from ai_agent.agent.skills import (
    LOAD_SKILL_TOOL,
    Skill,
    assess_risk,
    build_skill_tool,
    build_skills_catalog,
    load_bundled_skills,
    parse_skill_text,
)


def test_parse_skill_text_valid() -> None:
    text = (
        "---\n"
        "name: example-skill\n"
        "description: An example skill for testing.\n"
        "risk: read-only\n"
        "---\n\n"
        "# Example Body\n"
        "Instructions here.\n"
    )
    skill = parse_skill_text(text)
    assert skill is not None
    assert skill.name == "example-skill"
    assert skill.description == "An example skill for testing."
    assert skill.risk == "read-only"
    assert "Instructions here." in skill.content


def test_parse_skill_text_invalid() -> None:
    assert parse_skill_text("no frontmatter here") is None
    assert parse_skill_text("---\nonly_key: val\n---\n") is None


def test_load_bundled_skills_loads_all_nine() -> None:
    skills = load_bundled_skills()
    assert len(skills) >= 9
    expected = {
        "client-script",
        "custom-field",
        "letter-head",
        "print-format",
        "system-health",
        "user-access",
        "user-create",
        "user-password-reset",
        "web-research-records",
    }
    assert expected.issubset(set(skills.keys()))
    for name in expected:
        skill = skills[name]
        assert skill.name == name
        assert len(skill.description) > 0
        assert len(skill.content) > 0


def test_build_skills_catalog() -> None:
    skills = {
        "print-format": Skill(
            name="print-format",
            description="Create custom print formats.",
            risk="writes",
            content="print format body",
        ),
        "system-health": Skill(
            name="system-health",
            description="Check system health.",
            risk="read-only",
            content="health body",
        ),
    }
    catalog = build_skills_catalog(skills)
    assert "# Assistant Skills" in catalog
    assert "- print-format: Create custom print formats." in catalog
    assert "- system-health: Check system health." in catalog


def test_build_skills_catalog_empty() -> None:
    assert build_skills_catalog({}) == ""


@pytest.mark.asyncio
async def test_skill_tool_invocations() -> None:
    skills = {
        "print-format": Skill(
            name="print-format",
            description="Create custom print formats.",
            risk="writes",
            content="Jinja print format instructions.",
        )
    }
    tool = build_skill_tool(skills)
    assert tool.name == LOAD_SKILL_TOOL
    assert tool.metadata is not None
    assert tool.metadata.get("readOnlyHint") is True

    # Loading existing skill
    res = await tool.ainvoke({"name": "print-format"})
    assert res == "Jinja print format instructions."

    # Loading non-existent skill
    err = await tool.ainvoke({"name": "unknown-skill"})
    assert "error: skill 'unknown-skill' not found" in err
    assert "available: ['print-format']" in err


@pytest.mark.asyncio
async def test_skill_tool_site_override() -> None:
    shipped = {
        "print-format": Skill(
            name="print-format",
            description="Shipped print format",
            risk="writes",
            content="shipped body",
        )
    }

    async def mock_site_loader(name: str) -> Skill | None:
        if name == "print-format":
            return Skill(
                name="print-format",
                description="Custom site print format",
                risk="writes",
                content="site custom body",
                source="site",
            )
        return None

    tool = build_skill_tool(shipped, fetch_site_skill=mock_site_loader)
    res = await tool.ainvoke({"name": "print-format"})
    assert res == "site custom body"


def test_assess_risk() -> None:
    # Always-ask doctypes
    assert assess_risk("create_document", {"doctype": "Client Script"}) == "always-ask"
    assert assess_risk("update_document", {"doctype": "User"}) == "always-ask"
    assert assess_risk("create_document", {"doctype": "User Permission"}) == "always-ask"
    assert assess_risk("update_document", {"doctype": "Role Profile"}) == "always-ask"

    # Ordinary writes
    assert assess_risk("create_document", {"doctype": "Customer"}) is None
    assert assess_risk("update_document", {"doctype": "Sales Invoice"}) is None


def test_system_prompt_includes_skills_catalog() -> None:
    skills = load_bundled_skills()
    catalog = build_skills_catalog(skills)
    prompt = build_system_prompt({}, skills_catalog=catalog)
    assert "# Assistant Skills" in prompt
    assert "- print-format:" in prompt
    assert "- user-create:" in prompt
