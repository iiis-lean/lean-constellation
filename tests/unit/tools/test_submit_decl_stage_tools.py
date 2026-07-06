from __future__ import annotations

from pathlib import Path

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.tool_facade import (
    ActorContext,
    DeclStageContextView,
    NodeContextView,
    RepoContextView,
    RuntimeToolContext,
    SubmitBehavior,
    ToolExecutionContext,
)
from lean_constellation.tools.submit_args import SubmitStageReviewArgs
from lean_constellation.tools.submit_handlers import submit_stage_review
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

    def aggregate_stage_review_marks(self, repo_root, *, node_path: str, round_id: str, stage: str, summary: str, marks: list):
        del repo_root, node_path, round_id, stage, summary
        assert marks
        return self.foundation.ok(_ReviewResult(passed=self.passed))


class _FakeStep:
    step_id = "step_1"
    step_type = "decl_stage_reviewer_agent_step"

    def __init__(self) -> None:
        self.state = DeclStageReviewerStepState(
            agent_role="statement_nl_reviewer",
            agent_type="StatementNLReviewerAgent",
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
        decl_stage=DeclStageContextView(stage="statement_nl", round_id="round_1", summary="stage"),
        actor=ActorContext(agent_type="DeclStageReviewerAgent", role="reviewer", added_by="worker", summary="reviewer"),
    )


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
