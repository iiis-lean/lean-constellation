from __future__ import annotations

from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_decl_graph_tools_are_registered() -> None:
    expected = {
        "ensure_current_decl_graph",
        "get_current_decl_graph_index",
        "get_current_decl_graph_store",
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
        "get_decl",
        "get_decl_revision",
        "get_decl_change",
        "preview_decl_delete_closure",
        "validate_decl_round_draft",
        "compute_decl_dependency_closure",
        "check_decl_ready",
        "list_content_public_decls",
        "list_active_decl_names",
        "check_content_node_ready",
        "run_decl_round_local_audit",
    }

    assert_tools_registered(expected)


def test_decl_graph_groups_expose_expected_tools() -> None:
    assert_group_contains("decl_graph_read_current", {"ensure_current_decl_graph", "get_current_decl_graph_index", "list_decl_strategies"})
    assert_group_contains("decl_strategy_write", {"ensure_open_decl_strategy", "close_decl_strategy"})
    assert_group_contains("decl_round_change_write", {"create_decl_round_draft", "plan_create_decl", "validate_decl_round_draft"})
    assert_group_contains("decl_round_closeout_write", {"write_decl_change_summary", "write_decl_round_summary", "mark_decl_round_terminal"})
    assert_group_contains("decl_catalog_plan_write", {"plan_create_decl", "plan_update_decl", "plan_delete_decl"})
    assert_group_contains("decl_detail_read", {"list_current_decls", "get_decl"})
    assert_group_contains("decl_history_read", {"get_decl_revision", "get_decl_change"})
    assert_group_contains("decl_readiness_read", {"compute_decl_dependency_closure", "check_decl_ready", "check_content_node_ready"})
