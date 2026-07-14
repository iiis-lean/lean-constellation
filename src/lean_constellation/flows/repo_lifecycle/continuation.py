"""Native repository continuation orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowReadContext, FlowStepContext, StableStepTerminalContext, StepRunContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, BaseStep, BaseStepResult, BaseStepState, ChildFlowDispatchSubmission, FlowPosition, FlowRequest, FlowStatus, StepTerminalReceipt, utc_now_iso
from agent_runtime_kit.flow.standard_steps import DispatchStep, DispatchStepResult, DispatchStepState
from pydantic import Field

from lean_constellation.domain.repo_run import RepoRunContext, RepoRunSpec
from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult, LeanRenderableStepResult
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.repo_lifecycle.root_interface import RootInterfacePreparationResult
from lean_constellation.flows.repo_lifecycle.run_steps import ApplyNativeRunResult, ApplyNativeRunStep, ContinuationHandoffGateResult, ContinuationHandoffGateStep, PrepareNativeRunMutationResult, PrepareNativeRunMutationStep
from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildResult
from lean_constellation.flows.repo_lifecycle.steps import new_repo_lifecycle_step_id
from lean_constellation.services.validation_snapshot import RepoCheckpointKind


class NativeRepoContinuationParams(LeanFlowParams):
    repo_key: str
    repo_root: str
    run_spec: RepoRunSpec
    base_release_id: str
    start_reason: Literal["admin_continue", "provider_upgrade", "repair_after_preflight"] = "admin_continue"
    admin_notes: str | None = None


class NativeRepoContinuationInput(LeanRenderableFlowInput):
    input_type: Literal["native_repo_continuation"] = "native_repo_continuation"
    repo_key: str
    repo_root: str
    run_spec: RepoRunSpec
    base_release_id: str
    start_reason: Literal["admin_continue", "provider_upgrade", "repair_after_preflight"]
    admin_notes: str | None = None

    def agent_title(self) -> str:
        return f"Continue native repo {self.repo_key}"


class NativeRepoContinuationState(BaseFlowState):
    state_type: Literal["native_repo_continuation"] = "native_repo_continuation"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="prepare_checkpoint"))
    pre_run_mutation_checkpoint_id: str | None = None
    publication_started_stable: bool = False
    publication_transitioned: bool = False
    previous_target: str | None = None
    previous_work_mode: str | None = None
    config_change_summary: str | None = None
    resolved_source_files: list[str] = Field(default_factory=list)
    source_index_result: SourceIndexBuildResult | None = None
    root_interface_result: RootInterfacePreparationResult | None = None
    pending_source_step_id: str | None = None
    waiting_dispatch_step_id: str | None = None
    waiting_child_kind: Literal["source_index", "root_interface"] | None = None
    coordinator_flow_id: str | None = None


class NativeRepoContinuationResult(LeanRenderableFlowResult):
    result_type: Literal["native_repo_continuation"] = "native_repo_continuation"
    outcome: Literal["handoff_dispatched", "blocked", "invalid_input"]
    repo_key: str
    run_objective: str
    reason: str | None = None


class PrepareContinuationDispatchResult(LeanRenderableStepResult):
    result_type: Literal["prepare_continuation_dispatch"] = "prepare_continuation_dispatch"
    outcome: Literal["prepared", "blocked"]
    child_kind: Literal["source_index", "root_interface", "coordinator"] | None = None
    reason: str | None = None


class PrepareContinuationDispatchStep(BaseStep):
    step_type: ClassVar[str] = "prepare_continuation_dispatch_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = PrepareContinuationDispatchResult
    Results = {"prepare_continuation_dispatch": PrepareContinuationDispatchResult}
    Submissions = {"child_flow_dispatch": ChildFlowDispatchSubmission}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = ctx.ark.flow_service.get_flow(ctx.flow_id)
        inp, state = flow.input, flow.state
        if not isinstance(inp, NativeRepoContinuationInput) or not isinstance(state, NativeRepoContinuationState):
            raise TypeError("continuation flow truth is invalid")
        spec = inp.run_spec
        if state.position.phase == "prepare_source":
            kind = "source_index"
            request = FlowRequest(flow_type="source_index_build", scope_id=ctx.scope_id, params={
                "repo_key": inp.repo_key, "repo_root": inp.repo_root, "run_objective": spec.run_objective,
                "target_proof_availability": spec.target_proof_availability, "work_mode": spec.work_mode,
                "source_scope": spec.source_scope.model_dump(mode="json"), "index_policy": spec.index_policy,
                "start_reason": "continuation", "pre_update_checkpoint_id": state.pre_run_mutation_checkpoint_id,
            })
        elif state.position.phase == "prepare_root":
            kind = "root_interface"
            source = state.source_index_result or SourceIndexBuildResult(
                outcome="no_op", repo_key=inp.repo_key, resolved_file_paths=state.resolved_source_files,
                summary="SourceIndex reuse requested."
            )
            run_context = RepoRunContext(start_kind="continuation", run_spec=spec,
                resolved_source_files=state.resolved_source_files, source_index_delta_summary=source.summary,
                base_release_id=inp.base_release_id)
            request = FlowRequest(flow_type="root_interface_preparation", scope_id=ctx.scope_id, params={
                "repo_key": inp.repo_key, "repo_root": inp.repo_root, "run_context": run_context.model_dump(mode="json"),
                "source_index_delta": source.model_dump(mode="json"), "start_reason": "continuation",
                "pre_run_mutation_checkpoint_id": state.pre_run_mutation_checkpoint_id,
            })
        else:
            kind = "coordinator"
            source_summary = state.source_index_result.summary if state.source_index_result else "SourceIndex reused."
            root_summary = state.root_interface_result.summary if state.root_interface_result else "Root interfaces reused."
            run_context = RepoRunContext(start_kind="continuation", run_spec=spec,
                resolved_source_files=state.resolved_source_files, source_index_delta_summary=source_summary,
                root_interface_delta_summary=root_summary, base_release_id=inp.base_release_id)
            run_context = run_context.model_copy(update={"config_change_summary": state.config_change_summary})
            request = FlowRequest(flow_type="native_repo_coordinator", scope_id=ctx.scope_id, params={
                "repo_key": inp.repo_key, "repo_root": inp.repo_root, "start_mode": "continuation_handoff",
                "start_reason": spec.run_objective, "admin_note": inp.admin_notes,
                "run_context": run_context.model_dump(mode="json"),
            })
        submission = ChildFlowDispatchSubmission(submission_id=new_submission_id(f"continuation_{kind}"),
            submission_type="child_flow_dispatch", tool_name="prepare_continuation_dispatch",
            summary=f"Dispatch continuation {kind} Flow.", requests=[request],
            continuation="wait_for_callback" if kind != "coordinator" else "terminal_handoff")
        ctx.accept_step_submission(submission)
        return ctx.complete_step(PrepareContinuationDispatchResult(outcome="prepared", child_kind=kind, summary=submission.summary))


class NativeRepoContinuationFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "native_repo_continuation"
    Params = NativeRepoContinuationParams
    Input: ClassVar[type[BaseFlowInput]] = NativeRepoContinuationInput
    State: ClassVar[type[BaseFlowState]] = NativeRepoContinuationState
    Result: ClassVar[type[BaseFlowResult]] = NativeRepoContinuationResult
    Results = {"native_repo_continuation": NativeRepoContinuationResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "NativeRepoContinuationFlow":
        params = NativeRepoContinuationParams.model_validate(ctx.params)
        return cls._build(ctx, input_model=NativeRepoContinuationInput(summary=f"Continue {params.repo_key}.", **params.model_dump()), state=NativeRepoContinuationState())

    def can_exit_waiting(self, ctx: FlowReadContext) -> bool:
        state = self.state
        if not isinstance(state, NativeRepoContinuationState) or not state.waiting_dispatch_step_id:
            return False
        children = ctx.ark.flow_service.store.list_child_flows(parent_flow_id=self.flow_id, parent_dispatch_step_id=state.waiting_dispatch_step_id)
        return len(children) == 1 and children[0].status in {FlowStatus.COMPLETED, FlowStatus.FAILED}

    def on_exit_waiting(self, ctx: FlowContext) -> None:
        state = self.state
        children = ctx.ark.flow_service.store.list_child_flows(parent_flow_id=self.flow_id, parent_dispatch_step_id=state.waiting_dispatch_step_id)
        if len(children) != 1:
            self._fail("continuation_child_resolution_failed", "Continuation callback did not resolve exactly one child Flow.")
            return
        child = children[0]
        if child.status is FlowStatus.FAILED:
            self._fail("continuation_child_failed", child.error.message if child.error else "Child flow failed.")
        elif state.waiting_child_kind == "source_index" and isinstance(child.result, SourceIndexBuildResult):
            if child.result.outcome in {"committed", "no_op"}:
                state.source_index_result = child.result
                state.resolved_source_files = child.result.resolved_file_paths
                state.position = FlowPosition(phase="prepare_root")
            else:
                self._finish("invalid_input" if child.result.outcome == "invalid_input" else "blocked", child.result.reason or child.result.summary)
        elif state.waiting_child_kind == "root_interface" and isinstance(child.result, RootInterfacePreparationResult):
            if child.result.outcome == "ready":
                state.root_interface_result = child.result
                state.position = FlowPosition(phase="handoff_gate")
            else:
                self._finish(child.result.outcome, child.result.blocked_reason or child.result.summary)
        else:
            self._finish("blocked", "Continuation child returned an invalid result.")
        state.waiting_dispatch_step_id = None
        state.waiting_child_kind = None
        if self.result is None and self.error is None:
            super().on_exit_waiting(ctx)

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = self.state
        phase = state.position.phase
        if phase == "prepare_checkpoint":
            return ctx.create_step(PrepareNativeRunMutationStep(step_id=new_repo_lifecycle_step_id("prepare_run_checkpoint"), flow_id=self.flow_id, scope_id=self.scope_id))
        if phase == "apply_run":
            return ctx.create_step(ApplyNativeRunStep(step_id=new_repo_lifecycle_step_id("apply_run"), flow_id=self.flow_id, scope_id=self.scope_id))
        if phase == "handoff_gate":
            return ctx.create_step(ContinuationHandoffGateStep(step_id=new_repo_lifecycle_step_id("continuation_handoff_gate"), flow_id=self.flow_id, scope_id=self.scope_id))
        if phase in {"prepare_source", "prepare_root", "prepare_coordinator"}:
            return ctx.create_step(PrepareContinuationDispatchStep(step_id=new_repo_lifecycle_step_id(phase), flow_id=self.flow_id, scope_id=self.scope_id))
        if phase == "dispatch":
            source = ctx.ark.flow_service.get_step(state.pending_source_step_id)
            return ctx.create_step(DispatchStep(step_id=new_repo_lifecycle_step_id("dispatch_continuation"), flow_id=self.flow_id, scope_id=self.scope_id,
                state=DispatchStepState(source_step_id=source.step_id, source_submission_id=source.submission.submission_id,
                    requests=source.submission.requests, continuation=source.submission.continuation)))
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state, inp, result = self.state, self.input, ctx.step.result
        if ctx.step.error is not None:
            self._fail("native_continuation_step_failed", ctx.step.error.message)
            super().on_step_terminal(ctx)
            return
        if isinstance(result, PrepareNativeRunMutationResult):
            if result.outcome == "prepared":
                state.pre_run_mutation_checkpoint_id = result.checkpoint_id
                state.publication_started_stable = result.started_stable
                state.previous_target = result.previous_target
                state.previous_work_mode = result.previous_work_mode
                state.position = FlowPosition(phase="apply_run")
            else:
                self._finish("blocked", result.reason)
        elif isinstance(result, ApplyNativeRunResult):
            if result.outcome == "applied":
                state.publication_transitioned = result.transitioned
                state.resolved_source_files = result.resolved_source_files
                state.config_change_summary = result.config_change_summary
                state.position = FlowPosition(phase="prepare_source")
            else:
                self._finish("blocked", result.reason)
        elif isinstance(result, PrepareContinuationDispatchResult):
            if result.outcome != "prepared" or result.child_kind is None or ctx.step.submission is None:
                self._finish("blocked", result.reason or result.summary)
            else:
                state.pending_source_step_id = ctx.step.step_id
                state.waiting_child_kind = result.child_kind if result.child_kind != "coordinator" else None
                state.position = FlowPosition(phase="dispatch")
        elif isinstance(result, ContinuationHandoffGateResult):
            if result.outcome == "passed":
                state.position = FlowPosition(phase="prepare_coordinator")
            else:
                self._finish(result.outcome, result.reason or result.summary)
        elif isinstance(result, DispatchStepResult):
            if result.outcome != "dispatched" or len(result.child_flow_ids) != 1:
                self._finish("blocked", result.summary or "Continuation dispatch failed.")
            elif result.continuation == "wait_for_callback":
                state.waiting_dispatch_step_id = ctx.step.step_id
                state.position = FlowPosition(phase="waiting_child")
            elif result.continuation == "terminal_handoff":
                state.coordinator_flow_id = result.child_flow_ids[0]
                self.result = NativeRepoContinuationResult(outcome="handoff_dispatched", repo_key=inp.repo_key, run_objective=inp.run_spec.run_objective, summary=result.summary)
            else:
                self._finish("blocked", "Continuation dispatch returned an unsupported continuation mode.")
        super().on_step_terminal(ctx)
        if self.result is None and state.position.phase == "waiting_child":
            self.status = FlowStatus.WAITING

    def after_step_terminal_stable(self, ctx: StableStepTerminalContext) -> None:
        result = ctx.step.result
        if not isinstance(result, PrepareNativeRunMutationResult) or result.outcome != "prepared" or not result.checkpoint_id:
            return
        try:
            snapshot = ctx.app.snapshot_runtime.create_repo_stable_point_snapshot_with_id(Path(self.input.repo_root), snapshot_id=result.checkpoint_id,
                checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
                label=f"before native continuation mutation for {self.input.repo_key}", scope_ids=[self.scope_id])
            failure = None if snapshot.ok else (snapshot.issues[0].message if snapshot.issues else "Continuation checkpoint failed.")
        except Exception as exc:  # stable hook must persist infrastructure failure
            failure = str(exc)
        if failure is not None:
            def fail(flow):
                flow.error = BaseFlowError(error_type="native_continuation_stable_snapshot_failed", message=failure)
                flow.status = FlowStatus.FAILED
                flow.finished_at = utc_now_iso()
                flow.updated_at = utc_now_iso()
            ctx.ark.flow_service.store.update_flow_record(self.flow_id, fail)

    def _finish(self, outcome: Literal["blocked", "invalid_input"], reason: str | None) -> None:
        inp = self.input
        self.result = NativeRepoContinuationResult(outcome=outcome, repo_key=inp.repo_key, run_objective=inp.run_spec.run_objective, reason=reason, summary=reason or outcome)
        self.status = FlowStatus.COMPLETED
        self.finished_at = self.finished_at or utc_now_iso()
        self.updated_at = utc_now_iso()

    def _fail(self, error_type: str, message: str) -> None:
        self.error = BaseFlowError(error_type=error_type, message=message)
        self.status = FlowStatus.FAILED
        self.finished_at = self.finished_at or utc_now_iso()
        self.updated_at = utc_now_iso()


CONTINUATION_FLOW_TYPES = (NativeRepoContinuationFlow,)
CONTINUATION_STEP_TYPES = (PrepareContinuationDispatchStep,)

__all__ = ["CONTINUATION_FLOW_TYPES", "CONTINUATION_STEP_TYPES", "NativeRepoContinuationFlow", "NativeRepoContinuationInput", "NativeRepoContinuationParams", "NativeRepoContinuationResult", "NativeRepoContinuationState"]
