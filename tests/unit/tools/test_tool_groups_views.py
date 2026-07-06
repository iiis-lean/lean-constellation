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
