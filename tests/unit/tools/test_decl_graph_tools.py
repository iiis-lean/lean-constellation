from __future__ import annotations

import pytest
from pydantic import ValidationError

from lean_constellation.tools import build_application_tool_specs
from lean_constellation.tools.args import (
    CurrentDeclVisibilityRevisionArgs,
    DeclCreateArgs,
    DeclUpdateArgs,
    NodeDeclVisibilityRevisionArgs,
    RoundDiscardArgs,
)
from lean_constellation.tools.keys import ApplicationToolViewKey
from lean_constellation.tools.views import build_application_tool_views

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
        "discard_decl_round_draft",
        "list_decl_rounds",
        "get_decl_round",
        "write_decl_change_summary",
        "write_decl_round_summary",
        "mark_decl_round_terminal",
        "plan_create_decl",
        "plan_update_decl",
        "restore_decl_revision",
        "delete_decls",
        "list_current_node_decls",
        "inspect_current_node_decl",
        "list_node_decls",
        "inspect_node_decl",
        "read_statement_nl",
        "read_proof_nl",
        "read_formal",
        "preview_decl_delete_closure",
        "validate_decl_round_draft",
        "compute_current_node_decl_dependency_closure",
        "preview_current_node_decl_delete_closure",
        "list_visible_nodes",
        "list_imported_repos",
        "list_current_node_public_decls",
        "inspect_current_node_public_decl",
        "list_node_public_decls",
        "inspect_node_public_decl",
        "list_repo_public_decls",
        "inspect_repo_public_decl",
        "read_visible_decl_lean_file",
        "list_active_decl_names",
        "check_current_content_node_completion",
        "run_decl_round_local_audit",
    }

    assert_tools_registered(expected)


def test_decl_planning_tool_schemas_expose_actual_transition_fields_only() -> None:
    created = DeclCreateArgs.model_validate(
        {
            "round_id": "round_1",
            "decl_name": "main_result",
            "kind": "theorem",
            "objective": "Create the result.",
            "summary": "Main result.",
        }
    )
    parsed = DeclUpdateArgs.model_validate(
        {
            "round_id": "round_1",
            "decl_name": "main_result",
            "objective": "Continue proof work.",
            "target_state": "proved",
            "start_stage": "proof_nl",
        }
    )
    assert created.decl_name == "main_result"
    assert parsed.start_stage == "proof_nl"
    schema = DeclUpdateArgs.model_json_schema()["properties"]
    assert "decl_name" in schema
    assert "start_stage" in schema
    assert "target_state" in schema
    assert "name" not in schema
    assert "base_revision" not in schema
    assert "reset_to_state" not in schema
    assert "anticipated_statement_dep_names" not in schema
    assert "anticipated_proof_dep_names" not in schema
    assert "start_before_state" not in schema
    assert "end_after_state" not in schema

    for legacy_payload in (
        {"name": "main_result", "start_stage": "proof_nl"},
        {"decl_name": "main_result", "base_revision": 1, "start_stage": "proof_nl"},
        {"decl_name": "main_result", "reset_to_state": "declared", "start_stage": "proof_nl"},
        {"decl_name": "main_result", "anticipated_statement_dep_names": [], "start_stage": "proof_nl"},
    ):
        with pytest.raises(ValidationError):
            DeclUpdateArgs.model_validate(
                {
                    "round_id": "round_1",
                    "objective": "Legacy request.",
                    "target_state": "proved",
                    **legacy_payload,
                }
            )


def test_discard_decl_round_draft_rejects_removed_reason_field() -> None:
    assert RoundDiscardArgs.model_validate({"round_id": "round_1"}).round_id == "round_1"
    with pytest.raises(ValidationError):
        RoundDiscardArgs.model_validate(
            {"round_id": "round_1", "reason": "Legacy narrative field."}
        )


def test_content_plan_reuses_actual_dependency_tool_groups_without_reviewer_write_expansion() -> None:
    views = {view.key: view for view in build_application_tool_views()}
    plan = views[ApplicationToolViewKey.CONTENT_PLAN.value]

    for group in (
        "decl_statement_dependency_read",
        "decl_statement_repo_dependency_write",
        "decl_statement_mathlib_dependency_write",
        "decl_proof_dependency_read",
        "decl_proof_repo_dependency_write",
        "decl_proof_mathlib_dependency_write",
    ):
        assert group in plan.group_keys
    for reviewer_key in (
        ApplicationToolViewKey.STATEMENT_NL_REVIEWER.value,
        ApplicationToolViewKey.STATEMENT_FORMAL_REVIEWER.value,
        ApplicationToolViewKey.PROOF_NL_REVIEWER.value,
        ApplicationToolViewKey.PROOF_FORMAL_REVIEWER.value,
    ):
        assert "decl_statement_repo_dependency_write" not in views[reviewer_key].group_keys
        assert "decl_proof_repo_dependency_write" not in views[reviewer_key].group_keys


def test_decl_graph_groups_expose_expected_tools() -> None:
    assert_group_contains("decl_graph_current_navigation_read", {"get_current_decl_graph_index", "list_decl_strategies"})
    assert_group_contains(
        "decl_graph_read_by_node",
        {"get_node_decl_graph_index", "get_node_decl_graph_store", "list_node_decls", "inspect_node_decl"},
    )
    assert_group_contains("decl_graph_current_write", {"ensure_current_decl_graph", "rebuild_current_decl_graph_index"})
    assert_group_contains("decl_strategy_write", {"ensure_open_decl_strategy", "close_decl_strategy"})
    assert_group_contains(
        "decl_round_change_write",
        {
            "create_decl_round_draft",
            "discard_decl_round_draft",
            "plan_create_decl",
            "validate_decl_round_draft",
        },
    )
    assert_group_contains("decl_round_closeout_write", {"write_decl_change_summary", "write_decl_round_summary", "mark_decl_round_terminal"})
    assert_group_contains("decl_maintenance_write", {"restore_decl_revision", "delete_decls", "preview_decl_delete_closure"})
    assert_group_contains("decl_stage_round_read", {"get_decl_round", "run_decl_round_local_audit"})
    assert_group_contains("decl_stage_statement_nl_read", {"read_statement_nl"})
    assert_group_contains("decl_stage_proof_nl_read", {"read_proof_nl"})
    assert_group_contains("decl_stage_formal_read", {"read_formal"})
    assert_group_contains("current_node_decl_read", {"list_current_node_decls", "inspect_current_node_decl"})
    assert_group_contains(
        "decl_dependency_analysis_read",
        {"compute_current_node_decl_dependency_closure", "preview_current_node_decl_delete_closure"},
    )
    assert_group_contains("node_visibility_read_current", {"list_visible_nodes", "list_imported_repos"})
    assert_group_contains(
        "current_node_public_decl_read",
        {"list_current_node_public_decls", "inspect_current_node_public_decl"},
    )
    assert_group_contains(
        "visible_node_public_decl_read",
        {"list_node_public_decls", "inspect_node_public_decl"},
    )
    assert_group_contains(
        "imported_repo_public_decl_read",
        {"list_repo_public_decls", "inspect_repo_public_decl"},
    )
    assert_group_contains("content_completion_gate_read", {"check_current_content_node_completion"})
    assert_group_contains("visible_decl_lean_file_read", {"read_visible_decl_lean_file"})


def test_public_boundary_tool_descriptions_are_role_neutral() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    for name in (
        "list_visible_nodes",
        "list_imported_repos",
        "list_repo_public_decls",
        "inspect_repo_public_decl",
        "read_visible_decl_lean_file",
    ):
        description = specs[name].description
        assert "Coordinator" not in description
        assert "worker" not in description
        assert "current" in description
        assert "context" in description


def test_cross_node_decl_tool_descriptions_are_actor_neutral() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}
    expected = {
        "get_node_decl_graph_index": "Read the DeclGraph index for one permitted node in the current repository.",
        "get_node_decl_graph_store": "Read DeclGraph store counts and paths for one permitted node in the current repository.",
        "list_node_decls": "List all public and private declarations in one permitted node of the current repository.",
        "inspect_node_decl": "Inspect one public or private declaration revision in a permitted node of the current repository.",
    }

    assert {name: specs[name].description for name in expected} == expected


def test_visibility_revision_tools_replace_single_promotion_tools() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert "revise_current_decl_visibility" in specs
    assert "revise_content_decl_visibility" in specs
    assert "promote_current_decl_public" not in specs
    assert "promote_content_decl_public" not in specs
    assert "promote_current_node_public_statement_closure" in specs
    assert "promote_public_statement_closure" in specs
    for name in ("revise_current_decl_visibility", "revise_content_decl_visibility"):
        assert "Compare-and-swap" in specs[name].description
        assert "audit reason" in specs[name].description
        assert "never removed" in specs[name].description
        assert "automatically" in specs[name].description
        assert specs[name].result_view == "decl_visibility_revision_receipt"
    assert "exact current committed Decl revisions" in specs[
        "promote_current_node_public_statement_closure"
    ].description
    assert "without creating or committing a Content contract version" in specs[
        "promote_current_node_public_statement_closure"
    ].description
    assert "Scope targets use stable committed child boundaries" in specs[
        "promote_public_statement_closure"
    ].description
    assert "existing caller-owned open target" in specs[
        "promote_public_statement_closure"
    ].description


def test_visible_node_descriptions_distinguish_live_current_and_stable_provider_truth() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert "repo-wide planning reads live node truth" in specs[
        "list_visible_nodes"
    ].description
    assert "provider nodes through stable committed boundaries" in specs[
        "list_visible_nodes"
    ].description
    assert "stable consumable public API" in specs[
        "list_node_public_decls"
    ].description
    assert "use current-node or DeclGraph tools for live planning truth" in specs[
        "list_node_public_decls"
    ].description


def test_visibility_revision_args_require_cas_and_reason() -> None:
    current = CurrentDeclVisibilityRevisionArgs.model_validate(
        {
            "decl_name": "helper",
            "expected_current_visibility": "public",
            "new_visibility": "private",
            "reason": "Proof-only helper.",
        }
    )
    selected = NodeDeclVisibilityRevisionArgs.model_validate(
        {**current.model_dump(), "node_path": "Main.Topic.Core"}
    )

    assert selected.node_path == "Main.Topic.Core"
    with pytest.raises(ValidationError):
        CurrentDeclVisibilityRevisionArgs.model_validate(
            {
                "decl_name": "helper",
                "expected_current_visibility": "public",
                "new_visibility": "private",
            }
        )
    with pytest.raises(ValidationError):
        CurrentDeclVisibilityRevisionArgs.model_validate(
            {
                "decl_name": "helper",
                "expected_current_visibility": "stale",
                "new_visibility": "private",
                "reason": "Invalid CAS.",
            }
        )
