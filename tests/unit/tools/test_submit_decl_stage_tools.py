from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.domain.refs import DeclRef, MathlibRef
from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.decl_graph.models import DeclOriginRef, MathlibDeclDep, RepoDeclDep
from lean_constellation.services.tool_facade import (
    ActorContext,
    DeclStageContextView,
    NodeContextView,
    RepoContextView,
    RuntimeToolContext,
    SubmitBehavior,
    ToolExecutionContext,
)
from lean_constellation.tools.submit_args import SubmitStageReviewArgs, SubmitStageWorkerBlockedArgs, SubmitStageWorkerCompletedArgs
from lean_constellation.tools.submit_handlers import submit_stage_review, submit_stage_worker_blocked, submit_stage_worker_completed
from tests.unit.tools._submit_family_helpers import assert_submit_tools


def test_decl_stage_submit_tools_registered() -> None:
    assert_submit_tools(
        {"submit_stage_worker_completed", "submit_stage_worker_blocked", "submit_stage_review"},
        behavior=SubmitBehavior.TERMINAL,
    )


class _ReviewResult:
    def __init__(self, *, passed: bool) -> None:
        self.passed = passed
        self.reviewed_decl_names = ["main_result"]
        self.failed_decl_names = [] if passed else ["main_result"]
        self.missing_decl_names = []
        self.feedback = []

    def model_dump(self, mode: str = "python"):
        del mode
        return {
            "passed": self.passed,
            "reviewed_decl_names": self.reviewed_decl_names,
            "failed_decl_names": self.failed_decl_names,
            "missing_decl_names": self.missing_decl_names,
            "feedback": self.feedback,
        }


class _FakeDeclGraph:
    def __init__(self, foundation, *, passed: bool) -> None:
        self.foundation = foundation
        self.passed = passed

    def aggregate_stage_review_marks(self, repo_root, *, node_path: str, round_id: str, stage: str, summary: str, marks: list, expected_decl_names=None):
        del repo_root, node_path, round_id, stage, summary, expected_decl_names
        assert marks
        return self.foundation.ok(_ReviewResult(passed=self.passed))


class _FakeStep:
    step_id = "step_1"
    step_type = "decl_stage_reviewer_agent_step"

    def __init__(self) -> None:
        self.state = DeclStageReviewerStepState(
            agent_role="statement_nl_reviewer",
            agent_type="StatementNLReviewerAgent",
            round_id="round_1",
            node_path="Main.Core",
            stage="statement_nl",
            expected_decl_names=["main_result"],
            review_marks=[
                {
                    "round_id": "round_1",
                    "node_path": "Main.Core",
                    "stage": "statement_nl",
                    "decl_name": "main_result",
                    "passed": True,
                    "summary": "mark",
                }
            ],
        )


class _FakeStepStore:
    def __init__(self) -> None:
        self.step = _FakeStep()

    def get_step(self, step_id: str):
        assert step_id == "step_1"
        return self.step


class _FakeStepService:
    def __init__(self) -> None:
        self.store = _FakeStepStore()


def _ctx(repo_root: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        runtime=RuntimeToolContext(
            flow_id="flow_1",
            step_id="step_1",
            agent_id="agent_1",
            agent_type="DeclStageReviewerAgent",
            agent_role="reviewer",
            expected_view_key="decl_stage_reviewer_submit",
            repo_root=repo_root,
            node_path="Main.Core",
            stage="statement_nl",
            round_id="round_1",
        ),
        endpoint_view_key="decl_stage_reviewer_submit",
        expected_view_key="decl_stage_reviewer_submit",
        repo_root=repo_root,
        repo=RepoContextView(repo_key=repo_root.name, summary="repo"),
        node=NodeContextView(node_path="Main.Core", node_kind="content", summary="node"),
        decl_stage=DeclStageContextView(stage="statement_nl", round_id="round_1", batch_decls=["main_result"], summary="stage"),
        actor=ActorContext(agent_type="DeclStageReviewerAgent", role="reviewer", added_by="worker", summary="reviewer"),
    )


def _worker_ctx(repo_root: Path, *, round_id: str, stage: str = "statement_formal", batch_decls: list[str] | None = None) -> ToolExecutionContext:
    batch_decls = batch_decls if batch_decls is not None else ["main_result"]
    return ToolExecutionContext(
        runtime=RuntimeToolContext(
            flow_id="flow_1",
            step_id="step_1",
            agent_id="agent_1",
            agent_type="StatementFormalWorkerAgent",
            agent_role="worker",
            expected_view_key="decl_stage_worker_submit",
            repo_root=repo_root,
            node_path="Main.Topic.Core",
            stage=stage,
            round_id=round_id,
            batch_decls=batch_decls,
        ),
        endpoint_view_key="decl_stage_worker_submit",
        expected_view_key="decl_stage_worker_submit",
        repo_root=repo_root,
        repo=RepoContextView(repo_key=repo_root.name, summary="repo"),
        node=NodeContextView(node_path="Main.Topic.Core", node_kind="content", summary="node"),
        decl_stage=DeclStageContextView(stage=stage, round_id=round_id, batch_decls=batch_decls, summary="stage"),
        actor=ActorContext(agent_type="StatementFormalWorkerAgent", role="worker", added_by="worker", summary="worker"),
    )


class _FakeLeanProjectionSync:
    def __init__(self, runtime, *, passed: bool) -> None:
        self.runtime = runtime
        self.passed = passed

    def check_decl_file_snapshot_sync(self, repo_root: Path, *, node_path: str, decl_name: str, stage: str):
        del repo_root, node_path, decl_name, stage
        if self.passed:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("decl_file_capture_sync", summary="synced"))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_failed(
                "decl_file_capture_sync",
                self.runtime.foundation.issue("decl_file_capture_stale", "stale capture"),
                summary="stale",
            )
        )


def _setup_statement_formal_candidate(
    runtime,
    repo_root: Path,
    *,
    write_formal: bool = True,
    lean_check: dict[str, str] | None = None,
    deps: list[str] | None = None,
) -> str:
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create theorem",
        summary="Theorem",
        end_after_state=DeclState.DECLARED,
    )
    assert created.ok
    assert runtime.decl_graph.start_round(repo_root, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    assert runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        decl_name="main_result",
        nl="The main theorem states True.",
    ).ok
    if write_formal:
        assert runtime.decl_graph.write_statement_formal(
            repo_root,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name="main_result",
            lean_code="theorem main_result : True := by sorry",
            lean_check=lean_check or {"status": "passed", "allow_sorry": "true", "contains_sorry": "true"},
            deps=deps,
        ).ok
    return round_record.value.round_id


def _setup_statement_nl_candidate(runtime, repo_root: Path, *, decl_names: list[str] | None = None) -> str:
    decl_names = decl_names or ["main_result"]
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in decl_names:
        created = runtime.decl_graph.create_decl(
            repo_root,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            end_after_state=DeclState.DECLARED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(repo_root, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    for decl_name in decl_names:
        assert runtime.decl_graph.set_statement_nl(
            repo_root,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"{decl_name} states True.",
        ).ok
    return round_record.value.round_id


def _setup_proof_nl_candidate(runtime, repo_root: Path, *, decl_names: list[str] | None = None) -> str:
    decl_names = decl_names or ["main_result"]
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic", boundary="Topic boundary").ok
    assert runtime.node.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core",
        boundary="Core boundary",
        objective="Objective",
        success_criteria="Ready",
    ).ok
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path="Main.Topic.Core", objective="Strategy")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Round",
    )
    assert round_record.ok and round_record.value is not None
    for decl_name in decl_names:
        created = runtime.decl_graph.create_decl(
            repo_root,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            name=decl_name,
            kind="theorem",
            objective=f"Create {decl_name}",
            summary=decl_name,
            end_after_state=DeclState.PROVED,
        )
        assert created.ok
    assert runtime.decl_graph.start_round(repo_root, node_path="Main.Topic.Core", round_id=round_record.value.round_id).ok
    for decl_name in decl_names:
        assert runtime.decl_graph.write_statement_nl(
            repo_root,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"{decl_name} states True.",
        ).ok
        assert runtime.decl_graph.write_statement_formal(
            repo_root,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            lean_code=f"theorem {decl_name} : True := by trivial",
            lean_check={"status": "passed", "contains_sorry": False, "contains_axiom": False},
        ).ok
        assert runtime.decl_graph.set_proof_nl(
            repo_root,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
            decl_name=decl_name,
            nl=f"Proof route for {decl_name}.",
        ).ok
    return round_record.value.round_id


def _setup_proof_formal_candidate(
    runtime,
    repo_root: Path,
    *,
    decl_names: list[str] | None = None,
    write_formal: bool = True,
    lean_check: dict[str, object] | None = None,
) -> str:
    decl_names = decl_names or ["main_result"]
    round_id = _setup_proof_nl_candidate(runtime, repo_root, decl_names=decl_names)
    if write_formal:
        for decl_name in decl_names:
            assert runtime.decl_graph.write_proof_formal(
                repo_root,
                node_path="Main.Topic.Core",
                round_id=round_id,
                decl_name=decl_name,
                lean_code=f"theorem {decl_name} : True := by trivial",
                lean_check=lean_check or {"status": "passed", "contains_sorry": False, "contains_axiom": False},
            ).ok
    return round_id


def test_stage_review_submission_maps_passed_to_accepted_and_retry(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    runtime.app.decl_graph = _FakeDeclGraph(runtime.foundation, passed=True)
    runtime.ark.step_service = _FakeStepService()

    accepted = submit_stage_review(runtime, _ctx(tmp_path), SubmitStageReviewArgs(summary="accepted"))

    assert accepted.ok
    assert accepted.value is not None
    assert accepted.value.submission.accepted is True
    assert accepted.value.submission.retry_required is False

    runtime.app.decl_graph = _FakeDeclGraph(runtime.foundation, passed=False)
    runtime.ark.step_service = _FakeStepService()
    rejected = submit_stage_review(runtime, _ctx(tmp_path), SubmitStageReviewArgs(summary="retry"))

    assert rejected.ok
    assert rejected.value is not None
    assert rejected.value.submission.accepted is False
    assert rejected.value.submission.retry_required is True


@pytest.mark.parametrize("stage", ["statement_nl", "statement_formal", "proof_nl", "proof_formal"])
def test_stage_worker_blocked_rejects_affected_decl_outside_current_batch(tmp_path: Path, stage: str) -> None:
    runtime = create_test_runtime_services()
    ctx = _worker_ctx(tmp_path, round_id="round_1", stage=stage, batch_decls=["main_result"])

    blocked = submit_stage_worker_blocked(
        runtime,
        ctx,
        SubmitStageWorkerBlockedArgs(reason="Need planning help.", affected_decl_names=["other_decl"]),
    )

    assert not blocked.ok
    assert blocked.issues[0].kind == "stage_worker_blocked_decl_outside_batch"


def test_stage_worker_blocked_accepts_current_batch_subset(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    ctx = _worker_ctx(tmp_path, round_id="round_1", stage="proof_nl", batch_decls=["main_result", "helper"])

    blocked = submit_stage_worker_blocked(
        runtime,
        ctx,
        SubmitStageWorkerBlockedArgs(reason="Need a helper declaration.", affected_decl_names=["helper"]),
    )

    assert blocked.ok
    assert blocked.value is not None
    assert blocked.value.submission.affected_decl_names == ["helper"]


def test_stage_worker_blocked_reason_cannot_be_blank() -> None:
    with pytest.raises(ValueError, match="reason must be non-empty"):
        SubmitStageWorkerBlockedArgs(reason="   ")


def test_statement_nl_worker_completed_rejects_invalid_origin_and_mathlib_dep(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_statement_nl_candidate(runtime, tmp_path)
    ctx = _worker_ctx(tmp_path, round_id=round_id, stage="statement_nl")
    assert runtime.decl_graph.add_statement_origin(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        origin=DeclOriginRef(kind="source", source_path="missing.md", start_line=1, end_line=1),
    ).ok

    invalid_origin = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not invalid_origin.ok
    assert invalid_origin.issues[0].kind in {"statement_origin_source_index_missing", "statement_origin_source_missing"}
    assert runtime.decl_graph.clear_statement_origins(tmp_path, node_path="Main.Topic.Core", round_id=round_id, decl_name="main_result").ok
    assert runtime.decl_graph.add_statement_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=MathlibDeclDep(ref=MathlibRef(name="Bogus.missingDecl", module="Mathlib.Does.Not.Exist")),
    ).ok

    invalid_mathlib = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not invalid_mathlib.ok
    assert invalid_mathlib.issues[0].kind == "statement_mathlib_dep_not_recorded"


def test_statement_nl_worker_completed_rejects_unfinished_same_round_dep(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_statement_nl_candidate(runtime, tmp_path, decl_names=["main_result", "supporting_statement"])
    ctx = _worker_ctx(tmp_path, round_id=round_id, stage="statement_nl", batch_decls=["main_result"])
    assert runtime.decl_graph.add_statement_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=RepoDeclDep(ref=DeclRef(node="Main.Topic.Core", name="supporting_statement")),
    ).ok

    invalid_dep = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not invalid_dep.ok
    assert invalid_dep.issues[0].kind == "statement_dep_same_round_not_declared"


def test_proof_nl_worker_completed_rejects_invalid_origin_and_mathlib_dep(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_proof_nl_candidate(runtime, tmp_path)
    ctx = _worker_ctx(tmp_path, round_id=round_id, stage="proof_nl")
    assert runtime.decl_graph.add_proof_origin(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        origin=DeclOriginRef(kind="source", source_path="missing-proof.md", start_line=1, end_line=1),
    ).ok

    invalid_origin = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not invalid_origin.ok
    assert invalid_origin.issues[0].kind in {"proof_origin_source_index_missing", "proof_origin_source_missing"}
    assert runtime.decl_graph.clear_proof_origins(tmp_path, node_path="Main.Topic.Core", round_id=round_id, decl_name="main_result").ok
    assert runtime.decl_graph.add_proof_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=MathlibDeclDep(ref=MathlibRef(name="Bogus.missingProof", module="Mathlib.Does.Not.Exist")),
    ).ok

    invalid_mathlib = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not invalid_mathlib.ok
    assert invalid_mathlib.issues[0].kind == "proof_mathlib_dep_not_recorded"
    assert runtime.decl_graph.clear_proof_deps(tmp_path, node_path="Main.Topic.Core", round_id=round_id, decl_name="main_result").ok
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        summary="Successor function.",
    ).ok
    assert runtime.decl_graph.add_proof_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=MathlibDeclDep(ref=MathlibRef(name="Nat.succ", module="Mathlib.Init")),
    ).ok

    wrong_module = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not wrong_module.ok
    assert wrong_module.issues[0].kind == "proof_mathlib_dep_module_mismatch"


def test_proof_nl_worker_completed_rejects_unfinished_same_round_dep(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_proof_nl_candidate(runtime, tmp_path, decl_names=["main_result", "supporting_statement"])
    ctx = _worker_ctx(tmp_path, round_id=round_id, stage="proof_nl", batch_decls=["main_result"])
    assert runtime.decl_graph.add_proof_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=RepoDeclDep(ref=DeclRef(node="Main.Topic.Core", name="supporting_statement")),
    ).ok

    invalid_dep = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not invalid_dep.ok
    assert invalid_dep.issues[0].kind == "proof_dep_same_round_not_proved"


def test_proof_nl_worker_completed_accepts_valid_candidate(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_proof_nl_candidate(runtime, tmp_path)
    ctx = _worker_ctx(tmp_path, round_id=round_id, stage="proof_nl")

    result = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert result.ok, result.issues
    assert result.value is not None
    assert result.value.submission.completed_decl_names == ["main_result"]


def test_statement_formal_worker_completed_requires_snapshot_sync(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_statement_formal_candidate(runtime, tmp_path)
    ctx = _worker_ctx(tmp_path, round_id=round_id)

    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=False)  # type: ignore[assignment]
    stale = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not stale.ok
    assert stale.issues[0].kind == "decl_file_capture_stale"

    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=True)  # type: ignore[assignment]
    accepted = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert accepted.ok, accepted.issues
    assert accepted.value is not None
    assert accepted.value.submission.completed_decl_names == ["main_result"]


def test_statement_formal_worker_completed_requires_captured_candidate(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_statement_formal_candidate(runtime, tmp_path, write_formal=False)
    ctx = _worker_ctx(tmp_path, round_id=round_id)
    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=True)  # type: ignore[assignment]

    result = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not result.ok
    assert {issue.kind for issue in result.issues} >= {"statement_formal_candidate_missing", "statement_formal_check_missing"}


def test_statement_formal_worker_completed_rejects_failed_captured_check(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_statement_formal_candidate(
        runtime,
        tmp_path,
        lean_check={"status": "failed", "contains_sorry": "false", "allow_sorry": "true"},
    )
    ctx = _worker_ctx(tmp_path, round_id=round_id)
    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=True)  # type: ignore[assignment]

    result = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not result.ok
    assert "lean_check_failed" in {issue.kind for issue in result.issues}


def test_statement_formal_worker_completed_rejects_invisible_statement_deps(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_statement_formal_candidate(runtime, tmp_path, deps=["missing_helper"])
    ctx = _worker_ctx(tmp_path, round_id=round_id)
    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=True)  # type: ignore[assignment]

    result = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not result.ok
    assert "statement_dep_not_visible" in {issue.kind for issue in result.issues}


def test_proof_formal_worker_completed_requires_snapshot_sync(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_proof_formal_candidate(runtime, tmp_path)
    ctx = _worker_ctx(tmp_path, round_id=round_id, stage="proof_formal")

    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=False)  # type: ignore[assignment]
    stale = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not stale.ok
    assert stale.issues[0].kind == "decl_file_capture_stale"

    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=True)  # type: ignore[assignment]
    accepted = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert accepted.ok, accepted.issues
    assert accepted.value is not None
    assert accepted.value.submission.completed_decl_names == ["main_result"]


def test_proof_formal_worker_completed_rejects_failed_captured_check(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_proof_formal_candidate(
        runtime,
        tmp_path,
        lean_check={"status": "failed", "contains_sorry": False, "contains_axiom": False},
    )
    ctx = _worker_ctx(tmp_path, round_id=round_id, stage="proof_formal")
    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=True)  # type: ignore[assignment]

    result = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not result.ok
    assert "lean_check_failed" in {issue.kind for issue in result.issues}


def test_proof_formal_worker_completed_rejects_unfinished_same_round_dep(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_proof_formal_candidate(runtime, tmp_path, decl_names=["main_result", "unfinished_helper"])
    ctx = _worker_ctx(tmp_path, round_id=round_id, stage="proof_formal", batch_decls=["main_result"])
    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=True)  # type: ignore[assignment]
    assert runtime.decl_graph.add_proof_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=RepoDeclDep(ref=DeclRef(node="Main.Topic.Core", name="unfinished_helper")),
    ).ok

    result = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert not result.ok
    assert "proof_dep_same_round_not_proved" in {issue.kind for issue in result.issues}


def test_statement_formal_worker_completed_accepts_typed_mathlib_statement_dep(tmp_path: Path) -> None:
    runtime = create_test_runtime_services()
    round_id = _setup_statement_formal_candidate(runtime, tmp_path)
    assert runtime.mathlib.upsert_mathlib_decl_entry(
        tmp_path,
        name="Nat.succ",
        module="Mathlib.Data.Nat.Basic",
        kind="def",
        summary="Successor function.",
    ).ok
    assert runtime.decl_graph.add_statement_dep(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_id,
        decl_name="main_result",
        dep=MathlibDeclDep(ref=MathlibRef(name="Nat.succ", module="Mathlib.Data.Nat.Basic")),
    ).ok
    ctx = _worker_ctx(tmp_path, round_id=round_id)
    runtime.app.lean_projection = _FakeLeanProjectionSync(runtime, passed=True)  # type: ignore[assignment]

    result = submit_stage_worker_completed(runtime, ctx, SubmitStageWorkerCompletedArgs(summary="done"))

    assert result.ok, result.issues
    assert result.value is not None
    assert result.value.submission.completed_decl_names == ["main_result"]
