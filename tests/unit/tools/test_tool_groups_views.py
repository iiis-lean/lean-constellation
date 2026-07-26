from __future__ import annotations

from lean_constellation.services import create_test_runtime_services
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
)
from lean_constellation.tools.internal.source_material import _COMMITTED_SOURCE_INDEX_VIEWS
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup


def test_every_application_view_expands_without_overlap() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    view_keys = sorted(runtime.tool_facade.tool_view._views)

    assert view_keys
    for view_key in view_keys:
        expanded = runtime.tool_facade.tool_view.tool_names_for_view(view_key)
        assert expanded.ok, f"{view_key}: {expanded.issues}"
        assert expanded.value is not None
        assert len(expanded.value) == len(set(expanded.value))


def test_source_index_navigation_views_match_handler_routing() -> None:
    navigation_views = {
        view.key
        for view in build_application_tool_views()
        if AppGroup.SOURCE_INDEX_NAVIGATION_READ.value in view.group_keys
    }
    committed_read_views = navigation_views - {"source_index_builder", "source_index_reviewer"}

    assert _COMMITTED_SOURCE_INDEX_VIEWS == committed_read_views
    assert "content_plan" in committed_read_views
    assert "statement_formal_worker" in committed_read_views


def test_application_registry_is_orthogonal_and_fully_exposed() -> None:
    specs = build_application_tool_specs()
    groups = build_application_tool_groups(specs)
    views = build_application_tool_views(groups)
    group_by_key = {group.key: group for group in groups}

    assert len(specs) == 244
    assert all(len(spec.tool_groups) == 1 for spec in specs)
    assert all(group.tool_names for group in groups)
    assert all(view.extra_tool_names == [] for view in views)

    used_groups = {group_key for view in views for group_key in view.group_keys}
    assert used_groups == set(group_by_key)

    exposed_tools = {
        tool_name
        for view in views
        for group_key in view.group_keys
        for tool_name in group_by_key[group_key].tool_names
    }
    assert exposed_tools == {spec.name for spec in specs}

    for view in views:
        names = [
            tool_name
            for group_key in view.group_keys
            for tool_name in group_by_key[group_key].tool_names
        ]
        assert len(names) == len(set(names)), view.key


def test_representative_agent_type_resolves_expected_view() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.build_tool_view("CoordinatorAgent")
    plan = runtime.tool_facade.build_tool_view("ContentPlanAgent")
    statement_worker = runtime.tool_facade.build_tool_view("StatementNLWorkerAgent", {"stage": "statement_nl"})
    statement_reviewer = runtime.tool_facade.build_tool_view("StatementNLReviewerAgent", {"stage": "statement_nl"})
    formal_reviewer = runtime.tool_facade.build_tool_view("StatementFormalReviewerAgent", {"stage": "statement_formal"})
    adapter_catalog = runtime.tool_facade.build_tool_view("AdapterDeclCatalogAgent")

    assert coordinator.ok
    assert coordinator.value is not None
    assert coordinator.value.key == "native_repo_coordinator"
    assert plan.ok
    assert plan.value is not None
    assert plan.value.key == "content_plan"
    assert statement_worker.ok
    assert statement_worker.value is not None
    assert statement_worker.value.key == "statement_nl_worker"
    assert statement_reviewer.ok
    assert statement_reviewer.value is not None
    assert statement_reviewer.value.key == "statement_nl_reviewer"
    assert formal_reviewer.ok
    assert formal_reviewer.value is not None
    assert formal_reviewer.value.key == "statement_formal_reviewer"
    assert adapter_catalog.ok
    assert adapter_catalog.value is not None
    assert adapter_catalog.value.key == "adapter_repo_import"


def test_content_plan_exposes_only_current_node_interface_binding() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    view = runtime.tool_facade.tool_view.tool_names_for_view("content_plan")

    assert view.ok and view.value is not None
    tools = set(view.value)
    assert "bind_current_node_interface" in tools
    assert "bind_node_interface" not in tools
    assert "unbind_node_interface" not in tools


def test_repo_format_discovery_view_exposes_scoped_remote_tools_only() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    view = runtime.tool_facade.tool_view.tool_names_for_view("repo_format_discovery")

    assert view.ok and view.value is not None
    tools = set(view.value)
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
        "list_open_requirement_groups",
        "get_requirement_group",
        "list_requirement_resume_candidates",
        "checkout_repository",
        "probe_lean_repo",
    }.isdisjoint(tools)


def test_resource_discovery_tools_visible_to_coordinator_and_resource_recon() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")
    resource_recon = runtime.tool_facade.tool_view.tool_names_for_view("resource_recon")

    assert coordinator.ok and coordinator.value is not None
    assert resource_recon.ok and resource_recon.value is not None
    assert {"search_source_text", "search_resource_text", "search_arxiv_theorems"} <= set(coordinator.value)
    assert {"search_source_text", "search_resource_text", "search_arxiv_theorems"} <= set(resource_recon.value)


def test_adapter_decl_catalog_view_exposes_catalog_boundary_only() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    view = runtime.tool_facade.tool_view.tool_names_for_view("adapter_repo_import")

    assert view.ok and view.value is not None
    tools = set(view.value)
    assert {
        "list_preparation_requirements",
        "get_preparation_requirement",
        "list_root_interfaces",
        "find_adapter_decl_by_upstream",
        "check_adapter_catalog_ready_preflight",
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
    }.isdisjoint(tools)


def test_repo_work_config_tool_visible_to_coordinator_and_content_plan_only() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")
    content_plan = runtime.tool_facade.tool_view.tool_names_for_view("content_plan")
    statement_worker = runtime.tool_facade.tool_view.tool_names_for_view("statement_nl_worker")

    assert coordinator.ok and coordinator.value is not None
    assert content_plan.ok and content_plan.value is not None
    assert statement_worker.ok and statement_worker.value is not None
    assert "get_current_repo_completion_policy" in coordinator.value
    assert "get_current_repo_completion_policy" in content_plan.value
    assert "get_current_repo_completion_policy" not in statement_worker.value


def test_current_requirement_read_replaces_requirement_attach_on_coordinator_surface() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")

    assert coordinator.ok and coordinator.value is not None
    assert "get_current_repo_requirement" in coordinator.value
    assert "attach_requirement_provider_dependency" not in coordinator.value
    assert "list_requirement_resume_candidates" not in coordinator.value
    assert "mark_requirement_result_observed" not in coordinator.value


def test_coordinator_uses_workspace_and_tree_reads_instead_of_node_visibility_tools() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")

    assert coordinator.ok and coordinator.value is not None
    assert {"list_visible_nodes", "list_imported_repos"}.isdisjoint(coordinator.value)
    assert {
        "get_node_tree",
        "list_ready_provider_repos",
        "list_repo_public_decls",
        "inspect_repo_public_decl",
    } <= set(coordinator.value)


def test_public_decl_read_tools_visible_to_coordinator_plan_and_recon() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")
    content_plan = runtime.tool_facade.tool_view.tool_names_for_view("content_plan")
    node_dir = runtime.tool_facade.tool_view.tool_names_for_view("node_dir_dependency_recon")
    statement_worker = runtime.tool_facade.tool_view.tool_names_for_view("statement_nl_worker")

    assert coordinator.ok and coordinator.value is not None
    assert content_plan.ok and content_plan.value is not None
    assert node_dir.ok and node_dir.value is not None
    assert statement_worker.ok and statement_worker.value is not None
    visibility_tools = {"list_visible_nodes", "list_imported_repos"}
    current_public_decl_tools = {
        "list_current_node_public_decls",
        "inspect_current_node_public_decl",
    }
    path_public_decl_tools = {
        "list_node_public_decls",
        "inspect_node_public_decl",
        "list_repo_public_decls",
        "inspect_repo_public_decl",
    }
    assert visibility_tools.isdisjoint(coordinator.value)
    assert path_public_decl_tools <= set(coordinator.value)
    assert "read_visible_decl_lean_file" in coordinator.value
    assert current_public_decl_tools.isdisjoint(coordinator.value)
    assert visibility_tools | current_public_decl_tools | path_public_decl_tools <= set(content_plan.value)
    assert "read_visible_decl_lean_file" in content_plan.value
    assert visibility_tools | current_public_decl_tools | path_public_decl_tools <= set(node_dir.value)
    assert "read_visible_decl_lean_file" in node_dir.value
    assert visibility_tools | current_public_decl_tools | path_public_decl_tools <= set(statement_worker.value)
    assert "read_visible_decl_lean_file" in statement_worker.value


def test_current_node_decl_read_tools_visible_to_content_plan_only() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    content_plan = runtime.tool_facade.tool_view.tool_names_for_view("content_plan")
    node_dir = runtime.tool_facade.tool_view.tool_names_for_view("node_dir_dependency_recon")
    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")

    assert content_plan.ok and content_plan.value is not None
    assert node_dir.ok and node_dir.value is not None
    assert coordinator.ok and coordinator.value is not None
    current_node_decl_tools = {"list_current_node_decls", "inspect_current_node_decl"}
    dependency_analysis_tools = {"compute_current_node_decl_dependency_closure", "preview_current_node_decl_delete_closure"}
    assert current_node_decl_tools | dependency_analysis_tools <= set(content_plan.value)
    assert dependency_analysis_tools.isdisjoint(node_dir.value)
    assert current_node_decl_tools.isdisjoint(node_dir.value)
    assert current_node_decl_tools.isdisjoint(coordinator.value)
    coordinator_node_decl_tools = {
        "get_node_decl_graph_index",
        "get_node_decl_graph_store",
        "list_node_decls",
        "inspect_node_decl",
    }
    assert coordinator_node_decl_tools <= set(coordinator.value)
    assert coordinator_node_decl_tools.isdisjoint(content_plan.value)
    assert coordinator_node_decl_tools.isdisjoint(node_dir.value)


def test_legacy_decl_readiness_tools_are_not_in_production_views() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    legacy_tools = {
        "check_decl_ready",
        "check_content_node_ready",
        "list_content_public_decls",
        "list_current_visible_node_boundaries",
        "list_current_decls",
        "get_decl",
        "get_decl_revision",
        "get_decl_change",
        "compute_decl_dependency_closure",
    }

    for view_key in sorted(runtime.tool_facade.tool_view._views):
        expanded = runtime.tool_facade.tool_view.tool_names_for_view(view_key)
        assert expanded.ok and expanded.value is not None
        assert legacy_tools.isdisjoint(expanded.value), f"{view_key} still exposes legacy tools"


def test_internal_and_legacy_tools_are_not_registered_as_application_tools() -> None:
    retired = {
        "list_current_visible_node_boundaries",
        "list_current_decls",
        "get_decl",
        "get_decl_change",
        "compute_decl_dependency_closure",
        "check_decl_ready",
        "list_content_public_decls",
        "check_content_node_ready",
        "allocate_resource_draft",
        "abandon_resource_draft",
        "write_adapter_upstream_metadata",
        "mark_upstream_build_trusted",
        "record_visible_upstream_modules",
        "ensure_adapter_decl_catalog",
        "refresh_adapter_projection",
        "attach_requirement_provider_dependency",
        "list_requirement_resume_candidates",
        "mark_requirement_result_observed",
        "update_root_interface",
        "remove_root_interface",
    }

    assert retired.isdisjoint({spec.name for spec in build_application_tool_specs()})


def test_coordinator_contract_closeout_tools_only_visible_to_coordinator() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")
    root_interface = runtime.tool_facade.tool_view.tool_names_for_view("root_interface_prepare")
    content_plan = runtime.tool_facade.tool_view.tool_names_for_view("content_plan")
    node_dir = runtime.tool_facade.tool_view.tool_names_for_view("node_dir_dependency_recon")
    statement_worker = runtime.tool_facade.tool_view.tool_names_for_view("statement_nl_worker")

    assert coordinator.ok and coordinator.value is not None
    assert root_interface.ok and root_interface.value is not None
    assert content_plan.ok and content_plan.value is not None
    assert node_dir.ok and node_dir.value is not None
    assert statement_worker.ok and statement_worker.value is not None
    closeout_tools = {
        "list_recent_content_task_results",
        "inspect_content_task_result",
        "commit_content_contract",
        "commit_scope_contract",
    }
    assert closeout_tools <= set(coordinator.value)
    assert closeout_tools.isdisjoint(root_interface.value)
    assert closeout_tools.isdisjoint(content_plan.value)
    assert closeout_tools.isdisjoint(node_dir.value)
    assert closeout_tools.isdisjoint(statement_worker.value)


def test_decl_graph_store_write_tools_only_visible_to_content_plan() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    content_plan = runtime.tool_facade.tool_view.tool_names_for_view("content_plan")
    statement_worker = runtime.tool_facade.tool_view.tool_names_for_view("statement_nl_worker")
    statement_reviewer = runtime.tool_facade.tool_view.tool_names_for_view("statement_nl_reviewer")

    assert content_plan.ok and content_plan.value is not None
    assert statement_worker.ok and statement_worker.value is not None
    assert statement_reviewer.ok and statement_reviewer.value is not None
    assert {"ensure_current_decl_graph", "rebuild_current_decl_graph_index"} <= set(content_plan.value)
    assert "ensure_current_decl_graph" not in statement_worker.value
    assert "rebuild_current_decl_graph_index" not in statement_worker.value
    assert "ensure_current_decl_graph" not in statement_reviewer.value
    assert "rebuild_current_decl_graph_index" not in statement_reviewer.value


def test_formal_reviewer_views_do_not_expose_worker_file_write_tools() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    worker_write_tools = {
        "prepare_statement_formal_file",
        "capture_statement_formal_file",
        "prepare_proof_formal_file",
        "capture_proof_formal_file",
    }

    statement_worker = runtime.tool_facade.tool_view.tool_names_for_view("statement_formal_worker")
    proof_worker = runtime.tool_facade.tool_view.tool_names_for_view("proof_formal_worker")
    statement_reviewer = runtime.tool_facade.tool_view.tool_names_for_view("statement_formal_reviewer")
    proof_reviewer = runtime.tool_facade.tool_view.tool_names_for_view("proof_formal_reviewer")

    assert statement_worker.ok and statement_worker.value is not None
    assert proof_worker.ok and proof_worker.value is not None
    assert statement_reviewer.ok and statement_reviewer.value is not None
    assert proof_reviewer.ok and proof_reviewer.value is not None
    assert {
        "prepare_statement_formal_file",
        "capture_statement_formal_file",
    } <= set(statement_worker.value)
    assert {
        "prepare_proof_formal_file",
        "capture_proof_formal_file",
    } <= set(proof_worker.value)
    assert worker_write_tools.isdisjoint(statement_reviewer.value)
    assert worker_write_tools.isdisjoint(proof_reviewer.value)
    assert "sync_decl_file_after_revision_reset" not in statement_worker.value
    assert "remove_decl_file_for_delete" not in statement_worker.value
    assert "sync_decl_file_after_revision_reset" not in proof_worker.value
    assert "remove_decl_file_for_delete" not in proof_worker.value


def test_group_queries_return_registered_tools() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    expected = {spec.name for spec in build_application_tool_specs() if "mathlib_index_read" in spec.tool_groups}

    listed = runtime.tool_facade.list_registered_tools(group_key="mathlib_index_read")

    assert listed.ok
    assert {tool.name for tool in listed.value or []} == expected
