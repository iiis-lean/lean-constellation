from __future__ import annotations

from lean_constellation.agents import build_agent_type_specs, build_agent_surface_reports


EXPECTED_SURFACE_COUNTS = {
    "RepoFormatDiscoveryAgent": (3, 5, 1, 2, 0),
    "SourceCorpusPrepareAgent": (3, 8, 1, 2, 1),
    "SourceIndexBuilderAgent": (4, 19, 1, 1, 0),
    "SourceIndexReviewerAgent": (3, 7, 1, 1, 0),
    "RootInterfacePrepareAgent": (6, 17, 1, 1, 1),
    "AdapterDeclCatalogAgent": (12, 40, 1, 2, 0),
    "ResourceCuratorAgent": (5, 18, 1, 4, 2),
    "CoordinatorAgent": (34, 79, 2, 4, 13),
    "ContentPlanAgent": (25, 70, 3, 6, 16),
    "NodeDirDependencyReconAgent": (4, 13, 1, 1, 2),
    "MathlibReconAgent": (7, 22, 1, 1, 5),
    "ResourceReconAgent": (8, 16, 2, 3, 3),
    "StatementNLWorkerAgent": (11, 36, 1, 2, 4),
    "StatementNLReviewerAgent": (9, 30, 1, 1, 2),
    "StatementFormalWorkerAgent": (14, 47, 1, 2, 8),
    "StatementFormalReviewerAgent": (9, 30, 1, 1, 2),
    "ProofNLWorkerAgent": (11, 36, 1, 2, 4),
    "ProofNLReviewerAgent": (9, 30, 1, 1, 2),
    "ProofFormalWorkerAgent": (14, 47, 1, 2, 8),
    "ProofFormalReviewerAgent": (9, 30, 1, 1, 2),
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
    statement_worker_tools = {tool.name for tool in reports["StatementFormalWorkerAgent"].application_tools}
    statement_reviewer_tools = {tool.name for tool in reports["StatementFormalReviewerAgent"].application_tools}
    proof_worker_tools = {tool.name for tool in reports["ProofFormalWorkerAgent"].application_tools}
    proof_reviewer_tools = {tool.name for tool in reports["ProofFormalReviewerAgent"].application_tools}

    assert "capture_statement_formal_file" in statement_worker_tools
    assert "capture_statement_formal_file" not in statement_reviewer_tools
    assert "capture_proof_formal_file" in proof_worker_tools
    assert "capture_proof_formal_file" not in proof_reviewer_tools
    assert "check_statement_formal_policy" in statement_worker_tools
    assert "check_statement_formal_policy" in statement_reviewer_tools
    assert "check_statement_formal_policy" not in proof_worker_tools
    assert "check_statement_formal_policy" not in proof_reviewer_tools
    assert "check_proof_formal_policy" in proof_worker_tools
    assert "check_proof_formal_policy" in proof_reviewer_tools
    assert "check_proof_formal_policy" not in statement_worker_tools
    assert "check_proof_formal_policy" not in statement_reviewer_tools
    assert "sync_decl_file_after_revision_reset" not in statement_worker_tools | proof_worker_tools
    assert "remove_decl_file_for_delete" not in statement_worker_tools | proof_worker_tools


def test_coordinator_surface_uses_path_based_read_and_write_tools() -> None:
    reports = build_agent_surface_reports()
    coordinator_tools = {tool.name for tool in reports["CoordinatorAgent"].application_tools}
    content_plan_tools = {tool.name for tool in reports["ContentPlanAgent"].application_tools}
    mathlib_recon_tools = {tool.name for tool in reports["MathlibReconAgent"].application_tools}

    assert {
        "get_source_index",
        "get_source_index_coverage",
        "add_node_dep",
        "remove_node_dep",
        "add_node_material_ref",
        "remove_node_material_ref",
        "add_node_mathlib_module_hint",
        "add_node_mathlib_decl_hint",
        "list_node_public_decls",
        "inspect_node_public_decl",
    } <= coordinator_tools
    assert "list_current_node_public_decls" not in coordinator_tools
    assert "inspect_current_node_public_decl" not in coordinator_tools
    assert "add_node_dep" not in content_plan_tools
    assert "add_node_mathlib_module_hint" not in mathlib_recon_tools
    assert "search_arxiv_theorems" not in mathlib_recon_tools
    assert "search_arxiv_theorems" not in {tool.name for tool in reports["ProofNLWorkerAgent"].application_tools}
