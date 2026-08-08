"""Lean business policies for production semantic scheduler leases."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Literal
from weakref import WeakKeyDictionary

from agent_runtime_kit.flow import (
    AgentStep,
    FlowStatus,
    SchedulerRunDecision,
    SchedulerSemanticRunPolicy,
    StepStatus,
)
from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskInput, ContentNodeTaskState


class SemanticAdvanceSafety(StrictModel):
    max_flow_advances: int = Field(default=500, ge=0)
    max_step_starts: int = Field(default=200, ge=0)

    @model_validator(mode="after")
    def validate_non_empty(self) -> "SemanticAdvanceSafety":
        if self.max_flow_advances == 0 and self.max_step_starts == 0:
            raise ValueError("semantic advance safety must allow at least one scheduler action")
        return self


class RuntimeSemanticAdvanceInput(StrictModel):
    granularity: Literal["step", "content_phase", "content_task"]
    action: Literal["logic", "agent", "plan", "child"] | None = None
    scope_id: str | None = None
    step_id: str | None = None
    content_task_flow_id: str | None = None
    safety: SemanticAdvanceSafety = Field(default_factory=SemanticAdvanceSafety)

    @model_validator(mode="after")
    def validate_discriminated_shape(self) -> "RuntimeSemanticAdvanceInput":
        if self.granularity == "step":
            if self.action not in {"logic", "agent"}:
                raise ValueError("step semantic advance requires action=logic or action=agent")
            if self.content_task_flow_id is not None:
                raise ValueError("step semantic advance cannot specify content_task_flow_id")
            if self.action == "logic" and not self.scope_id:
                raise ValueError("step.logic requires scope_id")
            if self.action == "logic" and self.step_id is not None:
                raise ValueError("step.logic cannot specify step_id")
            if self.action == "agent" and not self.step_id:
                raise ValueError("step.agent requires step_id")
            return self
        if self.granularity == "content_phase":
            if self.action not in {"plan", "child"}:
                raise ValueError("content_phase semantic advance requires action=plan or action=child")
        elif self.action is not None:
            raise ValueError("content_task semantic advance does not accept action")
        if not self.content_task_flow_id:
            raise ValueError(f"{self.granularity} semantic advance requires content_task_flow_id")
        if self.scope_id is not None or self.step_id is not None:
            raise ValueError(f"{self.granularity} semantic advance cannot specify scope_id or step_id")
        return self


class SemanticAdvancePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class SemanticLeaseObservationContext:
    granularity: str
    action: str | None
    scope_id: str | None
    step_id: str | None
    content_task_flow_id: str | None


_observation_lock = RLock()
_observations_by_scheduler: WeakKeyDictionary[object, dict[str, SemanticLeaseObservationContext]] = WeakKeyDictionary()


def register_semantic_lease_observation(
    schedule_service: object,
    lease_id: str,
    request: RuntimeSemanticAdvanceInput,
) -> SemanticLeaseObservationContext:
    context = SemanticLeaseObservationContext(
        granularity=request.granularity,
        action=request.action,
        scope_id=request.scope_id,
        step_id=request.step_id,
        content_task_flow_id=request.content_task_flow_id,
    )
    with _observation_lock:
        observations = _observations_by_scheduler.setdefault(schedule_service, {})
        observations[lease_id] = context
    return context


def get_semantic_lease_observation(
    schedule_service: object,
    lease_id: str,
) -> SemanticLeaseObservationContext | None:
    with _observation_lock:
        return _observations_by_scheduler.get(schedule_service, {}).get(lease_id)


def build_semantic_run_policy(runtime, request: RuntimeSemanticAdvanceInput) -> SchedulerSemanticRunPolicy:  # noqa: ANN001
    if request.granularity == "step":
        if request.action == "logic":
            return _build_logic_policy(runtime, request)
        return _build_agent_policy(runtime, request)
    if request.granularity == "content_phase":
        if request.action == "plan":
            return _build_content_plan_policy(runtime, request)
        return _build_content_child_policy(runtime, request)
    return _build_content_task_policy(runtime, request)


def _build_logic_policy(runtime, request: RuntimeSemanticAdvanceInput) -> SchedulerSemanticRunPolicy:  # noqa: ANN001
    assert request.scope_id is not None
    scope_id = request.scope_id
    flow_service = _flow_service(runtime)
    step_service = _step_service(runtime)
    initial_flow_ids = tuple(
        sorted(flow.flow_id for flow in flow_service.list_non_terminal_flows(scope_id=scope_id))
    )

    def created_agent_steps() -> list[str]:
        return [
            step_id
            for step_id in step_service.list_created_steps(scope_id=scope_id)
            if isinstance(step_service.store.get_step(step_id), AgentStep)
        ]

    def decide(_scheduler) -> SchedulerRunDecision:  # noqa: ANN001
        agent_ids = created_agent_steps()
        if agent_ids:
            return SchedulerRunDecision(action="pause", reason=f"agent_step_created:{agent_ids[0]}")
        terminal_flow_ids = [
            flow_id
            for flow_id in initial_flow_ids
            if flow_service.get_flow(flow_id).status in {FlowStatus.COMPLETED, FlowStatus.FAILED}
        ]
        if terminal_flow_ids:
            return SchedulerRunDecision(action="pause", reason=f"flow_terminal:{terminal_flow_ids[0]}")
        return SchedulerRunDecision()

    return SchedulerSemanticRunPolicy(
        name="step.logic",
        allow_flow_advance=lambda flow: flow.scope_id == scope_id and not created_agent_steps(),
        allow_step_start=lambda step: (
            step.scope_id == scope_id and not isinstance(step, AgentStep) and not created_agent_steps()
        ),
        decide=decide,
        max_flow_advances=request.safety.max_flow_advances,
        max_step_starts=request.safety.max_step_starts,
        idle_grace_s=0.2,
    )


def _build_agent_policy(runtime, request: RuntimeSemanticAdvanceInput) -> SchedulerSemanticRunPolicy:  # noqa: ANN001
    assert request.step_id is not None
    step_service = _step_service(runtime)
    target = step_service.store.get_step(request.step_id)
    if not isinstance(target, AgentStep):
        raise SemanticAdvancePolicyError(f"step.agent target is not an AgentStep: {request.step_id}")
    if target.status is not StepStatus.CREATED:
        raise SemanticAdvancePolicyError(f"step.agent target must be created: {request.step_id} is {target.status.value}")

    def decide(_scheduler) -> SchedulerRunDecision:  # noqa: ANN001
        current = step_service.store.get_step(request.step_id)
        if current.status in {StepStatus.COMPLETED, StepStatus.FAILED}:
            return SchedulerRunDecision(action="pause", reason=f"agent_step_terminal:{request.step_id}")
        return SchedulerRunDecision()

    return SchedulerSemanticRunPolicy(
        name="step.agent",
        allow_flow_advance=lambda flow: False,
        allow_step_start=lambda step: step.step_id == request.step_id,
        decide=decide,
        max_flow_advances=0,
        max_step_starts=max(2, request.safety.max_step_starts),
        idle_grace_s=0.2,
    )


def _build_content_plan_policy(runtime, request: RuntimeSemanticAdvanceInput) -> SchedulerSemanticRunPolicy:  # noqa: ANN001
    task = _content_task(runtime, request.content_task_flow_id)
    state = _content_state(task)
    if state.position.phase not in {"admission", "plan_agent", "callback_plan_agent"}:
        raise SemanticAdvancePolicyError(
            f"content_phase.plan requires admission/plan_agent/callback_plan_agent, got {state.position.phase}"
        )
    step_service = _step_service(runtime)
    baseline_plan_ids = {
        step_id
        for step_id in task.step_ids
        if step_service.store.get_step(step_id).step_type == "content_plan_agent_step"
    }
    initial_current = step_service.store.get_step(task.current_step_id) if task.current_step_id else None
    tracked_plan_id: dict[str, str | None] = {
        "value": (
            initial_current.step_id
            if initial_current is not None
            and initial_current.step_type == "content_plan_agent_step"
            and initial_current.status is StepStatus.CREATED
            else None
        )
    }

    def current_plan_step():  # noqa: ANN202
        if tracked_plan_id["value"] is not None:
            return step_service.store.get_step(tracked_plan_id["value"])
        current_task = _flow_service(runtime).get_flow(task.flow_id)
        for step_id in reversed(current_task.step_ids):
            step = step_service.store.get_step(step_id)
            if step.step_type == "content_plan_agent_step" and step_id not in baseline_plan_ids:
                tracked_plan_id["value"] = step_id
                return step
        return None

    def decide(_scheduler) -> SchedulerRunDecision:  # noqa: ANN001
        current_task = _flow_service(runtime).get_flow(task.flow_id)
        if current_task.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}:
            return SchedulerRunDecision(action="pause", reason=f"content_task_terminal:{task.flow_id}")
        plan_step = current_plan_step()
        if plan_step is not None and plan_step.status in {StepStatus.COMPLETED, StepStatus.FAILED}:
            return SchedulerRunDecision(action="pause", reason=f"content_plan_step_terminal:{plan_step.step_id}")
        return SchedulerRunDecision()

    return SchedulerSemanticRunPolicy(
        name="content_phase.plan",
        allow_flow_advance=lambda flow: flow.flow_id == task.flow_id and current_plan_step() is None,
        allow_step_start=lambda step: (
            step.flow_id == task.flow_id
            and (
                (step.step_type == "content_task_admission_step" and current_plan_step() is None)
                or (
                    step.step_type == "content_plan_agent_step"
                    and (step.step_id not in baseline_plan_ids or step.status is StepStatus.CREATED)
                )
            )
        ),
        decide=decide,
        max_flow_advances=request.safety.max_flow_advances,
        max_step_starts=request.safety.max_step_starts,
        idle_grace_s=0.2,
    )


def _build_content_child_policy(runtime, request: RuntimeSemanticAdvanceInput) -> SchedulerSemanticRunPolicy:  # noqa: ANN001
    task = _content_task(runtime, request.content_task_flow_id)
    state = _content_state(task)
    if state.position.phase in {"plan_agent", "callback_plan_agent", "admission", "completed"}:
        raise SemanticAdvancePolicyError(f"content_phase.child has no pending child action at phase {state.position.phase}")
    _require_single_content_task(task)

    def in_task_subtree(flow_id: str) -> bool:
        return _is_descendant_or_self(_flow_service(runtime), flow_id, task.flow_id)

    def flow_allowed(flow) -> bool:  # noqa: ANN001
        if not in_task_subtree(flow.flow_id):
            return False
        if flow.flow_id == task.flow_id:
            current_state = _content_state(flow)
            return current_state.position.phase != "callback_plan_agent"
        return True

    def step_allowed(step) -> bool:  # noqa: ANN001
        return in_task_subtree(step.flow_id) and step.step_type != "content_plan_agent_step"

    def decide(_scheduler) -> SchedulerRunDecision:  # noqa: ANN001
        current = _flow_service(runtime).get_flow(task.flow_id)
        if current.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}:
            return SchedulerRunDecision(action="pause", reason=f"content_task_terminal:{task.flow_id}")
        current_state = _content_state(current)
        if current_state.position.phase == "callback_plan_agent":
            return SchedulerRunDecision(action="pause", reason=f"waiting_for_parent_callback:{task.flow_id}")
        return SchedulerRunDecision()

    return SchedulerSemanticRunPolicy(
        name="content_phase.child",
        allow_flow_advance=flow_allowed,
        allow_step_start=step_allowed,
        decide=decide,
        max_flow_advances=request.safety.max_flow_advances,
        max_step_starts=request.safety.max_step_starts,
        idle_grace_s=0.2,
    )


def _build_content_task_policy(runtime, request: RuntimeSemanticAdvanceInput) -> SchedulerSemanticRunPolicy:  # noqa: ANN001
    task = _content_task(runtime, request.content_task_flow_id)
    _require_single_content_task(task)
    flow_service = _flow_service(runtime)
    coordinator_id = task.parent_flow_id

    def in_task_subtree(flow_id: str) -> bool:
        return _is_descendant_or_self(flow_service, flow_id, task.flow_id)

    def coordinator_closeout_allowed(flow) -> bool:  # noqa: ANN001
        if coordinator_id is None or flow.flow_id != coordinator_id:
            return False
        phase = getattr(getattr(flow.state, "position", None), "phase", None)
        return phase in {"waiting_content_tasks", "after_content_task_batch_snapshot"}

    def coordinator_step_allowed(step) -> bool:  # noqa: ANN001
        if coordinator_id is None or step.flow_id != coordinator_id or isinstance(step, AgentStep):
            return False
        return step.step_type == "coordinator_content_batch_snapshot_step"

    def decide(_scheduler) -> SchedulerRunDecision:  # noqa: ANN001
        current_task = flow_service.get_flow(task.flow_id)
        if current_task.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}:
            return SchedulerRunDecision()
        if coordinator_id is None:
            return SchedulerRunDecision(action="pause", reason=f"content_task_terminal:{task.flow_id}")
        coordinator = flow_service.get_flow(coordinator_id)
        phase = getattr(getattr(coordinator.state, "position", None), "phase", None)
        if phase == "coordinator_callback":
            return SchedulerRunDecision(action="pause", reason=f"content_task_batch_checkpointed:{task.flow_id}")
        if coordinator.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}:
            return SchedulerRunDecision(action="pause", reason=f"coordinator_terminal:{coordinator_id}")
        return SchedulerRunDecision()

    return SchedulerSemanticRunPolicy(
        name="content_task",
        allow_flow_advance=lambda flow: in_task_subtree(flow.flow_id) or coordinator_closeout_allowed(flow),
        allow_step_start=lambda step: in_task_subtree(step.flow_id) or coordinator_step_allowed(step),
        decide=decide,
        max_flow_advances=request.safety.max_flow_advances,
        max_step_starts=request.safety.max_step_starts,
        idle_grace_s=0.2,
    )


def _content_task(runtime, flow_id: str | None):  # noqa: ANN001, ANN202
    if not flow_id:
        raise SemanticAdvancePolicyError("content task flow id is required")
    flow = _flow_service(runtime).get_flow(flow_id)
    if flow.flow_type != "content_node_task":
        raise SemanticAdvancePolicyError(f"target flow is not content_node_task: {flow_id}")
    return flow


def _content_state(flow) -> ContentNodeTaskState:  # noqa: ANN001
    if not isinstance(flow.state, ContentNodeTaskState):
        raise SemanticAdvancePolicyError(f"content task has invalid state: {flow.flow_id}")
    return flow.state


def _require_single_content_task(task) -> None:  # noqa: ANN001
    if not isinstance(task.input, ContentNodeTaskInput):
        raise SemanticAdvancePolicyError(f"content task has invalid input: {task.flow_id}")
    if task.input.max_parallel_content_node_tasks != 1:
        raise SemanticAdvancePolicyError(
            "content semantic advance requires max_parallel_content_node_tasks=1; "
            f"got {task.input.max_parallel_content_node_tasks}"
        )


def _is_descendant_or_self(flow_service, flow_id: str, root_flow_id: str) -> bool:  # noqa: ANN001
    current_id: str | None = flow_id
    seen: set[str] = set()
    while current_id is not None and current_id not in seen:
        if current_id == root_flow_id:
            return True
        seen.add(current_id)
        current_id = flow_service.get_flow(current_id).parent_flow_id
    return False


def _flow_service(runtime):  # noqa: ANN001, ANN202
    service = runtime.ark.flow_service
    if service is None:
        raise SemanticAdvancePolicyError("flow service is not configured")
    return service


def _step_service(runtime):  # noqa: ANN001, ANN202
    service = runtime.ark.step_service
    if service is None:
        raise SemanticAdvancePolicyError("step service is not configured")
    return service


__all__ = [
    "RuntimeSemanticAdvanceInput",
    "SemanticAdvancePolicyError",
    "SemanticAdvanceSafety",
    "build_semantic_run_policy",
]
