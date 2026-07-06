from __future__ import annotations

from pathlib import Path

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.tool_facade import ActorContext, DeclStageContextView, NodeContextView, RepoContextView, RuntimeToolContext, ToolExecutionContext
from lean_constellation.tools.args import DeclReviewMarkArgs
from lean_constellation.tools.internal.decl_stage import _record_decl_review
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


class _FakeReviewerStep:
    step_id = "review_step_1"
    step_type = "decl_stage_reviewer_agent_step"

    def __init__(self) -> None:
        self.state = DeclStageReviewerStepState(
            agent_role="statement_nl_reviewer",
            agent_type="StatementNLReviewerAgent",
        )


class _FakeStepStore:
    def __init__(self) -> None:
        self.step = _FakeReviewerStep()

    def get_step(self, step_id: str):
        assert step_id == "review_step_1"
        return self.step

    def update_step_record(self, step_id: str, mutator):
        assert step_id == "review_step_1"
        mutator(self.step)
        return self.step


class _FakeStepService:
    def __init__(self) -> None:
        self.store = _FakeStepStore()


def _review_ctx(repo_root: Path, *, round_id: str) -> ToolExecutionContext:
    return ToolExecutionContext(
        runtime=RuntimeToolContext(
            flow_id="flow_1",
            step_id="review_step_1",
            agent_id="agent_1",
            agent_type="StatementNLReviewerAgent",
            agent_role="reviewer",
            expected_view_key="statement_nl_reviewer",
            repo_root=repo_root,
            node_path="Main.Topic.Core",
            stage="statement_nl",
            round_id=round_id,
            batch_decls=["main_result"],
        ),
        endpoint_view_key="statement_nl_reviewer",
        expected_view_key="statement_nl_reviewer",
        repo_root=repo_root,
        repo=RepoContextView(repo_key=repo_root.name, summary="repo"),
        node=NodeContextView(node_path="Main.Topic.Core", node_kind="content", summary="node"),
        decl_stage=DeclStageContextView(stage="statement_nl", round_id=round_id, batch_decls=["main_result"], summary="stage"),
        actor=ActorContext(agent_type="StatementNLReviewerAgent", role="reviewer", added_by="worker", summary="reviewer"),
    )


def test_record_decl_review_writes_current_reviewer_step_state(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    runtime.ark.step_service = _FakeStepService()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(tmp_path, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create theorem",
        summary="Theorem",
        end_after_state=DeclState.DECLARED,
    )
    assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    ctx = _review_ctx(tmp_path, round_id=round_record.value.round_id)

    result = _record_decl_review(
        runtime,
        ctx,
        DeclReviewMarkArgs(
            round_id=round_record.value.round_id,
            decl_name="main_result",
            stage="statement_nl",
            passed=True,
            summary="accepted",
        ),
    )

    assert result.ok, result.issues
    step = runtime.ark.step_service.store.get_step("review_step_1")
    assert [mark.decl_name for mark in step.state.review_marks] == ["main_result"]
    reviews_dir = runtime.decl_graph.graph_store.graph_root(tmp_path, node_path="Main.Topic.Core") / "reviews"
    assert not list(reviews_dir.glob("**/*.json"))
