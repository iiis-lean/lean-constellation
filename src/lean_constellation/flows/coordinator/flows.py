"""Native repo coordinator Flow type definitions."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowReadContext, FlowStepContext, StableStepTerminalContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition, FlowStatus, utc_now_iso
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState, DispatchStep, DispatchStepResult, DispatchStepState
from pydantic import Field

from lean_constellation.domain.preparation import RepoDependencyRequirementStatus
from lean_constellation.domain.repo_run import RepoRunContext
from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.checkpoint_policy import record_checkpoint_skip_summary, repo_flow_boundary_checkpoints_enabled
from lean_constellation.flows.common.flow_requests import node_scope_id
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.coordinator.submissions import (
    CoordinatorContentTasksSubmission,
    CoordinatorRepoRequirementSubmission,
    CoordinatorResourceRequestSubmission,
)
from lean_constellation.flows.coordinator.steps import (
    CoordinatorContentBatchSnapshotStep,
    CoordinatorContentBatchSnapshotStepResult,
    CoordinatorRequirementResumeGateStep,
    CoordinatorRequirementResumeGateStepResult,
    CoordinatorStepResult,
    MarkCoordinatorRepoReadyStep,
    MarkCoordinatorRepoReadyStepResult,
    new_coordinator_step_id,
)
from lean_constellation.services.validation_snapshot.release_finalizer import PreparedRepoReleaseView


CoordinatorStartMode = Literal["native_preparation_handoff", "continuation_handoff", "requirement_resume", "admin_start", "admin_resume"]


class NativeRepoCoordinatorParams(LeanFlowParams):
    repo_key: str | None = None
    repo_root: str | None = None
    start_mode: CoordinatorStartMode = "admin_start"
    start_reason: str | None = None
    resumed_requirement_name: str | None = None
    admin_note: str | None = None
    run_context: RepoRunContext | None = None


class NativeRepoCoordinatorInput(LeanRenderableFlowInput):
    input_type: Literal["native_repo_coordinator"] = "native_repo_coordinator"
    repo_key: str | None = None
    repo_root: str | None = None
    start_mode: CoordinatorStartMode
    start_reason: str | None = None
    resumed_requirement_name: str | None = None
    admin_note: str | None = None
    run_context: RepoRunContext | None = None

    def agent_title(self) -> str:
        repo = self.repo_key or "current repo"
        return f"Coordinate native repo {repo}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "start_mode": self.start_mode,
            "start_reason": self.start_reason,
            "resumed_requirement_name": self.resumed_requirement_name,
            "admin_note": self.admin_note,
            "run_context": self.run_context.model_dump(mode="json") if self.run_context is not None else None,
        }


class NativeRepoCoordinatorState(BaseFlowState):
    state_type: Literal["native_repo_coordinator"] = "native_repo_coordinator"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="coordinator_agent"))
    waiting_dispatch_step_id: str | None = None
    waiting_requirement_name: str | None = None
    waiting_reason: str | None = None
    resuming_requirement_name: str | None = None
    resuming_provider_repo: str | None = None
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
    outcome: Literal["repo_ready", "candidate_prepared"]
    repo_key: str | None = None
    provider_ready_marked: bool = False
    satisfied_requirement_count: int = 0
    prepared_release: PreparedRepoReleaseView | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "provider_ready_marked": self.provider_ready_marked,
            "satisfied_requirement_count": self.satisfied_requirement_count,
            "release_id": self.prepared_release.release.release_id if self.prepared_release else None,
            "checkpoint_id": self.prepared_release.release.repo_checkpoint_id if self.prepared_release else None,
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
        if state.position.phase == "waiting_requirement":
            if not state.waiting_requirement_name:
                return False
            repo_workspace = getattr(ctx.app, "repo_workspace", None)
            repo_root = _coordinator_repo_root(_require_native_coordinator_input(self.input))
            if repo_workspace is None or repo_root is None:
                return False
            loaded = repo_workspace.requirement.get_requirement(
                repo_root,
                name=state.waiting_requirement_name,
            )
            if not loaded.ok or loaded.value is None:
                return False
            requirement = loaded.value.requirement
            return (
                requirement.status
                in {
                    RepoDependencyRequirementStatus.SATISFIED,
                    RepoDependencyRequirementStatus.HANDLED,
                }
                and repo_workspace.requirement.is_requirement_result_observed(requirement)
            )
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
            state.position = FlowPosition(phase="after_resource_request_terminal_snapshot")
        elif state.position.phase == "waiting_requirement":
            state.position = FlowPosition(phase="requirement_resume_gate")
        super().on_exit_waiting(ctx)

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_native_coordinator_state(self.state)
        input_model = _require_native_coordinator_input(self.input)
        if state.position.phase == "coordinator_agent":
            return ctx.create_step(_coordinator_agent_step(ctx, self, input_model, state, callback=False))
        if state.position.phase == "coordinator_callback":
            return ctx.create_step(_coordinator_agent_step(ctx, self, input_model, state, callback=True))
        if state.position.phase == "requirement_resume_gate":
            return ctx.create_step(
                CoordinatorRequirementResumeGateStep(
                    step_id=new_coordinator_step_id("requirement_resume_gate"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "coordinator_requirement_resume":
            return ctx.create_step(
                _coordinator_agent_step(
                    ctx,
                    self,
                    input_model,
                    state,
                    callback=False,
                    requirement_resume=True,
                )
            )
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
        if state.position.phase == "before_resource_request_dispatch_snapshot":
            return ctx.create_step(
                CoordinatorContentBatchSnapshotStep(
                    step_id=new_coordinator_step_id("before_resource_request_dispatch_snapshot"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    checkpoint_kind="before_resource_request_dispatch",
                )
            )
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
        if state.position.phase == "after_resource_request_terminal_snapshot":
            return ctx.create_step(
                CoordinatorContentBatchSnapshotStep(
                    step_id=new_coordinator_step_id("after_resource_request_terminal_snapshot"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    checkpoint_kind="after_resource_request_terminal",
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
            self._consume_coordinator_agent_result(ctx, state, result, ctx.step.submission, ctx.step.step_id)
        elif isinstance(result, CoordinatorRequirementResumeGateStepResult):
            self._consume_requirement_resume_gate_result(state, result)
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
        if ctx.step.step_type == "mark_coordinator_repo_ready_step":
            if not isinstance(result, MarkCoordinatorRepoReadyStepResult):
                return
            if result.outcome != "candidate_prepared" or result.prepared_release is None:
                return
            input_model = _require_native_coordinator_input(self.input)
            repo_root = _coordinator_repo_root(input_model)
            validation_snapshot = getattr(ctx.app, "validation_snapshot", None)
            if repo_root is None or validation_snapshot is None:
                _mark_flow_failed_from_stable_snapshot(
                    ctx, "repo_release_finalize_failed", [ValueError("Release finalizer service or repo_root missing.")]
                )
                return
            from lean_constellation.flows.coordinator.release_runtime import check_repo_release_runtime_closeout

            runtime_closeout = check_repo_release_runtime_closeout(
                validation_snapshot.runtime,
                repo_root,
                owner_flow_id=self.flow_id,
                phase="commit",
            )
            if not runtime_closeout.ok or runtime_closeout.value is None or not runtime_closeout.value.passed:
                issues = runtime_closeout.issues if not runtime_closeout.ok else runtime_closeout.value.issues
                _mark_flow_failed_from_stable_snapshot(ctx, "repo_release_runtime_not_closed", list(issues))
                return
            committed = validation_snapshot.commit_prepared_release(
                repo_root,
                prepared=result.prepared_release,
            )
            if not committed.ok:
                publication = ctx.app.repo_workspace.metadata.get_repo_publication(repo_root)
                if (
                    publication.ok
                    and publication.value is not None
                    and publication.value.publication.status.value == "stable"
                    and publication.value.publication.latest_release_id
                    == result.prepared_release.release.release_id
                ):
                    return
                _mark_flow_failed_from_stable_snapshot(
                    ctx, "repo_release_finalize_failed", list(committed.issues)
                )
            return
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
        if not repo_flow_boundary_checkpoints_enabled(ctx.app):
            record_checkpoint_skip_summary(
                ctx,
                "Coordinator requirement-waiting checkpoint skipped because repo flow-boundary checkpoints are disabled.",
            )
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
        ctx: FlowStepContext,
        state: NativeRepoCoordinatorState,
        result: object | None,
        submission: object | None,
        step_id: str,
    ) -> None:
        input_model = _require_native_coordinator_input(self.input)
        requirement_resume_turn = state.position.phase == "coordinator_requirement_resume"
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
        if requirement_resume_turn:
            state.resuming_requirement_name = None
            state.resuming_provider_repo = None
        if result.outcome == "content_tasks" and isinstance(submission, CoordinatorContentTasksSubmission) and result.content_tasks is not None:
            max_parallel = (
                input_model.run_context.run_spec.max_parallel_content_node_tasks
                if input_model.run_context is not None
                else 1
            )
            if len(result.content_tasks.node_paths) > max_parallel:
                self._fail_coordinator(
                    "content_task_batch_parallelism_exceeded",
                    f"Coordinator requested {len(result.content_tasks.node_paths)} content tasks; run maximum is {max_parallel}.",
                )
                return
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
            state.position = FlowPosition(phase="before_resource_request_dispatch_snapshot")
            return
        if (
            result.outcome == "repo_requirement"
            and result.repo_requirement is not None
            and isinstance(submission, CoordinatorRepoRequirementSubmission)
        ):
            repo_workspace = getattr(ctx.app, "repo_workspace", None)
            repo_root = _coordinator_repo_root(_require_native_coordinator_input(self.input))
            if repo_workspace is None or repo_root is None:
                self._fail_coordinator("coordinator_requirement_service_missing", "Repo workspace service or repo root missing.")
                return
            created = repo_workspace.create_requirement_with_interfaces(
                repo_root,
                name=submission.requirement_name,
                target_repo=submission.target_repo,
                source_description=submission.source_description,
                reason=submission.reason,
                interfaces=list(submission.interfaces),
                required_proof_availability=submission.required_proof_availability,
            )
            if not created.ok or created.value is None:
                message = created.issues[0].message if created.issues else "Failed to create repo requirement."
                self._fail_coordinator("coordinator_requirement_create_failed", message)
                return
            waiting = repo_workspace.mark_requirement_waiting_for_provider(
                repo_root,
                requirement_name=submission.requirement_name,
                provider_repo=submission.target_repo,
                reason=submission.reason or submission.summary,
            )
            if not waiting.ok or waiting.value is None:
                message = waiting.issues[0].message if waiting.issues else "Failed to mark repo requirement waiting."
                self._fail_coordinator("coordinator_requirement_waiting_failed", message)
                return
            state.waiting_requirement_name = result.repo_requirement.requirement_name
            state.waiting_reason = result.repo_requirement.reason or result.summary
            state.position = FlowPosition(phase="waiting_requirement")
            return
        if result.outcome == "repo_ready" and result.repo_ready is not None:
            state.repo_ready_summary = result.repo_ready.repo_summary
            state.position = FlowPosition(phase="mark_repo_ready")
            return
        self._fail_coordinator("coordinator_agent_submission_mismatch", "CoordinatorAgent result did not match its accepted submission.")

    def _consume_requirement_resume_gate_result(
        self,
        state: NativeRepoCoordinatorState,
        result: CoordinatorRequirementResumeGateStepResult,
    ) -> None:
        if result.outcome == "still_waiting":
            state.position = FlowPosition(phase="waiting_requirement")
            return
        if result.outcome != "resumed":
            self._fail_coordinator(
                result.issue_code or "coordinator_requirement_resume_invalid",
                result.summary or "Requirement resume gate rejected the current provider truth.",
            )
            return
        if not result.requirement_name or not result.provider_repo:
            self._fail_coordinator(
                "coordinator_requirement_resume_result_invalid",
                "Requirement resume gate completed without requirement/provider identity.",
            )
            return
        if not result.requirement_handled or not result.lake_dependency_attached:
            self._fail_coordinator(
                "coordinator_requirement_resume_postcondition_failed",
                "Requirement resume gate did not satisfy handled and Lake dependency postconditions.",
            )
            return
        state.resuming_requirement_name = result.requirement_name
        state.resuming_provider_repo = result.provider_repo
        state.waiting_requirement_name = None
        state.waiting_reason = None
        state.position = FlowPosition(phase="coordinator_requirement_resume")

    def _consume_content_snapshot_result(
        self,
        state: NativeRepoCoordinatorState,
        result: CoordinatorContentBatchSnapshotStepResult,
    ) -> None:
        if result.outcome not in {"snapshot_created", "skipped"}:
            self._fail_coordinator(result.error_code or "coordinator_snapshot_failed", result.error_message or result.summary or "Coordinator snapshot failed.")
            return
        if result.checkpoint_kind == "before_content_task_dispatch":
            state.position = FlowPosition(phase="dispatch_content_tasks")
            return
        if result.checkpoint_kind == "after_content_task_batch_terminal":
            state.position = FlowPosition(phase="coordinator_callback")
            return
        if result.checkpoint_kind == "before_resource_request_dispatch":
            state.position = FlowPosition(phase="dispatch_resource_request")
            return
        if result.checkpoint_kind == "after_resource_request_terminal":
            state.position = FlowPosition(phase="coordinator_callback")
            return
        self._fail_coordinator("coordinator_snapshot_kind_unsupported", f"Unsupported checkpoint kind: {result.checkpoint_kind}.")

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
        if result.outcome in {"blocked", "candidate_blocked"}:
            state.position = FlowPosition(phase="coordinator_callback")
            state.repo_ready_summary = None
            return
        if result.outcome not in {"ready_marked", "candidate_prepared"}:
            self._fail_coordinator(result.error_code or "repo_ready_mark_failed", result.error_message or result.summary or "Repo ready marker failed.")
            return
        state.position = FlowPosition(phase="completed")
        self.result = NativeRepoCoordinatorResult(
            outcome="candidate_prepared" if result.outcome == "candidate_prepared" else "repo_ready",
            repo_key=input_model.repo_key,
            provider_ready_marked=result.provider_ready_marked,
            satisfied_requirement_count=result.satisfied_requirement_count,
            prepared_release=result.prepared_release,
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
    scope_ids = [ctx.flow.scope_id]
    for path in effective_node_paths:
        node = ctx.app.node.node_tree.node_store.resolve_active_node(repo_root, path=path)
        if not node.ok or node.value is None:
            _mark_flow_failed_from_stable_snapshot(ctx, failure_type, node.issues)
            return
        scope_ids.append(node_scope_id(repo_key, node.value.node_id))
    snapshot = ctx.app.snapshot_runtime.create_repo_stable_point_snapshot(
        repo_root,
        checkpoint_kind=checkpoint_kind,
        label=label,
        node_paths=effective_node_paths,
        scope_ids=scope_ids,
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
    ctx: FlowContext,
    flow: NativeRepoCoordinatorFlow,
    input_model: NativeRepoCoordinatorInput,
    state: NativeRepoCoordinatorState,
    *,
    callback: bool,
    requirement_resume: bool = False,
):
    from lean_constellation.flows.common.agent_steps import CoordinatorAgentStep

    repo_ready_rejection_prompt = _repo_ready_rejection_callback_prompt(ctx, flow) if callback else None
    return CoordinatorAgentStep(
        step_id=new_coordinator_step_id(
            "coordinator_callback"
            if callback
            else "coordinator_requirement_resume"
            if requirement_resume
            else "coordinator"
        ),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=AgentStepState(
            agent_role="coordinator",
            agent_type="CoordinatorAgent",
            home_id="CoordinatorAgent",
            create_agent_if_missing=not requirement_resume,
            bind_created_agent_to="flow",
            variables={
                "repo_key": input_model.repo_key,
                "start_mode": input_model.start_mode,
                "coordinator_turn_index": state.coordinator_turn_index,
                "waiting_requirement_name": state.waiting_requirement_name,
                "resuming_requirement_name": state.resuming_requirement_name,
                "resuming_provider_repo": state.resuming_provider_repo,
            },
            prompt_mode="callback" if callback and repo_ready_rejection_prompt is None else "initial",
            prompt_override=(
                repo_ready_rejection_prompt
                if repo_ready_rejection_prompt is not None
                else None
                if callback
                else _coordinator_requirement_resume_prompt(state)
                if requirement_resume
                else _coordinator_initial_prompt(input_model)
            ),
            callback_dispatch_step_id=(
                state.waiting_dispatch_step_id
                if callback and repo_ready_rejection_prompt is None
                else None
            ),
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
    work_mode = (
        input_model.run_context.run_spec.work_mode.value
        if input_model.run_context is not None
        else "proved_full_graph"
    )
    mode_skill = f"coordinator-{work_mode.replace('_', '-').lower()}-mode"
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
        f"Required Skill re-entry for this turn: read and apply {mode_skill} from the current Home. "
        "After choosing a branch, read its branch Skill before mutation or dispatch."
    )
    parts.append(
        "Observe repo truth through tools and submit exactly one coordination move: content node tasks, "
        "resource request, repo requirement, or repo ready."
    )
    return "\n".join(parts)


def _repo_ready_rejection_callback_prompt(ctx: FlowContext, flow: NativeRepoCoordinatorFlow) -> str | None:
    if not flow.step_ids:
        return None
    latest = ctx.ark.step_service.store.get_step(flow.step_ids[-1])
    if latest.step_type != "mark_coordinator_repo_ready_step":
        return None
    result = latest.result
    if not isinstance(result, MarkCoordinatorRepoReadyStepResult):
        raise TypeError("repo-ready callback predecessor has no MarkCoordinatorRepoReadyStepResult")
    if result.outcome not in {"blocked", "candidate_blocked"}:
        raise TypeError(f"repo-ready callback predecessor has non-rejection outcome: {result.outcome}")
    work_mode = (
        flow.input.run_context.run_spec.work_mode.value
        if isinstance(flow.input, NativeRepoCoordinatorInput) and flow.input.run_context is not None
        else "proved_full_graph"
    )
    mode_skill = f"coordinator-{work_mode.replace('_', '-').lower()}-mode"
    issue = result.error_code or "repo_ready_gate_rejected"
    detail = result.error_message or result.summary or "The deterministic repo-ready gate rejected the candidate."
    return "\n".join(
        [
            "The deterministic repo-ready lifecycle step rejected the candidate; this is an internal wake, not a child callback.",
            f"Gate issue: {issue}.",
            f"Gate summary: {detail}",
            "Required Skill order for this turn: read and apply coordinator-repo-ready-lifecycle first, "
            f"then {mode_skill}.",
            "Repair only Coordinator-owned truth identified by the gate, re-read current runtime/repo truth, "
            "and submit exactly one normal coordination move. Do not reuse a stale child-dispatch result.",
        ]
    )


def _coordinator_requirement_resume_prompt(state: NativeRepoCoordinatorState) -> str:
    requirement_name = state.resuming_requirement_name or "the resumed requirement"
    provider_repo = state.resuming_provider_repo or "the provider repo"
    return "\n".join(
        [
            f"Resume coordination after requirement {requirement_name} was satisfied by provider {provider_repo}.",
            "The deterministic resume gate verified the provider contract, automatically attached the provider as a Lake dependency, and marked the requirement handled.",
            f"Use get_current_repo_requirement for {requirement_name}, then re-read the current Lake dependencies, provider public API, and node tree truth.",
            "Close out the effect of this requirement result, return to the normal next-action decision loop, and submit exactly one normal coordination move: content node tasks, resource request, repo requirement, or repo ready.",
        ]
    )


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
