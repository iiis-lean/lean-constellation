from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.common.flow_requests import build_content_node_task_request, build_resource_curation_request
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.flows.coordinator.submissions import (
    CoordinatorContentTasksSubmission,
    CoordinatorRepoReadySubmission,
    CoordinatorRepoRequirementSubmission,
    CoordinatorResourceRequestSubmission,
)
from lean_constellation.flows.resource_request.flows import ResourceCurationResult
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
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref=f"ark:{snapshot_id}",
                changed=True,
                summary="Restored fake ARK snapshot.",
            )
        )


class FakeConsistencyForReadiness:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def check_source_corpus_consistency(self, repo_root: Path):
        del repo_root
        return self.foundation.ok(self.foundation.gate_passed("source_corpus_consistency", summary="Source corpus consistency passed."))

    def check_source_index_consistency(self, repo_root: Path):
        del repo_root
        return self.foundation.ok(self.foundation.gate_passed("source_index_consistency", summary="Source index consistency passed."))

    def check_projection_sync(self, repo_root: Path, *, scope: str = "repo"):
        del repo_root
        return self.foundation.ok(self.foundation.gate_passed("projection_sync", summary=f"Projection sync passed for {scope}."))


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, object, FakeRuntimeStabilityProvider, FakeArkSnapshotProvider]:
    lean_runtime = make_runtime()
    foundation = lean_runtime.foundation
    runtime_stability = FakeRuntimeStabilityProvider(foundation)
    ark_snapshot = FakeArkSnapshotProvider(foundation)
    lean_runtime.app.validation_snapshot = ValidationSnapshotService(
        lean_runtime,
        consistency=FakeConsistencyForReadiness(lean_runtime.foundation),
        runtime_stability_provider=runtime_stability,
        ark_snapshot_provider=ark_snapshot,
    )
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    return flow_runtime, lean_runtime, runtime_stability, ark_snapshot


def _start_coordinator(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    repo_root.mkdir(parents=True, exist_ok=True)
    return runtime.start_flow(
        "native_repo_coordinator",
        {
            "repo_key": repo_root.name,
            "repo_root": str(repo_root),
            "start_mode": "admin_start",
            "start_reason": "unit",
        },
        scope_id=f"repo:{repo_root.name}",
    )


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _complete_child_flow(runtime: FakeLeanFlowRuntime, child_flow_id: str, result) -> None:
    runtime.flow_service.store.update_flow_record(
        child_flow_id,
        lambda flow: (
            setattr(flow, "result", result),
            setattr(flow, "status", FlowStatus.COMPLETED),
            setattr(flow, "current_step_id", None),
        ),
    )


def test_content_task_dispatch_waiting_snapshot_and_callback(tmp_path: Path) -> None:
    runtime, _, runtime_stability, ark_snapshot = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    flow_id = _start_coordinator(runtime, repo_root)

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
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "before_content_task_dispatch_snapshot"

    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "dispatch_content_tasks"

    dispatch_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_content_tasks"
    child_flows = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)
    assert len(child_flows) == 1

    _complete_child_flow(
        runtime,
        child_flows[0].flow_id,
        ContentNodeTaskResult(outcome="ready", repo_key="Repo", node_path="Main.Core", summary="Content task ready."),
    )
    assert runtime.flow_service.can_advance_flow(flow_id)
    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.RUNNING
    assert flow.state.position.phase == "coordinator_callback"
    assert runtime_stability.calls == [
        (RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH, ["Main.Core"]),
        (RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL, ["Main.Core"]),
    ]
    assert ark_snapshot.created[0][0] == ["repo", "node:Main.Core"]
    assert ark_snapshot.created[1][0] == ["repo", "node:Main.Core"]

    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            repo_key="Repo",
            requirement_name="provider_req",
            target_repo="Provider",
            reason="Need external provider.",
            summary="Wait for provider.",
        )
    )
    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_requirement"
    assert flow.state.waiting_requirement_name == "provider_req"
    assert len(runtime.agent_service.start_records) == 2
    assert runtime.agent_service.start_records[0].agent_id == runtime.agent_service.start_records[1].agent_id
    assert "The child workflows you requested have finished." in (runtime.agent_service.start_records[1].prompt or "")


def test_resource_request_dispatch_waiting_and_callback(tmp_path: Path) -> None:
    runtime, _, _, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    flow_id = _start_coordinator(runtime, repo_root)

    runtime.agent_service.queue_submission(
        CoordinatorResourceRequestSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_resource_request",
            tool_name="submit_resource_request",
            repo_key="Repo",
            target_kind="arxiv",
            target="2501.12345",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo",
                    repo_key="Repo",
                    repo_root=str(repo_root),
                    target_kind="arxiv",
                    target="2501.12345",
                    requested_by="coordinator",
                )
            ],
            continuation="wait_for_callback",
            summary="Curate arxiv source.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "dispatch_resource_request"

    dispatch_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_resource_request"
    child_flows = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)
    assert len(child_flows) == 1

    _complete_child_flow(
        runtime,
        child_flows[0].flow_id,
        ResourceCurationResult(
            outcome="duplicate",
            repo_key="Repo",
            target_summary="arxiv:2501.12345",
            existing_resource_key="res_existing",
            summary="Duplicate resource.",
        ),
    )
    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    assert callback_step_id is not None
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.RUNNING
    assert flow.state.position.phase == "coordinator_callback"

    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            repo_key="Repo",
            requirement_name="provider_req",
            target_repo="Provider",
            summary="Wait for provider.",
        )
    )
    runtime.run_step(callback_step_id)
    assert "Duplicate resource." in (runtime.agent_service.start_records[1].prompt or "")


def test_repo_ready_submission_marks_provider_ready_and_completes(tmp_path: Path) -> None:
    runtime, lean_runtime, _, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    written = lean_runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Formalize a small source corpus.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            source_corpus_relpath=".lean_constellation/source",
            interface_inputs=[],
        ),
    )
    assert written.ok
    (repo_root / ".lean_constellation" / "source").mkdir(parents=True, exist_ok=True)
    initialized = lean_runtime.node.ensure_native_root_main_contract(repo_root)
    assert initialized.ok
    committed = lean_runtime.node.commit_scope_contract(repo_root, scope_path="Main", summary="Main scope complete.")
    assert committed.ok
    flow_id = _start_coordinator(runtime, repo_root)

    runtime.agent_service.queue_submission(
        CoordinatorRepoReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_ready",
            tool_name="submit_repo_ready",
            repo_key="Repo",
            summary="Repo exposes a completed small formalization.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "mark_repo_ready"

    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "repo_ready"
    assert flow.result.provider_ready_marked is True
    ready = lean_runtime.repo_workspace.metadata.get_provider_ready(repo_root)
    assert ready.ok and ready.value.ready is True
    model = lean_runtime.repo_workspace.metadata.get_repo_model(repo_root)
    assert model.ok and model.value.summary == "Repo exposes a completed small formalization."
