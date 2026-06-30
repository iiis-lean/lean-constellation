from __future__ import annotations

from lean_constellation.agents import agent_skill_keys, build_agent_type_specs, get_agent_type_spec


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
