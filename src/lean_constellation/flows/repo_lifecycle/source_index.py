"""Reusable scoped SourceIndex builder/reviewer Flow."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext, StableStepTerminalContext
from agent_runtime_kit.flow.models import (
    BaseFlowError,
    BaseFlowInput,
    BaseFlowResult,
    BaseFlowState,
    FlowPosition,
    FlowStatus,
    utc_now_iso,
)
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState
from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.repo import ProofAvailability, RepoConfig, RepoWorkMode
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.repo_lifecycle.source_index_steps import (
    OpenSourceIndexUpdateStep,
    OpenSourceIndexUpdateStepResult,
    PrepareSourceIndexBaselineStep,
    PrepareSourceIndexBaselineStepResult,
    ResolveSourceScopeStep,
    ResolveSourceScopeStepResult,
    ValidateAndCommitSourceIndexUpdateStep,
    ValidateAndCommitSourceIndexUpdateStepResult,
    ValidateSourceIndexRunStep,
    ValidateSourceIndexRunStepResult,
)
from lean_constellation.flows.repo_lifecycle.steps import new_repo_lifecycle_step_id
from lean_constellation.flows.repo_lifecycle.submissions import (
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)


class SourceIndexBuildParams(LeanFlowParams):
    repo_key: str
    repo_root: str
    run_objective: str
    target_proof_availability: ProofAvailability
    work_mode: RepoWorkMode
    source_scope: SourceScope
    index_policy: Literal["auto", "update", "reuse"] = "auto"
    start_reason: Literal["initial", "continuation", "admin_preprocess"] = "initial"
    max_review_rounds: int = Field(default=3, ge=1)
    pre_update_checkpoint_id: str | None = None

    @field_validator("repo_key", "repo_root", "run_objective")
    @classmethod
    def _require_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized

    @field_validator("pre_update_checkpoint_id")
    @classmethod
    def _normalize_checkpoint_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("pre_update_checkpoint_id must be non-empty when provided")
        return normalized

    @model_validator(mode="after")
    def _validate_run_target(self) -> "SourceIndexBuildParams":
        RepoConfig(
            target_proof_availability=self.target_proof_availability,
            work_mode=self.work_mode,
        )
        return self


class SourceIndexBuildInput(LeanRenderableFlowInput):
    input_type: Literal["source_index_build"] = "source_index_build"
    repo_key: str
    repo_root: str
    run_objective: str
    target_proof_availability: ProofAvailability
    work_mode: RepoWorkMode
    source_scope: SourceScope
    index_policy: Literal["auto", "update", "reuse"]
    start_reason: Literal["initial", "continuation", "admin_preprocess"]
    max_review_rounds: int
    pre_update_checkpoint_id: str | None = None

    def agent_title(self) -> str:
        return f"Build scoped SourceIndex for {self.repo_key}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "run_objective": self.run_objective,
            "target_proof_availability": self.target_proof_availability.value,
            "work_mode": self.work_mode.value,
            "source_scope": self.source_scope.model_dump(mode="json"),
            "index_policy": self.index_policy,
            "start_reason": self.start_reason,
        }


class SourceIndexBuildState(BaseFlowState):
    state_type: Literal["source_index_build"] = "source_index_build"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="validate_input"))
    active_update_id: str
    pre_update_checkpoint_id: str
    resolved_file_paths: list[str] = Field(default_factory=list)
    readable_file_paths: list[str] = Field(default_factory=list)
    artifact_file_paths: list[str] = Field(default_factory=list)
    manifest_digest: str | None = None
    baseline_digest: str | None = None
    new_file_paths: list[str] = Field(default_factory=list)
    already_committed_file_paths: list[str] = Field(default_factory=list)
    uncommitted_file_paths: list[str] = Field(default_factory=list)
    review_round: int = 0
    latest_builder_summary: str | None = None
    latest_reviewer_feedback: str | None = None
    review_approved: bool = False


class SourceIndexBuildResult(LeanRenderableFlowResult):
    result_type: Literal["source_index_build"] = "source_index_build"
    outcome: Literal["committed", "no_op", "blocked", "invalid_input"]
    repo_key: str
    resolved_file_paths: list[str] = Field(default_factory=list)
    newly_committed_file_paths: list[str] = Field(default_factory=list)
    appended_block_ids: list[str] = Field(default_factory=list)
    appended_link_ids: list[str] = Field(default_factory=list)
    appended_ref_ids: list[str] = Field(default_factory=list)
    coverage_summary: str | None = None
    reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "resolved_file_paths": self.resolved_file_paths,
            "newly_committed_file_paths": self.newly_committed_file_paths,
            "appended_block_ids": self.appended_block_ids,
            "appended_link_ids": self.appended_link_ids,
            "reason": self.reason,
        }


class SourceIndexBuildFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "source_index_build"
    Params: ClassVar[type[LeanFlowParams]] = SourceIndexBuildParams
    Input: ClassVar[type[BaseFlowInput]] = SourceIndexBuildInput
    State: ClassVar[type[BaseFlowState]] = SourceIndexBuildState
    Result: ClassVar[type[BaseFlowResult]] = SourceIndexBuildResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"source_index_build": SourceIndexBuildResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "SourceIndexBuildFlow":
        params = SourceIndexBuildParams.model_validate(ctx.params)
        suffix = ctx.flow_id.removeprefix("f_")
        checkpoint_id = params.pre_update_checkpoint_id or f"source_index_cp_{suffix}"
        return cls._build(
            ctx,
            input_model=SourceIndexBuildInput(
                summary=f"Build the selected SourceIndex scope for {params.repo_key}.",
                **params.model_dump(),
            ),
            state=SourceIndexBuildState(
                active_update_id=f"source_index_update_{suffix}",
                pre_update_checkpoint_id=checkpoint_id,
            ),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_state(self.state)
        input_model = _require_input(self.input)
        if state.position.phase == "validate_input":
            return ctx.create_step(_step(ValidateSourceIndexRunStep, self, "validate_source_index_run"))
        if state.position.phase == "resolve_scope":
            return ctx.create_step(_step(ResolveSourceScopeStep, self, "resolve_source_scope"))
        if state.position.phase == "prepare_baseline":
            return ctx.create_step(_step(PrepareSourceIndexBaselineStep, self, "prepare_source_index_baseline"))
        if state.position.phase == "open_update":
            return ctx.create_step(_step(OpenSourceIndexUpdateStep, self, "open_source_index_update"))
        if state.position.phase == "builder":
            from lean_constellation.flows.common.agent_steps import SourceIndexBuilderAgentStep

            return ctx.create_step(
                SourceIndexBuilderAgentStep(
                    step_id=new_repo_lifecycle_step_id("scoped_source_index_builder"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="source_index_builder",
                        agent_type="SourceIndexBuilderAgent",
                        home_id="SourceIndexBuilderAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="flow",
                        variables=_agent_context(input_model, state),
                        prompt_override=_builder_prompt(input_model, state),
                        env_overrides=_agent_env("SourceIndexBuilderAgent", "source_index_builder", "source_index_builder_submit"),
                        workdir_override=input_model.repo_root,
                    ),
                )
            )
        if state.position.phase == "reviewer":
            from lean_constellation.flows.common.agent_steps import SourceIndexReviewerAgentStep

            return ctx.create_step(
                SourceIndexReviewerAgentStep(
                    step_id=new_repo_lifecycle_step_id("scoped_source_index_reviewer"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="source_index_reviewer",
                        agent_type="SourceIndexReviewerAgent",
                        home_id="SourceIndexReviewerAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="flow",
                        variables=_agent_context(input_model, state),
                        prompt_override=_reviewer_prompt(input_model, state),
                        env_overrides=_agent_env("SourceIndexReviewerAgent", "source_index_reviewer", "source_index_reviewer_submit"),
                        workdir_override=input_model.repo_root,
                    ),
                )
            )
        if state.position.phase == "validate_commit":
            return ctx.create_step(
                _step(ValidateAndCommitSourceIndexUpdateStep, self, "validate_commit_source_index_update")
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_state(self.state)
        input_model = _require_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="source_index_build_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return
        result = ctx.step.result
        if isinstance(result, ValidateSourceIndexRunStepResult):
            if result.outcome == "passed":
                state.position = FlowPosition(phase="resolve_scope")
            else:
                self._finish_failure(input_model, state, result.outcome, _error_reason(result))
        elif isinstance(result, ResolveSourceScopeStepResult):
            if result.outcome == "resolved":
                state.resolved_file_paths = list(result.resolved_file_paths)
                state.readable_file_paths = list(result.readable_file_paths)
                state.artifact_file_paths = list(result.artifact_file_paths)
                state.manifest_digest = result.manifest_digest
                state.position = FlowPosition(phase="prepare_baseline")
            else:
                self._finish_failure(input_model, state, result.outcome, _error_reason(result))
        elif isinstance(result, PrepareSourceIndexBaselineStepResult):
            if result.outcome in {"prepared", "reused"}:
                state.baseline_digest = result.baseline_digest
                state.position = FlowPosition(phase="open_update")
            else:
                self._finish_failure(input_model, state, "blocked", _error_reason(result))
        elif isinstance(result, OpenSourceIndexUpdateStepResult):
            self._consume_open(input_model, state, result)
        elif ctx.step.step_type == "source_index_builder_agent_step":
            self._consume_builder(input_model, state, result, ctx.step.submission)
        elif ctx.step.step_type == "source_index_reviewer_agent_step":
            self._consume_reviewer(input_model, state, result, ctx.step.submission)
        elif isinstance(result, ValidateAndCommitSourceIndexUpdateStepResult):
            self._consume_commit(input_model, state, result)
        super().on_step_terminal(ctx)

    def after_step_terminal_stable(self, ctx: StableStepTerminalContext) -> None:
        result = ctx.step.result
        if not isinstance(result, PrepareSourceIndexBaselineStepResult) or not result.requires_materialization:
            return
        state = _require_state(self.state)
        input_model = _require_input(self.input)
        flow_service = ctx.ark.flow_service
        if flow_service is None:
            return
        try:
            materialized = ctx.app.source_index_checkpoint.materialize_source_index_baseline_checkpoint(
                Path(input_model.repo_root),
                checkpoint_id=state.pre_update_checkpoint_id,
                scope_ids=[self.scope_id],
                label=f"before SourceIndex update for {input_model.repo_key}",
            )
        except Exception as exc:  # noqa: BLE001 - stable hooks must persist infrastructure failure.
            _mark_baseline_checkpoint_failed(
                flow_service,
                self.flow_id,
                f"SourceIndex baseline checkpoint raised {type(exc).__name__}: {exc}",
            )
            return
        if not materialized.ok or materialized.value is None:
            reason = "; ".join(issue.message for issue in materialized.issues) or "SourceIndex baseline checkpoint failed."
            _mark_baseline_checkpoint_failed(flow_service, self.flow_id, reason)
            return

        def record(flow) -> None:  # noqa: ANN001
            flow_state = _require_state(flow.state)
            flow_state.baseline_digest = materialized.value.baseline_digest

        flow_service.store.update_flow_record(self.flow_id, record)

    def _consume_open(
        self,
        input_model: SourceIndexBuildInput,
        state: SourceIndexBuildState,
        result: OpenSourceIndexUpdateStepResult,
    ) -> None:
        if result.outcome in {"opened", "already_open"}:
            state.baseline_digest = result.baseline_digest
            state.new_file_paths = list(result.new_file_paths)
            state.already_committed_file_paths = list(result.already_committed_file_paths)
            state.uncommitted_file_paths = list(result.uncommitted_file_paths)
            state.review_round = max(state.review_round, 1)
            state.position = FlowPosition(phase="builder", round_index=state.review_round)
            return
        if result.outcome == "no_op":
            state.position = FlowPosition(phase="completed")
            self.result = SourceIndexBuildResult(
                outcome="no_op",
                repo_key=input_model.repo_key,
                resolved_file_paths=list(state.resolved_file_paths),
                summary=result.summary,
            )
            return
        self._finish_failure(input_model, state, result.outcome, _error_reason(result))

    def _consume_builder(self, input_model, state, result, submission) -> None:  # noqa: ANN001
        if isinstance(result, AgentStepIncompleteResult) or not isinstance(submission, SourceIndexBuilderRoundSubmission):
            self._finish_failure(input_model, state, "blocked", "SourceIndexBuilderAgent did not submit a builder round.")
            return
        state.latest_builder_summary = submission.summary
        state.position = FlowPosition(phase="reviewer", round_index=state.review_round)

    def _consume_reviewer(self, input_model, state, result, submission) -> None:  # noqa: ANN001
        if isinstance(result, AgentStepIncompleteResult) or not isinstance(submission, SourceIndexReviewerRoundSubmission):
            self._finish_failure(input_model, state, "blocked", "SourceIndexReviewerAgent did not submit a review round.")
            return
        if submission.approved:
            state.review_approved = True
            state.latest_reviewer_feedback = None
            state.position = FlowPosition(phase="validate_commit", round_index=state.review_round)
            return
        state.review_approved = False
        state.latest_reviewer_feedback = submission.feedback
        if state.review_round < input_model.max_review_rounds:
            state.review_round += 1
            state.position = FlowPosition(phase="builder", round_index=state.review_round)
            return
        self._finish_failure(
            input_model,
            state,
            "blocked",
            submission.feedback or "SourceIndex review rounds were exhausted.",
        )

    def _consume_commit(self, input_model, state, result) -> None:  # noqa: ANN001
        state.position = FlowPosition(phase="completed")
        if result.outcome == "committed":
            self.result = SourceIndexBuildResult(
                outcome="committed",
                repo_key=input_model.repo_key,
                resolved_file_paths=list(state.resolved_file_paths),
                newly_committed_file_paths=result.newly_committed_file_paths,
                appended_block_ids=result.appended_block_ids,
                appended_link_ids=result.appended_link_ids,
                appended_ref_ids=result.appended_ref_ids,
                coverage_summary=result.coverage_summary,
                summary=result.summary,
            )
            return
        self._finish_failure(input_model, state, "blocked", _error_reason(result))

    def _finish_failure(
        self,
        input_model: SourceIndexBuildInput,
        state: SourceIndexBuildState,
        outcome: Literal["blocked", "invalid_input"],
        reason: str,
    ) -> None:
        state.position = FlowPosition(phase="completed")
        self.result = SourceIndexBuildResult(
            outcome=outcome,
            repo_key=input_model.repo_key,
            resolved_file_paths=list(state.resolved_file_paths),
            reason=reason,
            summary=reason,
        )


def _step(step_cls, flow: SourceIndexBuildFlow, label: str):  # noqa: ANN001
    return step_cls(
        step_id=new_repo_lifecycle_step_id(label),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
    )


def _mark_baseline_checkpoint_failed(flow_service, flow_id: str, reason: str) -> None:  # noqa: ANN001
    def fail(flow) -> None:  # noqa: ANN001
        flow.error = BaseFlowError(error_type="source_index_baseline_checkpoint_failed", message=reason)
        flow.status = FlowStatus.FAILED
        flow.finished_at = utc_now_iso()
        flow.updated_at = flow.finished_at

    flow_service.store.update_flow_record(flow_id, fail)


def _require_state(value: BaseFlowState) -> SourceIndexBuildState:
    if not isinstance(value, SourceIndexBuildState):
        raise TypeError("SourceIndexBuildFlow has invalid state model.")
    return value


def _require_input(value: BaseFlowInput | None) -> SourceIndexBuildInput:
    if not isinstance(value, SourceIndexBuildInput):
        raise TypeError("SourceIndexBuildFlow has invalid input model.")
    return value


def _error_reason(result) -> str:  # noqa: ANN001
    error = getattr(result, "error", None)
    return error.message if error is not None else result.summary or result.outcome


def _agent_context(input_model: SourceIndexBuildInput, state: SourceIndexBuildState) -> dict[str, object]:
    return {
        "repo_key": input_model.repo_key,
        "run_objective": input_model.run_objective,
        "target_proof_availability": input_model.target_proof_availability.value,
        "work_mode": input_model.work_mode.value,
        "start_reason": input_model.start_reason,
        "round_index": state.review_round,
        "active_file_scope": list(state.resolved_file_paths),
        "new_file_paths": list(state.new_file_paths),
        "already_committed_file_paths": list(state.already_committed_file_paths),
        "forbidden_boundaries": [
            "Do not modify committed SourceIndex blocks, refs, links, or file payloads.",
            "New source evidence must stay inside active_file_scope.",
            "Do not choose or expose a SourceIndex update id; the Flow owns it.",
        ],
    }


def _builder_prompt(input_model: SourceIndexBuildInput, state: SourceIndexBuildState) -> str:
    lines = [
        f"Build SourceIndex round {state.review_round} for {input_model.repo_key}.",
        f"Run objective: {input_model.run_objective}",
        f"Active files: {', '.join(state.resolved_file_paths) or '(none)'}",
        "Append index material only for the active files. Existing committed semantic content is immutable.",
        "Use the Flow-provided SourceIndex tools; the system injects update ownership.",
        "Submit the builder round only after the scoped draft is ready for review.",
    ]
    if state.latest_reviewer_feedback:
        lines.extend(["", "Previous reviewer feedback:", state.latest_reviewer_feedback])
    return "\n".join(lines)


def _reviewer_prompt(input_model: SourceIndexBuildInput, state: SourceIndexBuildState) -> str:
    lines = [
        f"Review SourceIndex round {state.review_round} for {input_model.repo_key}.",
        f"Run objective: {input_model.run_objective}",
        f"Active files: {', '.join(state.resolved_file_paths) or '(none)'}",
        "Reject edits to committed baseline semantics, evidence outside the active scope, incomplete blocks, or source drift.",
        "Approve only the scoped delta; final deterministic validation remains authoritative.",
    ]
    if state.latest_builder_summary:
        lines.extend(["", "Builder summary:", state.latest_builder_summary])
    return "\n".join(lines)


def _agent_env(agent_type: str, app_view: str, submit_view: str) -> dict[str, str]:
    return {
        "LEAN_CONSTELLATION_AGENT_TYPE": agent_type,
        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": app_view,
        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": submit_view,
    }


SOURCE_INDEX_BUILD_FLOW_TYPES: tuple[type[LeanBusinessFlow], ...] = (SourceIndexBuildFlow,)


__all__ = [
    "SOURCE_INDEX_BUILD_FLOW_TYPES",
    "SourceIndexBuildFlow",
    "SourceIndexBuildInput",
    "SourceIndexBuildParams",
    "SourceIndexBuildResult",
    "SourceIndexBuildState",
]
