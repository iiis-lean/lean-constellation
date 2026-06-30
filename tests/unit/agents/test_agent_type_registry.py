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


def test_agent_skill_mapping_reuses_shared_skills() -> None:
    mapping = agent_skill_keys()

    assert "resource-request-handling" in mapping["CoordinatorAgent"]
    assert "resource-request-handling" in mapping["ContentPlanAgent"]
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
