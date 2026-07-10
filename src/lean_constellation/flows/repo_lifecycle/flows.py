"""Repo lifecycle Flow type definitions."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext, StableStepTerminalContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition, FlowStatus, utc_now_iso
from agent_runtime_kit.flow.standard_steps import (
    AgentStepIncompleteResult,
    AgentStepState,
    DispatchStep,
    DispatchStepResult,
    DispatchStepState,
)
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.repo_lifecycle.steps import (
    ApplyRepoFormatChoiceStep,
    ApplyRepoFormatChoiceStepResult,
    BootstrapInputValidationStepResult,
    AdapterInputValidationStepResult,
    CommitSourceIndexStep,
    CommitSourceIndexStepResult,
    CreateDraftSourceIndexStep,
    CreateDraftSourceIndexStepResult,
    EnsureAdapterMainCatalogStep,
    EnsureAdapterMainCatalogStepResult,
    ExistingSourceCorpusScanStep,
    ExistingSourceCorpusScanStepResult,
    FinalizeAdapterReadyStep,
    FinalizeAdapterReadyStepResult,
    HandoffGateStep,
    HandoffGateStepResult,
    MarkAdapterProviderReadyStep,
    MarkAdapterProviderReadyStepResult,
    PrepareCoordinatorDispatchStep,
    PrepareCoordinatorDispatchStepResult,
    RootInterfaceDirectReadyStep,
    RootInterfaceDirectReadyStepResult,
    ValidateBootstrapInputStep,
    ValidateAdapterPreparationInputStep,
    ValidateAndInitializeNativePreparationStep,
    ValidateAndInitializeNativePreparationStepResult,
    new_repo_lifecycle_step_id,
)
from lean_constellation.flows.repo_lifecycle.submissions import (
    NativeCoordinatorHandoffSubmission,
    AdapterCatalogBlockedSubmission,
    AdapterCatalogReadySubmission,
    RepoFormatAdapterChoiceSubmission,
    RepoFormatNativeChoiceSubmission,
    RootInterfacePrepareReadySubmission,
    SourceCorpusBlockedSubmission,
    SourceCorpusPreparedSubmission,
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)


class RequirementGroupRepoBootstrapParams(LeanFlowParams):
    target_repo: str
    repo_root: str
    workspace_root: str
    requirement_refs: list[str] = Field(default_factory=list)
    admin_notes: str | None = None


class RequirementGroupRepoBootstrapInput(LeanRenderableFlowInput):
    input_type: Literal["requirement_group_repo_bootstrap"] = "requirement_group_repo_bootstrap"
    target_repo: str
    repo_root: str
    workspace_root: str
    requirement_refs: list[str] = Field(default_factory=list)
    admin_notes: str | None = None

    def agent_title(self) -> str:
        return f"Bootstrap provider repo {self.target_repo}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "requirement_count": len(self.requirement_refs),
            "admin_notes": self.admin_notes,
        }


class RequirementGroupRepoBootstrapState(BaseFlowState):
    state_type: Literal["requirement_group_repo_bootstrap"] = "requirement_group_repo_bootstrap"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="validate_input"))
    preparation_input_validated: bool = False
    selected_repo_format: Literal["adapter", "native"] | None = None
    adapter_choice_summary: str | None = None
    native_choice_summary: str | None = None
    applied_repo_format: bool = False


class RequirementGroupRepoBootstrapResult(LeanRenderableFlowResult):
    result_type: Literal["requirement_group_repo_bootstrap"] = "requirement_group_repo_bootstrap"
    outcome: Literal["adapter_bootstrap_ready", "native_bootstrap_ready", "needs_admin_repair"]
    repo_key: str
    next_preparation_flow: Literal["adapter_repo_preparation", "native_repo_preparation"] | None = None
    reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "next_preparation_flow": self.next_preparation_flow,
            "reason": self.reason,
        }


class RequirementGroupRepoBootstrapFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "requirement_group_repo_bootstrap"
    Params: ClassVar[type[LeanFlowParams]] = RequirementGroupRepoBootstrapParams
    Input: ClassVar[type[BaseFlowInput]] = RequirementGroupRepoBootstrapInput
    State: ClassVar[type[BaseFlowState]] = RequirementGroupRepoBootstrapState
    Result: ClassVar[type[BaseFlowResult]] = RequirementGroupRepoBootstrapResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {
        "requirement_group_repo_bootstrap": RequirementGroupRepoBootstrapResult,
    }

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "RequirementGroupRepoBootstrapFlow":
        params = RequirementGroupRepoBootstrapParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=RequirementGroupRepoBootstrapInput(
                summary=f"Bootstrap provider repo {params.target_repo}.",
                **params.model_dump(),
            ),
            state=RequirementGroupRepoBootstrapState(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_requirement_bootstrap_state(self.state)
        if state.position.phase == "validate_input":
            return ctx.create_step(
                ValidateBootstrapInputStep(
                    step_id=new_repo_lifecycle_step_id("validate_bootstrap_input"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "format_discovery":
            from lean_constellation.flows.common.agent_steps import RepoFormatDiscoveryAgentStep

            input_model = _require_requirement_bootstrap_input(self.input)
            return ctx.create_step(
                RepoFormatDiscoveryAgentStep(
                    step_id=new_repo_lifecycle_step_id("repo_format_discovery"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="repo_format_discovery",
                        agent_type="RepoFormatDiscoveryAgent",
                        home_id="RepoFormatDiscoveryAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="step",
                        variables={
                            "target_repo": input_model.target_repo,
                            "requirement_count": len(input_model.requirement_refs),
                        },
                        prompt_override=_repo_format_discovery_prompt(input_model),
                        env_overrides={
                            "LEAN_CONSTELLATION_AGENT_TYPE": "RepoFormatDiscoveryAgent",
                            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "repo_format_discovery",
                            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "repo_format_discovery_submit",
                        },
                        workdir_override=input_model.repo_root,
                    ),
                )
            )
        if state.position.phase == "apply_format_choice":
            return ctx.create_step(
                ApplyRepoFormatChoiceStep(
                    step_id=new_repo_lifecycle_step_id("apply_repo_format_choice"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_requirement_bootstrap_state(self.state)
        input_model = _require_requirement_bootstrap_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="repo_bootstrap_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return

        result = ctx.step.result
        if isinstance(result, BootstrapInputValidationStepResult):
            self._consume_validate_result(state, input_model, result)
        elif ctx.step.step_type == "repo_format_discovery_agent_step":
            self._consume_repo_format_result(state, input_model, result, ctx.step.submission)
        elif isinstance(result, ApplyRepoFormatChoiceStepResult):
            self._consume_apply_result(state, input_model, result)
        super().on_step_terminal(ctx)

    def after_step_terminal_stable(self, ctx: StableStepTerminalContext) -> None:
        if ctx.step.step_type != "apply_repo_format_choice_step":
            return
        result = ctx.step.result
        if not isinstance(result, ApplyRepoFormatChoiceStepResult) or result.outcome not in {"adapter_initialized", "native_initialized"}:
            return
        flow_result = self.result
        if not isinstance(flow_result, RequirementGroupRepoBootstrapResult) or flow_result.outcome not in {
            "adapter_bootstrap_ready",
            "native_bootstrap_ready",
        }:
            return
        input_model = _require_requirement_bootstrap_input(self.input)
        _record_stable_repo_snapshot(
            ctx,
            input_model.repo_root,
            checkpoint_kind="requirement_bootstrap_terminal",
            label=f"requirement bootstrap terminal for {input_model.target_repo}",
            failure_type="requirement_bootstrap_stable_snapshot_failed",
        )

    def _consume_validate_result(
        self,
        state: RequirementGroupRepoBootstrapState,
        input_model: RequirementGroupRepoBootstrapInput,
        result: BootstrapInputValidationStepResult,
    ) -> None:
        if result.outcome == "passed":
            state.preparation_input_validated = True
            state.position = FlowPosition(phase="format_discovery")
            return
        state.position = FlowPosition(phase="completed")
        self.result = RequirementGroupRepoBootstrapResult(
            outcome="needs_admin_repair",
            repo_key=input_model.target_repo,
            reason=result.error.message if result.error else result.summary,
            summary=result.summary,
        )

    def _consume_repo_format_result(
        self,
        state: RequirementGroupRepoBootstrapState,
        input_model: RequirementGroupRepoBootstrapInput,
        result: BaseFlowResult | object | None,
        submission: object | None,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or submission is None:
            state.position = FlowPosition(phase="completed")
            self.result = RequirementGroupRepoBootstrapResult(
                outcome="needs_admin_repair",
                repo_key=input_model.target_repo,
                reason="RepoFormatDiscoveryAgent did not produce a successful route submission.",
                summary="Repo format discovery did not submit a route.",
            )
            return
        if isinstance(submission, RepoFormatAdapterChoiceSubmission):
            state.selected_repo_format = "adapter"
            state.adapter_choice_summary = submission.summary
            state.position = FlowPosition(phase="apply_format_choice")
            return
        if isinstance(submission, RepoFormatNativeChoiceSubmission):
            state.selected_repo_format = "native"
            state.native_choice_summary = submission.summary
            state.position = FlowPosition(phase="apply_format_choice")
            return
        state.position = FlowPosition(phase="completed")
        self.result = RequirementGroupRepoBootstrapResult(
            outcome="needs_admin_repair",
            repo_key=input_model.target_repo,
            reason=f"Unsupported repo format submission: {getattr(submission, 'submission_type', None)}",
            summary="Repo format discovery submitted an unsupported result.",
        )

    def _consume_apply_result(
        self,
        state: RequirementGroupRepoBootstrapState,
        input_model: RequirementGroupRepoBootstrapInput,
        result: ApplyRepoFormatChoiceStepResult,
    ) -> None:
        state.position = FlowPosition(phase="completed")
        if result.outcome == "adapter_initialized":
            state.applied_repo_format = True
            state.selected_repo_format = "adapter"
            self.result = RequirementGroupRepoBootstrapResult(
                outcome="adapter_bootstrap_ready",
                repo_key=input_model.target_repo,
                next_preparation_flow="adapter_repo_preparation",
                summary=result.summary,
            )
            return
        if result.outcome == "native_initialized":
            state.applied_repo_format = True
            state.selected_repo_format = "native"
            self.result = RequirementGroupRepoBootstrapResult(
                outcome="native_bootstrap_ready",
                repo_key=input_model.target_repo,
                next_preparation_flow="native_repo_preparation",
                summary=result.summary,
            )
            return
        self.result = RequirementGroupRepoBootstrapResult(
            outcome="needs_admin_repair",
            repo_key=input_model.target_repo,
            reason=result.error.message if result.error else result.summary,
            summary=result.summary,
        )


class NativeRepoPreparationParams(LeanFlowParams):
    repo_key: str
    repo_root: str | None = None
    preparation_input_ref: str = ".lean_constellation/preparation_input.json"
    start_reason: Literal["admin", "bootstrap", "repair_resume"] = "admin"
    admin_notes: str | None = None


class NativeRepoPreparationInput(LeanRenderableFlowInput):
    input_type: Literal["native_repo_preparation"] = "native_repo_preparation"
    repo_key: str
    repo_root: str | None = None
    preparation_input_ref: str = ".lean_constellation/preparation_input.json"
    start_reason: Literal["admin", "bootstrap", "repair_resume"] = "admin"
    admin_notes: str | None = None

    def agent_title(self) -> str:
        return f"Prepare native repo {self.repo_key}"

    def agent_fields(self) -> dict[str, object]:
        return {"start_reason": self.start_reason, "admin_notes": self.admin_notes}


class NativeRepoPreparationState(BaseFlowState):
    state_type: Literal["native_repo_preparation"] = "native_repo_preparation"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="validate_input"))
    source_corpus_mode: Literal["existing", "prepare"] | None = None
    allow_interface_supplement: bool | None = None
    source_corpus_ready: bool = False
    source_index_created: bool = False
    source_index_round: int = 0
    max_source_index_rounds: int = 3
    last_source_index_review_approved: bool = False
    latest_source_index_builder_summary: str | None = None
    latest_source_index_reviewer_feedback: str | None = None
    source_index_committed: bool = False
    root_interface_ready: bool = False
    handoff_gate_passed: bool = False
    waiting_dispatch_step_id: str | None = None


class NativeRepoPreparationResult(LeanRenderableFlowResult):
    result_type: Literal["native_repo_preparation"] = "native_repo_preparation"
    outcome: Literal["handoff_dispatched", "blocked", "invalid_input"]
    repo_key: str
    blocked_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {"outcome": self.outcome, "repo_key": self.repo_key, "blocked_reason": self.blocked_reason}


class NativeRepoPreparationFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "native_repo_preparation"
    Params: ClassVar[type[LeanFlowParams]] = NativeRepoPreparationParams
    Input: ClassVar[type[BaseFlowInput]] = NativeRepoPreparationInput
    State: ClassVar[type[BaseFlowState]] = NativeRepoPreparationState
    Result: ClassVar[type[BaseFlowResult]] = NativeRepoPreparationResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"native_repo_preparation": NativeRepoPreparationResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "NativeRepoPreparationFlow":
        params = NativeRepoPreparationParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=NativeRepoPreparationInput(
                summary=f"Prepare native repo {params.repo_key}.",
                **params.model_dump(),
            ),
            state=NativeRepoPreparationState(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_native_preparation_state(self.state)
        input_model = _require_native_preparation_input(self.input)
        repo_root = _native_repo_root(input_model)
        if state.position.phase == "validate_input":
            return ctx.create_step(
                ValidateAndInitializeNativePreparationStep(
                    step_id=new_repo_lifecycle_step_id("validate_native_preparation"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "source_corpus":
            if state.source_corpus_mode == "existing":
                return ctx.create_step(
                    ExistingSourceCorpusScanStep(
                        step_id=new_repo_lifecycle_step_id("existing_source_corpus_scan"),
                        flow_id=self.flow_id,
                        scope_id=self.scope_id,
                    )
                )
            from lean_constellation.flows.common.agent_steps import SourceCorpusPrepareAgentStep

            source_root = _source_corpus_workdir(ctx, repo_root)
            return ctx.create_step(
                SourceCorpusPrepareAgentStep(
                    step_id=new_repo_lifecycle_step_id("source_corpus_prepare"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="source_corpus_preparer",
                        agent_type="SourceCorpusPrepareAgent",
                        home_id="SourceCorpusPrepareAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="step",
                        variables={"repo_key": input_model.repo_key},
                        prompt_override=_source_corpus_prepare_prompt(input_model),
                        env_overrides=_agent_env("SourceCorpusPrepareAgent", "source_corpus_prepare", "source_corpus_prepare_submit"),
                        workdir_override=str(source_root),
                    ),
                )
            )
        if state.position.phase == "source_index_create":
            return ctx.create_step(
                CreateDraftSourceIndexStep(
                    step_id=new_repo_lifecycle_step_id("create_draft_source_index"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "source_index_builder":
            from lean_constellation.flows.common.agent_steps import SourceIndexBuilderAgentStep

            return ctx.create_step(
                SourceIndexBuilderAgentStep(
                    step_id=new_repo_lifecycle_step_id("source_index_builder"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="source_index_builder",
                        agent_type="SourceIndexBuilderAgent",
                        home_id="SourceIndexBuilderAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="flow",
                        variables={"repo_key": input_model.repo_key, "round_index": state.source_index_round},
                        prompt_override=_source_index_builder_prompt(
                            input_model,
                            state.source_index_round,
                            reviewer_feedback=state.latest_source_index_reviewer_feedback,
                        ),
                        env_overrides=_agent_env("SourceIndexBuilderAgent", "source_index_builder", "source_index_builder_submit"),
                        workdir_override=str(repo_root),
                    ),
                )
            )
        if state.position.phase == "source_index_reviewer":
            from lean_constellation.flows.common.agent_steps import SourceIndexReviewerAgentStep

            return ctx.create_step(
                SourceIndexReviewerAgentStep(
                    step_id=new_repo_lifecycle_step_id("source_index_reviewer"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="source_index_reviewer",
                        agent_type="SourceIndexReviewerAgent",
                        home_id="SourceIndexReviewerAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="flow",
                        variables={"repo_key": input_model.repo_key, "round_index": state.source_index_round},
                        prompt_override=_source_index_reviewer_prompt(
                            input_model,
                            state.source_index_round,
                            builder_summary=state.latest_source_index_builder_summary,
                        ),
                        env_overrides=_agent_env("SourceIndexReviewerAgent", "source_index_reviewer", "source_index_reviewer_submit"),
                        workdir_override=str(repo_root),
                    ),
                )
            )
        if state.position.phase == "source_index_commit":
            return ctx.create_step(
                CommitSourceIndexStep(
                    step_id=new_repo_lifecycle_step_id("commit_source_index"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "root_interface_prepare":
            if state.allow_interface_supplement is False:
                return ctx.create_step(
                    RootInterfaceDirectReadyStep(
                        step_id=new_repo_lifecycle_step_id("root_interface_direct_ready"),
                        flow_id=self.flow_id,
                        scope_id=self.scope_id,
                    )
                )
            from lean_constellation.flows.common.agent_steps import RootInterfacePrepareAgentStep

            return ctx.create_step(
                RootInterfacePrepareAgentStep(
                    step_id=new_repo_lifecycle_step_id("root_interface_prepare"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="root_interface_preparer",
                        agent_type="RootInterfacePrepareAgent",
                        home_id="RootInterfacePrepareAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="step",
                        variables={"repo_key": input_model.repo_key},
                        prompt_override=_root_interface_prepare_prompt(input_model),
                        env_overrides=_agent_env("RootInterfacePrepareAgent", "root_interface_prepare", "root_interface_prepare_submit"),
                        workdir_override=str(repo_root),
                    ),
                )
            )
        if state.position.phase == "handoff_gate":
            return ctx.create_step(
                HandoffGateStep(
                    step_id=new_repo_lifecycle_step_id("native_handoff_gate"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "coordinator_dispatch":
            return ctx.create_step(
                PrepareCoordinatorDispatchStep(
                    step_id=new_repo_lifecycle_step_id("prepare_coordinator_dispatch"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "dispatch_step":
            source_step_id = state.waiting_dispatch_step_id
            if source_step_id is None:
                return None
            flow_service = ctx.ark.flow_service
            if flow_service is None:
                return None
            source_step = flow_service.get_step(source_step_id)
            submission = source_step.submission
            if not isinstance(submission, NativeCoordinatorHandoffSubmission):
                return None
            return ctx.create_step(
                DispatchStep(
                    step_id=new_repo_lifecycle_step_id("native_coordinator_dispatch"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=DispatchStepState(
                        source_step_id=source_step_id,
                        source_submission_id=submission.submission_id,
                        requests=submission.requests,
                        continuation=submission.continuation,
                    ),
                )
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_native_preparation_state(self.state)
        input_model = _require_native_preparation_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="native_preparation_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return

        result = ctx.step.result
        if isinstance(result, ValidateAndInitializeNativePreparationStepResult):
            self._consume_native_validate_result(state, input_model, result)
        elif isinstance(result, ExistingSourceCorpusScanStepResult):
            self._consume_existing_source_corpus_result(state, input_model, result)
        elif ctx.step.step_type == "source_corpus_prepare_agent_step":
            self._consume_source_corpus_agent_result(ctx, state, input_model, result, ctx.step.submission)
        elif isinstance(result, CreateDraftSourceIndexStepResult):
            self._consume_create_source_index_result(state, input_model, result)
        elif ctx.step.step_type == "source_index_builder_agent_step":
            self._consume_source_index_builder_result(state, input_model, result, ctx.step.submission)
        elif ctx.step.step_type == "source_index_reviewer_agent_step":
            self._consume_source_index_reviewer_result(state, input_model, result, ctx.step.submission)
        elif isinstance(result, CommitSourceIndexStepResult):
            self._consume_commit_source_index_result(state, input_model, result)
        elif isinstance(result, RootInterfaceDirectReadyStepResult):
            self._consume_root_interface_direct_result(state, input_model, result)
        elif ctx.step.step_type == "root_interface_prepare_agent_step":
            self._consume_root_interface_agent_result(state, input_model, result, ctx.step.submission)
        elif isinstance(result, HandoffGateStepResult):
            self._consume_handoff_gate_result(state, input_model, result)
        elif isinstance(result, PrepareCoordinatorDispatchStepResult):
            self._consume_prepare_dispatch_result(state, input_model, result, ctx.step.submission, ctx.step.step_id)
        elif isinstance(result, DispatchStepResult):
            self._consume_dispatch_result(state, input_model, result)
        super().on_step_terminal(ctx)

    def after_step_terminal_stable(self, ctx: StableStepTerminalContext) -> None:
        if ctx.step.step_type != "prepare_coordinator_dispatch_step":
            return
        result = ctx.step.result
        if not isinstance(result, PrepareCoordinatorDispatchStepResult) or result.outcome != "prepared":
            return
        input_model = _require_native_preparation_input(self.input)
        repo_root = _native_repo_root(input_model)
        _record_stable_repo_snapshot(
            ctx,
            repo_root,
            checkpoint_kind="before_native_coordinator_dispatch",
            label=f"before native coordinator dispatch for {input_model.repo_key}",
            failure_type="native_preparation_stable_snapshot_failed",
        )

    def _consume_native_validate_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: ValidateAndInitializeNativePreparationStepResult,
    ) -> None:
        if result.outcome == "initialized":
            state.source_corpus_mode = result.source_corpus_mode
            state.allow_interface_supplement = result.allow_interface_supplement
            state.position = FlowPosition(phase="source_corpus")
            return
        self._finish_native_preparation(state, input_model, result.outcome, result.error.message if result.error else result.summary, result.summary)

    def _consume_existing_source_corpus_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: ExistingSourceCorpusScanStepResult,
    ) -> None:
        if result.outcome == "ready":
            state.source_corpus_ready = True
            state.position = FlowPosition(phase="source_index_create")
            return
        self._finish_native_preparation(state, input_model, "blocked", result.error.message if result.error else result.summary, result.summary)

    def _consume_source_corpus_agent_result(
        self,
        ctx: FlowStepContext,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: object | None,
        submission: object | None,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or submission is None:
            self._finish_native_preparation(
                state,
                input_model,
                "blocked",
                "SourceCorpusPrepareAgent did not produce a successful prepared or blocked submission.",
                "Source corpus prepare agent did not submit.",
            )
            return
        if isinstance(submission, SourceCorpusPreparedSubmission):
            material = getattr(ctx.app, "material", None)
            if material is None:
                self._finish_native_preparation(
                    state,
                    input_model,
                    "blocked",
                    "Material service is not registered; cannot finalize source corpus manifest.",
                    "Source corpus manifest finalize failed.",
                )
                return
            finalized = material.finalize_source_corpus_prepared(
                _native_repo_root(input_model),
                entry_path=submission.entry_path,
                overview=submission.overview,
                preparation_summary=submission.preparation_summary,
                relpath=submission.relpath,
            )
            if not finalized.ok:
                reason = finalized.issues[0].message if finalized.issues else "Source corpus manifest finalize failed."
                self._finish_native_preparation(state, input_model, "blocked", reason, "Source corpus manifest finalize failed.")
                return
            state.source_corpus_ready = True
            state.position = FlowPosition(phase="source_index_create")
            return
        if isinstance(submission, SourceCorpusBlockedSubmission):
            self._finish_native_preparation(state, input_model, "blocked", submission.reason, submission.summary)
            return
        self._finish_native_preparation(state, input_model, "blocked", "Unsupported source corpus submission.", "Unsupported source corpus submission.")

    def _consume_create_source_index_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: CreateDraftSourceIndexStepResult,
    ) -> None:
        if result.outcome == "created":
            state.source_index_created = True
            state.source_index_round = max(state.source_index_round, 1)
            state.position = FlowPosition(phase="source_index_builder", round_index=state.source_index_round)
            return
        self._finish_native_preparation(state, input_model, "blocked", result.error.message if result.error else result.summary, result.summary)

    def _consume_source_index_builder_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: object | None,
        submission: object | None,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or not isinstance(submission, SourceIndexBuilderRoundSubmission):
            self._finish_native_preparation(
                state,
                input_model,
                "blocked",
                "SourceIndexBuilderAgent did not submit a builder round.",
                "Source index builder did not submit.",
            )
            return
        state.latest_source_index_builder_summary = submission.summary
        state.position = FlowPosition(phase="source_index_reviewer", round_index=state.source_index_round)

    def _consume_source_index_reviewer_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: object | None,
        submission: object | None,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or not isinstance(submission, SourceIndexReviewerRoundSubmission):
            self._finish_native_preparation(
                state,
                input_model,
                "blocked",
                "SourceIndexReviewerAgent did not submit a review round.",
                "Source index reviewer did not submit.",
            )
            return
        if submission.approved:
            state.last_source_index_review_approved = True
            state.latest_source_index_reviewer_feedback = None
            state.position = FlowPosition(phase="source_index_commit", round_index=state.source_index_round)
            return
        state.last_source_index_review_approved = False
        state.latest_source_index_reviewer_feedback = submission.feedback
        if state.source_index_round < state.max_source_index_rounds:
            state.source_index_round += 1
            state.position = FlowPosition(phase="source_index_builder", round_index=state.source_index_round)
            return
        self._finish_native_preparation(
            state,
            input_model,
            "blocked",
            submission.feedback or "SourceIndex review rejected and max rounds were exhausted.",
            submission.summary,
        )

    def _consume_commit_source_index_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: CommitSourceIndexStepResult,
    ) -> None:
        if result.outcome == "committed":
            state.source_index_committed = True
            state.position = FlowPosition(phase="root_interface_prepare")
            return
        outcome: Literal["blocked", "invalid_input"] = "invalid_input" if result.outcome == "invalid_input" else "blocked"
        self._finish_native_preparation(state, input_model, outcome, result.error.message if result.error else result.summary, result.summary)

    def _consume_root_interface_direct_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: RootInterfaceDirectReadyStepResult,
    ) -> None:
        if result.outcome == "ready":
            state.root_interface_ready = True
            state.position = FlowPosition(phase="handoff_gate")
            return
        self._finish_native_preparation(state, input_model, "blocked", result.error.message if result.error else result.summary, result.summary)

    def _consume_root_interface_agent_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: object | None,
        submission: object | None,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or not isinstance(submission, RootInterfacePrepareReadySubmission):
            self._finish_native_preparation(
                state,
                input_model,
                "blocked",
                "RootInterfacePrepareAgent did not submit ready.",
                "Root interface prepare agent did not submit ready.",
            )
            return
        state.root_interface_ready = True
        state.position = FlowPosition(phase="handoff_gate")

    def _consume_handoff_gate_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: HandoffGateStepResult,
    ) -> None:
        if result.outcome == "passed":
            state.handoff_gate_passed = True
            state.position = FlowPosition(phase="coordinator_dispatch")
            return
        self._finish_native_preparation(state, input_model, result.outcome, result.error.message if result.error else result.summary, result.summary)

    def _consume_prepare_dispatch_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: PrepareCoordinatorDispatchStepResult,
        submission: object | None,
        step_id: str,
    ) -> None:
        if result.outcome == "prepared" and isinstance(submission, NativeCoordinatorHandoffSubmission):
            state.waiting_dispatch_step_id = step_id
            state.position = FlowPosition(phase="dispatch_step")
            return
        self._finish_native_preparation(state, input_model, "blocked", result.error.message if result.error else result.summary, result.summary)

    def _consume_dispatch_result(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: DispatchStepResult,
    ) -> None:
        if result.outcome == "dispatched" and result.continuation == "terminal_handoff" and len(result.child_flow_ids) == 1:
            state.position = FlowPosition(phase="completed")
            self.result = NativeRepoPreparationResult(
                outcome="handoff_dispatched",
                repo_key=input_model.repo_key,
                summary=result.summary or "Native preparation dispatched coordinator handoff.",
            )
            return
        self._finish_native_preparation(state, input_model, "blocked", result.summary or "Coordinator dispatch failed.", result.summary)

    def _finish_native_preparation(
        self,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        outcome: Literal["blocked", "invalid_input"],
        reason: str | None,
        summary: str | None,
    ) -> None:
        state.position = FlowPosition(phase="completed")
        self.result = NativeRepoPreparationResult(
            outcome=outcome,
            repo_key=input_model.repo_key,
            blocked_reason=reason,
            summary=summary or reason or outcome,
        )


class AdapterRepoPreparationParams(LeanFlowParams):
    repo_key: str
    repo_root: str | None = None
    preparation_input_ref: str = ".lean_constellation/preparation_input.json"
    upstream_metadata_ref: str = ".lean_constellation/adapter_upstream.json"
    start_reason: Literal["admin", "bootstrap", "repair_resume"] = "admin"
    admin_notes: str | None = None


class AdapterRepoPreparationInput(LeanRenderableFlowInput):
    input_type: Literal["adapter_repo_preparation"] = "adapter_repo_preparation"
    repo_key: str
    repo_root: str | None = None
    preparation_input_ref: str = ".lean_constellation/preparation_input.json"
    upstream_metadata_ref: str = ".lean_constellation/adapter_upstream.json"
    start_reason: Literal["admin", "bootstrap", "repair_resume"] = "admin"
    admin_notes: str | None = None

    def agent_title(self) -> str:
        return f"Prepare adapter repo {self.repo_key}"

    def agent_fields(self) -> dict[str, object]:
        return {"start_reason": self.start_reason, "admin_notes": self.admin_notes}


class AdapterRepoPreparationState(BaseFlowState):
    state_type: Literal["adapter_repo_preparation"] = "adapter_repo_preparation"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="validate_input"))
    upstream_validated: bool = False
    main_catalog_ready: bool = False
    projection_refreshed: bool = False
    ready_gate_passed: bool = False
    provider_ready_marked: bool = False
    catalog_decl_count: int = 0
    bound_interface_count: int = 0
    imported_modules_count: int = 0


class AdapterRepoPreparationResult(LeanRenderableFlowResult):
    result_type: Literal["adapter_repo_preparation"] = "adapter_repo_preparation"
    outcome: Literal["adapter_ready", "blocked", "invalid_input"]
    repo_key: str
    catalog_decl_count: int = 0
    bound_interface_count: int = 0
    imported_modules_count: int = 0
    blocked_reason: str | None = None
    missing_interfaces: list[str] = Field(default_factory=list)
    suggested_next_action: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "catalog_decl_count": self.catalog_decl_count,
            "bound_interface_count": self.bound_interface_count,
            "imported_modules_count": self.imported_modules_count,
            "blocked_reason": self.blocked_reason,
            "missing_interfaces": list(self.missing_interfaces),
            "suggested_next_action": self.suggested_next_action,
        }


class AdapterRepoPreparationFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "adapter_repo_preparation"
    Params: ClassVar[type[LeanFlowParams]] = AdapterRepoPreparationParams
    Input: ClassVar[type[BaseFlowInput]] = AdapterRepoPreparationInput
    State: ClassVar[type[BaseFlowState]] = AdapterRepoPreparationState
    Result: ClassVar[type[BaseFlowResult]] = AdapterRepoPreparationResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"adapter_repo_preparation": AdapterRepoPreparationResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "AdapterRepoPreparationFlow":
        params = AdapterRepoPreparationParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=AdapterRepoPreparationInput(
                summary=f"Prepare adapter repo {params.repo_key}.",
                **params.model_dump(),
            ),
            state=AdapterRepoPreparationState(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_adapter_preparation_state(self.state)
        input_model = _require_adapter_preparation_input(self.input)
        repo_root = _adapter_repo_root(input_model)
        if state.position.phase == "validate_input":
            return ctx.create_step(
                ValidateAdapterPreparationInputStep(
                    step_id=new_repo_lifecycle_step_id("validate_adapter_preparation"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "ensure_main_catalog":
            return ctx.create_step(
                EnsureAdapterMainCatalogStep(
                    step_id=new_repo_lifecycle_step_id("ensure_adapter_main_catalog"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "agent_catalog":
            from lean_constellation.flows.common.agent_steps import AdapterDeclCatalogAgentStep

            return ctx.create_step(
                AdapterDeclCatalogAgentStep(
                    step_id=new_repo_lifecycle_step_id("adapter_decl_catalog"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="adapter_decl_catalog",
                        agent_type="AdapterDeclCatalogAgent",
                        home_id="AdapterDeclCatalogAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="step",
                        variables={"repo_key": input_model.repo_key},
                        prompt_override=_adapter_decl_catalog_prompt(input_model),
                        env_overrides=_agent_env("AdapterDeclCatalogAgent", "adapter_repo_import", "adapter_repo_import_submit"),
                        workdir_override=str(repo_root),
                    ),
                )
            )
        if state.position.phase == "finalize_ready":
            return ctx.create_step(
                FinalizeAdapterReadyStep(
                    step_id=new_repo_lifecycle_step_id("finalize_adapter_ready"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "provider_ready":
            return ctx.create_step(
                MarkAdapterProviderReadyStep(
                    step_id=new_repo_lifecycle_step_id("mark_adapter_provider_ready"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_adapter_preparation_state(self.state)
        input_model = _require_adapter_preparation_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="adapter_preparation_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return

        result = ctx.step.result
        if isinstance(result, AdapterInputValidationStepResult):
            self._consume_adapter_validate_result(state, input_model, result)
        elif isinstance(result, EnsureAdapterMainCatalogStepResult):
            self._consume_ensure_adapter_catalog_result(state, input_model, result)
        elif ctx.step.step_type == "adapter_decl_catalog_agent_step":
            self._consume_adapter_agent_result(state, input_model, result, ctx.step.submission)
        elif isinstance(result, FinalizeAdapterReadyStepResult):
            self._consume_finalize_adapter_result(state, input_model, result)
        elif isinstance(result, MarkAdapterProviderReadyStepResult):
            self._consume_mark_adapter_ready_result(state, input_model, result)
        super().on_step_terminal(ctx)

    def after_step_terminal_stable(self, ctx: StableStepTerminalContext) -> None:
        if ctx.step.step_type != "mark_adapter_provider_ready_step":
            return
        result = ctx.step.result
        if not isinstance(result, MarkAdapterProviderReadyStepResult) or result.outcome != "marked_ready":
            return
        flow_result = self.result
        if not isinstance(flow_result, AdapterRepoPreparationResult) or flow_result.outcome != "adapter_ready":
            return
        input_model = _require_adapter_preparation_input(self.input)
        _record_stable_repo_snapshot(
            ctx,
            _adapter_repo_root(input_model),
            checkpoint_kind="adapter_preparation_terminal",
            label=f"adapter preparation terminal for {input_model.repo_key}",
            failure_type="adapter_preparation_stable_snapshot_failed",
        )

    def _consume_adapter_validate_result(
        self,
        state: AdapterRepoPreparationState,
        input_model: AdapterRepoPreparationInput,
        result: AdapterInputValidationStepResult,
    ) -> None:
        if result.outcome == "passed":
            state.upstream_validated = True
            state.position = FlowPosition(phase="ensure_main_catalog")
            return
        self._finish_adapter_preparation(state, input_model, result.outcome, result.error.message if result.error else result.summary, result.summary)

    def _consume_ensure_adapter_catalog_result(
        self,
        state: AdapterRepoPreparationState,
        input_model: AdapterRepoPreparationInput,
        result: EnsureAdapterMainCatalogStepResult,
    ) -> None:
        if result.outcome == "ready":
            state.main_catalog_ready = True
            state.catalog_decl_count = result.active_decl_count
            state.position = FlowPosition(phase="agent_catalog")
            return
        self._finish_adapter_preparation(state, input_model, "blocked", result.error.message if result.error else result.summary, result.summary)

    def _consume_adapter_agent_result(
        self,
        state: AdapterRepoPreparationState,
        input_model: AdapterRepoPreparationInput,
        result: object | None,
        submission: object | None,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or submission is None:
            self._finish_adapter_preparation(
                state,
                input_model,
                "blocked",
                "AdapterDeclCatalogAgent did not produce a successful ready or blocked submission.",
                "Adapter decl catalog agent did not submit.",
            )
            return
        if isinstance(submission, AdapterCatalogReadySubmission):
            state.position = FlowPosition(phase="finalize_ready")
            return
        if isinstance(submission, AdapterCatalogBlockedSubmission):
            self._finish_adapter_preparation(
                state,
                input_model,
                "blocked",
                submission.reason,
                submission.summary,
                missing_interfaces=submission.missing_interfaces,
                suggested_next_action=submission.suggested_next_action,
            )
            return
        self._finish_adapter_preparation(state, input_model, "blocked", "Unsupported adapter catalog submission.", "Unsupported adapter catalog submission.")

    def _consume_finalize_adapter_result(
        self,
        state: AdapterRepoPreparationState,
        input_model: AdapterRepoPreparationInput,
        result: FinalizeAdapterReadyStepResult,
    ) -> None:
        if result.outcome == "ready":
            state.projection_refreshed = result.projection_refreshed
            state.ready_gate_passed = result.ready_gate_passed
            state.catalog_decl_count = result.catalog_decl_count
            state.bound_interface_count = result.bound_interface_count
            state.imported_modules_count = result.imported_modules_count
            state.position = FlowPosition(phase="provider_ready")
            return
        self._finish_adapter_preparation(state, input_model, result.outcome, result.error.message if result.error else result.summary, result.summary)

    def _consume_mark_adapter_ready_result(
        self,
        state: AdapterRepoPreparationState,
        input_model: AdapterRepoPreparationInput,
        result: MarkAdapterProviderReadyStepResult,
    ) -> None:
        if result.outcome == "marked_ready":
            state.provider_ready_marked = True
            state.position = FlowPosition(phase="completed")
            self.result = AdapterRepoPreparationResult(
                outcome="adapter_ready",
                repo_key=input_model.repo_key,
                catalog_decl_count=state.catalog_decl_count,
                bound_interface_count=state.bound_interface_count,
                imported_modules_count=state.imported_modules_count,
                summary=result.repo_summary or result.summary or "Adapter provider repo is ready.",
            )
            return
        self._finish_adapter_preparation(state, input_model, "blocked", result.error.message if result.error else result.summary, result.summary)

    def _finish_adapter_preparation(
        self,
        state: AdapterRepoPreparationState,
        input_model: AdapterRepoPreparationInput,
        outcome: Literal["blocked", "invalid_input"],
        reason: str | None,
        summary: str | None,
        *,
        missing_interfaces: list[str] | None = None,
        suggested_next_action: str | None = None,
    ) -> None:
        state.position = FlowPosition(phase="completed")
        self.result = AdapterRepoPreparationResult(
            outcome=outcome,
            repo_key=input_model.repo_key,
            catalog_decl_count=state.catalog_decl_count,
            bound_interface_count=state.bound_interface_count,
            imported_modules_count=state.imported_modules_count,
            blocked_reason=reason,
            missing_interfaces=missing_interfaces or [],
            suggested_next_action=suggested_next_action,
            summary=summary or reason or outcome,
        )


def _require_requirement_bootstrap_state(value: BaseFlowState) -> RequirementGroupRepoBootstrapState:
    if not isinstance(value, RequirementGroupRepoBootstrapState):
        raise TypeError("RequirementGroupRepoBootstrapFlow has invalid state model.")
    return value


def _require_requirement_bootstrap_input(value: BaseFlowInput | None) -> RequirementGroupRepoBootstrapInput:
    if not isinstance(value, RequirementGroupRepoBootstrapInput):
        raise TypeError("RequirementGroupRepoBootstrapFlow has invalid input model.")
    return value


def _repo_format_discovery_prompt(input_model: RequirementGroupRepoBootstrapInput) -> str:
    lines = [
        f"Bootstrap provider repo {input_model.target_repo}.",
        "",
        "The provider repo shell and preparation input already exist.",
        "Your only task is to choose whether this repo should continue as an adapter around an upstream Lean repo or as a native Lean Constellation repo.",
        "Use tools to read preparation input and scoped requirement details; requirement refs below are only navigation hints.",
        "Use remote GitHub search, tree/file/code read, and Lean repo probe tools for upstream evidence.",
        "When ready, call either submit_adapter_repo_choice or submit_native_repo_choice. Do not initialize the Lake skeleton or source corpus yourself.",
    ]
    if input_model.requirement_refs:
        lines.extend(["", f"Requirement refs: {', '.join(input_model.requirement_refs)}"])
    if input_model.admin_notes:
        lines.extend(["", f"Admin notes: {input_model.admin_notes}"])
    return "\n".join(lines)


def _require_native_preparation_state(value: BaseFlowState) -> NativeRepoPreparationState:
    if not isinstance(value, NativeRepoPreparationState):
        raise TypeError("NativeRepoPreparationFlow has invalid state model.")
    return value


def _record_stable_repo_snapshot(
    ctx: StableStepTerminalContext,
    repo_root: Path,
    *,
    checkpoint_kind: str,
    label: str,
    failure_type: str,
    node_paths: list[str] | None = None,
) -> None:
    snapshot = ctx.app.validation_snapshot.create_repo_stable_point_snapshot(
        repo_root,
        checkpoint_kind=checkpoint_kind,
        label=label,
        node_paths=node_paths,
        scope_ids=[ctx.flow.scope_id],
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


def _require_native_preparation_input(value: BaseFlowInput | None) -> NativeRepoPreparationInput:
    if not isinstance(value, NativeRepoPreparationInput):
        raise TypeError("NativeRepoPreparationFlow has invalid input model.")
    return value


def _native_repo_root(input_model: NativeRepoPreparationInput) -> Path:
    from pathlib import Path

    return Path(input_model.repo_root or input_model.repo_key)


def _agent_env(agent_type: str, app_view: str, submit_view: str) -> dict[str, str]:
    return {
        "LEAN_CONSTELLATION_AGENT_TYPE": agent_type,
        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": app_view,
        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": submit_view,
    }


def _source_corpus_workdir(ctx: FlowContext, repo_root: Path) -> Path:
    relpath = ".lean_constellation/source"
    preparation = ctx.app.repo_workspace.preparation.get_preparation_input(repo_root)
    if preparation.ok and preparation.value is not None:
        relpath = preparation.value.input.source_corpus_relpath or relpath
    path = Path(relpath)
    return path if path.is_absolute() else repo_root / path


def _source_corpus_prepare_prompt(input_model: NativeRepoPreparationInput) -> str:
    return "\n".join(
        [
            f"Prepare the source corpus for native repo {input_model.repo_key}.",
            "Read the repository preparation input through tools, work only inside the source corpus directory, and submit prepared or blocked.",
        ]
    )


def _source_index_builder_prompt(
    input_model: NativeRepoPreparationInput,
    round_index: int,
    *,
    reviewer_feedback: str | None = None,
) -> str:
    lines = [
        f"Build SourceIndex round {round_index} for native repo {input_model.repo_key}.",
        "Use the source corpus and existing draft SourceIndex tools. Submit the builder round when the draft is ready for review.",
    ]
    if reviewer_feedback:
        lines.extend(["", "Previous reviewer feedback:", reviewer_feedback])
    return "\n".join(lines)


def _source_index_reviewer_prompt(
    input_model: NativeRepoPreparationInput,
    round_index: int,
    *,
    builder_summary: str | None = None,
) -> str:
    lines = [
        f"Review SourceIndex round {round_index} for native repo {input_model.repo_key}.",
        "Inspect the draft SourceIndex and source evidence. Submit approved or rejected with actionable feedback.",
    ]
    if builder_summary:
        lines.extend(["", "Latest builder summary:", builder_summary])
    return "\n".join(lines)


def _root_interface_prepare_prompt(input_model: NativeRepoPreparationInput) -> str:
    return "\n".join(
        [
            f"Prepare root Main interfaces for native repo {input_model.repo_key}.",
            "Read the preparation input, committed SourceIndex, and current root interfaces. Add only supplement interfaces, then submit ready.",
        ]
    )


def _require_adapter_preparation_state(value: BaseFlowState) -> AdapterRepoPreparationState:
    if not isinstance(value, AdapterRepoPreparationState):
        raise TypeError("AdapterRepoPreparationFlow has invalid state model.")
    return value


def _require_adapter_preparation_input(value: BaseFlowInput | None) -> AdapterRepoPreparationInput:
    if not isinstance(value, AdapterRepoPreparationInput):
        raise TypeError("AdapterRepoPreparationFlow has invalid input model.")
    return value


def _adapter_repo_root(input_model: AdapterRepoPreparationInput) -> Path:
    from pathlib import Path

    return Path(input_model.repo_root or input_model.repo_key)


def _adapter_decl_catalog_prompt(input_model: AdapterRepoPreparationInput) -> str:
    return "\n".join(
        [
            f"Prepare the adapter declaration catalog for adapter repo {input_model.repo_key}.",
            "Read preparation input, upstream metadata, root interfaces, and current adapter catalog through tools.",
            "Register finalized adapter declarations, bind required interfaces, then submit ready or blocked.",
        ]
    )


REPO_LIFECYCLE_FLOW_TYPES: tuple[type[LeanBusinessFlow], ...] = (
    RequirementGroupRepoBootstrapFlow,
    NativeRepoPreparationFlow,
    AdapterRepoPreparationFlow,
)
