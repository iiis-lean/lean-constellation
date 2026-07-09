from __future__ import annotations

from lean_constellation.agents import build_agent_type_specs, build_agent_surface_reports


EXPECTED_SURFACE_COUNTS = {
    "RepoFormatDiscoveryAgent": (5, 12, 1, 2, 0),
    "SourceCorpusPrepareAgent": (3, 8, 1, 2, 1),
    "SourceIndexBuilderAgent": (4, 19, 1, 1, 0),
    "SourceIndexReviewerAgent": (3, 7, 1, 1, 0),
    "RootInterfacePrepareAgent": (6, 17, 1, 1, 1),
    "AdapterDeclCatalogAgent": (12, 40, 1, 2, 0),
    "ResourceCuratorAgent": (8, 20, 1, 4, 2),
    "CoordinatorAgent": (34, 79, 2, 4, 13),
    "ContentPlanAgent": (25, 70, 3, 6, 16),
    "NodeDirDependencyReconAgent": (4, 13, 1, 1, 2),
    "MathlibReconAgent": (7, 22, 1, 1, 5),
    "ResourceReconAgent": (8, 16, 2, 3, 3),
    "StatementNLWorkerAgent": (12, 46, 1, 2, 4),
    "StatementNLReviewerAgent": (14, 42, 1, 1, 2),
    "StatementFormalWorkerAgent": (16, 53, 1, 2, 8),
    "StatementFormalReviewerAgent": (14, 42, 1, 1, 2),
    "ProofNLWorkerAgent": (19, 63, 1, 2, 7),
    "ProofNLReviewerAgent": (16, 45, 1, 1, 2),
    "ProofFormalWorkerAgent": (21, 64, 1, 2, 8),
    "ProofFormalReviewerAgent": (15, 44, 1, 1, 2),
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
    proof_nl_worker_tools = {tool.name for tool in reports["ProofNLWorkerAgent"].application_tools}
    proof_nl_reviewer_tools = {tool.name for tool in reports["ProofNLReviewerAgent"].application_tools}

    assert "capture_statement_formal_file" in statement_worker_tools
    assert "capture_statement_formal_file" not in statement_reviewer_tools
    assert {"add_statement_decl_dep", "add_statement_mathlib_dep", "remove_statement_dep", "clear_statement_deps"} <= statement_worker_tools
    assert "write_statement_formal_deps" not in statement_worker_tools
    assert "write_statement_formal_deps" not in statement_reviewer_tools
    assert "add_current_node_dep" in statement_worker_tools
    assert "add_current_node_dep" not in statement_reviewer_tools
    assert "capture_proof_formal_file" in proof_worker_tools
    assert "capture_proof_formal_file" not in proof_reviewer_tools
    assert "check_statement_formal_policy" in statement_worker_tools
    assert "check_statement_formal_policy" not in statement_reviewer_tools
    assert "run_lean_file_diagnostics" not in statement_reviewer_tools
    assert "check_decl_file_snapshot_sync" not in statement_reviewer_tools
    assert "check_statement_formal_policy" not in proof_worker_tools
    assert "check_statement_formal_policy" not in proof_reviewer_tools
    assert "check_proof_formal_policy" in proof_worker_tools
    assert "check_proof_formal_policy" not in proof_reviewer_tools
    assert "run_lean_file_diagnostics" not in proof_reviewer_tools
    assert "check_decl_file_snapshot_sync" not in proof_reviewer_tools
    assert "check_proof_formal_policy" not in statement_worker_tools
    assert "check_proof_formal_policy" not in statement_reviewer_tools
    assert "record_statement_formal_review_passed" in statement_reviewer_tools
    assert "record_statement_formal_review_rejected" in statement_reviewer_tools
    assert "record_decl_review" not in statement_reviewer_tools
    assert {
        "set_proof_nl",
        "add_proof_source_origin",
        "add_proof_resource_origin",
        "add_proof_decl_dep",
        "add_proof_mathlib_dep",
        "remove_proof_dep",
        "clear_proof_deps",
    } <= proof_nl_worker_tools
    assert "write_proof_nl" not in proof_nl_worker_tools
    assert "record_proof_nl_review_passed" in proof_nl_reviewer_tools
    assert "record_proof_nl_review_rejected" in proof_nl_reviewer_tools
    assert "record_decl_review" not in proof_nl_reviewer_tools
    assert "inspect_current_stage_review_status" in proof_nl_reviewer_tools
    assert {"add_proof_decl_dep", "add_proof_mathlib_dep", "remove_proof_dep", "clear_proof_deps"} <= proof_worker_tools
    assert "add_current_node_dep" in proof_worker_tools
    assert "search_arxiv_theorems" not in proof_worker_tools
    assert "record_proof_formal_review_passed" in proof_reviewer_tools
    assert "record_proof_formal_review_rejected" in proof_reviewer_tools
    assert "record_decl_review" not in proof_reviewer_tools
    assert "inspect_current_stage_review_status" in proof_reviewer_tools
    assert "sync_decl_file_after_revision_reset" not in statement_worker_tools | proof_worker_tools
    assert "remove_decl_file_for_delete" not in statement_worker_tools | proof_worker_tools


def test_repo_format_discovery_surface_matches_remote_only_design() -> None:
    reports = build_agent_surface_reports()
    report = reports["RepoFormatDiscoveryAgent"]
    tools = {tool.name for tool in report.application_tools}

    assert {
        "get_preparation_input",
        "get_preparation_start_preflight",
        "list_preparation_requirements",
        "get_preparation_requirement",
        "inspect_workspace_for_coordinator",
        "search_github_lean_repositories",
        "inspect_github_lean_repository",
        "probe_github_lean_repo_candidate",
        "get_github_repository",
        "list_github_repository_tree",
        "read_github_repository_file",
        "search_github_code",
    } == tools
    assert {
        "repo_preparation_input_read",
        "repo_preparation_requirement_read",
        "workspace_repo_catalog_read",
        "upstream_repo_search",
        "github_repository_read",
    } == set(report.application_group_keys)
    assert {
        "list_open_requirement_groups",
        "get_requirement_group",
        "list_requirement_resume_candidates",
        "checkout_repository",
        "probe_lean_repo",
    }.isdisjoint(tools)


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
    assert "search_arxiv_theorems" in {tool.name for tool in reports["ProofNLWorkerAgent"].application_tools}


def test_source_prepare_and_resource_curator_keep_acquisition_boundaries() -> None:
    reports = build_agent_surface_reports()
    source_tools = {tool.name for tool in reports["SourceCorpusPrepareAgent"].application_tools}
    curator_tools = {tool.name for tool in reports["ResourceCuratorAgent"].application_tools}

    source_write_tools = {
        "acquire_source_material",
        "import_source_material",
        "extract_source_artifact",
        "normalize_source_text_material",
    }
    resource_write_tools = {
        "acquire_resource_material",
        "import_resource_material",
        "extract_resource_artifact",
        "normalize_resource_text_material",
    }

    assert source_write_tools <= source_tools
    assert resource_write_tools.isdisjoint(source_tools)
    assert resource_write_tools <= curator_tools
    assert source_write_tools.isdisjoint(curator_tools)
    assert {"scan_source_corpus", "search_material_text", "get_source_index"} <= curator_tools
