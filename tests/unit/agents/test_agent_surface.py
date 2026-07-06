from __future__ import annotations

from lean_constellation.agents import build_agent_type_specs, build_agent_surface_reports


EXPECTED_SURFACE_COUNTS = {
    "RepoFormatDiscoveryAgent": (3, 5, 1, 2, 0),
    "SourceCorpusPrepareAgent": (3, 8, 1, 2, 1),
    "SourceIndexBuilderAgent": (4, 19, 1, 1, 0),
    "SourceIndexReviewerAgent": (3, 7, 1, 1, 0),
    "RootInterfacePrepareAgent": (6, 17, 1, 1, 1),
    "AdapterDeclCatalogAgent": (12, 40, 1, 2, 0),
    "ResourceCuratorAgent": (4, 18, 1, 4, 2),
    "CoordinatorAgent": (28, 68, 2, 4, 13),
    "ContentPlanAgent": (24, 70, 3, 6, 16),
    "NodeDirDependencyReconAgent": (4, 13, 1, 4, 2),
    "MathlibReconAgent": (8, 23, 1, 4, 5),
    "ResourceReconAgent": (6, 14, 2, 5, 3),
    "StatementNLWorkerAgent": (11, 36, 1, 2, 4),
    "StatementNLReviewerAgent": (9, 30, 1, 1, 2),
    "StatementFormalWorkerAgent": (14, 50, 1, 2, 8),
    "StatementFormalReviewerAgent": (9, 31, 1, 1, 2),
    "ProofNLWorkerAgent": (12, 37, 1, 2, 4),
    "ProofNLReviewerAgent": (9, 30, 1, 1, 2),
    "ProofFormalWorkerAgent": (14, 50, 1, 2, 8),
    "ProofFormalReviewerAgent": (9, 31, 1, 1, 2),
}


def test_agent_surface_reports_cover_every_production_agent() -> None:
    reports = build_agent_surface_reports()

    assert set(reports) == {spec.agent_type for spec in build_agent_type_specs()}
    assert set(reports) == set(EXPECTED_SURFACE_COUNTS)
    for agent_type, report in reports.items():
        expected = EXPECTED_SURFACE_COUNTS[agent_type]
        assert (
            len(report.application_group_keys),
            len(report.application_tools),
            len(report.submit_group_keys),
            len(report.submit_tools),
            len(report.skills),
        ) == expected
        assert report.missing_skill_required_groups == {}


def test_decl_stage_surfaces_keep_reviewer_and_worker_file_boundaries() -> None:
    reports = build_agent_surface_reports()

    assert "capture_statement_formal_file" in {tool.name for tool in reports["StatementFormalWorkerAgent"].application_tools}
    assert "capture_statement_formal_file" not in {tool.name for tool in reports["StatementFormalReviewerAgent"].application_tools}
    assert "capture_proof_formal_file" in {tool.name for tool in reports["ProofFormalWorkerAgent"].application_tools}
    assert "capture_proof_formal_file" not in {tool.name for tool in reports["ProofFormalReviewerAgent"].application_tools}
