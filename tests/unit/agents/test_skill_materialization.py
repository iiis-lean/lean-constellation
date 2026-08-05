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

    assert "material-acquisition" not in specs
    assert "source-material-acquisition" in specs
    assert "resource-material-acquisition" in specs
    assert "resource-request-submission" in specs
    assert "resource-result-closeout" in specs
    assert "resource-request-handling" not in specs
    assert "lean-proof-formalization" in specs
    assert specs["source-material-acquisition"].description
    assert "## Workflow" in specs["source-material-acquisition"].body


def test_skill_materialization_writes_skill_md_without_registry_references(tmp_path: Path) -> None:
    paths = materialize_skill_specs(
        tmp_path,
        ["source-material-acquisition", "resource-request-submission"],
    )

    material_skill = paths["source-material-acquisition"]
    request_skill = paths["resource-request-submission"]

    assert (material_skill / "SKILL.md").read_text(encoding="utf-8").startswith("---")
    assert 'name: "source-material-acquisition"' in (material_skill / "SKILL.md").read_text(encoding="utf-8")
    assert not (material_skill / "references" / "tool_groups.md").exists()
    assert not (request_skill / "references" / "tool_groups.md").exists()


def test_multiple_agent_types_reuse_same_skill_spec() -> None:
    coordinator = get_agent_type_spec("CoordinatorAgent")
    plan = get_agent_type_spec("ContentPlanAgent")

    assert "resource-request-submission" in coordinator.skill_keys
    assert "resource-result-closeout" in coordinator.skill_keys
    assert "coordinator-provider-dependency-lifecycle" in coordinator.skill_keys
    assert "coordinator-requirement-result-closeout" in coordinator.skill_keys
    assert "resource-request-submission" in plan.skill_keys
    assert "resource-result-closeout" in plan.skill_keys


def test_shared_resource_request_skill_does_not_reference_content_plan_only_attachment_tool() -> None:
    specs = build_skill_specs()
    request_body = specs["resource-request-submission"].body
    closeout_body = specs["resource-result-closeout"].body

    assert "add_current_material_ref" not in request_body
    assert "add_current_material_ref" not in closeout_body
    assert "role-appropriate semantic" in closeout_body


def test_coordinator_repo_requirement_skill_defines_independent_repo_naming() -> None:
    body = build_skill_specs()["coordinator-provider-dependency-lifecycle"].body

    assert "attach_ready_workspace_repo_dependency" in body
    assert "submit_repo_requirement" in body
    assert "get_current_repo_requirement" in body
    assert "attach_requirement_provider_dependency" not in body
    assert "requirement resume gate" in body
    assert "lower_snake_case" not in body


def test_alignment_related_shared_skill_tool_refs_are_visible_to_all_users() -> None:
    specs = build_skill_specs()
    registered = _registered_tool_names()
    reports = build_agent_surface_reports()
    skill_keys = {
        "content-contract-reading",
        "decl-dependency-origin-curation",
        "decl-owned-lean-file-capture-check",
        "external-resource-discovery",
        "mathlib-index-first-recon",
        "mathlib-semantic-search-navigation",
        "resource-request-submission",
        "resource-result-closeout",
        "scope-export-interface-curation",
        "visible-node-dependency-recon",
    }

    for skill_key in skill_keys:
        users = [agent_spec for agent_spec in build_agent_type_specs() if skill_key in agent_spec.skill_keys]
        if len(users) < 2:
            continue
        refs = _tool_refs(specs[skill_key].body) & registered
        for agent_spec in users:
            report = reports[agent_spec.agent_type]
            visible = {tool.name for tool in report.application_tools} | {tool.name for tool in report.submit_tools}
            assert refs <= visible, f"{skill_key}: {report.agent_type}"


def test_coordinator_skill_inventory_and_workflow_boundaries() -> None:
    coordinator = get_agent_type_spec("CoordinatorAgent")
    specs = build_skill_specs()

    assert len(coordinator.skill_keys) == 19
    for removed in (
        "coordinator-content-task-lifecycle",
        "coordinator-repo-requirement-lifecycle",
        "resource-request-handling",
    ):
        assert removed not in specs
        assert removed not in coordinator.skill_keys

    for key in (
        "coordinator-content-result-closeout",
        "resource-result-closeout",
        "coordinator-requirement-result-closeout",
    ):
        body = specs[key].body
        assert "Postcondition" in body
        assert "next-action loop" in body

    for key in (
        "coordinator-dependency-readiness",
        "coordinator-node-decomposition",
        "coordinator-scope-lifecycle",
        "coordinator-content-task-dispatch",
        "resource-request-submission",
        "coordinator-provider-dependency-lifecycle",
        "coordinator-repo-ready-lifecycle",
    ):
        body = specs[key].body
        assert "Postcondition" in body or "Postconditions" in body


def test_coordinator_exploration_skill_describes_only_current_workflow() -> None:
    body = build_skill_specs()["coordinator-repo-exploration"].body

    assert "Flow-owned initial exploration batch" in body
    assert "consume all resource, Lean-provider, and Mathlib outcomes" in body
    assert "retain useful findings even when another category was incomplete" in body
    assert "Do not submit another exploration batch" in body
    assert "During later work" in body
    for migration_term in ("retroactive", "restored mature", "capability became available"):
        assert migration_term not in body


def test_coordinator_skill_metadata_is_discoverable_and_compact() -> None:
    coordinator = get_agent_type_spec("CoordinatorAgent")
    specs = build_skill_specs()

    for key in coordinator.skill_keys:
        spec = specs[key]
        assert spec.name == key
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", spec.name)
        assert spec.description.startswith(("Use when", "Use after", "Use inside"))
        assert len(spec.body.splitlines()) < 500
        assert "creates a new AgentStep" not in spec.body
        assert "starts a new Flow" not in spec.body


def test_external_resource_discovery_matches_arxiv_only_tool_capability() -> None:
    spec = build_skill_specs()["external-resource-discovery"]

    combined = f"{spec.description}\n{spec.body}".lower()
    assert "search_arxiv_theorems" in spec.body
    assert "generic web" not in combined
    assert "local-file discovery" not in combined


def test_visible_node_dependency_recon_skill_spells_out_dependency_evidence_policy() -> None:
    body = build_skill_specs()["visible-node-dependency-recon"].body

    assert "expected_public_decl_names" in body
    assert "remove_current_node_dep" in body
    assert "worker-owned dependencies that are clearly stale" in body
    assert "unresolved_within_visible_boundaries" in body
    assert "structured evidence" in body


def test_source_and_resource_acquisition_skills_reference_visible_tools() -> None:
    specs = build_skill_specs()
    reports = build_agent_surface_reports()

    source_refs = _tool_refs(specs["source-material-acquisition"].body)
    source_visible = {tool.name for tool in reports["SourceCorpusPrepareAgent"].application_tools}
    assert source_refs <= source_visible
    assert "acquire_resource_material" not in specs["source-material-acquisition"].body

    resource_refs = _tool_refs(specs["resource-material-acquisition"].body)
    resource_visible = {tool.name for tool in reports["ResourceCuratorAgent"].application_tools}
    assert resource_refs <= resource_visible
    assert "acquire_source_material" not in specs["resource-material-acquisition"].body


def test_source_corpus_preparation_skill_preserves_supplied_formal_material() -> None:
    body = build_skill_specs()["source-corpus-faithful-preparation"].body

    assert "Preserve supplied Lean specifications, formal targets, solutions, and proof references" in body
    assert "Do not invent targets, answers, proofs, expected node trees" in body
    assert "Do not create generated summaries" not in body


def test_resource_draft_skill_keeps_requested_use_advisory() -> None:
    body = build_skill_specs()["resource-draft-curation"].body

    assert "formal_dependency as strong provider evidence rather than an irreversible classification" in body
    assert "record the corrected ownership in the README and submission" in body
    assert "Do not submit a local Resource when" not in body


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
    assert "discard_decl_round_draft" in round_planning
    assert "submit_current_decl_round" in round_planning
    assert "anticipated_statement_dep_names" in round_planning
    assert "Do not round or pad its end line" in round_planning
    assert "split provider before consumer" in round_planning

    closeout = specs["decl-round-closeout"].body
    assert "write_decl_change_summary" in closeout
    assert "write_decl_round_summary" in closeout
    assert "mark_decl_round_terminal" in closeout

    completion = specs["content-node-completion-decision"].body
    assert "bind_current_node_interface" in completion
    assert "This is ContentPlan closeout work" in completion
    assert "check_current_content_node_completion" in completion
    assert "submit_content_node_ready" in completion
    assert "submit_content_node_blocked" in completion
    assert "submit_content_node_failed" in completion

    completion_policy = specs["content-plan-completion-policy"].body
    assert "interface_declared:" in completion_policy
    assert "graph_declared:" in completion_policy
    assert "graph_proved:" in completion_policy
    assert "Do not plan proof-only" in completion_policy
    assert "graph_proved: build bottom-up" in completion_policy


def test_decl_stage_common_skills_keep_stage_specific_tools_out_of_shared_skill() -> None:
    specs = build_skill_specs()

    shared = specs["decl-owned-lean-file-capture-check"].body
    assert "prepare_statement_formal_file" not in shared
    assert "prepare_proof_formal_file" not in shared
    assert "check_statement_formal_policy" not in shared
    assert "check_proof_formal_policy" not in shared
    assert "capture_statement_formal_file" not in shared
    assert "capture_proof_formal_file" not in shared
    assert "uncaptured working-file edits" in shared

    statement = specs["lean-statement-formalization"].body
    assert "prepare_statement_formal_file" in statement
    assert "check_statement_formal_policy" not in statement
    assert "capture_statement_formal_file" in statement
    assert "check_formal_stage_consistency" in statement
    assert "write_statement_formal_deps" not in statement
    assert "add_statement_mathlib_dependency" in statement
    assert "add_current_mathlib_hints" in statement
    assert "add_current_node_dep" in statement
    assert "prepare_proof_formal_file" not in statement
    assert "check_proof_formal_policy" not in statement
    assert "capture_proof_formal_file" not in statement

    proof = specs["lean-proof-formalization"].body
    assert "prepare_proof_formal_file" in proof
    assert "check_proof_formal_policy" in proof
    assert "capture_proof_formal_file" in proof
    assert "reread_required=true" in proof
    assert "is not a blocker" in proof
    assert "same AgentStep" in proof
    assert "prepare_statement_formal_file" not in proof
    assert "check_statement_formal_policy" not in proof
    assert "capture_statement_formal_file" not in proof


def test_decl_dependency_origin_curation_spells_out_stability_rules() -> None:
    body = build_skill_specs()["decl-dependency-origin-curation"].body

    assert "stable evidence" in body
    assert "not a stable origin" in body
    assert "external theorem discovery only as discovery" in body
    assert "statement dependencies and proof dependencies separate" in body
    assert "unfinished same-round declarations" in body
    assert "blocked needs" in body
    assert "unresolved consumer-side goal or formal shape" in body
    assert "precise semantic or signature mismatch" in body
    assert "must not design the final public theorem" in body


def test_content_blocker_and_dependency_planning_skills_preserve_consumer_semantics() -> None:
    specs = build_skill_specs()

    contract_reading = specs["content-contract-reading"].body
    assert "statement hint as contract guidance" in contract_reading
    assert "not an exact header" in contract_reading
    assert "infer a special node category" in contract_reading

    contract_design = specs["node-contract-design"].body
    assert "consumer declaration or revision" in contract_design
    assert "expected input/output shape" in contract_design
    assert "minimal consumer-side Lean snippet" in contract_design

    closeout = specs["decl-round-closeout"].body
    assert "unresolved consumer-side local goal or formal shape" in closeout
    assert "conditions that must hold before retrying a parent declaration" in closeout
    assert '"needs a helper"' in closeout

    completion = specs["content-node-completion-decision"].body
    assert "## Interface Semantic Fit" in completion
    assert "structured multiline reason" in completion
    assert "checked Mathlib/current-node/provider declarations" in closeout
    assert "do not create or decide the repository node tree" in completion

    result_closeout = specs["coordinator-content-result-closeout"].body
    assert "private consumer inspection is mandatory" in result_closeout
    assert "blocked reason is an index into authoritative truth" in result_closeout
    assert "actual bound public declaration" in result_closeout
    assert "does not establish consumer applicability merely by name" in result_closeout

    decomposition = specs["coordinator-node-decomposition"].body
    for expected in (
        "continue the current Content node",
        "reuse an existing Content node",
        "create a new ordinary Content node",
        "repair an existing interface",
    ):
        assert expected in decomposition

    readiness = specs["coordinator-dependency-readiness"].body
    assert "derive the Source-visible dependency frontier" in readiness
    assert "assumptions, parameter and index representation, and conclusion direction" in readiness

    dispatch = specs["coordinator-content-task-dispatch"].body
    assert "authoritative private consumer" in dispatch
    assert "A vague request" in dispatch

    strategy = specs["decl-strategy-planning"].body
    round_planning = specs["decl-round-change-planning"].body
    assert "consumer-side shape" in strategy
    assert "consumer-side Lean shape" in round_planning
    assert "Do not grow the round or strategy indefinitely" in round_planning

    native_text = "\n".join(
        specs[key].body
        for key in (
            "content-contract-reading",
            "node-contract-design",
            "coordinator-node-decomposition",
            "coordinator-content-result-closeout",
            "coordinator-dependency-readiness",
            "coordinator-content-task-dispatch",
            "decl-strategy-planning",
            "decl-round-change-planning",
            "decl-round-closeout",
            "content-node-completion-decision",
            "decl-dependency-origin-curation",
        )
    )
    for forbidden in (
        "support node",
        "adapter node",
        "support node ready",
        "expected_statement_lean_code",
        "NodeKind.SUPPORT",
        "NodeKind.ADAPTER",
    ):
        assert forbidden not in native_text


def test_coordinator_completion_policy_spells_out_node_tree_policy() -> None:
    specs = build_skill_specs()

    completion_policy = specs["coordinator-completion-policy"].body
    assert "interface_declared:" in completion_policy
    assert "graph_declared:" in completion_policy
    assert "graph_proved:" in completion_policy
    assert "smallest stable node subtree" in completion_policy
    assert "complete declaration\n  graph" in completion_policy
    assert "complete proof graph" in completion_policy


def test_bottom_up_completion_and_lean_gap_policy_has_single_owned_decisions() -> None:
    specs = build_skill_specs()
    coordinator_policy = specs["coordinator-completion-policy"].body
    readiness = specs["coordinator-dependency-readiness"].body
    content_policy = specs["content-plan-completion-policy"].body
    strategy = specs["decl-strategy-planning"].body
    round_planning = specs["decl-round-change-planning"].body
    closeout = specs["decl-round-closeout"].body
    decomposition = specs["coordinator-node-decomposition"].body

    assert "Source-visible dependency frontier" in coordinator_policy
    assert "ready to the required state, visible through the consumer boundary" in readiness
    assert "do not over-raise the target to proved" in readiness
    assert "declared parent may be an intentional intermediate round" in content_policy
    assert "terminal Content result" in content_policy

    for expected in (
        "If Mathlib already supplies",
        "current repository node or attached provider already supplies",
        "Keep a small helper in this Content node",
        "Report blocked for Coordinator ownership",
        "No declaration-count cutoff",
        "provider-before-consumer rounds",
    ):
        assert expected in strategy
    assert "layered definitions or lemmas" in strategy
    assert "new Source/Resource/provider responsibility" in strategy
    assert "core representation, index discipline, typeclass design, or proof architecture" in strategy
    assert "follow the strategy's Lean-emergent gap classification" in round_planning

    for expected in (
        "affected declaration and revision",
        "checked Mathlib/current-node/provider declarations",
        "dependency frontier and consumers",
        "why the gap appears local or package-shaped",
        "recommended repair, new-node, or provider branch",
    ):
        assert expected in closeout

    assert "Declaration count is context, never a mechanical split threshold" in decomposition
    assert "layered definitions or lemmas" in decomposition
    assert strategy.count("Keep a small helper in this Content node") == 1
    assert all(
        "Keep a small helper in this Content node" not in specs[key].body
        for key in specs
        if key != "decl-strategy-planning"
    )
