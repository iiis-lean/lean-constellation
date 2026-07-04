"""Native repo coordinator Flow type definitions."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowReadContext, FlowStepContext, StableStepTerminalContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition, FlowStatus, utc_now_iso
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState, DispatchStep, DispatchStepResult, DispatchStepState
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.flow_requests import node_scope_id
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.coordinator.submissions import (
    CoordinatorContentTasksSubmission,
    CoordinatorResourceRequestSubmission,
)
from lean_constellation.flows.coordinator.steps import (
    CoordinatorContentBatchSnapshotStep,
    CoordinatorContentBatchSnapshotStepResult,
    CoordinatorStepResult,
    MarkCoordinatorRepoReadyStep,
    MarkCoordinatorRepoReadyStepResult,
    new_coordinator_step_id,
)


CoordinatorStartMode = Literal["native_preparation_handoff", "requirement_resume", "admin_start", "admin_resume"]


class NativeRepoCoordinatorParams(LeanFlowParams):
    repo_key: str | None = None
    repo_root: str | None = None
    start_mode: CoordinatorStartMode = "admin_start"
    start_reason: str | None = None
    resumed_requirement_name: str | None = None
    admin_note: str | None = None


class NativeRepoCoordinatorInput(LeanRenderableFlowInput):
    input_type: Literal["native_repo_coordinator"] = "native_repo_coordinator"
    repo_key: str | None = None
    repo_root: str | None = None
    start_mode: CoordinatorStartMode
    start_reason: str | None = None
    resumed_requirement_name: str | None = None
    admin_note: str | None = None

    def agent_title(self) -> str:
        repo = self.repo_key or "current repo"
        return f"Coordinate native repo {repo}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "start_mode": self.start_mode,
            "start_reason": self.start_reason,
            "resumed_requirement_name": self.resumed_requirement_name,
            "admin_note": self.admin_note,
        }


class NativeRepoCoordinatorState(BaseFlowState):
    state_type: Literal["native_repo_coordinator"] = "native_repo_coordinator"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="coordinator_agent"))
    waiting_dispatch_step_id: str | None = None
    waiting_requirement_name: str | None = None
    waiting_reason: str | None = None
    coordinator_turn_index: int = 0
    pending_dispatch_source_step_id: str | None = None
    pending_dispatch_source_submission_id: str | None = None
    pending_dispatch_kind: Literal["content_tasks", "resource_request"] | None = None
    pending_content_node_paths: list[str] = Field(default_factory=list)
    pending_resource_target_summary: str | None = None
    active_content_task_count: int = 0
    completed_content_task_count: int = 0
    repo_ready_summary: str | None = None


class NativeRepoCoordinatorResult(LeanRenderableFlowResult):
    result_type: Literal["native_repo_coordinator"] = "native_repo_coordinator"
    outcome: Literal["repo_ready"]
    repo_key: str | None = None
    provider_ready_marked: bool = False
    satisfied_requirement_count: int = 0

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "provider_ready_marked": self.provider_ready_marked,
            "satisfied_requirement_count": self.satisfied_requirement_count,
        }


class NativeRepoCoordinatorFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "native_repo_coordinator"
    Params: ClassVar[type[LeanFlowParams]] = NativeRepoCoordinatorParams
    Input: ClassVar[type[BaseFlowInput]] = NativeRepoCoordinatorInput
    State: ClassVar[type[BaseFlowState]] = NativeRepoCoordinatorState
    Result: ClassVar[type[BaseFlowResult]] = NativeRepoCoordinatorResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"native_repo_coordinator": NativeRepoCoordinatorResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "NativeRepoCoordinatorFlow":
        params = NativeRepoCoordinatorParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=NativeRepoCoordinatorInput(
                summary=params.start_reason or "Start native repo coordination.",
                **params.model_dump(),
            ),
            state=NativeRepoCoordinatorState(),
        )

    def can_exit_waiting(self, ctx: FlowReadContext) -> bool:
        state = _require_native_coordinator_state(self.state)
        if state.position.phase not in {"waiting_content_tasks", "waiting_resource_request"}:
            return False
        if not state.waiting_dispatch_step_id:
            return False
        child_flows = _child_flows_for_dispatch(ctx, self.flow_id, state.waiting_dispatch_step_id)
        if not child_flows:
            return False
        return all(child.status in {FlowStatus.COMPLETED, FlowStatus.FAILED} for child in child_flows)

    def on_exit_waiting(self, ctx: FlowContext) -> None:
        state = _require_native_coordinator_state(self.state)
        if state.position.phase == "waiting_content_tasks":
            child_flows = _child_flows_for_dispatch(ctx, self.flow_id, state.waiting_dispatch_step_id)
            state.completed_content_task_count = sum(
                1 for child in child_flows if child.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}
            )
            state.position = FlowPosition(phase="after_content_task_batch_snapshot")
        elif state.position.phase == "waiting_resource_request":
            state.position = FlowPosition(phase="coordinator_callback")
        super().on_exit_waiting(ctx)

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_native_coordinator_state(self.state)
        input_model = _require_native_coordinator_input(self.input)
        if state.position.phase == "coordinator_agent":
            return ctx.create_step(_coordinator_agent_step(self, input_model, state, callback=False))
        if state.position.phase == "coordinator_callback":
            return ctx.create_step(_coordinator_agent_step(self, input_model, state, callback=True))
        if state.position.phase == "before_content_task_dispatch_snapshot":
            return ctx.create_step(
                CoordinatorContentBatchSnapshotStep(
                    step_id=new_coordinator_step_id("before_content_task_dispatch_snapshot"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    checkpoint_kind="before_content_task_dispatch",
                    node_paths=list(state.pending_content_node_paths),
                )
            )
        if state.position.phase == "dispatch_content_tasks":
            return ctx.create_step(_dispatch_step_from_pending(ctx, self, state, expected_submission=CoordinatorContentTasksSubmission))
        if state.position.phase == "dispatch_resource_request":
            return ctx.create_step(_dispatch_step_from_pending(ctx, self, state, expected_submission=CoordinatorResourceRequestSubmission))
        if state.position.phase == "after_content_task_batch_snapshot":
            return ctx.create_step(
                CoordinatorContentBatchSnapshotStep(
                    step_id=new_coordinator_step_id("after_content_task_batch_snapshot"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    checkpoint_kind="after_content_task_batch_terminal",
                    node_paths=list(state.pending_content_node_paths),
                )
            )
        if state.position.phase == "mark_repo_ready":
            if not state.repo_ready_summary:
                return None
            return ctx.create_step(
                MarkCoordinatorRepoReadyStep(
                    step_id=new_coordinator_step_id("mark_repo_ready"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    repo_summary=state.repo_ready_summary,
                )
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_native_coordinator_state(self.state)
        input_model = _require_native_coordinator_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="native_repo_coordinator_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return

        result = ctx.step.result
        if ctx.step.step_type == "coordinator_agent_step":
            self._consume_coordinator_agent_result(state, result, ctx.step.submission, ctx.step.step_id)
        elif isinstance(result, CoordinatorContentBatchSnapshotStepResult):
            self._consume_content_snapshot_result(state, result)
        elif isinstance(result, DispatchStepResult):
            self._consume_dispatch_result(state, result, ctx.step.step_id)
        elif isinstance(result, MarkCoordinatorRepoReadyStepResult):
            self._consume_mark_ready_result(state, input_model, result)
        super().on_step_terminal(ctx)
        if self.result is None and self.error is None and state.position.phase in {
            "waiting_content_tasks",
            "waiting_resource_request",
            "waiting_requirement",
        }:
            self.status = FlowStatus.WAITING

    def after_step_terminal_stable(self, ctx: StableStepTerminalContext) -> None:
        result = ctx.step.result
        if ctx.step.step_type == "coordinator_content_batch_snapshot_step":
            if not isinstance(result, CoordinatorContentBatchSnapshotStepResult) or result.outcome != "snapshot_created":
                return
            input_model = _require_native_coordinator_input(self.input)
            repo_root = _coordinator_repo_root(input_model)
            if repo_root is None:
                _mark_flow_failed_from_stable_snapshot(
                    ctx,
                    "coordinator_stable_snapshot_failed",
                    [ValueError("Coordinator content task snapshot requires repo_root in Flow input.")],
                )
                return
            _record_stable_repo_snapshot(
                ctx,
                repo_root,
                checkpoint_kind=result.checkpoint_kind,
                label=f"{result.checkpoint_kind} for {input_model.repo_key or repo_root.name}",
                node_paths=list(result.node_paths),
                failure_type="coordinator_stable_snapshot_failed",
            )
            return

        if ctx.step.step_type != "coordinator_agent_step":
            return
        if not isinstance(result, CoordinatorStepResult) or result.outcome != "repo_requirement":
            return
        state = _require_native_coordinator_state(self.state)
        if state.position.phase != "waiting_requirement":
            return
        input_model = _require_native_coordinator_input(self.input)
        repo_root = _coordinator_repo_root(input_model)
        if repo_root is None:
            _mark_flow_failed_from_stable_snapshot(
                ctx,
                "coordinator_requirement_waiting_snapshot_failed",
                [ValueError("Coordinator requirement waiting snapshot requires repo_root in Flow input.")],
            )
            return
        _record_stable_repo_snapshot(
            ctx,
            repo_root,
            checkpoint_kind="coordinator_requirement_waiting",
            label=f"coordinator requirement waiting for {input_model.repo_key or repo_root.name}",
            failure_type="coordinator_requirement_waiting_snapshot_failed",
        )

    def _consume_coordinator_agent_result(
        self,
        state: NativeRepoCoordinatorState,
        result: object | None,
        submission: object | None,
        step_id: str,
    ) -> None:
        state.coordinator_turn_index += 1
        if isinstance(result, AgentStepIncompleteResult) or result is None:
            self._fail_coordinator("coordinator_agent_incomplete", "CoordinatorAgent did not submit a valid result.")
            return
        if not isinstance(result, CoordinatorStepResult):
            self._fail_coordinator(
                "coordinator_agent_result_unsupported",
                f"CoordinatorAgent returned unsupported result: {getattr(result, 'result_type', None)}.",
            )
            return
        if result.outcome == "incomplete":
            self._fail_coordinator("coordinator_agent_incomplete", result.incomplete_reason or result.summary or "CoordinatorAgent incomplete.")
            return
        if result.outcome == "content_tasks" and isinstance(submission, CoordinatorContentTasksSubmission) and result.content_tasks is not None:
            state.pending_dispatch_source_step_id = step_id
            state.pending_dispatch_source_submission_id = submission.submission_id
            state.pending_dispatch_kind = "content_tasks"
            state.pending_content_node_paths = list(result.content_tasks.node_paths)
            state.active_content_task_count = len(result.content_tasks.node_paths)
            state.completed_content_task_count = 0
            state.position = FlowPosition(phase="before_content_task_dispatch_snapshot")
            return
        if result.outcome == "resource_request" and isinstance(submission, CoordinatorResourceRequestSubmission) and result.resource_request is not None:
            state.pending_dispatch_source_step_id = step_id
            state.pending_dispatch_source_submission_id = submission.submission_id
            state.pending_dispatch_kind = "resource_request"
            state.pending_resource_target_summary = f"{result.resource_request.target_kind}:{result.resource_request.target}"
            state.position = FlowPosition(phase="dispatch_resource_request")
            return
        if result.outcome == "repo_requirement" and result.repo_requirement is not None:
            state.waiting_requirement_name = result.repo_requirement.requirement_name
            state.waiting_reason = result.repo_requirement.reason or result.summary
            state.position = FlowPosition(phase="waiting_requirement")
            return
        if result.outcome == "repo_ready" and result.repo_ready is not None:
            state.repo_ready_summary = result.repo_ready.repo_summary
            state.position = FlowPosition(phase="mark_repo_ready")
            return
        self._fail_coordinator("coordinator_agent_submission_mismatch", "CoordinatorAgent result did not match its accepted submission.")

    def _consume_content_snapshot_result(
        self,
        state: NativeRepoCoordinatorState,
        result: CoordinatorContentBatchSnapshotStepResult,
    ) -> None:
        if result.outcome != "snapshot_created":
            self._fail_coordinator(result.error_code or "coordinator_snapshot_failed", result.error_message or result.summary or "Coordinator snapshot failed.")
            return
        if result.checkpoint_kind == "before_content_task_dispatch":
            state.position = FlowPosition(phase="dispatch_content_tasks")
            return
        state.position = FlowPosition(phase="coordinator_callback")

    def _consume_dispatch_result(
        self,
        state: NativeRepoCoordinatorState,
        result: DispatchStepResult,
        step_id: str,
    ) -> None:
        if result.outcome != "dispatched" or result.continuation != "wait_for_callback":
            self._fail_coordinator("coordinator_dispatch_failed", result.summary or "Coordinator dispatch did not create callback child flows.")
            return
        state.waiting_dispatch_step_id = step_id
        if state.pending_dispatch_kind == "content_tasks":
            state.active_content_task_count = len(result.child_flow_ids)
            state.completed_content_task_count = 0
            state.position = FlowPosition(phase="waiting_content_tasks")
            return
        if state.pending_dispatch_kind == "resource_request":
            state.position = FlowPosition(phase="waiting_resource_request")
            return
        self._fail_coordinator("coordinator_dispatch_kind_missing", "Coordinator dispatch completed without pending dispatch kind.")

    def _consume_mark_ready_result(
        self,
        state: NativeRepoCoordinatorState,
        input_model: NativeRepoCoordinatorInput,
        result: MarkCoordinatorRepoReadyStepResult,
    ) -> None:
        if result.outcome != "ready_marked":
            self._fail_coordinator(result.error_code or "repo_ready_mark_failed", result.error_message or result.summary or "Repo ready marker failed.")
            return
        state.position = FlowPosition(phase="completed")
        self.result = NativeRepoCoordinatorResult(
            outcome="repo_ready",
            repo_key=input_model.repo_key,
            provider_ready_marked=result.provider_ready_marked,
            satisfied_requirement_count=result.satisfied_requirement_count,
            summary=result.repo_summary or result.summary or state.repo_ready_summary or "Repo ready.",
        )

    def _fail_coordinator(self, error_type: str, message: str) -> None:
        self.error = BaseFlowError(error_type=error_type, message=message)


COORDINATOR_FLOW_TYPES: tuple[type[LeanBusinessFlow], ...] = (NativeRepoCoordinatorFlow,)


def _record_stable_repo_snapshot(
    ctx: StableStepTerminalContext,
    repo_root,
    *,
    checkpoint_kind: str,
    label: str,
    node_paths: list[str] | None = None,
    failure_type: str,
) -> None:
    effective_node_paths = list(node_paths or [])
    repo_key = _repo_scope_key(ctx.flow.scope_id, repo_root)
    snapshot = ctx.app.validation_snapshot.create_repo_stable_point_snapshot(
        repo_root,
        checkpoint_kind=checkpoint_kind,
        label=label,
        node_paths=effective_node_paths,
        scope_ids=[ctx.flow.scope_id, *(node_scope_id(repo_key, path) for path in effective_node_paths)],
    )
    if not snapshot.ok or snapshot.value is None:
        _mark_flow_failed_from_stable_snapshot(ctx, failure_type, snapshot.issues)
        return

    def patch_step(step) -> None:  # noqa: ANN001
        if step.result is not None and hasattr(step.result, "model_copy"):
            step.result = step.result.model_copy(
                update={
                    "snapshot_id": snapshot.value.snapshot_id,
                    "summary": snapshot.value.summary,
                }
            )

    ctx.ark.flow_service.store.update_step_record(ctx.step.step_id, patch_step)


def _mark_flow_failed_from_stable_snapshot(ctx: StableStepTerminalContext, error_type: str, issues: list[object]) -> None:
    message = "; ".join(str(getattr(issue, "message", issue)) for issue in issues) or "Stable checkpoint snapshot failed."
    now = utc_now_iso()

    def patch_flow(flow) -> None:  # noqa: ANN001
        flow.error = BaseFlowError(error_type=error_type, message=message)
        flow.status = FlowStatus.FAILED
        flow.finished_at = now
        flow.updated_at = now

    ctx.ark.flow_service.store.update_flow_record(ctx.flow.flow_id, patch_flow)


def _repo_scope_key(scope_id: str, repo_root) -> str:
    if scope_id.startswith("repo:") and ":node:" not in scope_id:
        return scope_id.removeprefix("repo:")
    return getattr(repo_root, "name", str(repo_root).rstrip("/").rsplit("/", maxsplit=1)[-1])


def _coordinator_repo_root(input_model: NativeRepoCoordinatorInput):
    if not input_model.repo_root:
        return None
    from pathlib import Path

    return Path(input_model.repo_root)


def _require_native_coordinator_state(state: BaseFlowState) -> NativeRepoCoordinatorState:
    if not isinstance(state, NativeRepoCoordinatorState):
        raise TypeError("native_repo_coordinator flow has invalid state")
    return state


def _require_native_coordinator_input(input_model: BaseFlowInput | None) -> NativeRepoCoordinatorInput:
    if not isinstance(input_model, NativeRepoCoordinatorInput):
        raise TypeError("native_repo_coordinator flow has invalid input")
    return input_model


def _coordinator_agent_step(
    flow: NativeRepoCoordinatorFlow,
    input_model: NativeRepoCoordinatorInput,
    state: NativeRepoCoordinatorState,
    *,
    callback: bool,
):
    from lean_constellation.flows.common.agent_steps import CoordinatorAgentStep

    return CoordinatorAgentStep(
        step_id=new_coordinator_step_id("coordinator_callback" if callback else "coordinator"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=AgentStepState(
            agent_role="coordinator",
            agent_type="CoordinatorAgent",
            home_id="CoordinatorAgent",
            create_agent_if_missing=True,
            bind_created_agent_to="flow",
            variables={
                "repo_key": input_model.repo_key,
                "start_mode": input_model.start_mode,
                "coordinator_turn_index": state.coordinator_turn_index,
                "waiting_requirement_name": state.waiting_requirement_name,
            },
            prompt_mode="callback" if callback else "initial",
            prompt_override=None if callback else _coordinator_initial_prompt(input_model),
            callback_dispatch_step_id=state.waiting_dispatch_step_id if callback else None,
            env_overrides={
                "LEAN_CONSTELLATION_AGENT_TYPE": "CoordinatorAgent",
                "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "native_repo_coordinator",
                "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "native_repo_coordinator_submit",
            },
            workdir_override=input_model.repo_root,
            max_auto_continue_turns=1,
        ),
    )


def _coordinator_initial_prompt(input_model: NativeRepoCoordinatorInput) -> str:
    parts = [
        f"Coordinate native repo {input_model.repo_key or 'current repo'}.",
        f"Start mode: {input_model.start_mode}.",
    ]
    if input_model.start_reason:
        parts.append(f"Start reason: {input_model.start_reason}.")
    if input_model.resumed_requirement_name:
        parts.append(f"Resumed requirement: {input_model.resumed_requirement_name}.")
    if input_model.admin_note:
        parts.append(f"Admin note: {input_model.admin_note}.")
    parts.append(
        "Observe repo truth through tools and submit exactly one coordination move: content node tasks, "
        "resource request, repo requirement, or repo ready."
    )
    return "\n".join(parts)


def _dispatch_step_from_pending(
    ctx: FlowContext,
    flow: NativeRepoCoordinatorFlow,
    state: NativeRepoCoordinatorState,
    *,
    expected_submission: type[CoordinatorContentTasksSubmission] | type[CoordinatorResourceRequestSubmission],
) -> DispatchStep:
    source_step_id = state.pending_dispatch_source_step_id
    source_submission_id = state.pending_dispatch_source_submission_id
    if source_step_id is None or source_submission_id is None:
        raise TypeError("coordinator dispatch source step/submission is missing")
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise TypeError("ark.flow_service is not registered")
    source_step = flow_service.get_step(source_step_id)
    submission = source_step.submission
    if not isinstance(submission, expected_submission):
        raise TypeError(f"coordinator dispatch expected {expected_submission.__name__}, got {type(submission).__name__}")
    return DispatchStep(
        step_id=new_coordinator_step_id(f"dispatch_{state.pending_dispatch_kind or 'child_flows'}"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=DispatchStepState(
            source_step_id=source_step_id,
            source_submission_id=source_submission_id,
            requests=list(submission.requests),
            continuation=submission.continuation,
        ),
    )


def _child_flows_for_dispatch(ctx: FlowReadContext | FlowContext, parent_flow_id: str, dispatch_step_id: str | None):
    if dispatch_step_id is None:
        return []
    flow_service = ctx.ark.flow_service
    store = getattr(flow_service, "store", None) if flow_service is not None else None
    if store is not None and hasattr(store, "list_child_flows"):
        return list(store.list_child_flows(parent_flow_id=parent_flow_id, parent_dispatch_step_id=dispatch_step_id))
    if flow_service is None:
        return []
    return [
        flow
        for flow in flow_service.list_flows()
        if flow.parent_flow_id == parent_flow_id and flow.parent_dispatch_step_id == dispatch_step_id
    ]
