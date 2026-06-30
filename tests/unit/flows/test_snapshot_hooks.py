from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.flows.common.flow_requests import build_content_node_task_request
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.flows.coordinator.submissions import CoordinatorContentTasksSubmission
from lean_constellation.services.foundation import FoundationService
from lean_constellation.services.validation_snapshot import RepoCheckpointKind, ValidationSnapshotService
from tests.unit_services_helpers import make_runtime


class FakeRuntimeStabilityProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.calls: list[tuple[RepoCheckpointKind, list[str]]] = []

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ):
        del repo_root
        self.calls.append((checkpoint_kind, list(node_paths or [])))
        return self.foundation.ok(self.foundation.gate_passed("runtime_stability", summary="Runtime is stable."))


class FakeArkSnapshotProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.created: list[tuple[list[str], str | None]] = []

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root
        self.created.append((list(scope_ids), label))
        return self.foundation.ok(f"ark_snapshot_{len(self.created)}")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root, leave_runtime_paused
        return self.foundation.ok(snapshot_id)


def test_before_dispatch_snapshot(tmp_path: Path) -> None:
    runtime, repo_root, stability, ark_snapshot = _runtime(tmp_path)
    flow_id = _start_coordinator(runtime, repo_root)
    _queue_content_task_dispatch(runtime)

    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "before_content_task_dispatch_snapshot"

    snapshot_step_id = _advance_and_run(runtime, flow_id)
    snapshot_step = runtime.flow_service.get_step(snapshot_step_id)
    assert snapshot_step.step_type == "coordinator_content_batch_snapshot_step"
    assert snapshot_step.result.outcome == "snapshot_created"
    assert snapshot_step.result.checkpoint_kind == "before_content_task_dispatch"
    assert stability.calls == [(RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH, ["Main.Core"])]
    assert ark_snapshot.created == [(["repo", "node:Main.Core"], "before_content_task_dispatch for Repo")]

    dispatch_step_id = runtime.flow_service.advance_flow(flow_id)
    assert dispatch_step_id is not None
    dispatch_step = runtime.flow_service.get_step(dispatch_step_id)
    assert dispatch_step.step_type == "dispatch_step"


def test_after_child_batch_snapshot(tmp_path: Path) -> None:
    runtime, repo_root, stability, ark_snapshot = _runtime(tmp_path)
    flow_id = _start_coordinator(runtime, repo_root)
    _queue_content_task_dispatch(runtime)

    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    child_flows = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)
    assert len(child_flows) == 1
    _complete_child_flow(runtime, child_flows[0].flow_id)

    after_snapshot_step_id = runtime.flow_service.advance_flow(flow_id)
    assert after_snapshot_step_id is not None
    after_snapshot_step = runtime.flow_service.get_step(after_snapshot_step_id)
    assert after_snapshot_step.step_type == "coordinator_content_batch_snapshot_step"
    runtime.run_step(after_snapshot_step_id)
    after_snapshot_step = runtime.flow_service.get_step(after_snapshot_step_id)
    assert after_snapshot_step.result.outcome == "snapshot_created"
    assert after_snapshot_step.result.checkpoint_kind == "after_content_task_batch_terminal"

    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    assert callback_step_id is not None
    callback_step = runtime.flow_service.get_step(callback_step_id)
    assert callback_step.step_type == "coordinator_agent_step"
    assert stability.calls == [
        (RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH, ["Main.Core"]),
        (RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL, ["Main.Core"]),
    ]
    assert ark_snapshot.created == [
        (["repo", "node:Main.Core"], "before_content_task_dispatch for Repo"),
        (["repo", "node:Main.Core"], "after_content_task_batch_terminal for Repo"),
    ]


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, Path, FakeRuntimeStabilityProvider, FakeArkSnapshotProvider]:
    lean_runtime = make_runtime()
    stability = FakeRuntimeStabilityProvider(lean_runtime.foundation)
    ark_snapshot = FakeArkSnapshotProvider(lean_runtime.foundation)
    lean_runtime.app.validation_snapshot = ValidationSnapshotService(
        lean_runtime,
        runtime_stability_provider=stability,
        ark_snapshot_provider=ark_snapshot,
    )
    runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    return runtime, repo_root, stability, ark_snapshot


def _start_coordinator(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    return runtime.start_flow(
        "native_repo_coordinator",
        {
            "repo_key": "Repo",
            "repo_root": str(repo_root),
            "start_mode": "admin_start",
            "start_reason": "snapshot hook test",
        },
        scope_id="repo:Repo",
    )


def _queue_content_task_dispatch(runtime: FakeLeanFlowRuntime) -> None:
    runtime.agent_service.queue_submission(
        CoordinatorContentTasksSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_content_tasks",
            tool_name="submit_content_node_tasks",
            repo_key="Repo",
            node_paths=["Main.Core"],
            requests=[build_content_node_task_request(repo_key="Repo", node_path="Main.Core", scope_id="repo:Repo:node:Main.Core")],
            continuation="wait_for_callback",
            summary="Run Main.Core.",
        )
    )


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _complete_child_flow(runtime: FakeLeanFlowRuntime, child_flow_id: str) -> None:
    runtime.flow_service.store.update_flow_record(
        child_flow_id,
        lambda flow: (
            setattr(flow, "result", ContentNodeTaskResult(outcome="ready", repo_key="Repo", node_path="Main.Core", summary="Ready.")),
            setattr(flow, "status", FlowStatus.COMPLETED),
            setattr(flow, "current_step_id", None),
        ),
    )
