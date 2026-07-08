from __future__ import annotations

from pathlib import Path

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.tool_facade import ActorContext, DeclStageContextView, NodeContextView, RepoContextView, RuntimeToolContext, ToolExecutionContext
from lean_constellation.tools import build_application_tool_specs
from lean_constellation.tools.args import DeclReviewMarkArgs, DeclStageFileCheckArgs, NoArgs, StatementNlReviewPassedArgs, StatementNlReviewRejectedArgs
from lean_constellation.tools.internal.decl_stage import (
    _check_file_capture_sync,
    _check_formal_stage_consistency,
    _inspect_current_stage_review_status,
    _record_decl_review,
    _record_statement_nl_review_passed,
    _record_statement_nl_review_rejected,
)
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
        "check_formal_stage_consistency",
        "record_decl_review",
        "run_lean_file_diagnostics",
        "scan_lean_sorry_axiom",
        "check_statement_formal_policy",
        "check_proof_formal_policy",
        "inspect_current_stage_review_status",
        "record_statement_nl_review_passed",
        "record_statement_nl_review_rejected",
    }

    assert_tools_registered(expected)


def test_decl_stage_projection_reset_delete_tools_are_not_application_specs() -> None:
    names = {spec.name for spec in build_application_tool_specs()}

    assert "sync_decl_file_after_revision_reset" not in names
    assert "remove_decl_file_for_delete" not in names


def test_decl_stage_groups_expose_expected_tools() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    assert_group_contains("decl_stage_statement_nl_write", {"write_statement_nl"})
    assert_group_contains("decl_stage_proof_nl_write", {"write_proof_nl"})
    assert_group_contains("decl_stage_statement_formal_file", {"check_decl_file_snapshot_sync", "check_formal_stage_consistency"})
    assert_group_contains("decl_stage_statement_formal_file_write", {"prepare_statement_formal_file", "capture_statement_formal_file"})
    assert_group_contains("decl_stage_proof_formal_file", {"check_decl_file_snapshot_sync", "check_formal_stage_consistency"})
    assert_group_contains("decl_stage_proof_formal_file_write", {"prepare_proof_formal_file", "capture_proof_formal_file"})
    assert_group_contains("decl_stage_review_mark_write", {"record_decl_review"})
    assert_group_contains("decl_stage_review_status_read", {"inspect_current_stage_review_status"})
    assert_group_contains(
        "decl_stage_statement_nl_review_mark_write",
        {"record_statement_nl_review_passed", "record_statement_nl_review_rejected"},
    )
    assert_group_contains(
        "statement_formal_diagnostics_read",
        {"run_lean_file_diagnostics", "scan_lean_sorry_axiom", "check_statement_formal_policy"},
    )
    assert_group_contains(
        "proof_formal_diagnostics_read",
        {"run_lean_file_diagnostics", "scan_lean_sorry_axiom", "check_proof_formal_policy"},
    )
    statement_group = runtime.tool_facade.list_registered_tools(group_key="statement_formal_diagnostics_read")
    proof_group = runtime.tool_facade.list_registered_tools(group_key="proof_formal_diagnostics_read")
    assert statement_group.ok and statement_group.value is not None
    assert proof_group.ok and proof_group.value is not None
    assert "check_proof_formal_policy" not in {tool.name for tool in statement_group.value}
    assert "check_statement_formal_policy" not in {tool.name for tool in proof_group.value}


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


def _review_ctx(repo_root: Path, *, round_id: str, batch_decls: list[str] | None = None) -> ToolExecutionContext:
    batch_decls = batch_decls if batch_decls is not None else ["main_result"]
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
            batch_decls=batch_decls,
        ),
        endpoint_view_key="statement_nl_reviewer",
        expected_view_key="statement_nl_reviewer",
        repo_root=repo_root,
        repo=RepoContextView(repo_key=repo_root.name, summary="repo"),
        node=NodeContextView(node_path="Main.Topic.Core", node_kind="content", summary="node"),
        decl_stage=DeclStageContextView(stage="statement_nl", round_id=round_id, batch_decls=batch_decls, summary="stage"),
        actor=ActorContext(agent_type="StatementNLReviewerAgent", role="reviewer", added_by="worker", summary="reviewer"),
    )


def _formal_ctx(repo_root: Path, *, stage: str, role: str = "worker", batch_decls: list[str] | None = None) -> ToolExecutionContext:
    agent_type_by_stage = {
        "statement_formal": "StatementFormalWorkerAgent",
        "statement_formal_review": "StatementFormalReviewerAgent",
        "proof_formal": "ProofFormalWorkerAgent",
        "proof_formal_review": "ProofFormalReviewerAgent",
        "statement_nl": "StatementNLWorkerAgent",
    }
    worker_view_by_stage = {
        "statement_formal": "statement_formal_worker",
        "proof_formal": "proof_formal_worker",
        "statement_nl": "statement_nl_worker",
    }
    reviewer_view_by_stage = {
        "statement_formal": "statement_formal_reviewer",
        "statement_formal_review": "statement_formal_reviewer",
        "proof_formal": "proof_formal_reviewer",
        "proof_formal_review": "proof_formal_reviewer",
    }
    agent_type = agent_type_by_stage[stage]
    view = reviewer_view_by_stage[stage] if role == "reviewer" else worker_view_by_stage[stage]
    return ToolExecutionContext(
        runtime=RuntimeToolContext(
            flow_id="flow_1",
            step_id="step_1",
            agent_id="agent_1",
            agent_type=agent_type,
            agent_role=role,  # type: ignore[arg-type]
            expected_view_key=view,
            repo_root=repo_root,
            node_path="Main.Topic.Core",
            stage=stage,
            round_id="round_1",
            batch_decls=batch_decls if batch_decls is not None else ["main_result"],
        ),
        endpoint_view_key=view,
        expected_view_key=view,
        repo_root=repo_root,
        repo=RepoContextView(repo_key=repo_root.name, summary="repo"),
        node=NodeContextView(node_path="Main.Topic.Core", node_kind="content", summary="node"),
        decl_stage=DeclStageContextView(stage=stage, round_id="round_1", batch_decls=batch_decls if batch_decls is not None else ["main_result"], summary="stage"),
        actor=ActorContext(agent_type=agent_type, role=role, added_by="worker" if role == "reviewer" else role, summary=role),
    )


class _FakeFormalDeclGraph:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.calls: list[tuple[str, str]] = []

    def check_formal_stage_consistency(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str):
        del repo_root, node_path
        self.calls.append((decl_name, stage))
        return self.runtime.foundation.ok({"decl_name": decl_name, "stage": stage, "passed": True})


class _FakeLeanProjection:
    def __init__(self, runtime) -> None:
        self.runtime = runtime
        self.calls: list[tuple[str, str]] = []

    def check_decl_file_snapshot_sync(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str):
        del repo_root, node_path
        self.calls.append((decl_name, stage))
        return self.runtime.foundation.ok({"decl_name": decl_name, "stage": stage, "passed": True})


def test_formal_stage_consistency_rejects_cross_stage_checks(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()

    statement = _check_formal_stage_consistency(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="proof"),
    )
    proof = _check_formal_stage_consistency(
        runtime,
        _formal_ctx(tmp_path, stage="proof_formal"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )

    assert not statement.ok
    assert statement.issues[0].kind == "decl_stage_formal_read_rejected"
    assert not proof.ok
    assert proof.issues[0].kind == "decl_stage_formal_read_rejected"


def test_formal_file_sync_rejects_cross_stage_reviewer_checks(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()

    statement = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal_review", role="reviewer"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="proof"),
    )
    proof = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="proof_formal_review", role="reviewer"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )

    assert not statement.ok
    assert statement.issues[0].kind == "decl_stage_formal_read_rejected"
    assert not proof.ok
    assert proof.issues[0].kind == "decl_stage_formal_read_rejected"


def test_formal_read_checks_reject_non_formal_stage_and_out_of_batch_decl(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()

    non_formal = _check_formal_stage_consistency(
        runtime,
        _formal_ctx(tmp_path, stage="statement_nl"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )
    out_of_batch = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal", batch_decls=["other_decl"]),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )

    assert not non_formal.ok
    assert non_formal.issues[0].kind == "decl_stage_formal_read_rejected"
    assert not out_of_batch.ok
    assert out_of_batch.issues[0].kind == "decl_stage_formal_read_rejected"


def test_formal_read_checks_normalize_stage_and_call_underlying_services(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    fake_decl_graph = _FakeFormalDeclGraph(runtime)
    fake_projection = _FakeLeanProjection(runtime)
    runtime.app.decl_graph = fake_decl_graph  # type: ignore[assignment]
    runtime.app.lean_projection = fake_projection  # type: ignore[assignment]

    consistency = _check_formal_stage_consistency(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement_formal"),
    )
    sync = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="proof_formal"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="proof_formal"),
    )

    assert consistency.ok
    assert sync.ok
    assert fake_decl_graph.calls == [("main_result", "statement")]
    assert fake_projection.calls == [("main_result", "proof")]


def test_formal_read_checks_allow_reviewer_role_on_reviewed_formal_stage(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    fake_projection = _FakeLeanProjection(runtime)
    runtime.app.lean_projection = fake_projection  # type: ignore[assignment]

    sync = _check_file_capture_sync(
        runtime,
        _formal_ctx(tmp_path, stage="statement_formal", role="reviewer"),
        DeclStageFileCheckArgs(decl_name="main_result", stage="statement"),
    )

    assert sync.ok
    assert fake_projection.calls == [("main_result", "statement")]


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


def test_statement_nl_review_tools_record_marks_and_report_status(tmp_path: Path) -> None:
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
    for decl_name in ["main_result", "helper_def"]:
        created = runtime.decl_graph.create_decl(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem" if decl_name == "main_result" else "definition",
            objective=f"Create {decl_name}",
            summary=decl_name,
            end_after_state=DeclState.DECLARED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    ctx = _review_ctx(tmp_path, round_id=round_record.value.round_id, batch_decls=["main_result", "helper_def"])

    passed = _record_statement_nl_review_passed(
        runtime,
        ctx,
        StatementNlReviewPassedArgs(decl_name="main_result", summary="Main statement is acceptable."),
    )
    assert passed.ok, passed.issues
    status = _inspect_current_stage_review_status(runtime, ctx, NoArgs())
    assert status.ok and status.value is not None
    assert status.value["passed_decl_names"] == ["main_result"]
    assert status.value["missing_decl_names"] == ["helper_def"]
    assert status.value["ready_to_submit"] is False

    rejected = _record_statement_nl_review_rejected(
        runtime,
        ctx,
        StatementNlReviewRejectedArgs(
            decl_name="helper_def",
            summary="Helper definition statement is underspecified.",
            issue_categories=["origin_gap"],
            required_changes=["Attach the exact source origin and quantify the output."],
        ),
    )
    assert rejected.ok, rejected.issues
    status = _inspect_current_stage_review_status(runtime, ctx, NoArgs())
    assert status.ok and status.value is not None
    assert status.value["passed_decl_names"] == ["main_result"]
    assert status.value["failed_decl_names"] == ["helper_def"]
    assert status.value["missing_decl_names"] == []
    assert status.value["ready_to_submit"] is True
