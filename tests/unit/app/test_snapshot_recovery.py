from contextlib import nullcontext
from threading import RLock
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from agent_runtime_kit.flow import FlowStatus, StepStatus
from lean_constellation.app.snapshot_recovery import retry_content_snapshot


def setup_runtime(success=True):
    result = NS(outcome="snapshot_created", snapshot_id=None)
    step = NS(flow_id="f", status=StepStatus.COMPLETED, step_type="coordinator_content_batch_snapshot_step", result=result)
    flow = NS(flow_id="f", flow_type="native_repo_coordinator", status=FlowStatus.FAILED,
              error=NS(error_type="coordinator_stable_snapshot_failed"), finished_at="old",
              current_step_id=None, step_ids=["s"], state=NS(position=NS(phase="coordinator_callback")))
    def hook(ctx):
        if success:
            result.snapshot_id = "checkpoint"
        else:
            raise RuntimeError("capture failed")
    flow.after_step_terminal_stable = hook
    store = Mock()
    store.get_step.return_value = step
    store.update_flow_record.side_effect = lambda fid, patch: patch(flow)
    fs = NS(lock=RLock(), store=store, get_flow=lambda fid: flow)
    ark = NS(flow_service=fs, pause_controller=Mock(), schedule_service=Mock(), agent_service=Mock(), step_service=Mock())
    ark.pause_controller.hold_paused.return_value = nullcontext()
    ark.schedule_service.active_flow_advances = set()
    ark.agent_service.has_running_agents.return_value = False
    ark.step_service.store.list_steps.return_value = []
    return NS(ark=ark, app=Mock()), flow, step


def test_retry_preserves_completed_step_and_continues_same_flow():
    runtime, flow, step = setup_runtime()
    assert retry_content_snapshot(runtime, "f", "s")["snapshot_id"] == "checkpoint"
    assert flow.status == FlowStatus.RUNNING and flow.error is None
    assert step.status == StepStatus.COMPLETED
    runtime.ark.schedule_service.enqueue_flow.assert_called_once_with("f")


def test_retry_failure_keeps_failed_boundary():
    runtime, flow, _ = setup_runtime(False)
    with pytest.raises(RuntimeError):
        retry_content_snapshot(runtime, "f", "s")
    assert flow.status == FlowStatus.FAILED and flow.finished_at == "old"
    runtime.ark.schedule_service.enqueue_flow.assert_not_called()


@pytest.mark.parametrize("case", ["active", "wrong_step", "wrong_error", "already_created"])
def test_retry_rejects_other_boundaries(case):
    runtime, flow, step = setup_runtime()
    if case == "active":
        runtime.ark.schedule_service.active_flow_advances = {"other"}
    if case == "wrong_step":
        flow.step_ids = ["other"]
    if case == "wrong_error":
        flow.error.error_type = "business_failed"
    if case == "already_created":
        step.result.snapshot_id = "old_checkpoint"
    with pytest.raises(ValueError):
        retry_content_snapshot(runtime, "f", "s")
    runtime.ark.flow_service.store.update_flow_record.assert_not_called()
