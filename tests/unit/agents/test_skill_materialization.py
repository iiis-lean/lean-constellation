from __future__ import annotations

from pathlib import Path

from lean_constellation.agents import build_skill_specs, get_agent_type_spec, materialize_skill_specs


def test_skill_registry_builds_all_fixed_skills() -> None:
    specs = build_skill_specs()

    assert "material-acquisition" in specs
    assert "resource-request-handling" in specs
    assert "lean-proof-formalization" in specs
    assert specs["material-acquisition"].description
    assert "## Workflow" in specs["material-acquisition"].body


def test_skill_materialization_writes_skill_md_and_references(tmp_path: Path) -> None:
    paths = materialize_skill_specs(
        tmp_path,
        ["material-acquisition", "resource-request-handling"],
    )

    material_skill = paths["material-acquisition"]
    request_skill = paths["resource-request-handling"]

    assert (material_skill / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert 'name: "material-acquisition"' in (material_skill / "SKILL.md").read_text(encoding="utf-8")
    assert "None required beyond the Agent-specific submit workflow." in (
        material_skill / "references" / "tool_groups.md"
    ).read_text(encoding="utf-8")
    assert "resource_request_submit" in (request_skill / "references" / "tool_groups.md").read_text(encoding="utf-8")


def test_multiple_agent_types_reuse_same_skill_spec() -> None:
    coordinator = get_agent_type_spec("CoordinatorAgent")
    plan = get_agent_type_spec("ContentPlanAgent")

    assert "resource-request-handling" in coordinator.skill_keys
    assert "resource-request-handling" in plan.skill_keys
