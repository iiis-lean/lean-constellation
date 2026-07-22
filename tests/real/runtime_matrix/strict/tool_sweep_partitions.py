"""Strict ToolSweep partitions for application tool execution evidence."""

from __future__ import annotations

from tests.real.runtime_matrix.strict.tool_cases import implemented_tool_cases


DECL_STAGE_FORMAL_TOOL_SWEEP_NAMES = frozenset(
    {
        "set_statement_nl",
        "add_statement_source_origin",
        "add_statement_resource_origin",
        "remove_statement_origin",
        "clear_statement_origins",
        "add_statement_decl_dep",
        "add_statement_mathlib_dep",
        "remove_statement_dep",
        "clear_statement_deps",
        "set_proof_nl",
        "add_proof_source_origin",
        "add_proof_resource_origin",
        "remove_proof_origin",
        "clear_proof_origins",
        "add_proof_decl_dep",
        "add_proof_mathlib_dep",
        "remove_proof_dep",
        "clear_proof_deps",
        "prepare_statement_formal_file",
        "capture_statement_formal_file",
        "prepare_proof_formal_file",
        "capture_proof_formal_file",
        "check_decl_file_snapshot_sync",
        "check_formal_stage_consistency",
        "record_statement_nl_review_passed",
        "record_statement_nl_review_rejected",
        "record_statement_formal_review_passed",
        "record_statement_formal_review_rejected",
        "record_proof_nl_review_passed",
        "record_proof_nl_review_rejected",
        "record_proof_formal_review_passed",
        "record_proof_formal_review_rejected",
        "inspect_current_stage_review_status",
        "run_decl_round_local_audit",
        "run_lean_file_diagnostics",
        "scan_lean_sorry_axiom",
        "check_statement_formal_policy",
        "check_proof_formal_policy",
    }
)

DECL_GRAPH_TOOL_SWEEP_NAMES = frozenset(
    {
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
        "list_current_node_decls",
        "inspect_current_node_decl",
        "preview_decl_delete_closure",
        "validate_decl_round_draft",
        "compute_current_node_decl_dependency_closure",
        "list_active_decl_names",
        "bind_current_node_interface",
        "check_current_content_node_completion",
    }
)

SCOPE_EXPORT_TOOL_SWEEP_NAMES = frozenset(
    {
        "add_scope_export",
        "remove_scope_export",
        "bind_node_interface",
        "unbind_node_interface",
    }
)

LOCAL_BOUNDARY_TOOL_SWEEP_NAMES = frozenset(
    {
        "check_mathlib_name",
    }
)

def core_tool_sweep_names() -> set[str]:
    """Implemented ToolCases covered by the broad non-DeclStage sweep."""

    return (
        set(implemented_tool_cases())
        - set(DECL_STAGE_FORMAL_TOOL_SWEEP_NAMES)
        - set(DECL_GRAPH_TOOL_SWEEP_NAMES)
        - set(SCOPE_EXPORT_TOOL_SWEEP_NAMES)
        - set(LOCAL_BOUNDARY_TOOL_SWEEP_NAMES)
    )


def decl_stage_formal_tool_sweep_names() -> set[str]:
    """Implemented DeclStage/formal ToolCases covered by the real Lake sweep."""

    return set(DECL_STAGE_FORMAL_TOOL_SWEEP_NAMES) & set(implemented_tool_cases())


def decl_graph_tool_sweep_names() -> set[str]:
    """Implemented DeclGraph strategy/round/readiness ToolCases."""

    return set(DECL_GRAPH_TOOL_SWEEP_NAMES) & set(implemented_tool_cases())


def scope_export_tool_sweep_names() -> set[str]:
    """Implemented scope export write ToolCases."""

    return set(SCOPE_EXPORT_TOOL_SWEEP_NAMES) & set(implemented_tool_cases())
