from __future__ import annotations

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
        "preview_delete_node",
        "delete_node",
        "list_current_visible_node_boundaries",
        "list_current_node_deps",
        "add_current_node_dep",
        "remove_current_node_dep",
        "add_current_material_ref",
        "remove_current_material_ref",
        "list_node_material_refs",
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
        "get_scope_close_view",
        "get_repo_ready_node_view",
        "check_content_task_admission",
        "check_content_node_batch",
    }

    assert_tools_registered(expected)


def test_node_contract_groups_expose_expected_tools() -> None:
    assert_group_contains("node_contract_read_current", {"get_current_node_contract", "list_current_node_deps", "list_node_material_refs"})
    assert_group_contains("node_contract_read_coordinator", {"get_node_contract"})
    assert_group_contains("node_tree_coordinator_read", {"get_node_tree", "get_node"})
    assert_group_contains("node_tree_coordinator_write", {"create_scope_node", "create_content_node", "preview_delete_node", "delete_node"})
    assert_group_contains("node_contract_core_coordinator_write", {"update_node_contract_text"})
    assert_group_contains("node_contract_dependency_current_write", {"add_current_node_dep", "remove_current_node_dep"})
    assert_group_contains("node_contract_material_current_write", {"add_current_material_ref", "remove_current_material_ref"})
    assert_group_contains("scope_export_interface_read", {"list_node_interfaces", "list_scope_exports", "list_scope_export_candidates"})
    assert_group_contains("scope_export_interface_write", {"add_node_interface", "bind_node_interface", "add_scope_export"})
    assert_group_contains("scope_close_read", {"get_scope_close_view"})
    assert_group_contains("repo_ready_read", {"get_repo_ready_node_view"})
    assert_group_contains("content_task_admission_read", {"list_runnable_content_nodes", "check_content_task_admission", "check_content_node_batch"})
