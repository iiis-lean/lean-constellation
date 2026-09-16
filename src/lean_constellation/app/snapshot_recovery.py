"""Retry only a failed post-content stable snapshot from unchanged current truth."""
from agent_runtime_kit.flow import FlowStatus, StepStatus
from agent_runtime_kit.flow.contexts import StableStepTerminalContext


def retry_content_snapshot(runtime, flow_id: str, step_id: str) -> dict[str, str]:
    ark = runtime.ark
    flows = ark.flow_service
    with ark.pause_controller.hold_paused(None), flows.lock:
        if (ark.schedule_service.active_flow_advances or ark.agent_service.has_running_agents()
                or ark.step_service.store.list_steps(status=StepStatus.RUNNING)
                or ark.step_service.store.list_steps(status=StepStatus.CREATED)):
            raise ValueError("Stable snapshot retry requires zero active work")
        flow = flows.get_flow(flow_id)
        step = flows.store.get_step(step_id)
        if (flow.flow_type != "native_repo_coordinator" or flow.status != FlowStatus.FAILED
                or getattr(flow.error, "error_type", None) != "coordinator_stable_snapshot_failed"
                or flow.current_step_id is not None
                or not flow.step_ids or flow.step_ids[-1] != step_id
                or flow.state.position.phase != "coordinator_callback"
                or step.flow_id != flow_id or step.status != StepStatus.COMPLETED
                or step.step_type != "coordinator_content_batch_snapshot_step"
                or getattr(step.result, "outcome", None) != "snapshot_created"
                or getattr(step.result, "snapshot_id", None) is not None):
            raise ValueError("Stable snapshot retry boundary changed or unsupported")
        original_error, original_finished = flow.error, flow.finished_at

        def reopen(record):
            record.status = FlowStatus.RUNNING
            record.error = None
            record.finished_at = None

        def rollback(record):
            record.status = FlowStatus.FAILED
            record.error = original_error
            record.finished_at = original_finished

        flows.store.update_flow_record(flow_id, reopen)
        try:
            current = flows.get_flow(flow_id)
            current.after_step_terminal_stable(StableStepTerminalContext(
                ark=ark, app=runtime.app, flow=current, step=step,
            ))
            current = flows.get_flow(flow_id)
            result = flows.store.get_step(step_id).result
            if current.status != FlowStatus.RUNNING or not getattr(result, "snapshot_id", None):
                if current.status != FlowStatus.FAILED:
                    flows.store.update_flow_record(flow_id, rollback)
                raise ValueError("Stable snapshot retry did not create a checkpoint; flow remains failed")
        except Exception:
            if flows.get_flow(flow_id).status != FlowStatus.FAILED:
                flows.store.update_flow_record(flow_id, rollback)
            raise
        ark.schedule_service.enqueue_flow(flow_id)
        return {"flow_id": flow_id, "step_id": step_id, "snapshot_id": result.snapshot_id}
