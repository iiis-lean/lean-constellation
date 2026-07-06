from __future__ import annotations

from pathlib import Path
import re

from lean_constellation.agents import build_agent_type_specs, build_skill_specs, get_agent_type_spec, materialize_skill_specs
from lean_constellation.agents.surface import build_agent_surface_reports
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


def _tool_refs(text: str) -> set[str]:
    return {
        ref
        for ref in _CODE_REF_RE.findall(text)
        if _TOOL_NAME_RE.fullmatch(ref) and "_" in ref
    }


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


def test_shared_resource_request_skill_does_not_reference_content_plan_only_attachment_tool() -> None:
    body = build_skill_specs()["resource-request-handling"].body

    assert "add_current_material_ref" not in body
    assert "visible node material or contract tools" in body


def test_selected_shared_skill_tool_refs_are_visible_to_all_users() -> None:
    specs = build_skill_specs()
    registered = _registered_tool_names()
    reports = build_agent_surface_reports()

    for skill_key in ["external-resource-discovery", "mathlib-index-first-recon"]:
        refs = _tool_refs(specs[skill_key].body) & registered
        for agent_spec in build_agent_type_specs():
            if skill_key not in agent_spec.skill_keys:
                continue
            report = reports[agent_spec.agent_type]
            visible = {tool.name for tool in report.application_tools} | {tool.name for tool in report.submit_tools}
            assert refs <= visible, f"{skill_key}: {report.agent_type}"


def test_all_skill_bodies_are_english_and_tool_refs_resolve() -> None:
    for key, spec in build_skill_specs().items():
        assert re.search(r"[\u3400-\u9fff]", spec.body) is None, key
        assert not _unknown_tool_refs(spec.body), key


def test_content_plan_skill_tool_refs_are_visible_to_content_plan() -> None:
    specs = build_skill_specs()
    plan = get_agent_type_spec("ContentPlanAgent")
    report = build_agent_surface_reports()[plan.agent_type]
    visible = {tool.name for tool in report.application_tools} | {tool.name for tool in report.submit_tools}
    registered = _registered_tool_names()

    refs: set[str] = set()
    for skill_key in plan.skill_keys:
        refs |= _tool_refs(specs[str(skill_key)].body) & registered

    assert refs <= visible


def test_content_plan_specialized_skills_spell_out_operational_flow() -> None:
    specs = build_skill_specs()

    prep = specs["content-preparation-orchestration"].body
    assert "submit_content_preparation_recon" in prep
    assert "In one content node task, dispatch each preparation kind at most once" in prep
    assert "Re-read current node truth with `get_current_node_contract`" in prep

    strategy = specs["decl-strategy-planning"].body
    assert "ensure_open_decl_strategy" in strategy
    assert "close_decl_strategy" in strategy
    assert "DeclGraph read tools" in strategy

    round_planning = specs["decl-round-change-planning"].body
    assert "create_decl_round_draft" in round_planning
    assert "plan_create_decl" in round_planning
    assert "plan_update_decl" in round_planning
    assert "preview_decl_delete_closure" in round_planning
    assert "validate_decl_round_draft" in round_planning
    assert "submit_current_decl_round" in round_planning

    closeout = specs["decl-round-closeout"].body
    assert "write_decl_change_summary" in closeout
    assert "write_decl_round_summary" in closeout
    assert "mark_decl_round_terminal" in closeout

    completion = specs["content-node-completion-decision"].body
    assert "check_current_content_node_completion" in completion
    assert "submit_content_node_ready" in completion
    assert "submit_content_node_blocked" in completion
    assert "submit_content_node_failed" in completion

    proved_mode = specs["content-plan-proved-full-graph-mode"].body
    assert "Bottom-up strategy" in proved_mode
    assert "Top-down strategy" in proved_mode
    assert "require_target_state_satisfied=false" in proved_mode

    declared_full_mode = specs["content-plan-declared-full-graph-mode"].body
    assert "Bottom-up skeleton strategy" in declared_full_mode
    assert "Top-down skeleton strategy" in declared_full_mode

    declared_interface_mode = specs["content-plan-declared-interface-mode"].body
    assert "smallest useful declared interface" in declared_interface_mode
    assert "Do not create proof-only hidden helper lemmas" in declared_interface_mode


def test_coordinator_mode_skills_spell_out_node_tree_policy() -> None:
    specs = build_skill_specs()

    proved_mode = specs["coordinator-proved-full-graph-mode"].body
    assert "complete proof-oriented native repository" in proved_mode
    assert "intermediate lemmas" in proved_mode
    assert "expected proof completion" in proved_mode

    declared_full_mode = specs["coordinator-declared-full-graph-mode"].body
    assert "full declaration skeleton" in declared_full_mode
    assert "future proved work" in declared_full_mode

    declared_interface_mode = specs["coordinator-declared-interface-mode"].body
    assert "smallest stable provider boundary" in declared_interface_mode
    assert "Avoid creating proof-only internal lemma nodes" in declared_interface_mode
