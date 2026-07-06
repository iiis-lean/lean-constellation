from __future__ import annotations

from lean_constellation.services import create_test_runtime_services
from lean_constellation.tools import build_application_tool_specs


def test_every_application_view_expands_without_overlap() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    view_keys = sorted(runtime.tool_facade.tool_view._views)

    assert view_keys
    for view_key in view_keys:
        expanded = runtime.tool_facade.tool_view.tool_names_for_view(view_key)
        assert expanded.ok, f"{view_key}: {expanded.issues}"
        assert expanded.value is not None
        assert len(expanded.value) == len(set(expanded.value))


def test_representative_agent_type_resolves_expected_view() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.build_tool_view("CoordinatorAgent")
    plan = runtime.tool_facade.build_tool_view("ContentPlanAgent")
    statement_worker = runtime.tool_facade.build_tool_view("StatementNLWorkerAgent", {"stage": "statement_nl"})

    assert coordinator.ok
    assert coordinator.value is not None
    assert coordinator.value.key == "native_repo_coordinator"
    assert plan.ok
    assert plan.value is not None
    assert plan.value.key == "content_plan"
    assert statement_worker.ok
    assert statement_worker.value is not None
    assert statement_worker.value.key == "statement_nl_worker"


def test_resource_discovery_tools_visible_to_coordinator_and_resource_recon() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")
    resource_recon = runtime.tool_facade.tool_view.tool_names_for_view("resource_recon")

    assert coordinator.ok and coordinator.value is not None
    assert resource_recon.ok and resource_recon.value is not None
    assert {"search_material_text", "search_arxiv_theorems"} <= set(coordinator.value)
    assert {"search_material_text", "search_arxiv_theorems"} <= set(resource_recon.value)


def test_repo_work_config_tool_visible_to_coordinator_and_content_plan_only() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.tool_view.tool_names_for_view("native_repo_coordinator")
    content_plan = runtime.tool_facade.tool_view.tool_names_for_view("content_plan")
    statement_worker = runtime.tool_facade.tool_view.tool_names_for_view("statement_nl_worker")

    assert coordinator.ok and coordinator.value is not None
    assert content_plan.ok and content_plan.value is not None
    assert statement_worker.ok and statement_worker.value is not None
    assert "get_current_repo_work_config" in coordinator.value
    assert "get_current_repo_work_config" in content_plan.value
    assert "get_current_repo_work_config" not in statement_worker.value


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
    public_decl_tools = {
        "list_current_node_public_decls",
        "inspect_current_node_public_decl",
        "list_node_public_decls",
        "inspect_node_public_decl",
        "list_repo_public_decls",
        "inspect_repo_public_decl",
    }
    assert visibility_tools | public_decl_tools <= set(coordinator.value)
    assert visibility_tools | public_decl_tools <= set(content_plan.value)
    assert visibility_tools | public_decl_tools <= set(node_dir.value)
    assert visibility_tools | public_decl_tools <= set(statement_worker.value)


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
        "sync_decl_file_after_revision_reset",
        "remove_decl_file_for_delete",
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
        "sync_decl_file_after_revision_reset",
        "remove_decl_file_for_delete",
    } <= set(statement_worker.value)
    assert {
        "prepare_proof_formal_file",
        "capture_proof_formal_file",
        "sync_decl_file_after_revision_reset",
        "remove_decl_file_for_delete",
    } <= set(proof_worker.value)
    assert worker_write_tools.isdisjoint(statement_reviewer.value)
    assert worker_write_tools.isdisjoint(proof_reviewer.value)


def test_group_queries_return_registered_tools() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    expected = {spec.name for spec in build_application_tool_specs() if "mathlib_index_read" in spec.tool_groups}

    listed = runtime.tool_facade.list_registered_tools(group_key="mathlib_index_read")

    assert listed.ok
    assert {tool.name for tool in listed.value or []} == expected
