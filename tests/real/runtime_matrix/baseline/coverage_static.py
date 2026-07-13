"""Coverage declarations for the first-layer Runtime Matrix tests."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from lean_constellation.agents import build_agent_type_specs
from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES
from lean_constellation.flows.registry import BUSINESS_FLOW_TYPES, BUSINESS_LOGIC_STEP_TYPES
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    build_submit_tool_groups,
    build_submit_tool_specs,
    build_submit_tool_views,
)


@dataclass(frozen=True)
class RuntimeMatrixCase:
    case_id: str
    summary: str
    tags: frozenset[str]
    env_gated: bool = False
    notes: str = ""


@dataclass(frozen=True)
class RegistrySnapshot:
    flows: tuple[str, ...]
    logic_steps: tuple[str, ...]
    agent_steps: tuple[str, ...]
    agent_types: tuple[str, ...]
    application_tools: tuple[str, ...]
    application_tool_groups: tuple[str, ...]
    application_tool_views: tuple[str, ...]
    submit_tools: tuple[str, ...]
    submit_tool_groups: tuple[str, ...]
    submit_tool_views: tuple[str, ...]


def current_registry_snapshot() -> RegistrySnapshot:
    app_tools = build_application_tool_specs()
    app_groups = build_application_tool_groups(app_tools)
    submit_tools = build_submit_tool_specs()
    submit_groups = build_submit_tool_groups(submit_tools)
    return RegistrySnapshot(
        flows=tuple(cls.flow_type for cls in BUSINESS_FLOW_TYPES),
        logic_steps=tuple(cls.step_type for cls in BUSINESS_LOGIC_STEP_TYPES),
        agent_steps=tuple(cls.step_type for cls in BUSINESS_AGENT_STEP_TYPES),
        agent_types=tuple(spec.agent_type for spec in build_agent_type_specs()),
        application_tools=tuple(spec.name for spec in app_tools),
        application_tool_groups=tuple(group.key for group in app_groups),
        application_tool_views=tuple(view.key for view in build_application_tool_views(app_groups)),
        submit_tools=tuple(spec.name for spec in submit_tools),
        submit_tool_groups=tuple(group.key for group in submit_groups),
        submit_tool_views=tuple(view.key for view in build_submit_tool_views(submit_groups)),
    )


def registry_fingerprint(snapshot: RegistrySnapshot | None = None) -> str:
    snapshot = snapshot or current_registry_snapshot()
    payload = "\n".join(
        [
            "flows:" + ",".join(snapshot.flows),
            "logic_steps:" + ",".join(snapshot.logic_steps),
            "agent_steps:" + ",".join(snapshot.agent_steps),
            "agent_types:" + ",".join(snapshot.agent_types),
            "application_tools:" + ",".join(snapshot.application_tools),
            "application_tool_groups:" + ",".join(snapshot.application_tool_groups),
            "application_tool_views:" + ",".join(snapshot.application_tool_views),
            "submit_tools:" + ",".join(snapshot.submit_tools),
            "submit_tool_groups:" + ",".join(snapshot.submit_tool_groups),
            "submit_tool_views:" + ",".join(snapshot.submit_tool_views),
        ]
    )
    return sha256(payload.encode("utf-8")).hexdigest()


# This pin makes registry additions/removals fail until the Runtime Matrix
# coverage declaration is consciously refreshed.
EXPECTED_REGISTRY_FINGERPRINT = "7a01d21270230948946b8afc41fdbfea064ccb131138c143c58c40cc5b98181a"


def required_registry_tags(snapshot: RegistrySnapshot | None = None) -> set[str]:
    snapshot = snapshot or current_registry_snapshot()
    return {
        *(f"flow:{name}" for name in snapshot.flows),
        *(f"logic_step:{name}" for name in snapshot.logic_steps),
        *(f"agent_step:{name}" for name in snapshot.agent_steps),
        *(f"agent_type:{name}" for name in snapshot.agent_types),
        *(f"application_tool:{name}" for name in snapshot.application_tools),
        *(f"application_tool_group:{name}" for name in snapshot.application_tool_groups),
        *(f"application_tool_view:{name}" for name in snapshot.application_tool_views),
        *(f"submit:{name}" for name in snapshot.submit_tools),
        *(f"submit_tool_group:{name}" for name in snapshot.submit_tool_groups),
        *(f"submit_tool_view:{name}" for name in snapshot.submit_tool_views),
    }


def manifest_tags(cases: Iterable[RuntimeMatrixCase] | None = None) -> set[str]:
    return set().union(*(case.tags for case in (cases or RUNTIME_MATRIX_CASES)))


def _registry_schema_case() -> RuntimeMatrixCase:
    snapshot = current_registry_snapshot()
    return RuntimeMatrixCase(
        case_id="registry-static-surface",
        summary="Pinned registry surface for coverage manifest and tool sweep classification.",
        tags=frozenset(required_registry_tags(snapshot)),
        notes="Application tools are classified dynamically in the sweep; the registry fingerprint pin catches surface drift.",
    )


RUNTIME_MATRIX_CASES: tuple[RuntimeMatrixCase, ...] = (
    RuntimeMatrixCase(
        case_id="runtime-matrix-workspace-fixtures",
        summary="RuntimeMatrixWorkspace creates provider, consumer, adapter, upstream, source, resource, content node, DeclGraph, Toolkit, and Mathlib-template fixtures.",
        tags=frozenset(
            {
                "fixture:provider_minimal_lake_repo",
                "fixture:consumer_requirement_repo",
                "fixture:adapter_repo",
                "fixture:local_git_upstream_repo",
                "fixture:source_corpus_source_index",
                "fixture:local_web_arxiv_resources",
                "fixture:content_node_decl_round",
                "fixture:live_toolkit_env_override",
                "fixture:mathlib_template_env_override",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="repo-format-native-adapter-branch-restore",
        summary="RequirementGroupRepoBootstrap native and adapter choices through Admin external takeover.",
        tags=frozenset(
            {
                "flow:requirement_group_repo_bootstrap",
                "logic_step:validate_bootstrap_input_step",
                "agent_step:repo_format_discovery_agent_step",
                "logic_step:apply_repo_format_choice_step",
                "agent_type:RepoFormatDiscoveryAgent",
                "submit:submit_native_repo_choice",
                "submit:submit_adapter_repo_choice",
                "submit_tool_view:repo_format_discovery_submit",
                "application_tool_view:repo_format_discovery",
                "snapshot:manual_test_stable_point_restore_prune",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="resource-curator-branch-restore",
        summary="ResourceCuration curator duplicate, local, external, and rejected submit branches.",
        tags=frozenset(
            {
                "flow:resource_curation",
                "logic_step:resource_curation_preflight_step",
                "agent_step:resource_curator_agent_step",
                "agent_type:ResourceCuratorAgent",
                "submit:submit_resource_duplicate",
                "submit:submit_local_resource_created",
                "submit:submit_external_repo_required",
                "submit:submit_resource_rejected",
                "submit_tool_view:resource_curator_submit",
                "application_tool_view:resource_curator",
                "snapshot:manual_test_stable_point_restore_prune",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="resource-preflight-duplicate",
        summary="ResourceCuration preflight duplicate hint branch continues to ResourceCurator Agent.",
        tags=frozenset(
            {
                "flow:resource_curation",
                "logic_step:resource_curation_preflight_step",
                "agent_step:resource_curator_agent_step",
                "submit:submit_resource_duplicate",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="repo-preparation-native-adapter-matrix",
        summary="Native and adapter repo preparation branches, including source prepared/blocked, review rejected/approved, root interface direct ready, adapter ready, and adapter blocked.",
        tags=frozenset(
            {
                "flow:native_repo_preparation",
                "flow:adapter_repo_preparation",
                "logic_step:native_preparation_start_step",
                "logic_step:root_interface_direct_ready_step",
                "logic_step:dispatch_native_coordinator_step",
                "agent_step:source_corpus_prepare_agent_step",
                "agent_step:source_index_builder_agent_step",
                "agent_step:source_index_reviewer_agent_step",
                "agent_step:root_interface_prepare_agent_step",
                "agent_step:adapter_decl_catalog_agent_step",
                "agent_type:SourceCorpusPrepareAgent",
                "agent_type:SourceIndexBuilderAgent",
                "agent_type:SourceIndexReviewerAgent",
                "agent_type:RootInterfacePrepareAgent",
                "agent_type:AdapterDeclCatalogAgent",
                "submit:submit_source_corpus_prepared",
                "submit:submit_source_corpus_blocked",
                "submit:submit_source_index_builder_round",
                "submit:submit_source_index_review_round",
                "submit:submit_root_interface_prepare_ready",
                "submit:submit_adapter_catalog_ready",
                "submit:submit_adapter_catalog_blocked",
                "application_tool_view:source_index_builder",
                "application_tool_view:source_index_reviewer",
                "application_tool_view:adapter_repo_import",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="native-coordinator-callback-ready-matrix",
        summary="Native coordinator content/resource/requirement callback paths and repo-ready terminal marker.",
        tags=frozenset(
            {
                "flow:native_repo_coordinator",
                "flow:content_node_task",
                "flow:resource_curation",
                "logic_step:coordinator_content_batch_snapshot_step",
                "logic_step:mark_coordinator_repo_ready_step",
                "agent_step:coordinator_agent_step",
                "agent_type:CoordinatorAgent",
                "submit:submit_content_node_tasks",
                "submit:submit_resource_request",
                "submit:submit_repo_requirement",
                "submit:submit_repo_ready",
                "submit_tool_view:native_repo_coordinator_submit",
                "callback:parent_resume_after_child_terminal",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="content-node-task-dispatch-terminal-matrix",
        summary="Content node task preparation/resource/decl_round dispatch callbacks plus ready, blocked, and failed terminal branches.",
        tags=frozenset(
            {
                "flow:content_node_task",
                "flow:node_dir_dependency_recon",
                "flow:resource_curation",
                "flow:decl_graph_round",
                "agent_step:content_plan_agent_step",
                "agent_type:ContentPlanAgent",
                "submit:submit_content_preparation_recon",
                "submit:submit_resource_request",
                "submit:submit_current_decl_round",
                "submit:submit_content_node_ready",
                "submit:submit_content_node_blocked",
                "submit:submit_content_node_failed",
                "callback:content_plan_after_child_terminal",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="recon-flow-matrix",
        summary="Node-dir, Mathlib, and resource recon completed/blocked/request_resource branches.",
        tags=frozenset(
            {
                "flow:node_dir_dependency_recon",
                "flow:mathlib_recon",
                "flow:resource_recon",
                "flow:resource_curation",
                "agent_step:node_dir_dependency_recon_agent_step",
                "agent_step:mathlib_recon_agent_step",
                "agent_step:resource_recon_agent_step",
                "agent_type:NodeDirDependencyReconAgent",
                "agent_type:MathlibReconAgent",
                "agent_type:ResourceReconAgent",
                "submit:submit_node_dir_dependency_recon_completed",
                "submit:submit_mathlib_recon_completed",
                "submit:submit_resource_recon_completed",
                "submit:submit_resource_recon_blocked",
                "submit:submit_resource_request",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="decl-graph-round-stage-matrix",
        summary="DeclGraphRound worker completed/blocked, reviewer passed/rejected, and statement/proof NL/formal stages.",
        tags=frozenset(
            {
                "flow:decl_graph_round",
                "logic_step:decl_round_start_validation_step",
                "logic_step:decl_round_delete_normalize_step",
                "logic_step:decl_round_prepare_stage_targets_step",
                "logic_step:decl_round_stage_gate_audit_step",
                "logic_step:decl_round_final_audit_step",
                "logic_step:decl_round_build_result_step",
                "agent_step:decl_stage_worker_agent_step",
                "agent_step:decl_stage_reviewer_agent_step",
                "agent_type:StatementNLWorkerAgent",
                "agent_type:StatementNLReviewerAgent",
                "agent_type:StatementFormalWorkerAgent",
                "agent_type:StatementFormalReviewerAgent",
                "agent_type:ProofNLWorkerAgent",
                "agent_type:ProofNLReviewerAgent",
                "agent_type:ProofFormalWorkerAgent",
                "agent_type:ProofFormalReviewerAgent",
                "submit:submit_stage_worker_completed",
                "submit:submit_stage_worker_blocked",
                "submit:submit_stage_review",
                "application_tool_view:statement_nl_worker",
                "application_tool_view:statement_formal_worker",
                "application_tool_view:proof_nl_worker",
                "application_tool_view:proof_formal_worker",
                "application_tool_view:statement_nl_reviewer",
                "application_tool_view:proof_formal_reviewer",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="real-lean-boundary-matrix",
        summary="Real Lake build, lake env lean JSON, snippet ok/fail, statement/proof formal capture, policy rejects, and projection sync/reset.",
        tags=frozenset(
            {
                "boundary:real_lake_build",
                "boundary:real_lake_env_lean_json",
                "boundary:real_snippet_ok_fail",
                "boundary:statement_formal_prepare_capture",
                "boundary:proof_formal_prepare_capture",
                "boundary:sorry_axiom_admit_policy_reject",
                "boundary:projection_sync_reset",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="live-toolkit-boundary-matrix",
        summary="Env-gated live Toolkit catalog, diagnostics/extract, repo_nav/mathlib_nav/lean_explore, and adapter upstream capture.",
        tags=frozenset(
            {
                "boundary:live_toolkit_catalog_probe",
                "boundary:live_toolkit_diagnostics_extract",
                "boundary:live_toolkit_repo_nav",
                "boundary:live_toolkit_mathlib_nav",
                "boundary:live_toolkit_lean_explore",
                "boundary:adapter_upstream_live_capture",
            }
        ),
        env_gated=True,
        notes="Skipped unless LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL or visible repo env is configured.",
    ),
    RuntimeMatrixCase(
        case_id="agent-resource-and-handoff-matrix",
        summary="Production/controlled AgentType home resources, application/submit ToolView visibility, and external takeover handoff prompt/env/tool lists.",
        tags=frozenset(
            {
                "agent_resource:20_production_agent_types",
                "agent_resource:controlled_inheritance",
                "agent_resource:20_application_tool_views",
                "agent_resource:14_submit_tool_views",
                "agent_resource:external_takeover_handoff",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="application-tool-sweep-matrix",
        summary="Dynamic 183 application tool classification, read-only calls, checkpointed write calls, env-gated classes, and schema-only reasons.",
        tags=frozenset(
            {
                "application_tool_sweep:183_tools",
                "application_tool_sweep:read_only_representatives",
                "application_tool_sweep:checkpointed_write_representatives",
                "application_tool_sweep:env_gated_network_toolkit_github",
                "application_tool_sweep:schema_only_with_reason",
            }
        ),
    ),
    RuntimeMatrixCase(
        case_id="real-codex-smoke-matrix",
        summary="Env-gated real Codex smoke for RepoFormatDiscoveryAgent MCP submit path.",
        tags=frozenset(
            {
                "agent_resource:real_codex_important_tool_smoke",
                "flow:requirement_group_repo_bootstrap",
                "agent_step:repo_format_discovery_agent_step",
                "agent_type:RepoFormatDiscoveryAgent",
                "submit:submit_native_repo_choice",
                "submit:submit_adapter_repo_choice",
            }
        ),
        env_gated=True,
        notes="Skipped unless LEAN_CONSTELLATION_RUN_REAL_CODEX=1 and Codex config/auth are available.",
    ),
    _registry_schema_case(),
)
