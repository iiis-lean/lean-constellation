from __future__ import annotations

from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_mathlib_tools_are_registered() -> None:
    expected = {
        "search_mathlib_index",
        "get_mathlib_module_entry",
        "get_mathlib_decl_entry",
        "record_mathlib_module",
        "record_mathlib_decl",
        "add_mathlib_module_important_decl",
        "search_external_mathlib",
        "search_mathlib_declarations",
        "inspect_mathlib_search_candidate",
        "inspect_mathlib_declaration",
        "inspect_mathlib_module",
        "check_mathlib_name",
        "ingest_mathlib_candidate",
        "search_arxiv_theorems",
        "get_current_node_mathlib_hints",
        "add_current_mathlib_hints",
        "remove_current_mathlib_module_hint",
        "remove_current_mathlib_decl_hint",
        "validate_current_node_mathlib_hints",
        "add_node_mathlib_module_hint",
        "remove_node_mathlib_module_hint",
        "add_node_mathlib_decl_hint",
        "remove_node_mathlib_decl_hint",
    }

    assert_tools_registered(expected)


def test_mathlib_groups_expose_expected_tools() -> None:
    assert_group_contains("mathlib_index_read", {"search_mathlib_index", "get_mathlib_module_entry", "get_mathlib_decl_entry"})
    assert_group_contains("mathlib_index_write", {"record_mathlib_module", "record_mathlib_decl", "ingest_mathlib_candidate"})
    assert_group_contains("mathlib_semantic_search", {"search_external_mathlib", "search_mathlib_declarations"})
    assert_group_contains("mathlib_navigation", {"inspect_mathlib_declaration", "inspect_mathlib_module", "check_mathlib_name"})
    assert_group_contains("external_resource_discovery", {"search_arxiv_theorems"})
    assert_group_contains("node_mathlib_hint_read", {"get_current_node_mathlib_hints", "validate_current_node_mathlib_hints"})
    assert_group_contains("node_mathlib_hint_write", {"add_current_mathlib_hints"})
    assert_group_contains("node_contract_mathlib_coordinator_write", {"add_node_mathlib_module_hint", "add_node_mathlib_decl_hint"})
