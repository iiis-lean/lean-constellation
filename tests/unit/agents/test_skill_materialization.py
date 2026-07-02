from __future__ import annotations

from pathlib import Path
import re

from lean_constellation.agents import build_skill_specs, get_agent_type_spec, materialize_skill_specs
from lean_constellation.tools import build_application_tool_specs, build_submit_tool_specs


_CODE_REF_RE = re.compile(r"`([^`]+)`")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]+$")


def _registered_tool_names() -> set[str]:
    return {spec.name for spec in build_application_tool_specs()} | {spec.name for spec in build_submit_tool_specs()}


def _unknown_tool_refs(text: str) -> list[str]:
    tool_names = _registered_tool_names()
    unknown: list[str] = []
    for ref in _CODE_REF_RE.findall(text):
        if _TOOL_NAME_RE.fullmatch(ref) and "_" in ref and ref not in tool_names:
            unknown.append(ref)
    return sorted(set(unknown))


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


def test_all_skill_bodies_are_english_and_tool_refs_resolve() -> None:
    for key, spec in build_skill_specs().items():
        assert re.search(r"[\u3400-\u9fff]", spec.body) is None, key
        assert not _unknown_tool_refs(spec.body), key
