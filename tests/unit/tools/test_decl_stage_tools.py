from __future__ import annotations

from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_decl_stage_tools_are_registered() -> None:
    expected = {
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
        "run_lean_file_diagnostics",
        "scan_lean_sorry_axiom",
        "check_statement_formal_policy",
        "check_proof_formal_policy",
    }

    assert_tools_registered(expected)


def test_decl_stage_groups_expose_expected_tools() -> None:
    assert_group_contains("decl_stage_statement_nl_write", {"write_statement_nl"})
    assert_group_contains("decl_stage_proof_nl_write", {"write_proof_nl"})
    assert_group_contains("decl_stage_statement_formal_file", {"check_decl_file_snapshot_sync", "check_formal_stage_consistency"})
    assert_group_contains("decl_stage_statement_formal_file_write", {"prepare_statement_formal_file", "capture_statement_formal_file"})
    assert_group_contains("decl_stage_proof_formal_file", {"check_decl_file_snapshot_sync", "check_formal_stage_consistency"})
    assert_group_contains("decl_stage_proof_formal_file_write", {"prepare_proof_formal_file", "capture_proof_formal_file"})
    assert_group_contains("decl_stage_review_mark_write", {"record_decl_review"})
    assert_group_contains("formal_diagnostics_read", {"run_lean_file_diagnostics", "scan_lean_sorry_axiom"})
