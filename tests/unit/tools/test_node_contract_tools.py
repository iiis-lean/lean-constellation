from __future__ import annotations

from lean_constellation.tools import build_application_tool_specs
from lean_constellation.tools.args import (
    ContractCoreUpdateArgs,
    CreateContentNodeArgs,
    CreateScopeNodeArgs,
    CurrentMaterialRefRemoveArgs,
    NodeDependencyRemoveArgs,
    NodeMaterialRefRemoveArgs,
)
from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_node_contract_tools_are_registered() -> None:
    expected = {
        "get_current_node_contract",
        "get_node_contract",
        "get_node_tree",
        "get_node",
        "list_runnable_content_nodes",
        "create_scope_node",
        "create_content_node",
        "update_node_contract_text",
        "set_node_contract_task_completion_mode",
        "preview_delete_node",
        "delete_node",
        "list_current_node_deps",
        "add_current_node_dep",
        "remove_current_node_dep",
        "add_node_dep",
        "remove_node_dep",
        "add_current_material_ref",
        "remove_current_material_ref",
        "add_node_material_ref",
        "remove_node_material_ref",
        "list_current_node_material_refs",
        "list_node_material_refs",
        "list_node_interfaces",
        "list_root_interfaces",
        "get_root_interface_run_context",
        "add_root_interface",
        "add_node_interface",
        "update_node_interface",
        "remove_node_interface",
        "bind_current_node_interface",
        "bind_node_interface",
        "unbind_node_interface",
        "list_recent_content_task_results",
        "inspect_content_task_result",
        "commit_content_contract",
        "list_scope_export_candidates",
        "list_scope_exports",
        "add_scope_export",
        "remove_scope_export",
        "commit_scope_contract",
        "get_scope_close_view",
        "get_repo_ready_node_view",
        "check_content_task_admission",
        "check_content_node_batch",
    }

    assert_tools_registered(expected)


def test_node_contract_remove_schemas_do_not_expose_unused_reason() -> None:
    for args_model in (
        NodeDependencyRemoveArgs,
        CurrentMaterialRefRemoveArgs,
        NodeMaterialRefRemoveArgs,
    ):
        assert "reason" not in args_model.model_json_schema()["properties"]


def test_node_contract_text_schemas_distinguish_stable_and_current_fields() -> None:
    scope = CreateScopeNodeArgs.model_json_schema()["properties"]
    content = CreateContentNodeArgs.model_json_schema()["properties"]
    update = ContractCoreUpdateArgs.model_json_schema()["properties"]

    assert "across contract versions" in scope["goal"]["description"]
    assert "excluded from siblings" in scope["boundary"]["description"]
    assert "Current contract-version action" in scope["objective"]["description"]
    assert "observable closeout conditions" in scope["success_criteria"]["description"]

    assert "across contract versions" in content["goal"]["description"]
    assert "Current contract-version action" in content["objective"]["description"]
    assert "target depth" not in content["objective"]["description"]
    assert "current content contract version" in content["success_criteria"]["description"]

    assert "enduring purpose changes" in update["goal"]["description"]
    assert "Main.goal is protected" in update["goal"]["description"]
    assert "current contract-version action" in update["objective"]["description"]


def test_node_contract_groups_expose_expected_tools() -> None:
    assert_group_contains(
        "node_contract_read_current",
        {"get_current_node_contract", "list_current_node_deps", "list_current_node_material_refs"},
    )
    assert_group_contains("node_contract_read_by_node", {"get_node_contract", "list_node_material_refs"})
    assert_group_contains("node_tree_read", {"get_node_tree", "get_node"})
    assert_group_contains("node_tree_write", {"create_scope_node", "create_content_node", "preview_delete_node", "delete_node"})
    assert_group_contains("node_contract_text_write_by_node", {"update_node_contract_text"})
    assert_group_contains(
        "node_contract_task_target_write_by_node",
        {"set_node_contract_task_completion_mode"},
    )
    assert_group_contains("node_contract_dependency_current_write", {"add_current_node_dep", "remove_current_node_dep"})
    assert_group_contains("node_contract_material_current_write", {"add_current_material_ref", "remove_current_material_ref"})
    assert_group_contains("node_contract_dependency_write_by_node", {"add_node_dep", "remove_node_dep"})
    assert_group_contains("node_contract_material_write_by_node", {"add_node_material_ref", "remove_node_material_ref"})
    assert_group_contains("scope_export_interface_read", {"list_node_interfaces", "list_scope_exports", "list_scope_export_candidates"})
    assert_group_contains("scope_export_interface_write", {"add_node_interface", "bind_node_interface", "add_scope_export"})
    assert_group_contains("content_interface_current_write", {"bind_current_node_interface"})
    assert_group_contains("root_interface_state_read", {"list_root_interfaces"})
    assert_group_contains("root_interface_prepare_read", {"get_root_interface_run_context"})
    assert_group_contains("root_interface_append_write", {"add_root_interface"})
    assert_group_contains("scope_contract_commit", {"commit_scope_contract"})
    assert_group_contains(
        "content_task_result_finalize",
        {"list_recent_content_task_results", "inspect_content_task_result", "commit_content_contract"},
    )
    assert_group_contains("scope_close_read", {"get_scope_close_view"})
    assert_group_contains("repo_ready_read", {"get_repo_ready_node_view"})
    assert_group_contains("content_task_admission_read", {"list_runnable_content_nodes", "check_content_task_admission", "check_content_node_batch"})


def test_current_content_interface_binding_is_plan_or_admin_only() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert specs["bind_current_node_interface"].allowed_roles == {"plan", "admin"}
    assert specs["set_node_contract_task_completion_mode"].allowed_roles == {
        "coordinator",
        "admin",
    }


def test_node_contract_mutation_result_views_match_receipts() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    for name in {
        "add_current_node_dep",
        "remove_current_node_dep",
        "add_node_dep",
        "remove_node_dep",
    }:
        assert specs[name].result_view == "node_dependency_mutation"
    for name in {
        "add_current_material_ref",
        "remove_current_material_ref",
        "add_node_material_ref",
        "remove_node_material_ref",
    }:
        assert specs[name].result_view == "current_node_material_mutation"
    for name in {
        "add_root_interface",
        "add_node_interface",
        "update_node_interface",
        "remove_node_interface",
    }:
        assert specs[name].result_view == "interface_mutation"


def test_dependency_and_scope_tool_descriptions_require_stable_child_boundaries() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert "stable committed visible node boundary" in specs[
        "add_current_node_dep"
    ].description
    assert "stable committed visible node boundary" in specs["add_node_dep"].description
    assert "active committed direct-child Content heads or Scope exports" in specs[
        "list_scope_export_candidates"
    ].description
    assert "active committed direct-child boundary" in specs[
        "add_scope_export"
    ].description
