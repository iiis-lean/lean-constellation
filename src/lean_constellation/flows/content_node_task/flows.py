"""Content node task Flow type definitions."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowReadContext, FlowStepContext, StableStepTerminalContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition, FlowStatus, utc_now_iso
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState, DispatchStep, DispatchStepResult, DispatchStepState
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.checkpoint_policy import content_task_progress_checkpoints_enabled
from lean_constellation.flows.common.flow_requests import repo_scope_id
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.content_node_task.decl_round.submissions import DeclRoundDispatchSubmission
from lean_constellation.flows.content_node_task.context_brief import build_content_plan_context_brief
from lean_constellation.flows.content_node_task.preparation.common import content_node_workdir
from lean_constellation.flows.content_node_task.steps import (
    ContentPlanStepResult,
    ContentProgressCheckpointStep,
    ContentProgressCheckpointStepResult,
    ContentTaskAdmissionStep,
    ContentTaskAdmissionStepResult,
    EnsureDeclStageAgentsStep,
    EnsureDeclStageAgentsStepResult,
    new_content_step_id,
)
from lean_constellation.flows.content_node_task.submissions import (
    ContentPreparationDispatchSubmission,
    ContentResourceRequestSubmission,
)


class ContentNodeTaskParams(LeanFlowParams):
    repo_key: str
    node_path: str
    repo_path: str | None = None
    contract_version: int | None = None
    max_parallel_content_node_tasks: int = Field(default=1, ge=1)


class ContentNodeTaskInput(LeanRenderableFlowInput):
    input_type: Literal["content_node_task"] = "content_node_task"
    repo_key: str
    repo_path: str | None = None
    node_path: str
    contract_version: int | None = None
    max_parallel_content_node_tasks: int = Field(default=1, ge=1)

    def agent_title(self) -> str:
        return f"Run content node task {self.node_path}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "contract_version": self.contract_version,
            "max_parallel_content_node_tasks": self.max_parallel_content_node_tasks,
        }


class ContentNodeTaskState(BaseFlowState):
    state_type: Literal["content_node_task"] = "content_node_task"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="admission"))
    waiting_dispatch_step_id: str | None = None
    used_preparation_kinds: list[Literal["node_dir_dependency", "mathlib", "resource"]] = Field(default_factory=list)
    decl_round_count: int = 0
    pending_dispatch_source_step_id: str | None = None
    pending_dispatch_source_submission_id: str | None = None
    waiting_child_kind: Literal["node_dir_dependency", "mathlib", "resource", "resource_curation", "decl_graph_round"] | None = None
    stage_agent_bindings_initialized: bool = False
    latest_callback_summary: str | None = None
    completed_child_flow_id: str | None = None
    completed_child_outcome: Literal["completed", "failed"] | None = None
    progress_checkpoint_repo_scope_captured: bool = False


class ContentNodeTaskResult(LeanRenderableFlowResult):
    result_type: Literal["content_node_task"] = "content_node_task"
    outcome: Literal["ready", "blocked", "failed"]
    repo_key: str
    node_path: str
    contract_version: int | None = None
    reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "contract_version": self.contract_version,
            "reason": self.reason,
        }


class ContentNodeTaskFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "content_node_task"
    Params: ClassVar[type[LeanFlowParams]] = ContentNodeTaskParams
    Input: ClassVar[type[BaseFlowInput]] = ContentNodeTaskInput
    State: ClassVar[type[BaseFlowState]] = ContentNodeTaskState
    Result: ClassVar[type[BaseFlowResult]] = ContentNodeTaskResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"content_node_task": ContentNodeTaskResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "ContentNodeTaskFlow":
        params = ContentNodeTaskParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=ContentNodeTaskInput(
                summary=f"Run task for content node {params.node_path}.",
                **params.model_dump(),
            ),
            state=ContentNodeTaskState(),
        )

    def can_exit_waiting(self, ctx: FlowReadContext) -> bool:
        state = _require_content_task_state(self.state)
        if state.position.phase != "waiting_child" or not state.waiting_dispatch_step_id:
            return False
        child_flows = _child_flows_for_dispatch(ctx, self.flow_id, state.waiting_dispatch_step_id)
        if not child_flows:
            return False
        return all(child.status in {FlowStatus.COMPLETED, FlowStatus.FAILED} for child in child_flows)

    def on_exit_waiting(self, ctx: FlowContext) -> None:
        state = _require_content_task_state(self.state)
        input_model = _require_content_task_input(self.input)
        children = _child_flows_for_dispatch(ctx, self.flow_id, state.waiting_dispatch_step_id)
        state.latest_callback_summary = _child_callback_summary(ctx, self.flow_id, state.waiting_dispatch_step_id)
        if children:
            child = children[0]
            state.completed_child_flow_id = child.flow_id
            state.completed_child_outcome = "failed" if child.status is FlowStatus.FAILED else "completed"
        if _should_create_progress_checkpoint(ctx.app, input_model, state):
            state.position = FlowPosition(phase="after_child_terminal_checkpoint", round_index=state.decl_round_count)
        else:
            if content_task_progress_checkpoints_enabled(ctx.app) and input_model.max_parallel_content_node_tasks != 1:
                warning = (
                    "Content task progress checkpoint skipped because "
                    f"max_parallel_content_node_tasks={input_model.max_parallel_content_node_tasks}, expected 1."
                )
                state.latest_callback_summary = "; ".join(
                    part for part in [state.latest_callback_summary, warning] if part
                )
            state.position = FlowPosition(phase="callback_plan_agent", round_index=state.decl_round_count)
        super().on_exit_waiting(ctx)

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_content_task_state(self.state)
        input_model = _require_content_task_input(self.input)
        if state.position.phase == "admission":
            return ctx.create_step(
                ContentTaskAdmissionStep(
                    step_id=new_content_step_id("content_task_admission"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "plan_agent":
            _inherit_content_plan_binding_from_prior_task(ctx, self)
            return ctx.create_step(
                _content_plan_agent_step(ctx, self, input_model, state, callback=False)
            )
        if state.position.phase == "callback_plan_agent":
            _inherit_content_plan_binding_from_prior_task(ctx, self)
            return ctx.create_step(
                _content_plan_agent_step(ctx, self, input_model, state, callback=True)
            )
        if state.position.phase == "after_child_terminal_checkpoint":
            checkpoint = _content_progress_checkpoint_step(self, input_model, state)
            if checkpoint is None:
                return None
            return ctx.create_step(checkpoint)
        if state.position.phase == "ensure_stage_agents":
            return ctx.create_step(
                EnsureDeclStageAgentsStep(
                    step_id=new_content_step_id("ensure_decl_stage_agents"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "dispatch_child":
            return ctx.create_step(_dispatch_step_from_pending(ctx, self, state))
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_content_task_state(self.state)
        input_model = _require_content_task_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="content_node_task_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return

        result = ctx.step.result
        if isinstance(result, ContentTaskAdmissionStepResult):
            self._consume_admission_result(state, input_model, result)
        elif ctx.step.step_type == "content_plan_agent_step":
            self._consume_plan_result(state, input_model, result, ctx.step.submission, ctx.step.step_id)
        elif isinstance(result, EnsureDeclStageAgentsStepResult):
            self._consume_stage_agents_result(state, input_model, result)
        elif isinstance(result, ContentProgressCheckpointStepResult):
            if result.outcome == "checkpoint_ready":
                state.position = FlowPosition(phase="callback_plan_agent", round_index=state.decl_round_count)
            else:
                self.error = BaseFlowError(
                    error_type=result.error_code or "content_progress_checkpoint_blocked",
                    message=result.summary or "Content task progress checkpoint was blocked.",
                )
        elif isinstance(result, DispatchStepResult):
            self._consume_dispatch_result(state, input_model, result, ctx.step.step_id)
        super().on_step_terminal(ctx)
        if self.result is None and self.error is None and state.position.phase == "waiting_child":
            self.status = FlowStatus.WAITING

    def after_step_terminal_stable(self, ctx: StableStepTerminalContext) -> None:
        result = ctx.step.result
        if not isinstance(result, ContentProgressCheckpointStepResult) or result.outcome != "checkpoint_ready":
            return
        input_model = _require_content_task_input(self.input)
        state = _require_content_task_state(self.state)
        repo_root = _content_repo_root(input_model)
        snapshot_runtime = getattr(ctx.app, "snapshot_runtime", None)
        if repo_root is None or snapshot_runtime is None:
            _mark_content_progress_snapshot_failed(
                ctx,
                "content_progress_snapshot_runtime_missing",
                "Content progress checkpoint requires repo_path and snapshot runtime.",
            )
            return
        refresh_scope_ids = [ctx.flow.scope_id]
        if not state.progress_checkpoint_repo_scope_captured:
            refresh_scope_ids.insert(0, repo_scope_id(input_model.repo_key))
        label_parts = [
            result.checkpoint_kind,
            f"node={result.node_path}",
            f"kind={result.child_kind}",
            f"child={result.child_flow_id}",
            f"outcome={result.child_outcome}",
        ]
        if result.child_kind == "decl_graph_round":
            child_flow = ctx.ark.flow_service.get_flow(result.child_flow_id)
            child_result = getattr(child_flow, "result", None)
            round_id = getattr(child_result, "round_id", None)
            round_index = getattr(child_result, "round_index", None)
            if round_id:
                label_parts.append(f"round_id={round_id}")
            if round_index is not None:
                label_parts.append(f"round_index={round_index}")
            if result.decl_round_count is not None:
                label_parts.append(f"task_round_count={result.decl_round_count}")
        snapshot = snapshot_runtime.create_repo_stable_point_snapshot(
            repo_root,
            checkpoint_kind=result.checkpoint_kind,
            label=" ".join(label_parts),
            node_paths=[input_model.node_path],
            scope_ids=refresh_scope_ids,
        )
        if not snapshot.ok or snapshot.value is None:
            message = "; ".join(str(getattr(issue, "message", issue)) for issue in snapshot.issues)
            _mark_content_progress_snapshot_failed(
                ctx,
                "content_progress_stable_snapshot_failed",
                message or "Content progress checkpoint snapshot failed.",
            )
            return

        def patch_flow(flow) -> None:  # noqa: ANN001
            flow.state.progress_checkpoint_repo_scope_captured = True

        def patch_step(step) -> None:  # noqa: ANN001
            step.result = step.result.model_copy(update={"snapshot_id": snapshot.value.snapshot_id})

        ctx.ark.flow_service.store.update_flow_record(ctx.flow.flow_id, patch_flow)
        ctx.ark.flow_service.store.update_step_record(ctx.step.step_id, patch_step)

    def _consume_admission_result(
        self,
        state: ContentNodeTaskState,
        input_model: ContentNodeTaskInput,
        result: ContentTaskAdmissionStepResult,
    ) -> None:
        if result.outcome == "accepted":
            state.position = FlowPosition(phase="plan_agent")
            return
        state.position = FlowPosition(phase="completed")
        self.result = ContentNodeTaskResult(
            outcome="failed",
            repo_key=input_model.repo_key,
            node_path=input_model.node_path,
            contract_version=input_model.contract_version,
            reason=result.reason or result.summary,
            summary=result.summary or result.reason or "Content node task admission rejected.",
        )

    def _consume_plan_result(
        self,
        state: ContentNodeTaskState,
        input_model: ContentNodeTaskInput,
        result: object | None,
        submission: object | None,
        step_id: str,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or result is None:
            self._finish_content_task(input_model, "failed", "ContentPlanAgent did not submit a valid result.", "ContentPlanAgent incomplete.")
            return
        if not isinstance(result, ContentPlanStepResult):
            self._finish_content_task(
                input_model,
                "failed",
                f"ContentPlanAgent returned unsupported result: {getattr(result, 'result_type', None)}.",
                "ContentPlanAgent returned unsupported result.",
            )
            return
        if result.outcome == "incomplete":
            self._finish_content_task(input_model, "failed", result.incomplete_reason or result.summary, result.summary)
            return
        if result.outcome == "preparation_dispatch" and isinstance(submission, ContentPreparationDispatchSubmission) and result.preparation is not None:
            kind = result.preparation.recon_kind
            if kind in state.used_preparation_kinds:
                self._finish_content_task(input_model, "failed", f"{kind} preparation recon has already been used.", "Duplicate preparation recon dispatch.")
                return
            if not result.preparation.objective or not result.preparation.objective.strip():
                self._finish_content_task(input_model, "failed", "Preparation recon dispatch requires a non-empty objective.", "Preparation recon objective missing.")
                return
            state.used_preparation_kinds.append(kind)
            self._set_pending_dispatch(state, step_id, submission.submission_id, kind)
            state.position = FlowPosition(phase="dispatch_child")
            return
        if result.outcome == "resource_request" and isinstance(submission, ContentResourceRequestSubmission):
            self._set_pending_dispatch(state, step_id, submission.submission_id, "resource_curation")
            state.position = FlowPosition(phase="dispatch_child")
            return
        if result.outcome == "decl_round_dispatch" and isinstance(submission, DeclRoundDispatchSubmission):
            state.decl_round_count += 1
            self._set_pending_dispatch(state, step_id, submission.submission_id, "decl_graph_round")
            state.position = FlowPosition(phase="ensure_stage_agents")
            return
        if result.outcome in {"ready", "blocked", "failed"}:
            reason = result.completion.reason if result.completion else None
            self._finish_content_task(input_model, result.outcome, reason, result.summary)
            return
        self._finish_content_task(input_model, "failed", "ContentPlanAgent result did not match its accepted submission.", "ContentPlanAgent submission mismatch.")

    def _consume_stage_agents_result(
        self,
        state: ContentNodeTaskState,
        input_model: ContentNodeTaskInput,
        result: EnsureDeclStageAgentsStepResult,
    ) -> None:
        if result.outcome == "ready":
            state.stage_agent_bindings_initialized = True
            state.position = FlowPosition(phase="dispatch_child", round_index=state.decl_round_count)
            return
        self._finish_content_task(input_model, "failed", result.reason or result.summary, result.summary)

    def _consume_dispatch_result(
        self,
        state: ContentNodeTaskState,
        input_model: ContentNodeTaskInput,
        result: DispatchStepResult,
        step_id: str,
    ) -> None:
        if result.outcome == "dispatched" and result.continuation == "wait_for_callback":
            state.waiting_dispatch_step_id = step_id
            state.position = FlowPosition(phase="waiting_child", round_index=state.decl_round_count)
            return
        self._finish_content_task(input_model, "failed", result.summary or "Child dispatch failed.", result.summary)

    def _set_pending_dispatch(self, state: ContentNodeTaskState, step_id: str, submission_id: str, child_kind: str) -> None:
        state.pending_dispatch_source_step_id = step_id
        state.pending_dispatch_source_submission_id = submission_id
        state.waiting_child_kind = child_kind  # type: ignore[assignment]

    def _finish_content_task(
        self,
        input_model: ContentNodeTaskInput,
        outcome: Literal["ready", "blocked", "failed"],
        reason: str | None,
        summary: str | None,
    ) -> None:
        state = _require_content_task_state(self.state)
        state.position = FlowPosition(phase="completed")
        self.result = ContentNodeTaskResult(
            outcome=outcome,
            repo_key=input_model.repo_key,
            node_path=input_model.node_path,
            contract_version=input_model.contract_version,
            reason=reason,
            summary=summary or reason or outcome,
        )


CONTENT_NODE_TASK_FLOW_TYPES: tuple[type[LeanBusinessFlow], ...] = (ContentNodeTaskFlow,)


def _require_content_task_state(state: BaseFlowState) -> ContentNodeTaskState:
    if not isinstance(state, ContentNodeTaskState):
        raise TypeError("content_node_task flow has invalid state")
    return state


def _require_content_task_input(input_model: BaseFlowInput | None) -> ContentNodeTaskInput:
    if not isinstance(input_model, ContentNodeTaskInput):
        raise TypeError("content_node_task flow has invalid input")
    return input_model


def _content_plan_agent_step(
    ctx: FlowContext,
    flow: ContentNodeTaskFlow,
    input_model: ContentNodeTaskInput,
    state: ContentNodeTaskState,
    *,
    callback: bool,
):
    from lean_constellation.flows.common.agent_steps import ContentPlanAgentStep

    brief = build_content_plan_context_brief(ctx, flow, input_model, state)
    brief_text = brief.render()
    return ContentPlanAgentStep(
        step_id=new_content_step_id("content_plan_callback" if callback else "content_plan"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            home_id="ContentPlanAgent",
            create_agent_if_missing=True,
            bind_created_agent_to="flow",
            variables={
                "repo_key": input_model.repo_key,
                "node_path": input_model.node_path,
                "contract_version": input_model.contract_version,
                "used_preparation_kinds": list(state.used_preparation_kinds),
                "decl_round_count": state.decl_round_count,
            },
            prompt_mode="callback" if callback else "initial",
            prompt_override=(
                None
                if callback
                else f"{_content_plan_initial_prompt(ctx, input_model)}\n\n{brief_text}"
            ),
            callback_dispatch_step_id=state.waiting_dispatch_step_id if callback else None,
            env_overrides={
                "LEAN_CONSTELLATION_AGENT_TYPE": "ContentPlanAgent",
                "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "content_plan",
                "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "content_plan_submit",
            },
            workdir_override=content_node_workdir(input_model.repo_path, input_model.node_path),
            max_auto_continue_turns=1,
        ),
    )


def _content_plan_initial_prompt(ctx: FlowContext, input_model: ContentNodeTaskInput) -> str:
    parts = [
        f"Run the content node task for {input_model.node_path}.",
        f"Repository: {input_model.repo_key}.",
    ]
    if input_model.contract_version is not None:
        parts.append(f"Contract version: {input_model.contract_version}.")
    parts.append(
        "Required Skill re-entry for this turn: read and apply "
        "$content-plan-completion-policy and "
        "$content-preparation-orchestration from the current Home before deciding. "
        "Do not rely on remembered Skill text from an earlier turn."
    )
    parts.append(
        "Read the current node contract and task context through tools. Submit exactly one next action: "
        "preparation recon, resource request, decl round, ready, blocked, or failed."
    )
    return "\n".join(parts)


def _inherit_content_plan_binding_from_prior_task(ctx: FlowContext, flow: ContentNodeTaskFlow) -> None:
    if flow.agent_bindings.get("content_plan") is not None:
        return
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        return
    prior_flows = [
        prior
        for prior in flow_service.list_flows(scope_id=flow.scope_id, flow_type=flow.flow_type)
        if prior.flow_id != flow.flow_id and prior.agent_bindings.get("content_plan") is not None
    ]
    if not prior_flows:
        return
    prior_flows.sort(key=lambda prior: (prior.created_at, prior.flow_id))
    inherited_agent_id = prior_flows[-1].agent_bindings.get("content_plan")
    if inherited_agent_id is not None and _valid_content_plan_agent_binding(ctx, inherited_agent_id, scope_id=flow.scope_id):
        flow.agent_bindings.by_role["content_plan"] = inherited_agent_id


def _valid_content_plan_agent_binding(ctx: FlowContext, agent_id: str, *, scope_id: str) -> bool:
    agent_service = ctx.ark.agent_service
    if agent_service is None:
        return False
    try:
        agent = agent_service.get_agent(agent_id)
    except Exception:  # noqa: BLE001
        return False
    if getattr(agent, "scope_id", None) != scope_id:
        return False
    agent_type = str(getattr(agent, "agent_type", ""))
    home_id = str(getattr(agent, "home_id", "") or "")
    if agent_type != "ContentPlanAgent" and home_id != "ContentPlanAgent":
        return False
    status = str(getattr(agent, "status", "idle"))
    if status in {"deleted", "archived", "failed"}:
        return False
    return True


def _dispatch_step_from_pending(ctx: FlowContext, flow: ContentNodeTaskFlow, state: ContentNodeTaskState) -> DispatchStep:
    source_step_id = state.pending_dispatch_source_step_id
    source_submission_id = state.pending_dispatch_source_submission_id
    if source_step_id is None or source_submission_id is None:
        raise TypeError("content node task dispatch source step/submission is missing")
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise TypeError("ark.flow_service is not registered")
    source_step = flow_service.get_step(source_step_id)
    submission = source_step.submission
    if not isinstance(submission, (ContentPreparationDispatchSubmission, ContentResourceRequestSubmission, DeclRoundDispatchSubmission)):
        raise TypeError(f"content node task dispatch got unsupported submission {type(submission).__name__}")
    return DispatchStep(
        step_id=new_content_step_id(f"dispatch_{state.waiting_child_kind or 'child'}"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=DispatchStepState(
            source_step_id=source_step_id,
            source_submission_id=source_submission_id,
            requests=list(submission.requests),
            continuation=submission.continuation,
        ),
    )


def _should_create_progress_checkpoint(app, input_model: ContentNodeTaskInput, state: ContentNodeTaskState) -> bool:  # noqa: ANN001
    if not content_task_progress_checkpoints_enabled(app):
        return False
    if input_model.max_parallel_content_node_tasks != 1:
        return False
    if state.completed_child_flow_id is None or state.completed_child_outcome is None:
        return False
    return state.waiting_child_kind in {
        "node_dir_dependency",
        "mathlib",
        "resource",
        "decl_graph_round",
    }


def _content_progress_checkpoint_step(
    flow: ContentNodeTaskFlow,
    input_model: ContentNodeTaskInput,
    state: ContentNodeTaskState,
) -> ContentProgressCheckpointStep | None:
    if state.completed_child_flow_id is None or state.completed_child_outcome is None:
        return None
    checkpoint_kind: Literal[
        "after_content_preparation_terminal",
        "after_content_decl_round_terminal",
    ]
    if state.waiting_child_kind == "decl_graph_round":
        checkpoint_kind = "after_content_decl_round_terminal"
    elif state.waiting_child_kind in {"node_dir_dependency", "mathlib", "resource"}:
        checkpoint_kind = "after_content_preparation_terminal"
    else:
        return None
    return ContentProgressCheckpointStep(
        step_id=new_content_step_id("content_progress_checkpoint"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        checkpoint_kind=checkpoint_kind,
        node_path=input_model.node_path,
        child_kind=str(state.waiting_child_kind),
        child_flow_id=state.completed_child_flow_id,
        child_outcome=state.completed_child_outcome,
        decl_round_count=state.decl_round_count if state.waiting_child_kind == "decl_graph_round" else None,
        callback_summary=state.latest_callback_summary,
    )


def _content_repo_root(input_model: ContentNodeTaskInput):
    if not input_model.repo_path:
        return None
    from pathlib import Path

    return Path(input_model.repo_path)


def _mark_content_progress_snapshot_failed(
    ctx: StableStepTerminalContext,
    error_type: str,
    message: str,
) -> None:
    now = utc_now_iso()

    def patch_flow(flow) -> None:  # noqa: ANN001
        flow.error = BaseFlowError(error_type=error_type, message=message)
        flow.status = FlowStatus.FAILED
        flow.finished_at = now
        flow.updated_at = now

    ctx.ark.flow_service.store.update_flow_record(ctx.flow.flow_id, patch_flow)


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


def _child_callback_summary(ctx: FlowReadContext | FlowContext, parent_flow_id: str, dispatch_step_id: str | None) -> str | None:
    child_flows = _child_flows_for_dispatch(ctx, parent_flow_id, dispatch_step_id)
    if not child_flows:
        return None
    parts = []
    for child in child_flows:
        if child.result is not None:
            parts.append(child.result.summary or getattr(child.result, "outcome", child.flow_type))
        elif child.error is not None:
            parts.append(child.error.message)
    return "; ".join(part for part in parts if part) or None
