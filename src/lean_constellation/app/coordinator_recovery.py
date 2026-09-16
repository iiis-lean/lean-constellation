"""Explicit current-truth retry for a settled, unsubmitted Coordinator callback."""

from agent_runtime_kit.flow import FlowStatus, StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult

from lean_constellation.flows.coordinator.steps import CoordinatorStepResult


def reset_incomplete_coordinator(runtime, flow_id: str, step_id: str, agent_id: str):
    ark = runtime.ark
    flows, agents = ark.flow_service, ark.agent_service

    def quiescent():
        if (ark.schedule_service.active_flow_advances or agents.has_running_agents()
                or ark.step_service.store.list_steps(status=StepStatus.RUNNING)
                or ark.step_service.store.list_steps(status=StepStatus.CREATED)):
            raise ValueError("Incomplete Coordinator reset requires zero active work")

    def validate(flow, step):
        incomplete = isinstance(step.result, AgentStepIncompleteResult) or (
            isinstance(step.result, CoordinatorStepResult) and step.result.outcome == "incomplete"
        )
        if (flow.flow_type != "native_repo_coordinator" or flow.status != FlowStatus.FAILED
                or getattr(flow.error, "error_type", None) != "coordinator_agent_incomplete"
                or flow.current_step_id is not None or flow.result is not None
                or not flow.step_ids or flow.step_ids[-1] != step_id
                or flow.state.position.phase != "coordinator_callback"
                or flow.agent_bindings.get("coordinator") != agent_id
                or step.flow_id != flow_id or step.scope_id != flow.scope_id
                or step.step_type != "coordinator_agent_step"
                or step.status != StepStatus.COMPLETED or step.submission is not None
                or step.error is not None or not incomplete
                or step.agent_bindings.get("coordinator") != agent_id):
            raise ValueError("Incomplete Coordinator reset boundary changed or unsupported")
        source = agents.get_agent(agent_id)
        if (source.scope_id != flow.scope_id or source.agent_type != "CoordinatorAgent"
                or source.status != "idle"):
            raise ValueError("Incomplete Coordinator reset requires the bound idle Coordinator Agent")

    with ark.pause_controller.hold_paused(None), agents.hold_agent_boundary(), flows.lock:
        quiescent()
        flow, step = flows.get_flow(flow_id), flows.store.get_step(step_id)
        validate(flow, step)
        # Resolve the current role default, never reuse the old native session.
        spec = agents.agent_types.get("CoordinatorAgent")
        agents.home_service.build_execution_context(
            spec.provider_type, spec.default_home_id or spec.agent_type,
        )
        fresh = agents.create_agent(flow.scope_id, "CoordinatorAgent")
        try:
            with flows.store.edit_session(flow.scope_id) as tx:
                current = tx.load_flow_for_update(flow_id)
                validate(current, flows.store.get_step(step_id))
                quiescent()
                current.status = FlowStatus.RUNNING
                current.error = None
                current.finished_at = None
                current.state.position.phase = "coordinator_agent"
                current.agent_bindings.by_role["coordinator"] = fresh.agent_id
        except Exception:
            agents.close_agent(fresh.agent_id)
            raise
        # Old Step, Agent, dispatch lineage and business truth remain audit evidence.
        # No enqueue/resume: the operator next admits one deterministic logic boundary.
        return fresh.agent_id
