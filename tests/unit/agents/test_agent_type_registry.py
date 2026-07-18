from __future__ import annotations

import pytest

from lean_constellation.agents import (
    agent_skill_keys,
    agent_type_permission_names,
    build_agent_type_specs,
    build_controlled_test_agent_type_specs,
    controlled_test_agent_type_name,
    derive_agent_type_spec,
    get_agent_type_spec,
)
from lean_constellation.agents.surface import build_agent_surface_reports


EXPECTED_AGENT_TYPES = {
    "RepoFormatDiscoveryAgent",
    "SourceCorpusPrepareAgent",
    "SourceIndexBuilderAgent",
    "SourceIndexReviewerAgent",
    "RootInterfacePrepareAgent",
    "AdapterDeclCatalogAgent",
    "ResourceCuratorAgent",
    "CoordinatorAgent",
    "ContentPlanAgent",
    "NodeDirDependencyReconAgent",
    "MathlibReconAgent",
    "ResourceReconAgent",
    "StatementNLWorkerAgent",
    "StatementNLReviewerAgent",
    "StatementFormalWorkerAgent",
    "StatementFormalReviewerAgent",
    "ProofNLWorkerAgent",
    "ProofNLReviewerAgent",
    "ProofFormalWorkerAgent",
    "ProofFormalReviewerAgent",
}


EXPECTED_COORDINATOR_SKILLS = [
    "coordinator-proved-full-graph-mode",
    "coordinator-declared-full-graph-mode",
    "coordinator-declared-interface-mode",
    "coordinator-content-result-closeout",
    "resource-result-closeout",
    "coordinator-requirement-result-closeout",
    "coordinator-dependency-readiness",
    "coordinator-node-decomposition",
    "coordinator-scope-lifecycle",
    "coordinator-content-task-dispatch",
    "resource-request-submission",
    "coordinator-provider-dependency-lifecycle",
    "coordinator-repo-ready-lifecycle",
    "node-contract-design",
    "scope-export-interface-curation",
    "external-resource-discovery",
    "mathlib-index-first-recon",
    "mathlib-semantic-search-navigation",
    "mathlib-index-entry-curation",
]


def test_all_designed_agent_types_are_registered() -> None:
    specs = build_agent_type_specs()

    assert {spec.agent_type for spec in specs} == EXPECTED_AGENT_TYPES
    assert len(specs) == len({spec.agent_type for spec in specs})


def test_representative_agent_specs_bind_expected_views_and_steps() -> None:
    coordinator = get_agent_type_spec("CoordinatorAgent")
    adapter = get_agent_type_spec("AdapterDeclCatalogAgent")
    statement_worker = get_agent_type_spec("StatementNLWorkerAgent")

    assert coordinator.role == "coordinator"
    assert coordinator.application_tool_view_key == "native_repo_coordinator"
    assert coordinator.submit_tool_view_key == "native_repo_coordinator_submit"
    assert coordinator.agent_step_type == "coordinator_agent_step"

    assert adapter.application_tool_view_key == "adapter_repo_import"
    assert "AdapterRepoImportAgent" in adapter.tool_view_agent_aliases

    assert statement_worker.agent_step_type == "decl_stage_worker_agent_step"
    assert statement_worker.stage == "statement_nl"


def test_decl_stage_reviewer_specs_use_base_runtime_stage_names() -> None:
    assert get_agent_type_spec("StatementNLReviewerAgent").stage == "statement_nl"
    assert get_agent_type_spec("StatementFormalReviewerAgent").stage == "statement_formal"
    assert get_agent_type_spec("ProofNLReviewerAgent").stage == "proof_nl"
    assert get_agent_type_spec("ProofFormalReviewerAgent").stage == "proof_formal"


def test_repo_format_discovery_has_native_and_adapter_context_fragments() -> None:
    spec = get_agent_type_spec("RepoFormatDiscoveryAgent")

    assert "repo.native_repo_context" in spec.instruction_fragment_keys
    assert "repo.adapter_repo_context" in spec.instruction_fragment_keys


def test_every_agent_uses_merged_common_fragments() -> None:
    for spec in build_agent_type_specs():
        assert spec.instruction_fragment_keys[:3] == [
            "common.runtime_contract",
            "common.role_filtered_tool_discovery",
            "common.submit_contract",
        ]
        assert "common.truth_and_tool_contract" not in spec.instruction_fragment_keys


def test_coordinator_public_fragments_match_native_repo_target_order() -> None:
    coordinator = get_agent_type_spec("CoordinatorAgent")

    assert coordinator.instruction_fragment_keys == [
        "common.runtime_contract",
        "common.role_filtered_tool_discovery",
        "common.submit_contract",
        "workspace.repo_workspace_context",
        "workspace.requirement_and_lake_dependency_context",
        "repo.native_repo_context",
        "source.source_corpus_context",
        "source.source_index_context",
        "resource.resource_library_context",
        "node.scope_content_node_context",
        "node.node_contract_context",
        "node.node_tree_decomposition_policy",
        "scope.scope_contract_exports_context",
        "decl.proof_policy_satisfaction_context",
        "decl.identity_projection_context",
        "quality.source_fidelity",
    ]
    assert "repo.adapter_repo_context" not in coordinator.instruction_fragment_keys
    assert "common.blocked_escalation_contract" not in coordinator.instruction_fragment_keys


def test_blocked_contract_is_only_installed_for_agents_with_structured_completion_paths() -> None:
    expected_paths = {
        "SourceCorpusPrepareAgent": {"submit_source_corpus_blocked"},
        "AdapterDeclCatalogAgent": {"submit_adapter_catalog_blocked"},
        "ResourceCuratorAgent": {"submit_external_repo_required", "submit_resource_rejected"},
        "ContentPlanAgent": {"submit_content_node_blocked"},
        "ResourceReconAgent": {"submit_resource_recon_blocked"},
        "StatementNLWorkerAgent": {"submit_stage_worker_blocked"},
        "StatementFormalWorkerAgent": {"submit_stage_worker_blocked"},
        "ProofNLWorkerAgent": {"submit_stage_worker_blocked"},
        "ProofFormalWorkerAgent": {"submit_stage_worker_blocked"},
    }
    specs = {spec.agent_type: spec for spec in build_agent_type_specs()}
    reports = build_agent_surface_reports()
    actual = {
        name
        for name, spec in specs.items()
        if "common.blocked_escalation_contract" in spec.instruction_fragment_keys
    }

    assert actual == set(expected_paths)
    for agent_type, completion_tools in expected_paths.items():
        visible = {tool.name for tool in reports[agent_type].submit_tools}
        assert completion_tools <= visible, agent_type


def test_agent_skill_mapping_reuses_shared_skills() -> None:
    mapping = agent_skill_keys()

    assert mapping["CoordinatorAgent"] == EXPECTED_COORDINATOR_SKILLS
    for agent_type in ("CoordinatorAgent", "ContentPlanAgent", "ResourceReconAgent"):
        assert "resource-request-submission" in mapping[agent_type]
        assert "resource-result-closeout" in mapping[agent_type]
    for removed in (
        "coordinator-content-task-lifecycle",
        "coordinator-repo-requirement-lifecycle",
        "resource-request-handling",
    ):
        assert all(removed not in skills for skills in mapping.values())
    assert "decl-dependency-origin-curation" in mapping["ProofFormalReviewerAgent"]
    assert "lean-proof-formalization" in mapping["ProofFormalWorkerAgent"]


def test_extra_agent_type_can_extend_existing_tool_permissions() -> None:
    controlled = derive_agent_type_spec(
        base_agent_type="CoordinatorAgent",
        agent_type="CoordinatorControlledTestAgent",
    )

    specs = build_agent_type_specs(extra_specs=[controlled])
    resolved = get_agent_type_spec("CoordinatorControlledTestAgent", specs=specs)
    names = agent_type_permission_names("CoordinatorControlledTestAgent", specs=specs)
    mapping = agent_skill_keys(specs=specs)

    assert resolved.extends_agent_type == "CoordinatorAgent"
    assert "CoordinatorControlledTestAgent" in names
    assert "CoordinatorAgent" in names
    assert "native_repo_coordinator" in names
    assert mapping["CoordinatorControlledTestAgent"] == mapping["CoordinatorAgent"]


def test_extra_agent_type_duplicate_is_rejected() -> None:
    duplicate = get_agent_type_spec("CoordinatorAgent").model_copy()

    with pytest.raises(ValueError, match="duplicate AgentType"):
        build_agent_type_specs(extra_specs=[duplicate])


def test_controlled_test_agent_type_specs_are_derived_from_requested_bases() -> None:
    base_specs = build_agent_type_specs()

    controlled = build_controlled_test_agent_type_specs(
        specs=base_specs,
        base_agent_types=["CoordinatorAgent", "ResourceCuratorAgent"],
    )
    specs = [*base_specs, *controlled]

    assert [spec.agent_type for spec in controlled] == [
        "CoordinatorControlledTestAgent",
        "ResourceCuratorControlledTestAgent",
    ]
    assert controlled_test_agent_type_name("RepoFormatDiscoveryAgent") == "RepoFormatDiscoveryControlledTestAgent"
    assert get_agent_type_spec("CoordinatorControlledTestAgent", specs=specs).extends_agent_type == "CoordinatorAgent"
    assert "CoordinatorAgent" in agent_type_permission_names("CoordinatorControlledTestAgent", specs=specs)
