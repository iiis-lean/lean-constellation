from __future__ import annotations

from hashlib import sha256

from lean_constellation.agents import build_agent_type_specs, build_agent_surface_reports
from lean_constellation.tools import build_application_tool_specs, build_submit_tool_specs


EXPECTED_SURFACE_COUNTS = {
    "RepoFormatDiscoveryAgent": (6, 12, 1, 2, 0),
    "SourceCorpusPrepareAgent": (3, 7, 1, 2, 1),
    "SourceIndexBuilderAgent": (6, 25, 1, 1, 0),
    "SourceIndexReviewerAgent": (5, 14, 1, 1, 0),
    "RootInterfacePrepareAgent": (7, 15, 1, 1, 0),
    "AdapterDeclCatalogAgent": (12, 39, 1, 2, 0),
    "ResourceCuratorAgent": (8, 24, 1, 4, 2),
    "CoordinatorAgent": (38, 89, 2, 4, 17),
    "ContentPlanAgent": (32, 82, 3, 6, 15),
    "NodeDirDependencyReconAgent": (7, 14, 1, 1, 2),
    "MathlibReconAgent": (7, 22, 1, 1, 5),
    "ResourceReconAgent": (8, 22, 2, 3, 4),
    "StatementNLWorkerAgent": (19, 51, 1, 2, 4),
    "StatementNLReviewerAgent": (18, 43, 1, 1, 2),
    "StatementFormalWorkerAgent": (24, 53, 1, 2, 7),
    "StatementFormalReviewerAgent": (19, 44, 1, 1, 2),
    "ProofNLWorkerAgent": (25, 61, 1, 2, 6),
    "ProofNLReviewerAgent": (22, 48, 1, 1, 2),
    "ProofFormalWorkerAgent": (27, 62, 1, 2, 7),
    "ProofFormalReviewerAgent": (21, 47, 1, 1, 2),
}

EXPECTED_APPLICATION_SURFACE_HASHES = {
    "RepoFormatDiscoveryAgent": "6a9b3b7a40f76fe2845129db49a0f1d8b34e704cd764f27c98b2b3c86362ac98",
    "SourceCorpusPrepareAgent": "8a56bf36f9cc83b6ad7eef83155f55a77bd60516c1c6a31851b6053edbb09bd3",
    "SourceIndexBuilderAgent": "83c7d2aa3b835727f89779c44b92f708371a1b3e7c085b9b1b8787f4fc1e5876",
    "SourceIndexReviewerAgent": "71ed43ad69003736a91fbb5146983683c80cc23eb821ee5abf464d546a32641f",
    "RootInterfacePrepareAgent": "744c85a080bb7ec6ecf1e2923beee4fb683ce68347599ab76c2eee9b3e905cd9",
    "AdapterDeclCatalogAgent": "832d605c4fca89e0bace44a54ab04487dbf11c268c50629a606b8e6519006fb5",
    "ResourceCuratorAgent": "6b8d4e4823e83c81b3a971103810bcb4fb934d156f9fabed276d2245efd1e069",
    "CoordinatorAgent": "9c37af24edb14574de83c3d8152334d64e521d76131bdbd7064bfed5606f25f8",
    "ContentPlanAgent": "bf10268f6fe7b7e29b55c41abfbd066d1d2f0958e3a9a0648142b5b0ee3fa4dc",
    "NodeDirDependencyReconAgent": "78424e9c83a6d31e464f5bcfaa583279a967ed718f7783ed820bd5ea5419709c",
    "MathlibReconAgent": "2106d09b06fa7140322909262cdb5a533b4ba881b13bb74ee1932e714a000220",
    "ResourceReconAgent": "02a24ca89792c62f0048e410363e3cbe50f3adb1e07c1ba853dbdacb50ed97b8",
    "StatementNLWorkerAgent": "8cdeacab5e02c431e57e7ed682cd883d95e540bfc39f40c3bae4cb5797c4b48a",
    "StatementNLReviewerAgent": "811e80e3382d93e44f4fcc5777e2572856489ac6332d7f467413aba31275dc18",
    "StatementFormalWorkerAgent": "076a0713389bd1ee00e668aca6b3e236803085c1c5a9bbe1c306c396e243f425",
    "StatementFormalReviewerAgent": "2f138ac5b6b413969ff8ec81645d85aed4d8f090b7a5d1410c4c83fde7f67917",
    "ProofNLWorkerAgent": "6a168beaf86c3cd1616bb1000bd52a5f3df77c322c8ae630bde6e382ca9bd395",
    "ProofNLReviewerAgent": "5e9698c61cd57f7fd0485476c5b02059b95042221b573b23db89f3063379bef8",
    "ProofFormalWorkerAgent": "e34179c14a3ff2c564aaec7ab6ef6d59d863825ea32b9e1fc5b319c766854fe3",
    "ProofFormalReviewerAgent": "374e489be28c239d3e7c356e223f8e3a78d63e006ea60a84abb415d0b2ba6f25",
}


def _assert_surface_tools_allow_role(agent_type: str, role: str) -> None:
    report = build_agent_surface_reports()[agent_type]
    application_specs = {tool.name: tool for tool in build_application_tool_specs()}
    submit_specs = {tool.name: tool for tool in build_submit_tool_specs()}

    for tool in report.application_tools:
        assert role in application_specs[tool.name].allowed_roles, tool.name
    for tool in report.submit_tools:
        assert role in submit_specs[tool.name].allowed_roles, tool.name


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
        names = sorted(tool.name for tool in report.application_tools)
        assert sha256("\n".join(names).encode()).hexdigest() == EXPECTED_APPLICATION_SURFACE_HASHES[agent_type]


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
    assert {
        "list_statement_dependencies",
        "add_statement_repo_dependency",
        "add_statement_repo_dependencies",
        "add_statement_mathlib_dependency",
        "add_statement_mathlib_dependencies",
        "remove_statement_dep",
        "clear_statement_deps",
    } <= statement_worker_tools
    assert {
        "list_statement_dependencies",
        "add_statement_mathlib_dependency",
        "add_statement_mathlib_dependencies",
    } <= statement_reviewer_tools
    assert {
        "add_statement_repo_dependency",
        "remove_statement_dep",
        "clear_statement_deps",
    }.isdisjoint(statement_reviewer_tools)
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
        "list_proof_dependencies",
        "add_proof_repo_dependency",
        "add_proof_repo_dependencies",
        "add_proof_mathlib_dependency",
        "add_proof_mathlib_dependencies",
        "remove_proof_dep",
        "clear_proof_deps",
    } <= proof_nl_worker_tools
    assert "write_proof_nl" not in proof_nl_worker_tools
    assert "record_proof_nl_review_passed" in proof_nl_reviewer_tools
    assert "record_proof_nl_review_rejected" in proof_nl_reviewer_tools
    assert "record_decl_review" not in proof_nl_reviewer_tools
    assert "inspect_current_stage_review_status" in proof_nl_reviewer_tools
    assert {
        "list_proof_dependencies",
        "add_proof_repo_dependency",
        "add_proof_repo_dependencies",
        "add_proof_mathlib_dependency",
        "add_proof_mathlib_dependencies",
        "remove_proof_dep",
        "clear_proof_deps",
    } <= proof_worker_tools
    assert {
        "list_proof_dependencies",
        "add_proof_mathlib_dependency",
        "add_proof_mathlib_dependencies",
    } <= proof_reviewer_tools
    assert {
        "add_proof_repo_dependency",
        "remove_proof_dep",
        "clear_proof_deps",
    }.isdisjoint(proof_reviewer_tools)
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
        "repo_preparation_start_preflight_read",
        "repo_preparation_requirement_read",
        "workspace_overview_read",
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


def test_coordinator_surface_matches_specific_agent_refactor() -> None:
    report = build_agent_surface_reports()["CoordinatorAgent"]
    tools = {tool.name for tool in report.application_tools}

    assert report.application_tool_view_key == "native_repo_coordinator"
    assert report.submit_tool_view_key == "native_repo_coordinator_submit"
    assert len(report.skills) == 17
    assert len(report.application_group_keys) == 38
    assert len(report.application_tools) == 89
    assert "read_visible_decl_lean_file" in tools
    assert len(report.submit_group_keys) == 2
    assert len(report.submit_tools) == 4
    assert {
        "list_requirement_resume_candidates",
        "mark_requirement_result_observed",
        "attach_requirement_provider_dependency",
        "list_visible_nodes",
        "list_imported_repos",
    }.isdisjoint(tools)
    assert {
        "get_current_repo_run_context",
        "get_current_repo_requirement",
        "list_ready_provider_repos",
        "list_repo_public_decls",
        "inspect_repo_public_decl",
        "attach_ready_workspace_repo_dependency",
        "get_node_tree",
        "get_node_decl_graph_index",
    } <= tools
    assert {
        "allocate_release_id",
        "create_release",
        "prepare_candidate_release",
        "commit_prepared_release",
        "mark_repo_stable",
    }.isdisjoint(tools)


def test_source_index_builder_and_reviewer_surfaces_match_draft_boundary() -> None:
    reports = build_agent_surface_reports()
    builder_tools = {tool.name for tool in reports["SourceIndexBuilderAgent"].application_tools}
    reviewer_tools = {tool.name for tool in reports["SourceIndexReviewerAgent"].application_tools}

    assert "create_draft_source_index" not in builder_tools
    assert {
        "scan_source_corpus",
        "check_source_corpus_draft",
        "read_source_range",
        "validate_source_range",
        "preview_source_ref",
        "get_source_index",
        "get_source_index_update_context",
        "get_source_index_coverage",
        "validate_source_index",
        "create_source_block",
        "add_source_block_ref",
        "mark_block_refs_done",
        "create_source_link",
        "mark_block_links_done",
        "mark_block_completed",
        "submit_source_index_builder_round",
    } <= builder_tools | {tool.name for tool in reports["SourceIndexBuilderAgent"].submit_tools}
    assert {
        "scan_source_corpus",
        "check_source_corpus_draft",
        "read_source_range",
        "validate_source_range",
        "preview_source_ref",
        "get_source_index",
        "get_source_index_coverage",
        "validate_source_index",
    } <= reviewer_tools
    assert {
        "set_source_index_overview",
        "create_source_block",
        "update_source_block",
        "add_source_block_ref",
        "remove_source_block_ref",
        "mark_block_refs_done",
        "create_source_link",
        "mark_block_links_done",
        "mark_block_completed",
        "set_file_survey_status",
        "set_file_indexing_status",
        "submit_source_index_builder_round",
    }.isdisjoint(reviewer_tools)


def test_source_index_surfaces_are_role_callable() -> None:
    _assert_surface_tools_allow_role("SourceIndexBuilderAgent", "worker")
    _assert_surface_tools_allow_role("SourceIndexReviewerAgent", "reviewer")


def test_root_interface_prepare_surface_uses_root_specific_tools() -> None:
    reports = build_agent_surface_reports()
    report = reports["RootInterfacePrepareAgent"]
    tools = {tool.name for tool in report.application_tools}

    assert {
        "get_preparation_input",
        "get_root_interface_run_context",
        "get_source_index",
        "get_source_index_coverage",
        "read_source_range",
        "validate_source_range",
        "preview_source_ref",
        "list_root_interfaces",
        "add_root_interface",
        "check_root_main_handoff_interfaces",
    } <= tools
    assert {
        "get_preparation_start_preflight",
        "list_node_interfaces",
        "add_node_interface",
        "update_node_interface",
        "remove_node_interface",
        "bind_node_interface",
        "unbind_node_interface",
        "list_scope_export_candidates",
        "list_scope_exports",
        "add_scope_export",
        "remove_scope_export",
        "update_root_interface",
        "remove_root_interface",
    }.isdisjoint(tools)
    assert report.skills == []


def test_root_interface_prepare_surface_is_worker_role_callable() -> None:
    _assert_surface_tools_allow_role("RootInterfacePrepareAgent", "worker")


def test_adapter_decl_catalog_surface_matches_catalog_only_boundary() -> None:
    reports = build_agent_surface_reports()
    report = reports["AdapterDeclCatalogAgent"]
    tools = {tool.name for tool in report.application_tools}

    assert {
        "get_preparation_input",
        "list_preparation_requirements",
        "get_preparation_requirement",
        "inspect_adapter_input",
        "list_root_interfaces",
        "get_adapter_upstream_metadata",
        "get_adapter_upstream_status",
        "search_upstream_declarations",
        "capture_upstream_declaration_code",
        "list_adapter_decls",
        "find_adapter_decl_by_upstream",
        "create_adapter_decl",
        "set_adapter_statement_formal",
        "set_adapter_statement_nl",
        "finalize_adapter_decl",
        "list_unbound_adapter_interfaces",
        "bind_adapter_interface",
        "validate_adapter_interface_bindings",
        "preview_adapter_import_modules",
        "check_adapter_catalog_ready_preflight",
        "check_adapter_ready",
    } <= tools
    assert {
        "get_preparation_start_preflight",
        "list_open_requirement_groups",
        "get_requirement_group",
        "write_adapter_upstream_metadata",
        "mark_upstream_build_trusted",
        "record_visible_upstream_modules",
        "ensure_adapter_decl_catalog",
        "refresh_adapter_projection",
        "add_root_interface",
        "update_root_interface",
        "remove_root_interface",
    }.isdisjoint(tools)
    assert {
        "repo_preparation_input_read",
        "repo_preparation_requirement_read",
        "adapter_input_read",
        "root_interface_state_read",
        "upstream_metadata_read",
        "upstream_navigation",
        "adapter_decl_catalog_read",
        "adapter_decl_catalog_write",
        "adapter_interface_binding_read",
        "adapter_interface_binding_write",
        "adapter_projection_check",
        "adapter_ready_read",
    } == set(report.application_group_keys)


def test_adapter_decl_catalog_surface_is_worker_role_callable() -> None:
    _assert_surface_tools_allow_role("AdapterDeclCatalogAgent", "worker")


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
    assert "bind_current_node_interface" in content_plan_tools
    assert "bind_node_interface" not in content_plan_tools
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
    assert {
        "scan_source_corpus",
        "search_source_text",
        "search_resource_text",
        "get_source_index_overview",
        "list_source_blocks",
        "get_source_block",
    } <= curator_tools
    assert "get_source_index" not in curator_tools
    assert {"get_resource_draft", "check_resource_draft"} <= curator_tools
    assert {"allocate_resource_draft", "abandon_resource_draft"}.isdisjoint(curator_tools)
