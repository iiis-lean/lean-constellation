from __future__ import annotations

from pathlib import Path

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

    def model_dump(self, mode: str = "python"):
        del mode
        return {"passed": self.passed}


class _FakeDeclGraph:
    def __init__(self, foundation, *, passed: bool) -> None:
        self.foundation = foundation
        self.passed = passed

    def submit_stage_review(self, repo_root, *, node_path: str, round_id: str, stage: str, summary: str):
        del repo_root, node_path, round_id, stage, summary
        return self.foundation.ok(_ReviewResult(passed=self.passed))


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

    accepted = submit_stage_review(runtime, _ctx(tmp_path), SubmitStageReviewArgs(summary="accepted"))

    assert accepted.ok
    assert accepted.value is not None
    assert accepted.value.submission.accepted is True
    assert accepted.value.submission.retry_required is False

    runtime.app.decl_graph = _FakeDeclGraph(runtime.foundation, passed=False)
    rejected = submit_stage_review(runtime, _ctx(tmp_path), SubmitStageReviewArgs(summary="retry"))

    assert rejected.ok
    assert rejected.value is not None
    assert rejected.value.submission.accepted is False
    assert rejected.value.submission.retry_required is True
