"""Strict ToolSweep partitions for application tool execution evidence."""

from __future__ import annotations

from tests.real.runtime_matrix.strict.tool_cases import implemented_tool_cases


DECL_STAGE_FORMAL_TOOL_SWEEP_NAMES = frozenset(
    {
        "write_statement_nl",
        "write_proof_nl",
        "prepare_statement_formal_file",
        "capture_statement_formal_file",
        "prepare_proof_formal_file",
        "capture_proof_formal_file",
        "check_decl_file_snapshot_sync",
        "sync_decl_file_after_revision_reset",
        "remove_decl_file_for_delete",
        "check_formal_stage_consistency",
        "record_decl_review",
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
