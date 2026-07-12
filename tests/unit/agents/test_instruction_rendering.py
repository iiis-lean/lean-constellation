from __future__ import annotations

import re

from lean_constellation.agents import build_agent_type_specs, get_agent_type_spec, render_agent_instruction
from lean_constellation.agents.instructions import PUBLIC_INSTRUCTION_FRAGMENTS
from lean_constellation.agents.surface import build_agent_surface_reports
from lean_constellation.tools import build_application_tool_specs, build_submit_tool_specs


_CODE_REF_RE = re.compile(r"`([^`]+)`")
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]+$")


def _registered_tool_names() -> set[str]:
    return {spec.name for spec in build_application_tool_specs()} | {spec.name for spec in build_submit_tool_specs()}


def _unknown_tool_refs(text: str) -> list[str]:
    tool_names = _registered_tool_names()
    return sorted(_tool_refs(text) - tool_names)


def _tool_refs(text: str) -> set[str]:
    unknown: list[str] = []
    for ref in _CODE_REF_RE.findall(text):
        if _TOOL_NAME_RE.fullmatch(ref) and "_" in ref:
            unknown.append(ref)
    return set(unknown)


def test_instruction_renderer_combines_public_and_agent_specific_fragments() -> None:
    text = render_agent_instruction("CoordinatorAgent")

    assert "## Operating Contract" in text
    assert "## Native Repository Coordinator" in text
    assert "get_current_repo_work_config" in text
    assert "Coordinator mode skill matching" in text
    assert "coordinator-node-decomposition" in text
    assert "write DeclGraph artifacts" in text


def test_coordinator_instruction_routes_closeout_then_repeated_next_actions() -> None:
    text = render_agent_instruction("CoordinatorAgent")

    assert "### Stage One: Reconcile The Wake Result" in text
    assert "coordinator-content-result-closeout" in text
    assert "resource-result-closeout" in text
    assert "coordinator-requirement-result-closeout" in text
    assert "### Stage Two: Next-Action Loop" in text
    assert "Repeat the following loop until a submit is accepted" in text
    assert "coordinator-dependency-readiness" in text
    assert "coordinator-node-decomposition" in text
    assert "coordinator-scope-lifecycle" in text
    assert "coordinator-content-task-dispatch" in text
    assert "coordinator-provider-dependency-lifecycle" in text
    assert "coordinator-repo-ready-lifecycle" in text
    assert "The resume gate has already validated and attached" in text
    assert "scope-export-interface-curation" in text
    assert "not a separate runtime action" in text
    assert "If a submit is rejected" in text
    assert "If a submit is accepted, do not make further state-changing calls" in text
    assert "UpperCamelCase" not in text
    assert "lower_snake_case" not in text
    assert "coordinator-content-task-lifecycle" not in text
    assert "coordinator-repo-requirement-lifecycle" not in text
    assert "resource-request-handling" not in text
    assert "attach_requirement_provider_dependency" not in text


def test_coordinator_instruction_lists_exact_normal_submit_boundary() -> None:
    text = render_agent_instruction("CoordinatorAgent")
    expected = {
        "submit_content_node_tasks",
        "submit_resource_request",
        "submit_repo_requirement",
        "submit_repo_ready",
    }

    assert expected <= _tool_refs(text)
    assert "A normal Coordinator AgentStep must eventually produce exactly one accepted submit" in text


def test_shared_resource_skill_routes_are_visible_in_plan_and_recon_instructions() -> None:
    for agent_type in ("ContentPlanAgent", "ResourceReconAgent"):
        text = render_agent_instruction(agent_type)
        assert "resource-request-submission" in text
        assert "resource-result-closeout" in text


def test_common_runtime_contract_is_the_single_truth_and_tool_authority() -> None:
    runtime = PUBLIC_INSTRUCTION_FRAGMENTS["common.runtime_contract"]
    submit = PUBLIC_INSTRUCTION_FRAGMENTS["common.submit_contract"]

    assert "common.truth_and_tool_contract" not in PUBLIC_INSTRUCTION_FRAGMENTS
    assert "structured repository and runtime views" in runtime
    assert "Conversation memory and callback summaries" in runtime
    assert "semantic tools" in runtime
    assert "requested mutation was not accepted" in runtime
    assert "accepted submit" not in runtime.lower()
    assert "blocked result" not in runtime.lower()
    assert "accepted submit" in submit.lower()
    assert "hand control back" in submit.lower()


def test_instruction_renderer_deduplicates_public_fragments() -> None:
    spec = get_agent_type_spec("ContentPlanAgent")
    duplicate = spec.model_copy(
        update={
            "instruction_fragment_keys": [
                "common.runtime_contract",
                *spec.instruction_fragment_keys,
                "common.runtime_contract",
            ]
        }
    )

    text = render_agent_instruction(duplicate)

    assert text.count("## Operating Contract") == 1


def test_all_rendered_instructions_have_unique_nonempty_fragment_headings() -> None:
    for spec in build_agent_type_specs():
        text = render_agent_instruction(spec)
        headings = [line for line in text.splitlines() if line.startswith("## ")]

        assert text.strip(), spec.agent_type
        assert headings[0] == "## Operating Contract", spec.agent_type
        assert headings.count("## Operating Contract") == 1, spec.agent_type
        assert headings.count("## Submit Contract") == 1, spec.agent_type
        assert "## Truth and Tool Contract" not in headings, spec.agent_type
        assert len(headings) == len(set(headings)), spec.agent_type
        assert all(PUBLIC_INSTRUCTION_FRAGMENTS[key].strip() for key in spec.instruction_fragment_keys)


def test_node_contract_public_fragment_is_business_context_not_permission_guessing() -> None:
    text = PUBLIC_INSTRUCTION_FRAGMENTS["node.node_contract_context"]

    assert "if your available tools allow it" not in text
    assert "goal" in text.lower()
    assert "boundary" in text.lower()
    assert "Interfaces, materials, node dependencies, and Mathlib context" in text


def test_runtime_instruction_output_is_english() -> None:
    text = render_agent_instruction("ProofFormalWorkerAgent")

    assert re.search(r"[\u3400-\u9fff]", text) is None
    assert "## Proof Formal Worker" in text


def test_content_plan_instruction_spells_out_operational_flow_and_tools() -> None:
    text = render_agent_instruction("ContentPlanAgent")

    assert "After every callback, re-read current truth" in text
    assert "get_current_repo_work_config" in text
    assert "matching the current work_mode" in text
    assert "submit_content_preparation_recon" in text
    assert "write_decl_change_summary" in text
    assert "mark_decl_round_terminal" in text
    assert "validate_decl_round_draft" in text
    assert "submit_current_decl_round" in text
    assert "check_current_content_node_completion" in text
    assert "submit_content_node_ready" in text
    assert "Do not replace NodeDirDependencyReconFlow, MathlibReconFlow, or ResourceReconFlow" in text


def test_repo_format_discovery_instruction_spells_out_scoped_remote_workflow() -> None:
    text = render_agent_instruction("RepoFormatDiscoveryAgent")

    assert "get_preparation_input" in text
    assert "list_preparation_requirements" in text
    assert "get_preparation_requirement" in text
    assert "probe_github_lean_repo_candidate" in text
    assert "list_github_repository_tree" in text
    assert "read_github_repository_file" in text
    assert "search_github_code" in text
    assert "workspace-wide requirement tools" in text
    assert "Do not clone upstream code" in text
    assert "change source corpus mode" in text
    assert "source_corpus_mode" not in text
    assert "adapter_repo_name" not in text
    assert "native_repo_name" not in text


def test_source_index_instructions_match_builder_reviewer_boundaries() -> None:
    builder = render_agent_instruction("SourceIndexBuilderAgent")
    reviewer = render_agent_instruction("SourceIndexReviewerAgent")

    assert "create_draft_source_index" not in builder
    assert "current draft SourceIndex" in builder
    assert "validate_source_range" in builder
    assert "preview_source_ref" in builder
    assert "mark_block_refs_done" in builder
    assert "mark_block_links_done" in builder
    assert "mark_block_completed" in builder
    assert "submit_source_index_builder_round" in builder
    assert "If submit succeeds, stop" in builder
    assert "Do not prepare or rewrite source corpus material" in builder

    assert "create_source_block" not in reviewer
    assert "add_source_block_ref" not in reviewer
    assert "set_source_index_overview" not in reviewer
    assert "get_source_index" in reviewer
    assert "get_source_index_coverage" in reviewer
    assert "validate_source_index" in reviewer
    assert "validate_source_range" in reviewer
    assert "preview_source_ref" in reviewer
    assert "submit_source_index_review_round" in reviewer
    assert "After an accepted submit, stop" in reviewer


def test_node_dir_recon_instruction_spells_out_dependency_evidence_policy() -> None:
    text = render_agent_instruction("NodeDirDependencyReconAgent")

    assert "remove_current_node_dep" in text
    assert "clearly stale, wrong, or outside the current node objective" in text
    assert "expected_public_decl_names" in text
    assert "structured evidence, not a free-form note" in text
    assert "unresolved_within_visible_boundaries" in text
    assert "If the evidence is uncertain" in text


def test_root_interface_prepare_instruction_uses_root_specific_tools() -> None:
    text = render_agent_instruction("RootInterfacePrepareAgent")

    assert "list_root_interfaces" in text
    assert "add_root_interface" in text
    assert "update_root_interface" not in text
    assert "remove_root_interface" not in text
    assert "submit_root_interface_prepare_ready" in text
    assert "Protected interfaces come from the preparation input" in text
    assert "Multi-run root-interface preparation is append-only" in text
    assert "After an accepted submit, stop" in text
    assert "add_node_interface" not in text
    assert "bind_node_interface" not in text
    assert "add_scope_export" not in text


def test_adapter_decl_catalog_instruction_matches_catalog_boundary() -> None:
    text = render_agent_instruction("AdapterDeclCatalogAgent")

    assert "list_preparation_requirements" in text
    assert "get_preparation_requirement" in text
    assert "list_root_interfaces" in text
    assert "find_adapter_decl_by_upstream" in text
    assert "check_adapter_catalog_ready_preflight" in text
    assert "submit_adapter_catalog_ready" in text
    assert "submit_adapter_catalog_blocked" in text
    assert "After an accepted submit, stop" in text
    assert "selected upstream as fixed" in text
    assert "write_adapter_upstream_metadata" in text
    assert "Never call" in text
    assert "refresh_adapter_projection" in text
    assert "root interface edits" in text


def test_all_runtime_instructions_are_english_and_tool_refs_resolve() -> None:
    for spec in build_agent_type_specs():
        text = render_agent_instruction(spec)

        assert re.search(r"[\u3400-\u9fff]", text) is None, spec.agent_type
        assert not _unknown_tool_refs(text), spec.agent_type


def test_formal_stage_instructions_match_stage_specific_tool_boundaries() -> None:
    statement_worker = render_agent_instruction("StatementFormalWorkerAgent")
    statement_reviewer = render_agent_instruction("StatementFormalReviewerAgent")
    proof_worker = render_agent_instruction("ProofFormalWorkerAgent")
    proof_reviewer = render_agent_instruction("ProofFormalReviewerAgent")

    assert "rewrites the working file" in statement_worker
    assert "Capture and deterministic gates own statement formal policy checks" in statement_worker
    assert "add_statement_decl_dep" in statement_worker
    assert "add_statement_mathlib_dep" in statement_worker
    assert "clear_statement_deps" in statement_worker
    assert "write_statement_formal_deps" not in statement_worker
    assert "add_current_node_dep" in statement_worker
    assert "check_statement_formal_policy" not in statement_worker
    assert "check_proof_formal_policy" not in statement_worker
    assert "prepare_statement_formal_file" not in statement_reviewer
    assert "capture_statement_formal_file" not in statement_reviewer
    assert "check_statement_formal_policy" not in statement_reviewer
    assert "run_lean_file_diagnostics" not in statement_reviewer
    assert "check_decl_file_snapshot_sync" not in statement_reviewer
    assert "Do not prepare files, capture files, write Lean code, or run formal diagnostics" in statement_reviewer
    assert "check_proof_formal_policy" not in statement_reviewer
    assert "record_statement_formal_review_passed" in statement_reviewer
    assert "record_statement_formal_review_rejected" in statement_reviewer
    assert "unavailable_repo_decl_dependency" in statement_reviewer
    assert "unresolved_mathlib_dependency" in statement_reviewer
    assert "proof_only_dependency_in_statement_deps" in statement_reviewer
    assert "same_round_repo_decl_dependency" in statement_reviewer
    assert "record_decl_review" not in statement_reviewer

    proof_nl_worker = render_agent_instruction("ProofNLWorkerAgent")
    proof_nl_reviewer = render_agent_instruction("ProofNLReviewerAgent")
    assert "set_proof_nl" in proof_nl_worker
    assert "add_proof_decl_dep" in proof_nl_worker
    assert "add_proof_mathlib_dep" in proof_nl_worker
    assert "write_proof_nl" not in proof_nl_worker
    assert "search_arxiv_theorems" in proof_nl_worker
    assert "record_proof_nl_review_passed" in proof_nl_reviewer
    assert "record_proof_nl_review_rejected" in proof_nl_reviewer
    assert "record_decl_review" not in proof_nl_reviewer
    assert "inspect_current_stage_review_status" in proof_nl_reviewer

    assert "discards uncaptured proof edits" in proof_worker
    assert "check_proof_formal_policy" in proof_worker
    assert "add_proof_decl_dep" in proof_worker
    assert "add_proof_mathlib_dep" in proof_worker
    assert "check_statement_formal_policy" not in proof_worker
    assert "prepare_proof_formal_file" not in proof_reviewer
    assert "capture_proof_formal_file" not in proof_reviewer
    assert "check_proof_formal_policy" not in proof_reviewer
    assert "run_lean_file_diagnostics" not in proof_reviewer
    assert "check_decl_file_snapshot_sync" not in proof_reviewer
    assert "Do not prepare files, capture files, write Lean code, or run formal diagnostics" in proof_reviewer
    assert "After editing, use the available capture and check workflow" not in proof_reviewer
    assert "record_proof_formal_review_passed" in proof_reviewer
    assert "record_proof_formal_review_rejected" in proof_reviewer
    assert "record_decl_review" not in proof_reviewer
    assert "check_statement_formal_policy" not in proof_reviewer


def test_runtime_instruction_tool_refs_are_visible_to_each_agent() -> None:
    reports = build_agent_surface_reports()

    for spec in build_agent_type_specs():
        refs = _tool_refs(render_agent_instruction(spec)) & _registered_tool_names()
        report = reports[spec.agent_type]
        visible = {tool.name for tool in report.application_tools} | {tool.name for tool in report.submit_tools}

        assert refs <= visible, spec.agent_type
