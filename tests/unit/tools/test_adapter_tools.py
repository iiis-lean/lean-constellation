from __future__ import annotations

from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_adapter_tools_are_registered() -> None:
    expected = {
        "inspect_adapter_input",
        "get_adapter_upstream_metadata",
        "get_adapter_upstream_status",
        "write_adapter_upstream_metadata",
        "mark_upstream_build_trusted",
        "record_visible_upstream_modules",
        "search_upstream_declarations",
        "search_upstream_modules",
        "list_upstream_module_declarations",
        "inspect_upstream_declaration",
        "read_upstream_source_context",
        "capture_upstream_declaration_code",
        "inspect_upstream_module_imports",
        "ensure_adapter_decl_catalog",
        "create_adapter_decl",
        "set_adapter_statement_formal",
        "set_adapter_statement_nl",
        "add_adapter_statement_origin",
        "add_adapter_statement_dep",
        "remove_adapter_statement_dep",
        "set_adapter_proof_formal",
        "set_adapter_proof_nl",
        "add_adapter_proof_origin",
        "add_adapter_proof_dep",
        "remove_adapter_proof_dep",
        "list_adapter_decls",
        "inspect_adapter_decl",
        "list_registered_adapter_modules",
        "check_adapter_decl_completeness",
        "find_adapter_decl_by_upstream",
        "finalize_adapter_decl",
        "bind_adapter_interface",
        "unbind_adapter_interface",
        "list_unbound_adapter_interfaces",
        "validate_adapter_interface_bindings",
        "preview_adapter_import_modules",
        "refresh_adapter_projection",
        "check_adapter_projection",
        "check_adapter_ready",
        "check_adapter_catalog_ready_preflight",
    }

    assert_tools_registered(expected)


def test_adapter_groups_expose_expected_tools() -> None:
    assert_group_contains("adapter_input_read", {"inspect_adapter_input"})
    assert_group_contains("upstream_metadata_read", {"get_adapter_upstream_metadata", "get_adapter_upstream_status"})
    assert_group_contains("upstream_metadata_write", {"write_adapter_upstream_metadata", "mark_upstream_build_trusted"})
    assert_group_contains("upstream_navigation", {"search_upstream_declarations", "capture_upstream_declaration_code"})
    assert_group_contains("adapter_decl_catalog_init_write", {"ensure_adapter_decl_catalog"})
    assert_group_contains("adapter_decl_catalog_write", {"create_adapter_decl", "finalize_adapter_decl"})
    assert_group_contains("adapter_decl_catalog_read", {"list_adapter_decls", "inspect_adapter_decl", "find_adapter_decl_by_upstream"})
    assert_group_contains("adapter_interface_binding_write", {"bind_adapter_interface", "unbind_adapter_interface"})
    assert_group_contains("adapter_interface_binding_read", {"list_unbound_adapter_interfaces", "validate_adapter_interface_bindings"})
    assert_group_contains("adapter_projection_check", {"preview_adapter_import_modules", "check_adapter_projection"})
    assert_group_contains("adapter_projection_write", {"refresh_adapter_projection"})
    assert_group_contains("adapter_ready_read", {"check_adapter_ready", "check_adapter_catalog_ready_preflight"})
