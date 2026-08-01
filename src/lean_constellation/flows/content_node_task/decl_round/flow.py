"""DeclGraph round Flow type definitions."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.content_node_task.preparation.common import content_node_workdir
from lean_constellation.flows.content_node_task.decl_round.steps import (
    BuildRoundResultStep,
    BuildRoundResultStepResult,
    BuildRoundResultStepState,
    DeclStageName,
    DeclStageReviewerStepState,
    DeclStageReviewerStepResult,
    DeclStageWorkerStepState,
    DeclStageWorkerStepResult,
    DeleteAndNormalizeStep,
    DeleteAndNormalizeStepResult,
    PrepareStageTargetsStep,
    PrepareStageTargetsStepResult,
    PrepareStageTargetsStepState,
    RoundFinalAuditStep,
    RoundFinalAuditStepResult,
    RoundStageRuntimeSummary,
    RoundStartValidationStep,
    RoundStartValidationStepResult,
    RoundTerminalReason,
    StageGateAndAuditStep,
    StageGateAndAuditStepResult,
    StageGateAndAuditStepState,
    new_decl_round_step_id,
)


STAGE_ORDER: tuple[DeclStageName, ...] = (
    "statement_nl",
    "statement_formal",
    "proof_nl",
    "proof_formal",
)

WORKER_AGENT_TYPES: dict[DeclStageName, str] = {
    "statement_nl": "StatementNLWorkerAgent",
    "statement_formal": "StatementFormalWorkerAgent",
    "proof_nl": "ProofNLWorkerAgent",
    "proof_formal": "ProofFormalWorkerAgent",
}

REVIEWER_AGENT_TYPES: dict[DeclStageName, str] = {
    "statement_nl": "StatementNLReviewerAgent",
    "statement_formal": "StatementFormalReviewerAgent",
    "proof_nl": "ProofNLReviewerAgent",
    "proof_formal": "ProofFormalReviewerAgent",
}

WORKER_VIEW_KEYS: dict[DeclStageName, str] = {
    "statement_nl": "statement_nl_worker",
    "statement_formal": "statement_formal_worker",
    "proof_nl": "proof_nl_worker",
    "proof_formal": "proof_formal_worker",
}

REVIEWER_VIEW_KEYS: dict[DeclStageName, str] = {
    "statement_nl": "statement_nl_reviewer",
    "statement_formal": "statement_formal_reviewer",
    "proof_nl": "proof_nl_reviewer",
    "proof_formal": "proof_formal_reviewer",
}


class DeclGraphRoundParams(LeanFlowParams):
    repo_key: str
    node_path: str
    strategy_id: str
    round_id: str
    repo_path: str | None = None
    contract_version: int | None = None
    round_index: int | None = None
    summary: str | None = None


class DeclGraphRoundInput(LeanRenderableFlowInput):
    input_type: Literal["decl_graph_round"] = "decl_graph_round"
    repo_key: str
    repo_path: str | None = None
    node_path: str
    contract_version: int | None = None
    strategy_id: str
    round_id: str
    round_index: int | None = None

    def agent_title(self) -> str:
        return f"Run DeclGraph round {self.round_id}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "contract_version": self.contract_version,
            "strategy_id": self.strategy_id,
            "round_id": self.round_id,
            "round_index": self.round_index,
        }


class DeclGraphRoundState(BaseFlowState):
    state_type: Literal["decl_graph_round"] = "decl_graph_round"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="round_start"))
    current_stage: DeclStageName | None = None
    current_retry_count: int = 0
    max_retries_per_stage: int = 2
    current_target_decl_names: list[str] = Field(default_factory=list)
    completed_stages: list[DeclStageName] = Field(default_factory=list)
    skipped_stages: list[DeclStageName] = Field(default_factory=list)
    stage_summaries: list[RoundStageRuntimeSummary] = Field(default_factory=list)
    terminal_reason: RoundTerminalReason | None = None
    pending_flow_outcome: Literal["completed", "blocked", "failed"] | None = None
    readiness_summary: str | None = None
    projection_summary: str | None = None
    latest_worker_result: DeclStageWorkerStepResult | None = None
    latest_reviewer_result: DeclStageReviewerStepResult | None = None


class DeclGraphRoundResult(LeanRenderableFlowResult):
    result_type: Literal["decl_graph_round"] = "decl_graph_round"
    outcome: Literal["completed", "blocked", "failed"]
    repo_key: str
    node_path: str
    round_id: str
    strategy_id: str | None = None
    round_index: int | None = None
    completed_stages: list[DeclStageName] = Field(default_factory=list)
    skipped_stages: list[DeclStageName] = Field(default_factory=list)
    terminal_stage: DeclStageName | None = None
    terminal_reason: RoundTerminalReason | None = None
    stage_summaries: list[RoundStageRuntimeSummary] = Field(default_factory=list)
    readiness_summary: str | None = None
    reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "strategy_id": self.strategy_id,
            "round_id": self.round_id,
            "round_index": self.round_index,
            "completed_stages": self.completed_stages,
            "skipped_stages": self.skipped_stages,
            "terminal_stage": self.terminal_stage,
            "terminal_reason": self.terminal_reason.message if self.terminal_reason else self.reason,
            "readiness_summary": self.readiness_summary,
        }


class DeclGraphRoundFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "decl_graph_round"
    Params: ClassVar[type[LeanFlowParams]] = DeclGraphRoundParams
    Input: ClassVar[type[BaseFlowInput]] = DeclGraphRoundInput
    State: ClassVar[type[BaseFlowState]] = DeclGraphRoundState
    Result: ClassVar[type[BaseFlowResult]] = DeclGraphRoundResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"decl_graph_round": DeclGraphRoundResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "DeclGraphRoundFlow":
        params = DeclGraphRoundParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=DeclGraphRoundInput(
                summary=params.summary,
                **params.model_dump(exclude={"summary"}),
            ),
            state=DeclGraphRoundState(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_decl_round_state(self.state)
        input_model = _require_decl_round_input(self.input)
        phase = state.position.phase
        if phase == "round_start":
            return ctx.create_step(
                RoundStartValidationStep(
                    step_id=new_decl_round_step_id("decl_round_start_validation"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if phase == "delete_normalize":
            return ctx.create_step(
                DeleteAndNormalizeStep(
                    step_id=new_decl_round_step_id("decl_round_delete_normalize"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if phase == "stage_prepare" and state.current_stage is not None:
            return ctx.create_step(
                PrepareStageTargetsStep(
                    step_id=new_decl_round_step_id(f"{state.current_stage}_targets"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=PrepareStageTargetsStepState(stage=state.current_stage),
                )
            )
        if phase == "stage_worker" and state.current_stage is not None:
            return ctx.create_step(_stage_worker_step(ctx, self, input_model, state))
        if phase == "stage_reviewer" and state.current_stage is not None:
            return ctx.create_step(_stage_reviewer_step(ctx, self, input_model, state))
        if phase == "stage_gate_audit" and state.current_stage is not None:
            return ctx.create_step(
                StageGateAndAuditStep(
                    step_id=new_decl_round_step_id(f"{state.current_stage}_gate_audit"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=StageGateAndAuditStepState(
                        stage=state.current_stage,
                        target_decl_names=list(state.current_target_decl_names),
                        retry_count=state.current_retry_count,
                        max_retries=state.max_retries_per_stage,
                    ),
                )
            )
        if phase == "final_audit":
            return ctx.create_step(
                RoundFinalAuditStep(
                    step_id=new_decl_round_step_id("decl_round_final_audit"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if phase == "build_result" and state.pending_flow_outcome is not None:
            return ctx.create_step(
                BuildRoundResultStep(
                    step_id=new_decl_round_step_id("decl_round_build_result"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=BuildRoundResultStepState(
                        flow_outcome=state.pending_flow_outcome,
                        reason=(
                            state.terminal_reason.message
                            if state.terminal_reason is not None
                            else None
                        ),
                    ),
                )
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_decl_round_state(self.state)
        input_model = _require_decl_round_input(self.input)
        if ctx.step.error is not None:
            recording_issues = _record_failed_step_execution_result(
                ctx,
                input_model=input_model,
                step_id=ctx.step.step_id,
                message=ctx.step.error.message,
            )
            self.error = BaseFlowError(
                error_type="decl_round_step_failed",
                message=ctx.step.error.message,
                details={
                    "step_type": ctx.step.step_type,
                    **ctx.step.error.details,
                    **(
                        {"round_execution_recording_issues": recording_issues}
                        if recording_issues
                        else {}
                    ),
                },
            )
            super().on_step_terminal(ctx)
            return

        result = ctx.step.result
        if isinstance(result, RoundStartValidationStepResult):
            self._consume_start_validation(state, result)
        elif isinstance(result, DeleteAndNormalizeStepResult):
            self._consume_delete_normalize(state, result)
        elif isinstance(result, PrepareStageTargetsStepResult):
            self._consume_stage_targets(state, result)
        elif ctx.step.step_type == "decl_stage_worker_agent_step":
            self._consume_worker_result(state, result)
        elif ctx.step.step_type == "decl_stage_reviewer_agent_step":
            self._consume_reviewer_result(state, result)
        elif isinstance(result, StageGateAndAuditStepResult):
            self._consume_stage_gate(state, result)
        elif isinstance(result, RoundFinalAuditStepResult):
            self._consume_final_audit(state, result)
        elif isinstance(result, BuildRoundResultStepResult):
            self._consume_built_result(state, input_model, result)
        super().on_step_terminal(ctx)

    def _consume_start_validation(self, state: DeclGraphRoundState, result: RoundStartValidationStepResult) -> None:
        if result.outcome == "valid":
            state.position = FlowPosition(phase="delete_normalize", round_index=result.round_index)
            return
        state.terminal_reason = result.error
        state.pending_flow_outcome = "failed"
        state.position = FlowPosition(phase="build_result")

    def _consume_delete_normalize(self, state: DeclGraphRoundState, result: DeleteAndNormalizeStepResult) -> None:
        if result.outcome == "normalized":
            _advance_to_next_stage(state)
            return
        state.terminal_reason = result.error
        state.pending_flow_outcome = "blocked" if result.outcome == "blocked" else "failed"
        state.position = FlowPosition(phase="build_result")

    def _consume_stage_targets(self, state: DeclGraphRoundState, result: PrepareStageTargetsStepResult) -> None:
        if result.outcome == "skipped":
            state.skipped_stages.append(result.stage)
            state.stage_summaries.append(
                RoundStageRuntimeSummary(
                    stage=result.stage,
                    outcome="skipped",
                    target_decl_names=[],
                    retry_count=state.current_retry_count,
                    summary=result.summary or result.skipped_reason or f"Skipped {result.stage}.",
                )
            )
            _advance_to_next_stage(state)
            return
        if result.outcome == "targets_ready":
            state.current_stage = result.stage
            state.current_target_decl_names = list(result.target_decl_names)
            state.latest_worker_result = None
            state.latest_reviewer_result = None
            state.position = FlowPosition(phase="stage_worker")
            return
        state.terminal_reason = result.error
        state.pending_flow_outcome = "blocked" if result.outcome == "blocked" else "failed"
        state.position = FlowPosition(phase="build_result")

    def _consume_worker_result(self, state: DeclGraphRoundState, result: object | None) -> None:
        if isinstance(result, AgentStepIncompleteResult) or result is None:
            state.terminal_reason = RoundTerminalReason(code="internal_service_error", message="Decl stage worker did not submit a valid result.", stage=state.current_stage)
            state.pending_flow_outcome = "failed"
            state.position = FlowPosition(phase="build_result")
            return
        if not isinstance(result, DeclStageWorkerStepResult):
            state.terminal_reason = RoundTerminalReason(code="internal_service_error", message="Decl stage worker returned an unsupported result.", stage=state.current_stage)
            state.pending_flow_outcome = "failed"
            state.position = FlowPosition(phase="build_result")
            return
        state.latest_worker_result = result
        if result.outcome == "completed":
            state.position = FlowPosition(phase="stage_reviewer")
            return
        reason = result.reason or result.incomplete_reason or result.summary or "Decl stage worker blocked."
        state.terminal_reason = RoundTerminalReason(
            code="worker_blocked" if result.outcome == "blocked" else "internal_service_error",
            message=reason,
            stage=state.current_stage,
            affected_decl_names=list(result.affected_decl_names or state.current_target_decl_names),
            suggested_plan_action="Return to ContentPlanAgent to gather resources, split dependencies, or re-plan the round.",
        )
        state.pending_flow_outcome = "blocked" if result.outcome == "blocked" else "failed"
        state.stage_summaries.append(
            RoundStageRuntimeSummary(
                stage=state.current_stage or result.stage or "statement_nl",
                outcome="blocked" if result.outcome == "blocked" else "failed",
                target_decl_names=list(state.current_target_decl_names),
                retry_count=state.current_retry_count,
                summary=reason,
            )
        )
        state.position = FlowPosition(phase="build_result")

    def _consume_reviewer_result(self, state: DeclGraphRoundState, result: object | None) -> None:
        if isinstance(result, AgentStepIncompleteResult) or result is None:
            state.terminal_reason = RoundTerminalReason(code="internal_service_error", message="Decl stage reviewer did not submit a valid result.", stage=state.current_stage)
            state.pending_flow_outcome = "failed"
            state.position = FlowPosition(phase="build_result")
            return
        if not isinstance(result, DeclStageReviewerStepResult):
            state.terminal_reason = RoundTerminalReason(code="internal_service_error", message="Decl stage reviewer returned an unsupported result.", stage=state.current_stage)
            state.pending_flow_outcome = "failed"
            state.position = FlowPosition(phase="build_result")
            return
        state.latest_reviewer_result = result
        state.position = FlowPosition(phase="stage_gate_audit")

    def _consume_stage_gate(self, state: DeclGraphRoundState, result: StageGateAndAuditStepResult) -> None:
        if result.outcome == "stage_passed":
            state.completed_stages.append(result.stage)
            state.stage_summaries.append(
                RoundStageRuntimeSummary(
                    stage=result.stage,
                    outcome="passed",
                    target_decl_names=list(result.advanced_decl_names),
                    retry_count=result.retry_count,
                    summary=result.summary or f"{result.stage} passed.",
                )
            )
            _advance_to_next_stage(state)
            return
        if result.outcome == "retry_worker":
            state.current_retry_count = result.retry_count
            state.stage_summaries.append(
                RoundStageRuntimeSummary(
                    stage=result.stage,
                    outcome="retry_worker",
                    target_decl_names=list(state.current_target_decl_names),
                    retry_count=result.retry_count,
                    summary=result.summary or f"Retry {result.stage} worker.",
                )
            )
            state.position = FlowPosition(phase="stage_worker")
            return
        state.terminal_reason = result.error
        state.pending_flow_outcome = "blocked" if result.outcome == "blocked" else "failed"
        state.stage_summaries.append(
            RoundStageRuntimeSummary(
                stage=result.stage,
                outcome="blocked" if result.outcome == "blocked" else "failed",
                target_decl_names=list(state.current_target_decl_names),
                retry_count=result.retry_count,
                summary=result.summary or (result.error.message if result.error else f"{result.stage} stopped."),
            )
        )
        state.position = FlowPosition(phase="build_result")

    def _consume_final_audit(self, state: DeclGraphRoundState, result: RoundFinalAuditStepResult) -> None:
        state.readiness_summary = result.readiness_summary
        state.projection_summary = result.projection_summary
        if result.outcome == "passed":
            state.pending_flow_outcome = "completed"
            state.position = FlowPosition(phase="build_result")
            return
        state.terminal_reason = result.error
        state.pending_flow_outcome = "blocked" if result.outcome == "blocked" else "failed"
        state.position = FlowPosition(phase="build_result")

    def _consume_built_result(
        self,
        state: DeclGraphRoundState,
        input_model: DeclGraphRoundInput,
        result: BuildRoundResultStepResult,
    ) -> None:
        reason = state.terminal_reason.message if state.terminal_reason else None
        self.result = DeclGraphRoundResult(
            outcome=result.flow_outcome,
            repo_key=input_model.repo_key,
            node_path=input_model.node_path,
            strategy_id=input_model.strategy_id,
            round_id=input_model.round_id,
            round_index=input_model.round_index,
            completed_stages=list(state.completed_stages),
            skipped_stages=list(state.skipped_stages),
            terminal_stage=state.terminal_reason.stage if state.terminal_reason else state.current_stage,
            terminal_reason=state.terminal_reason,
            stage_summaries=list(state.stage_summaries),
            readiness_summary=state.readiness_summary,
            reason=reason,
            summary=result.summary or reason or f"DeclGraph round {result.flow_outcome}.",
        )
        state.position = FlowPosition(phase="completed")


def _record_failed_step_execution_result(
    ctx: FlowStepContext,
    *,
    input_model: DeclGraphRoundInput,
    step_id: str,
    message: str,
) -> list[str]:
    repo_root = Path(input_model.repo_path) if input_model.repo_path else None
    service = getattr(ctx.app, "decl_graph", None)
    if repo_root is None or service is None:
        return ["Decl round failure could not record execution truth without repo_path and decl_graph service."]
    reason = f"Step {step_id} failed before DeclGraph round completion: {message}"
    try:
        recorded = service.record_round_execution_result(
            repo_root,
            node_path=input_model.node_path,
            round_id=input_model.round_id,
            outcome="failed",
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        return [f"Decl round failure could not record execution truth: {exc}"]
    if recorded.ok:
        return []
    return [
        str(getattr(issue, "message", None) or getattr(issue, "summary", None) or issue)
        for issue in recorded.issues
    ]


def _advance_to_next_stage(state: DeclGraphRoundState) -> None:
    if state.current_stage is None:
        next_index = 0
    else:
        next_index = STAGE_ORDER.index(state.current_stage) + 1
    if next_index >= len(STAGE_ORDER):
        state.current_stage = None
        state.current_target_decl_names = []
        state.position = FlowPosition(phase="final_audit")
        return
    state.current_stage = STAGE_ORDER[next_index]
    state.current_retry_count = 0
    state.current_target_decl_names = []
    state.latest_worker_result = None
    state.latest_reviewer_result = None
    state.position = FlowPosition(phase="stage_prepare")


def _stage_worker_step(ctx: FlowContext, flow: DeclGraphRoundFlow, input_model: DeclGraphRoundInput, state: DeclGraphRoundState):
    from lean_constellation.flows.common.agent_steps import DeclStageWorkerAgentStep

    stage = _require_stage(state)
    role = f"{stage}_worker"
    _inherit_stage_agent_binding_from_parent(ctx, flow, role)
    return DeclStageWorkerAgentStep(
        step_id=new_decl_round_step_id(f"{stage}_worker"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=DeclStageWorkerStepState(
            agent_role=role,
            agent_type=WORKER_AGENT_TYPES[stage],
            create_agent_if_missing=True,
            bind_created_agent_to="flow",
            round_id=input_model.round_id,
            node_path=input_model.node_path,
            stage=stage,
            expected_decl_names=list(state.current_target_decl_names),
            retry_attempt_index=state.current_retry_count,
            variables=_agent_variables(
                input_model,
                state,
                agent_role="worker",
                expected_view_key=WORKER_VIEW_KEYS[stage],
            ),
            prompt_override=_stage_worker_prompt(ctx, input_model, state),
            workdir_override=content_node_workdir(input_model.repo_path, input_model.node_path),
        ),
    )


def _stage_reviewer_step(ctx: FlowContext, flow: DeclGraphRoundFlow, input_model: DeclGraphRoundInput, state: DeclGraphRoundState):
    from lean_constellation.flows.common.agent_steps import DeclStageReviewerAgentStep

    stage = _require_stage(state)
    role = f"{stage}_reviewer"
    _inherit_stage_agent_binding_from_parent(ctx, flow, role)
    return DeclStageReviewerAgentStep(
        step_id=new_decl_round_step_id(f"{stage}_reviewer"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=DeclStageReviewerStepState(
            agent_role=role,
            agent_type=REVIEWER_AGENT_TYPES[stage],
            create_agent_if_missing=True,
            bind_created_agent_to="flow",
            round_id=input_model.round_id,
            node_path=input_model.node_path,
            stage=stage,
            expected_decl_names=list(state.current_target_decl_names),
            review_attempt_index=state.current_retry_count,
            variables=_agent_variables(
                input_model,
                state,
                agent_role="reviewer",
                expected_view_key=REVIEWER_VIEW_KEYS[stage],
            ),
            prompt_override=_stage_reviewer_prompt(ctx, input_model, state),
            workdir_override=content_node_workdir(input_model.repo_path, input_model.node_path),
        ),
    )


def _inherit_stage_agent_binding_from_parent(ctx: FlowContext, flow: DeclGraphRoundFlow, role: str) -> None:
    if flow.agent_bindings.get(role) is not None:
        return
    if not flow.parent_flow_id:
        return
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        return
    parent = flow_service.get_flow(flow.parent_flow_id)
    agent_id = parent.agent_bindings.get(role)
    if not agent_id:
        return
    flow.agent_bindings.by_role[role] = agent_id
    flow_service.store.update_flow_record(
        flow.flow_id,
        lambda stored: stored.agent_bindings.by_role.__setitem__(role, agent_id),
    )


def _agent_variables(
    input_model: DeclGraphRoundInput,
    state: DeclGraphRoundState,
    *,
    agent_role: Literal["worker", "reviewer"],
    expected_view_key: str,
) -> dict[str, object]:
    return {
        "repo_key": input_model.repo_key,
        "repo_root": input_model.repo_path,
        "node_path": input_model.node_path,
        "strategy_id": input_model.strategy_id,
        "round_id": input_model.round_id,
        "round_index": input_model.round_index,
        "stage": state.current_stage,
        "batch_decls": list(state.current_target_decl_names),
        "retry_attempt": state.current_retry_count,
        "agent_role": agent_role,
        "expected_view_key": expected_view_key,
    }


def _stage_worker_prompt(ctx: FlowContext, input_model: DeclGraphRoundInput, state: DeclGraphRoundState) -> str:
    stage = _require_stage(state)
    mode = "retry_after_review" if state.current_retry_count else "initial"
    metadata = _format_stage_target_metadata(ctx, input_model, state.current_target_decl_names)
    required_skills = _stage_required_skills(stage, role="worker")
    feedback = ""
    if state.latest_reviewer_result is not None:
        feedback = "\nPrevious review feedback:\n" + _format_reviewer_feedback(state.latest_reviewer_result)
    return (
        f"Run decl stage worker for {stage}.\n"
        f"Mode: {mode}.\n"
        f"Repo: {input_model.repo_key}. Node: {input_model.node_path}. Round: {input_model.round_id}.\n"
        f"Pipeline position: {_stage_pipeline_position(stage)}\n"
        f"Required Skill re-entry: read and apply {', '.join(f'${skill}' for skill in required_skills)} from the current Home before acting.\n"
        "The Flow owns later stages; global target_state does not expand this stage's authority. Missing later-stage artifacts are expected here.\n"
        f"Assigned declarations:\n{metadata}\n"
        f"Retry attempt: {state.current_retry_count} of {state.max_retries_per_stage}. "
        f"Retry remaining: {max(state.max_retries_per_stage - state.current_retry_count, 0)}."
        f"{feedback}\n"
        "Use only the stage-specific tools. Normal stage-local reading, editing, capture, dependency mutation, or reviewer repair is not a blocker. If Planner action is required, identify affected declarations, the missing interface, and the recommended planning change. On retry, re-read the current candidate and repair failed or missing declarations without regressing accepted work; the next reviewer must re-check the full current batch. Submit completed or blocked when the stage is ready."
    )


def _stage_reviewer_prompt(ctx: FlowContext, input_model: DeclGraphRoundInput, state: DeclGraphRoundState) -> str:
    stage = _require_stage(state)
    mode = "retry_review" if state.current_retry_count else "initial_review"
    metadata = _format_stage_target_metadata(ctx, input_model, state.current_target_decl_names)
    required_skills = _stage_required_skills(stage, role="reviewer")
    worker_summary = state.latest_worker_result.summary if state.latest_worker_result is not None else ""
    previous_feedback = ""
    if state.latest_reviewer_result is not None:
        previous_feedback = "\nPrevious reviewer feedback:\n" + _format_reviewer_feedback(state.latest_reviewer_result)
    return (
        f"Review decl stage {stage}.\n"
        f"Mode: {mode}.\n"
        f"Repo: {input_model.repo_key}. Node: {input_model.node_path}. Round: {input_model.round_id}.\n"
        f"Pipeline position: {_stage_pipeline_position(stage)}\n"
        f"Required Skill re-entry: read and apply {', '.join(f'${skill}' for skill in required_skills)} from the current Home before review.\n"
        "This is a read-only review role; do not perform worker mutation.\n"
        "Review only this layer. The deterministic final audit, not this review, decides whether global target_state was reached.\n"
        f"Assigned declarations:\n{metadata}\n"
        f"Review attempt: {state.current_retry_count}. Retry remaining: {max(state.max_retries_per_stage - state.current_retry_count, 0)}.\n"
        f"Worker summary: {worker_summary or '(not provided)'}.\n"
        f"{previous_feedback}\n"
        "Read current candidates through tools and record exactly one current mark for every target declaration. Re-check the full current batch even when retry feedback highlights only failed or missing declarations. Submit the stage review after all current marks are present."
    )


def _format_reviewer_feedback(result: DeclStageReviewerStepResult) -> str:
    lines = [
        f"- Summary: {result.summary or '(not provided)'}",
        f"- Reviewed: {', '.join(result.reviewed_decl_names) or 'none'}",
        f"- Failed: {', '.join(result.failed_decl_names) or 'none'}",
        f"- Missing: {', '.join(result.missing_decl_names) or 'none'}",
    ]
    for item in result.feedback:
        categories = ", ".join(item.issue_categories) or item.issue_kind or "unspecified"
        changes = "; ".join(item.required_changes) or item.suggested_fix or "unspecified"
        lines.append(f"- {item.decl_name}: {categories}; required changes: {changes}; summary: {item.summary}")
    return "\n".join(lines)


def _format_stage_target_metadata(
    ctx: FlowContext,
    input_model: DeclGraphRoundInput,
    decl_names: list[str],
) -> str:
    if not decl_names:
        return "- (no target metadata)"
    lines: list[str] = []
    repo_root = Path(input_model.repo_path) if input_model.repo_path else None
    for decl_name in decl_names:
        if repo_root is None:
            lines.append(f"- {decl_name}: current revision truth is available through stage read tools.")
            continue
        decl_result = ctx.app.decl_graph.get_decl(
            repo_root,
            node_path=input_model.node_path,
            name=decl_name,
        )
        if not decl_result.ok or decl_result.value is None:
            lines.append(f"- {decl_name}: declaration truth could not be loaded.")
            continue
        decl = decl_result.value
        revision_result = ctx.app.decl_graph.get_decl_revision(
            repo_root,
            node_path=input_model.node_path,
            name=decl_name,
            revision=decl.current_revision,
        )
        if not revision_result.ok or revision_result.value is None:
            lines.append(f"- {decl_name}: current revision truth could not be loaded.")
            continue
        revision = revision_result.value
        change = revision.change
        lines.extend(
            [
                f"- {decl_name}",
                f"  Kind: {decl.kind}",
                f"  Change: {change.kind.value if change is not None else 'none'}",
                f"  Objective: {change.objective if change is not None else '(not provided)'}",
                f"  Required through: {_target_state_display(change.target_state if change is not None else None)}",
            ]
        )
    return "\n".join(lines)


def _target_state_display(target_state) -> str:  # noqa: ANN001
    value = getattr(target_state, "value", target_state)
    return {
        "planned": "Planning",
        "specified": "Statement NL",
        "declared": "Statement Formal",
        "proof_planned": "Proof NL",
        "proved": "Proof Formal",
        "ready": "Deterministic release gate",
    }.get(str(value), str(value or "not specified"))


def _stage_required_skills(stage: DeclStageName, *, role: Literal["worker", "reviewer"]) -> tuple[str, ...]:
    if role == "reviewer":
        return ("content-contract-reading", "decl-dependency-origin-curation")
    if stage == "statement_formal":
        return (
            "content-contract-reading",
            "decl-owned-lean-file-capture-check",
            "lean-statement-formalization",
        )
    if stage == "proof_formal":
        return (
            "content-contract-reading",
            "decl-owned-lean-file-capture-check",
            "lean-proof-formalization",
        )
    return ("content-contract-reading", "decl-dependency-origin-curation")


def _stage_pipeline_position(stage: str) -> str:
    positions = {
        "statement_nl": "planned --Statement NL--> specified",
        "statement_formal": "specified --Statement Formal--> declared",
        "proof_nl": "declared --Proof NL--> proof_planned",
        "proof_formal": "proof_planned --Proof Formal--> proved",
    }
    return positions[stage]


def _require_stage(state: DeclGraphRoundState) -> DeclStageName:
    if state.current_stage is None:
        raise ValueError("DeclGraphRoundState has no current stage")
    return state.current_stage


def _require_decl_round_state(state: BaseFlowState) -> DeclGraphRoundState:
    if not isinstance(state, DeclGraphRoundState):
        raise TypeError("decl_graph_round flow has invalid state")
    return state


def _require_decl_round_input(input_model: BaseFlowInput | None) -> DeclGraphRoundInput:
    if not isinstance(input_model, DeclGraphRoundInput):
        raise TypeError("decl_graph_round flow has invalid input")
    return input_model


DECL_ROUND_FLOW_TYPES: tuple[type[LeanBusinessFlow], ...] = (DeclGraphRoundFlow,)
