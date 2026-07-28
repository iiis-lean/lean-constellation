from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from agent_runtime_kit.flow.models import FlowRequest
from agent_runtime_kit.flow.models import BaseFlowError, FlowStatus, StepTerminalReceipt
from agent_runtime_kit.flow.standard_steps import DispatchStep, DispatchStepResult

from lean_constellation.app import (
    LeanAdminApi,
    RepoRunRequestInput,
    StandaloneRootInterfaceRunInput,
    StandaloneSourceIndexRunInput,
    create_app_runtime_services,
)
from lean_constellation.domain.repo import RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.flows.repo_lifecycle.continuation import NativeRepoContinuationInput
from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildResult
from lean_constellation.flows.repo_lifecycle.root_interface import RootInterfacePreparationResult
from lean_constellation.flows.common.testing import create_fake_lean_flow_runtime
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.foundation import FoundationContext
from lean_constellation.services.validation_snapshot import RepoCheckpointKind
from tests.unit.flows.repo_lifecycle.test_native_repo_preparation_flow import _prepare_native_repo, _runtime


def test_continuation_flow_persists_complete_run_spec(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_continuation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(tmp_path / "Provider"),
                "base_release_id": "release-r1",
                "run_spec": {
                    "run_objective": "Prove the existing public theorem.",
                    "completion_mode": "graph_proved",
                    "source_scope": {"mode": "none", "selectors": []},
                    "index_policy": "reuse",
                    "root_interface_policy": "reuse",
                    "additional_required_interfaces": [],
                },
            },
        ),
        enqueue=False,
    )
    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert isinstance(flow.input, NativeRepoContinuationInput)
    assert flow.input.run_spec.run_objective == "Prove the existing public theorem."
    assert flow.input.base_release_id == "release-r1"


def _released_runtime(tmp_path):
    runtime, lean_runtime, _ = _runtime(tmp_path)
    root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, root, allow_interface_supplement=False)
    checkpoint = lean_runtime.app.snapshot_runtime.create_repo_stable_point_snapshot(
        root, checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
        label="release baseline", scope_ids=["repo:Provider"],
    )
    assert checkpoint.ok and checkpoint.value is not None
    release = RepoRelease(release_id="release-r1", node_contract_versions={"main": 1},
                          completion_mode="graph_proved",
                          semantic_manifest_digest="1" * 64,
                          dependency_lock_digest="2" * 64,
                          summary="R1")
    ctx = FoundationContext(repo_root=root)
    assert lean_runtime.foundation.store.write_json_atomic(lean_runtime.foundation.layout.release_path(ctx, "release-r1"), release).ok
    assert lean_runtime.foundation.store.write_json_atomic(
        lean_runtime.repo_workspace.metadata._repo_publication_path(root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id="release-r1"),
    ).ok
    initialized = lean_runtime.repo_workspace.git_release.ensure_independent_repo(root)
    assert initialized.ok and initialized.value is not None
    assert lean_runtime.repo_workspace.git_release.commit_release(
        root,
        release=release,
        candidate_files=[
            path.relative_to(root).as_posix()
            for path in lean_runtime.validation_snapshot.release_finalizer._candidate_files(root)
        ],
        expected_head=initialized.value.head_commit,
    ).ok
    return runtime, lean_runtime, root


def _run_to_source_waiting(tmp_path):
    runtime, lean_runtime, root = _released_runtime(tmp_path)
    flow_id = runtime.start_flow("native_repo_continuation", {
        "repo_key": "Provider", "repo_root": str(root), "base_release_id": "release-r1",
        "run_spec": {"run_objective": "Continue.", "completion_mode": "graph_proved",
                     "source_scope": {"mode": "none"},
                     "index_policy": "reuse", "root_interface_policy": "reuse"},
    }, scope_id="repo:Provider")
    for _ in range(4):
        step_id = runtime.flow_service.advance_flow(flow_id)
        assert step_id is not None
        runtime.run_step(step_id)
    parent = runtime.flow_service.get_flow(flow_id)
    assert parent.status is FlowStatus.WAITING
    children = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id, parent_dispatch_step_id=parent.state.waiting_dispatch_step_id,
    )
    assert len(children) == 1
    return runtime, lean_runtime, flow_id, children[0].flow_id


def _start_continuation(runtime, root) -> str:  # noqa: ANN001
    return runtime.start_flow("native_repo_continuation", {
        "repo_key": "Provider", "repo_root": str(root), "base_release_id": "release-r1",
        "run_spec": {"run_objective": "Continue.", "completion_mode": "graph_proved",
                     "source_scope": {"mode": "none"},
                     "index_policy": "reuse", "root_interface_policy": "reuse"},
    }, scope_id="repo:Provider")


def test_continuation_checkpoint_materialization_failure_fails_parent(tmp_path) -> None:
    runtime, lean_runtime, root = _released_runtime(tmp_path)
    flow_id = _start_continuation(runtime, root)

    def fail_snapshot(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise OSError("forced checkpoint failure")

    lean_runtime.app.snapshot_runtime.create_repo_stable_point_snapshot_with_id = fail_snapshot
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.FAILED
    assert flow.error.error_type == "native_continuation_stable_snapshot_failed"
    assert "forced checkpoint failure" in flow.error.message


def test_continuation_latest_release_drift_blocks_apply(tmp_path) -> None:
    runtime, lean_runtime, root = _released_runtime(tmp_path)
    flow_id = _start_continuation(runtime, root)
    prepare_id = runtime.flow_service.advance_flow(flow_id)
    assert prepare_id is not None
    runtime.run_step(prepare_id)
    assert lean_runtime.foundation.store.write_json_atomic(
        lean_runtime.repo_workspace.metadata._repo_publication_path(root),
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id="release-r2"),
    ).ok
    apply_id = runtime.flow_service.advance_flow(flow_id)
    assert apply_id is not None
    runtime.run_step(apply_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "blocked"
    assert "Latest release changed" in flow.result.reason


def _complete_child(runtime, child_id: str, result) -> None:  # noqa: ANN001
    def complete(flow) -> None:  # noqa: ANN001
        flow.result = result
        flow.error = None
        flow.status = FlowStatus.COMPLETED
        flow.current_step_id = None
    runtime.flow_service.store.update_flow_record(child_id, complete)


def test_source_business_blocked_propagates_exact_terminal_outcome(tmp_path) -> None:
    runtime, _, parent_id, child_id = _run_to_source_waiting(tmp_path)
    _complete_child(runtime, child_id, SourceIndexBuildResult(
        outcome="blocked", repo_key="Provider", reason="scope not committed", summary="scope not committed"
    ))
    assert runtime.flow_service.prepare_flow_for_advance(parent_id)
    parent = runtime.flow_service.get_flow(parent_id)
    assert parent.status is FlowStatus.COMPLETED
    assert parent.result.outcome == "blocked"
    assert not runtime.flow_service.can_advance_flow(parent_id)


def test_source_runtime_failure_fails_parent(tmp_path) -> None:
    runtime, _, parent_id, child_id = _run_to_source_waiting(tmp_path)
    def fail(flow) -> None:  # noqa: ANN001
        flow.error = BaseFlowError(error_type="child_failed", message="child exploded")
        flow.status = FlowStatus.FAILED
        flow.current_step_id = None
    runtime.flow_service.store.update_flow_record(child_id, fail)
    assert runtime.flow_service.prepare_flow_for_advance(parent_id)
    parent = runtime.flow_service.get_flow(parent_id)
    assert parent.status is FlowStatus.FAILED
    assert parent.error.error_type == "continuation_child_failed"


def _run_to_root_waiting(tmp_path):
    runtime, lean_runtime, parent_id, source_id = _run_to_source_waiting(tmp_path)
    _complete_child(runtime, source_id, SourceIndexBuildResult(
        outcome="no_op", repo_key="Provider", summary="index reused"
    ))
    assert runtime.flow_service.prepare_flow_for_advance(parent_id)
    for _ in range(2):
        step_id = runtime.flow_service.advance_flow(parent_id)
        assert step_id is not None
        runtime.run_step(step_id)
    parent = runtime.flow_service.get_flow(parent_id)
    children = runtime.flow_service.store.list_child_flows(
        parent_flow_id=parent_id, parent_dispatch_step_id=parent.state.waiting_dispatch_step_id,
    )
    assert parent.status is FlowStatus.WAITING and len(children) == 1, (
        parent.result, parent.error, parent.state,
        [step.model_dump(mode="json") for step in runtime.flow_service.store.list_steps(flow_id=parent_id)],
    )
    return runtime, lean_runtime, parent_id, children[0].flow_id


def test_root_invalid_input_propagates_exact_terminal_outcome(tmp_path) -> None:
    runtime, _, parent_id, root_id = _run_to_root_waiting(tmp_path)
    _complete_child(runtime, root_id, RootInterfacePreparationResult(
        outcome="invalid_input", repo_key="Provider", invocation_kind="child",
        blocked_reason="protected interface conflict", summary="protected interface conflict",
    ))
    assert runtime.flow_service.prepare_flow_for_advance(parent_id)
    parent = runtime.flow_service.get_flow(parent_id)
    assert parent.status is FlowStatus.COMPLETED
    assert parent.result.outcome == "invalid_input"


class FailingDispatchStep(DispatchStep):
    def run(self, ctx) -> StepTerminalReceipt:  # noqa: ANN001
        return ctx.complete_step(DispatchStepResult(
            outcome="failed", continuation=self.state.continuation,
            source_step_id=self.state.source_step_id,
            source_submission_id=self.state.source_submission_id,
            child_flow_ids=[], failed_request_indices=[0], summary="forced dispatch failure",
        ))


def test_coordinator_dispatch_failure_is_terminal_not_waiting(tmp_path) -> None:
    runtime, lean_runtime, parent_id, root_id = _run_to_root_waiting(tmp_path)
    _complete_child(runtime, root_id, RootInterfacePreparationResult(
        outcome="ready", repo_key="Provider", invocation_kind="child", summary="root ready",
    ))
    assert runtime.flow_service.prepare_flow_for_advance(parent_id)
    lean_runtime.validation_snapshot.readiness_gate.check_native_handoff_gate = lambda _root: ServiceResult(
        ok=True, value=GateReport(gate_name="native_handoff", passed=True, summary="ready")
    )
    for _ in range(2):
        step_id = runtime.flow_service.advance_flow(parent_id)
        assert step_id is not None
        runtime.run_step(step_id)
    restarted = create_fake_lean_flow_runtime(
        runtime.root, ark_services=lean_runtime.ark, app_services=lean_runtime.app,
        step_type_overrides={"dispatch_step": FailingDispatchStep},
    )
    dispatch_id = restarted.flow_service.advance_flow(parent_id)
    assert dispatch_id is not None
    restarted.run_step(dispatch_id)
    parent = restarted.flow_service.get_flow(parent_id)
    assert parent.status is FlowStatus.COMPLETED
    assert parent.result.outcome == "blocked"
    assert not restarted.flow_service.can_advance_flow(parent_id)


def test_successful_continuation_dispatches_new_coordinator_with_full_run_context(tmp_path) -> None:
    runtime, lean_runtime, parent_id, root_id = _run_to_root_waiting(tmp_path)
    _complete_child(runtime, root_id, RootInterfacePreparationResult(
        outcome="ready", repo_key="Provider", invocation_kind="child", summary="root delta ready",
    ))
    assert runtime.flow_service.prepare_flow_for_advance(parent_id)
    lean_runtime.validation_snapshot.readiness_gate.check_native_handoff_gate = lambda _root: ServiceResult(
        ok=True, value=GateReport(gate_name="native_handoff", passed=True, summary="ready")
    )
    for _ in range(3):
        step_id = runtime.flow_service.advance_flow(parent_id)
        assert step_id is not None
        runtime.run_step(step_id)
    parent = runtime.flow_service.get_flow(parent_id)
    assert parent.status is FlowStatus.COMPLETED
    assert parent.result.outcome == "handoff_dispatched"
    coordinators = runtime.flow_service.store.list_child_flows(parent_flow_id=parent_id)
    coordinator = next(flow for flow in coordinators if flow.flow_type == "native_repo_coordinator")
    context = coordinator.input.run_context
    assert context.start_kind == "continuation"
    assert context.base_release_id == "release-r1"
    assert context.run_spec.index_policy == "reuse"
    assert context.source_index_delta_summary == "index reused"
    assert context.root_interface_delta_summary == "root delta ready"
    assert context.config_change_summary == "completion_mode=graph_proved"


def test_stable_standalone_preprocess_then_developing_continue(tmp_path) -> None:
    runtime, lean_runtime, root = _released_runtime(tmp_path)
    admin = LeanAdminApi(lean_runtime)
    started = admin.start_standalone_source_index(StandaloneSourceIndexRunInput(
        repo_root=root, run_objective="Refresh selected source.",
        source_scope={"mode": "none"}, index_policy="reuse", enqueue=False,
    ))
    assert started.ok and started.value is not None
    assert lean_runtime.repo_workspace.metadata.get_repo_publication(root).value.publication.status is RepoPublicationStatus.DEVELOPING

    def complete(flow) -> None:  # noqa: ANN001
        flow.result = SourceIndexBuildResult(outcome="no_op", repo_key="Provider", summary="reused")
        flow.status = FlowStatus.COMPLETED
        flow.current_step_id = None

    runtime.flow_service.store.update_flow_record(started.value.flow_id, complete)
    continued = admin.continue_native_repo(RepoRunRequestInput(
        repo_root=root, run_objective="Continue after preprocessing.", enqueue=False,
    ))
    assert continued.ok and continued.value is not None
    continuation = runtime.flow_service.get_flow(continued.value.flow_id)
    assert continuation.input.base_release_id == "release-r1"


@pytest.mark.parametrize("standalone_kind", ["source", "root"])
def test_standalone_and_continuation_create_at_most_one_flow_truth(tmp_path, standalone_kind: str) -> None:
    runtime, lean_runtime, root = _released_runtime(tmp_path)
    admin = LeanAdminApi(lean_runtime)
    barrier = Barrier(2)

    def start_standalone():
        barrier.wait()
        if standalone_kind == "source":
            return admin.start_standalone_source_index(StandaloneSourceIndexRunInput(
                repo_root=root, run_objective="Refresh source.",
                source_scope={"mode": "none"}, index_policy="reuse", enqueue=False,
            ))
        return admin.start_standalone_root_interfaces(StandaloneRootInterfaceRunInput(
            repo_root=root, run_objective="Refresh root interfaces.",
            root_interface_policy="reuse", enqueue=False,
        ))

    def start_continuation():
        barrier.wait()
        return admin.continue_native_repo(RepoRunRequestInput(
            repo_root=root, run_objective="Continue proof work.", enqueue=False,
        ))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(start_standalone), pool.submit(start_continuation)]]
    assert sum(result.ok for result in results) == 1
    failed = next(result for result in results if not result.ok)
    assert failed.issues[0].kind in {"repo_lifecycle_flow_conflict", "repo_lifecycle_lock_busy"}
    active = [flow for flow in runtime.flow_service.list_flows(scope_id="repo:Provider")
              if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}]
    assert len(active) == 1


def test_standalone_flow_creation_failure_leaves_retryable_developing_repo(tmp_path) -> None:
    runtime, lean_runtime, root = _released_runtime(tmp_path)
    admin = LeanAdminApi(lean_runtime)
    original_start = admin.start_arbitrary_flow
    admin.start_arbitrary_flow = lambda *_args, **_kwargs: lean_runtime.foundation.fail(  # type: ignore[method-assign]
        lean_runtime.foundation.issue("forced_flow_create_failure", "forced flow create failure")
    )
    request = StandaloneSourceIndexRunInput(
        repo_root=root, run_objective="Refresh source.",
        source_scope={"mode": "none"}, index_policy="reuse", enqueue=False,
    )
    failed = admin.start_standalone_source_index(request)
    assert not failed.ok
    assert lean_runtime.repo_workspace.metadata.get_repo_publication(
        root
    ).value.publication.status is RepoPublicationStatus.DEVELOPING
    assert not [flow for flow in runtime.flow_service.list_flows(scope_id="repo:Provider")
                if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}]

    admin.start_arbitrary_flow = original_start  # type: ignore[method-assign]
    retried = admin.start_standalone_source_index(request)
    assert retried.ok and retried.value is not None
    assert runtime.flow_service.get_flow(retried.value.flow_id).flow_type == "source_index_build"
