from __future__ import annotations

from lean_constellation.tools import build_application_tool_specs

from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_decl_graph_tools_are_registered() -> None:
    expected = {
        "ensure_current_decl_graph",
        "get_current_decl_graph_index",
        "get_current_decl_graph_store",
        "get_node_decl_graph_index",
        "get_node_decl_graph_store",
        "rebuild_current_decl_graph_index",
        "ensure_open_decl_strategy",
        "close_decl_strategy",
        "list_decl_strategies",
        "get_decl_strategy",
        "create_decl_round_draft",
        "list_decl_rounds",
        "get_decl_round",
        "write_decl_change_summary",
        "write_decl_round_summary",
        "mark_decl_round_terminal",
        "plan_create_decl",
        "plan_update_decl",
        "plan_delete_decl",
        "list_current_decls",
        "list_current_node_decls",
        "get_decl",
        "inspect_current_node_decl",
        "list_node_decls",
        "inspect_node_decl",
        "get_decl_revision",
        "get_decl_change",
        "preview_decl_delete_closure",
        "validate_decl_round_draft",
        "compute_decl_dependency_closure",
        "compute_current_node_decl_dependency_closure",
        "preview_current_node_decl_delete_closure",
        "check_decl_ready",
        "list_content_public_decls",
        "list_visible_nodes",
        "list_imported_repos",
        "list_current_node_public_decls",
        "inspect_current_node_public_decl",
        "list_node_public_decls",
        "inspect_node_public_decl",
        "list_repo_public_decls",
        "inspect_repo_public_decl",
        "list_active_decl_names",
        "check_content_node_ready",
        "check_current_content_node_completion",
        "run_decl_round_local_audit",
    }

    assert_tools_registered(expected)


def test_decl_graph_groups_expose_expected_tools() -> None:
    assert_group_contains("decl_graph_read_current", {"get_current_decl_graph_index", "list_decl_strategies"})
    assert_group_contains(
        "decl_graph_read_coordinator",
        {"get_node_decl_graph_index", "get_node_decl_graph_store", "list_node_decls", "inspect_node_decl"},
    )
    assert_group_contains("decl_graph_current_write", {"ensure_current_decl_graph", "rebuild_current_decl_graph_index"})
    assert_group_contains("decl_strategy_write", {"ensure_open_decl_strategy", "close_decl_strategy"})
    assert_group_contains("decl_round_change_write", {"create_decl_round_draft", "plan_create_decl", "validate_decl_round_draft"})
    assert_group_contains("decl_round_closeout_write", {"write_decl_change_summary", "write_decl_round_summary", "mark_decl_round_terminal"})
    assert_group_contains("decl_catalog_plan_write", {"plan_create_decl", "plan_update_decl", "plan_delete_decl"})
    assert_group_contains("decl_detail_read", {"list_current_decls", "get_decl"})
    assert_group_contains("decl_history_read", {"get_decl_revision", "get_decl_change"})
    assert_group_contains("decl_readiness_read", {"compute_decl_dependency_closure", "check_decl_ready", "check_content_node_ready"})
    assert_group_contains("current_node_decl_read", {"list_current_node_decls", "inspect_current_node_decl"})
    assert_group_contains(
        "decl_dependency_analysis_read",
        {"compute_current_node_decl_dependency_closure", "preview_current_node_decl_delete_closure"},
    )
    assert_group_contains("node_visibility_read_current", {"list_visible_nodes", "list_imported_repos"})
    assert_group_contains(
        "public_decl_read",
        {"list_current_node_public_decls", "inspect_current_node_public_decl", "list_node_public_decls", "inspect_node_public_decl"},
    )
    assert_group_contains("content_completion_gate_read", {"check_current_content_node_completion"})


def test_public_boundary_tool_descriptions_are_role_neutral() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    for name in (
        "list_visible_nodes",
        "list_imported_repos",
        "list_repo_public_decls",
        "inspect_repo_public_decl",
    ):
        description = specs[name].description
        assert "Coordinator" not in description
        assert "worker" not in description
        assert "current context" in description


def test_cross_node_decl_tool_descriptions_are_actor_neutral() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}
    expected = {
        "get_node_decl_graph_index": "Read the DeclGraph index for one permitted node in the current repository.",
        "get_node_decl_graph_store": "Read DeclGraph store counts and paths for one permitted node in the current repository.",
        "list_node_decls": "List all public and private declarations in one permitted node of the current repository.",
        "inspect_node_decl": "Inspect one public or private declaration revision in a permitted node of the current repository.",
    }

    assert {name: specs[name].description for name in expected} == expected
