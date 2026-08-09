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
    assert "get_current_repo_completion_policy" in text
    assert "coordinator-completion-policy" in text
    assert "coordinator-node-decomposition" in text
    assert "write DeclGraph artifacts" in text


def test_coordinator_instruction_routes_closeout_then_repeated_next_actions() -> None:
    text = render_agent_instruction("CoordinatorAgent")

    assert "### Stage One: Reconcile The Wake Result" in text
    assert "coordinator-content-result-closeout" in text
    assert "coordinator-blocked-consumer-replan" in text
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
    assert "authoritative private consumer declaration" in text
    assert "owns the current-node, existing-node, coherent-package" in text
    assert "declaration-count threshold" in text
    assert "consumer anchor" in text
    assert "without becoming an exact header" in text


def test_coordinator_exploration_instruction_describes_only_current_workflow() -> None:
    text = render_agent_instruction("CoordinatorAgent")

    assert "The Flow owns one fixed resource, Lean-provider, and Mathlib exploration batch" in text
    assert "do not submit another batch merely to reconcile the initial callback" in text
    assert "Later exploration is optional and selective" in text
    assert "Exploration is optional and selective" not in text
    for migration_term in ("retroactive", "restored mature", "capability became available"):
        assert migration_term not in text


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


def test_common_tool_discovery_contract_uses_role_filtered_surface() -> None:
    text = PUBLIC_INSTRUCTION_FRAGMENTS["common.role_filtered_tool_discovery"]

    assert "mcp__lc_app__" in text
    assert "mcp__lc_submit__" in text
    assert "broad or complete ALL_TOOLS" in text
    assert "unrelated global apps or plugins" in text
    assert "exact locator listed under Available Skills" in text
    assert "Do not search from the current workdir" in text
    assert "precise read tools" in text


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
    assert "get_current_repo_completion_policy" in text
    assert "content-plan-completion-policy" in text
    assert "submit_content_preparation_recon" in text
    assert "decl-round-closeout" in text
    assert "mark_decl_round_terminal" in text
    assert "decl-round-change-planning" in text
    assert "submit_current_decl_round" in text
    assert "anticipated_statement_dep_names" in text
    assert "Never omit a known dependency to make validation pass" in text
    assert "current-node-public-boundary-curation" in text
    assert "content-node-completion-decision" in text
    assert "visibility just observed" in text
    assert "creates no Decl round or revision" in text
    assert "never silently removes" in text
    assert "Interface binding is ContentPlan closeout" in text
    assert "Do not replace NodeDirDependencyReconFlow, MathlibReconFlow, or ResourceReconFlow" in text
    assert "faithfully carry the evidence recorded by `decl-round-closeout`" in text
    assert "you do not decide the repository node tree" in text
    assert "interface fit and binding" in text


def test_bottom_up_policy_instruction_is_navigation_not_a_duplicate_decision_tree() -> None:
    coordinator = render_agent_instruction("CoordinatorAgent")
    content = render_agent_instruction("ContentPlanAgent")

    assert "bottom-up Source-visible dependency-frontier check" in coordinator
    assert "Mathlib/existing-boundary/local-helper/coherent-package classification" in content
    assert "No declaration-count cutoff" not in content
    assert "layered definitions or lemmas" not in content
    assert len(coordinator) <= 29_000
    assert len(content) <= 27_000


def test_decl_worker_instructions_preserve_precise_blocker_evidence() -> None:
    for agent_type in (
        "StatementNLWorkerAgent",
        "StatementFormalWorkerAgent",
        "ProofNLWorkerAgent",
        "ProofFormalWorkerAgent",
    ):
        text = render_agent_instruction(agent_type)
        assert "affected declaration" in text, agent_type
        assert "consumer-side formal goal or shape" in text or "exact local goal or diagnostic shape" in text, agent_type
        assert "declarations" in text and "concrete mismatch" in text, agent_type
        assert "final theorem for another Content node" in text, agent_type


def test_native_content_guidance_does_not_add_node_use_categories_or_exact_statement_ownership() -> None:
    text = "\n".join(
        render_agent_instruction(agent_type)
        for agent_type in ("CoordinatorAgent", "ContentPlanAgent")
    )

    for forbidden in (
        "support node",
        "adapter node",
        "support node ready",
        "expected_statement_lean_code",
        "NodeKind.SUPPORT",
        "NodeKind.ADAPTER",
    ):
        assert forbidden not in text


def test_repo_format_discovery_instruction_spells_out_scoped_remote_workflow() -> None:
    text = render_agent_instruction("RepoFormatDiscoveryAgent")

    assert "get_preparation_input" in text
    assert "$repo-format-discovery" in text
    assert "workspace-wide requirement state" in text
    assert "clone upstream code" in text
    assert "change source corpus mode" in text
    assert "package, import-module, toolchain, or resolved-revision" in text
    assert "placeholder schema probes" in text
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
    assert "sole submission gate" in text
    assert "Pre-finalize projection state" in text
    assert "submit_adapter_catalog_ready" in text
    assert "submit_adapter_catalog_blocked" in text
    assert "After an accepted submit, stop" in text
    assert "selected upstream as fixed" in text
    assert "write_adapter_upstream_metadata" not in text
    assert "refresh_adapter_projection" not in text
    assert "check_adapter_projection" not in text
    assert "check_adapter_ready" not in text
    assert "deterministic preparation responsibilities" in text
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
    assert "add_statement_repo_dependencies" in statement_worker
    assert "add_statement_mathlib_dependencies" in statement_worker
    assert "clear_statement_deps" in statement_worker
    assert "write_statement_formal_deps" not in statement_worker
    assert "add_current_node_dep" in statement_worker
    assert "linter.style.longLine" in statement_worker
    assert "declaration uses sorry" in statement_worker
    assert "check_statement_formal_policy" not in statement_worker
    assert "check_proof_formal_policy" not in statement_worker
    assert "prepare_statement_formal_file" not in statement_reviewer
    assert "capture_statement_formal_file" not in statement_reviewer
    assert "check_statement_formal_policy" not in statement_reviewer
    assert "run_lean_file_diagnostics" not in statement_reviewer
    assert "check_decl_file_snapshot_sync" not in statement_reviewer
    assert "Do not prepare files, capture files, write Lean code, or run formal diagnostics" in statement_reviewer
    assert "linter.style.longLine" in statement_reviewer
    assert "declaration uses sorry" in statement_reviewer
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
    assert "add_proof_repo_dependencies" in proof_nl_worker
    assert "add_proof_mathlib_dependencies" in proof_nl_worker
    assert "write_proof_nl" not in proof_nl_worker
    assert "search_arxiv_theorems" in proof_nl_worker
    assert "record_proof_nl_review_passed" in proof_nl_reviewer
    assert "record_proof_nl_review_rejected" in proof_nl_reviewer
    assert "record_decl_review" not in proof_nl_reviewer
    assert "inspect_current_stage_review_status" in proof_nl_reviewer

    assert "discards uncaptured proof edits" in proof_worker
    assert "check_proof_formal_policy" in proof_worker
    assert "add_proof_repo_dependencies" in proof_worker
    assert "add_proof_mathlib_dependencies" in proof_worker
    assert "reread_required=true" in proof_worker
    assert "is not a blocker" in proof_worker
    assert "same AgentStep" in proof_worker
    assert "Never submit blocked merely because rereading is required" in proof_worker
    assert "linter.style.longLine" in proof_worker
    assert "check_statement_formal_policy" not in proof_worker
    assert "prepare_proof_formal_file" not in proof_reviewer
    assert "capture_proof_formal_file" not in proof_reviewer
    assert "check_proof_formal_policy" not in proof_reviewer
    assert "run_lean_file_diagnostics" not in proof_reviewer
    assert "check_decl_file_snapshot_sync" not in proof_reviewer
    assert "Do not prepare files, capture files, write Lean code, or run formal diagnostics" in proof_reviewer
    assert "linter.style.longLine" in proof_reviewer
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


def test_source_reviewer_routes_corpus_fidelity_blocker_outside_builder_authority() -> None:
    text = render_agent_instruction("SourceIndexReviewerAgent")

    assert "source_corpus_fidelity_blocker:" in text
    assert "retained originals, acquisition provenance, or other canonical evidence" in text
    assert "Do not reject material merely because the supplied source is a Lean specification" in text
    assert "do not modify source files" in text
    assert "separately authorized SourceCorpus repair" in text


def test_source_prepare_preserves_supplied_targets_without_inventing_source_truth() -> None:
    text = render_agent_instruction("SourceCorpusPrepareAgent")

    assert "Treat Lean specifications, formal targets, solutions, proof references" in text
    assert "preserve their bytes or faithfully extracted meaning" in text
    assert "Do not invent a target, answer, proof, NodeTree, probe, or audit hint" in text
    assert "Do not replace source truth with" not in text


def test_resource_curator_treats_requested_use_as_advisory_evidence() -> None:
    text = render_agent_instruction("ResourceCuratorAgent")

    assert "formal-dependency as strong provider evidence, not an irreversible classification" in text
    assert "direct inspection shows that the actual target is narrow supporting material" in text
    assert "cannot become a local Resource" not in text
