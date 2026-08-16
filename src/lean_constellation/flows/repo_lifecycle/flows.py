"""Repo lifecycle Flow type definitions."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowReadContext, FlowStepContext, StableStepTerminalContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, ChildFlowDispatchSubmission, FlowPosition, FlowStatus, FlowStepValidationError, utc_now_iso
from agent_runtime_kit.flow.standard_steps import (
    AgentStepIncompleteResult,
    AgentStepState,
    DispatchStep,
    DispatchStepResult,
    DispatchStepState,
)
from pydantic import Field, model_validator

from lean_constellation.domain.preparation import (
    AdapterProviderRoute,
    AutoProviderRoute,
    NativeProviderRoute,
    ProviderRoute,
    VerifiedAdapterRouteReceipt,
)
from lean_constellation.domain.repo_recovery import NativeSourceIndexRecoveryContract
from lean_constellation.domain.repo_run import RepoRunSpec
from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.checkpoint_policy import record_checkpoint_skip_summary, repo_flow_boundary_checkpoints_enabled
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.repo_lifecycle.steps import (
    ApplyRepoFormatChoiceStep,
    ApplyRepoFormatChoiceStepResult,
    BootstrapInputValidationStepResult,
    AdapterInputValidationStepResult,
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
    PrepareNativeLifecycleChildStep,
    PrepareNativeLifecycleChildStepResult,
    ValidateBootstrapInputStep,
    ValidateAdapterPreparationInputStep,
    ValidateAndInitializeNativePreparationStep,
    ValidateAndInitializeNativePreparationStepResult,
    new_repo_lifecycle_step_id,
)
from lean_constellation.flows.repo_lifecycle.root_interface import RootInterfacePreparationResult
from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildResult
from lean_constellation.flows.repo_lifecycle.submissions import (
    NativeCoordinatorHandoffSubmission,
    AdapterCatalogBlockedSubmission,
    AdapterCatalogReadySubmission,
    RepoFormatAdapterChoiceSubmission,
    RepoFormatNativeChoiceSubmission,
    SourceCorpusBuilderBlockedSubmission,
    SourceCorpusBuilderReadySubmission,
    SourceCorpusReviewSubmission,
)
from lean_constellation.services.validation_snapshot.release_finalizer import (
    PreparedRepoReleaseView,
)
from lean_constellation.services.foundation import FoundationContext


class RequirementGroupRepoBootstrapParams(LeanFlowParams):
    target_repo: str
    repo_root: str
    workspace_root: str
    requirement_refs: list[str] = Field(default_factory=list)
    resolved_provider_route: ProviderRoute
    verified_adapter_route: VerifiedAdapterRouteReceipt | None = None
    admin_notes: str | None = None

    @model_validator(mode="after")
    def _validate_direct_route_receipt(
        self,
    ) -> RequirementGroupRepoBootstrapParams:
        route = self.resolved_provider_route
        receipt = self.verified_adapter_route
        if isinstance(route, AdapterProviderRoute):
            if receipt is None:
                raise ValueError("direct adapter route requires a verified adapter receipt")
            if receipt.git_url != route.git_url:
                raise ValueError("verified adapter receipt does not match the resolved provider route")
            for field_name in ("subdir", "package_name", "likely_import_module"):
                expected = getattr(route, field_name)
                if expected is not None and getattr(receipt, field_name) != expected:
                    raise ValueError("verified adapter receipt does not match the resolved provider route")
            if route.revision is not None and receipt.revision != route.revision:
                raise ValueError("verified adapter receipt changed the explicit route revision")
        elif receipt is not None:
            raise ValueError("verified adapter receipt is only valid for an adapter route")
        return self


class RequirementGroupRepoBootstrapInput(LeanRenderableFlowInput):
    input_type: Literal["requirement_group_repo_bootstrap"] = "requirement_group_repo_bootstrap"
    target_repo: str
    repo_root: str
    workspace_root: str
    requirement_refs: list[str] = Field(default_factory=list)
    resolved_provider_route: ProviderRoute
    verified_adapter_route: VerifiedAdapterRouteReceipt | None = None
    admin_notes: str | None = None

    def agent_title(self) -> str:
        return f"Bootstrap provider repo {self.target_repo}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "requirement_count": len(self.requirement_refs),
            "provider_route": self.resolved_provider_route.model_dump(mode="json"),
            "adapter_route_verified": self.verified_adapter_route is not None,
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
            if isinstance(input_model.resolved_provider_route, AutoProviderRoute):
                state.position = FlowPosition(phase="format_discovery")
            elif isinstance(input_model.resolved_provider_route, AdapterProviderRoute):
                state.selected_repo_format = "adapter"
                state.adapter_choice_summary = (
                    input_model.resolved_provider_route.evidence_summary
                )
                state.position = FlowPosition(phase="apply_format_choice")
            elif isinstance(input_model.resolved_provider_route, NativeProviderRoute):
                state.selected_repo_format = "native"
                state.native_choice_summary = (
                    input_model.resolved_provider_route.evidence_summary
                )
                state.position = FlowPosition(phase="apply_format_choice")
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
    run_spec: RepoRunSpec
    recovery: NativeSourceIndexRecoveryContract | None = None

    @model_validator(mode="after")
    def _validate_recovery(self) -> "NativeRepoPreparationParams":
        if self.recovery is not None and self.start_reason != "repair_resume":
            raise ValueError("Native preparation recovery requires repair_resume and a recovery contract")
        if self.recovery is not None:
            if self.repo_key != self.recovery.repo_key:
                raise ValueError("Native preparation recovery repo_key mismatch")
            if Path(self.repo_root or "").resolve(strict=False) != Path(
                self.recovery.repo_root
            ).resolve(strict=False):
                raise ValueError("Native preparation recovery repo_root mismatch")
        return self


class NativeRepoPreparationInput(LeanRenderableFlowInput):
    input_type: Literal["native_repo_preparation"] = "native_repo_preparation"
    repo_key: str
    repo_root: str | None = None
    preparation_input_ref: str = ".lean_constellation/preparation_input.json"
    start_reason: Literal["admin", "bootstrap", "repair_resume"] = "admin"
    admin_notes: str | None = None
    run_spec: RepoRunSpec
    recovery: NativeSourceIndexRecoveryContract | None = None

    def agent_title(self) -> str:
        return f"Prepare native repo {self.repo_key}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "start_reason": self.start_reason,
            "admin_notes": self.admin_notes,
            "run_spec": self.run_spec.model_dump(mode="json"),
            "recovery": self.recovery.model_dump(mode="json") if self.recovery else None,
        }


class NativeRepoPreparationState(BaseFlowState):
    state_type: Literal["native_repo_preparation"] = "native_repo_preparation"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="validate_input"))
    source_corpus_mode: Literal["existing", "prepare"] | None = None
    allow_interface_supplement: bool | None = None
    source_corpus_ready: bool = False
    source_corpus_candidate: SourceCorpusBuilderReadySubmission | None = None
    source_corpus_review_round: int = 0
    latest_source_corpus_reviewer_feedback: str | None = None
    source_corpus_reviewed: bool = False
    root_interface_ready: bool = False
    handoff_gate_passed: bool = False
    waiting_dispatch_step_id: str | None = None
    pre_run_mutation_checkpoint_id: str | None = None
    pending_child_source_step_id: str | None = None
    waiting_child_kind: Literal["source_index", "root_interface"] | None = None
    source_index_child_result: SourceIndexBuildResult | None = None
    root_interface_child_result: RootInterfacePreparationResult | None = None


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
        if params.recovery is None:
            state = NativeRepoPreparationState()
        else:
            preview = ctx.app.repo_workspace.native_source_index_recovery.preview(
                Path(params.repo_root or ""),
                repo_key=params.repo_key,
                failed_parent_flow_id=params.recovery.failed_parent_flow_id,
            )
            if not preview.ok or preview.value != params.recovery:
                issue_kinds = ", ".join(issue.kind for issue in preview.issues) or "contract_mismatch"
                raise ValueError(
                    "Native SourceIndex recovery contract is no longer eligible: "
                    f"{issue_kinds}"
                )
            state = NativeRepoPreparationState(
                position=FlowPosition(phase="prepare_source_index_child"),
                source_corpus_mode=params.recovery.source_corpus_mode,
                allow_interface_supplement=params.recovery.allow_interface_supplement,
                source_corpus_ready=True,
                pre_run_mutation_checkpoint_id=params.recovery.pre_run_mutation_checkpoint_id,
            )
        return cls._build(
            ctx,
            input_model=NativeRepoPreparationInput(
                summary=f"Prepare native repo {params.repo_key}.",
                **params.model_dump(exclude={"run_spec"}),
                run_spec=params.run_spec,
            ),
            state=state,
        )

    def can_exit_waiting(self, ctx: FlowReadContext) -> bool:
        state = _require_native_preparation_state(self.state)
        if state.position.phase not in {"waiting_source_index_child", "waiting_root_interface_child"}:
            return False
        children = _native_children_for_dispatch(ctx, self.flow_id, state.waiting_dispatch_step_id)
        return bool(children) and all(child.status in {FlowStatus.COMPLETED, FlowStatus.FAILED} for child in children)

    def on_exit_waiting(self, ctx: FlowContext) -> None:
        state = _require_native_preparation_state(self.state)
        input_model = _require_native_preparation_input(self.input)
        children = _native_children_for_dispatch(ctx, self.flow_id, state.waiting_dispatch_step_id)
        if len(children) != 1:
            reason = "Native preparation callback did not resolve exactly one child Flow."
            self.error = BaseFlowError(
                error_type="native_preparation_child_resolution_failed",
                message=reason,
                details={"child_count": len(children)},
            )
        elif children[0].status is FlowStatus.FAILED:
            child = children[0]
            reason = child.error.message if child.error is not None else "Native preparation child Flow failed."
            self.error = BaseFlowError(
                error_type="native_preparation_child_failed",
                message=reason,
                details={
                    "child_flow_id": child.flow_id,
                    "child_flow_type": child.flow_type,
                    "child_error_type": child.error.error_type if child.error is not None else None,
                },
            )
        elif state.waiting_child_kind == "source_index":
            result = children[0].result
            if not isinstance(result, SourceIndexBuildResult):
                self._finish_native_preparation(state, input_model, "blocked", "SourceIndex child result is invalid.", "SourceIndex child result is invalid.")
            elif result.outcome in {"committed", "no_op"}:
                state.source_index_child_result = result
                state.position = FlowPosition(phase="prepare_root_interface_child")
            else:
                outcome = "invalid_input" if result.outcome == "invalid_input" else "blocked"
                self._finish_native_preparation(state, input_model, outcome, result.reason or result.summary, result.summary)
        elif state.waiting_child_kind == "root_interface":
            result = children[0].result
            if not isinstance(result, RootInterfacePreparationResult):
                self._finish_native_preparation(state, input_model, "blocked", "Root-interface child result is invalid.", "Root-interface child result is invalid.")
            elif result.outcome == "ready":
                state.root_interface_child_result = result
                state.root_interface_ready = True
                state.position = FlowPosition(phase="handoff_gate")
            else:
                self._finish_native_preparation(state, input_model, result.outcome, result.blocked_reason or result.summary, result.summary)
        else:
            self._finish_native_preparation(state, input_model, "blocked", "Native preparation child kind is missing.", "Native preparation child kind is missing.")
        state.waiting_child_kind = None
        if self.error is not None:
            self.status = FlowStatus.FAILED
            self.finished_at = self.finished_at or utc_now_iso()
            self.updated_at = utc_now_iso()
            return
        if self.result is not None:
            self.status = FlowStatus.COMPLETED
            self.finished_at = self.finished_at or utc_now_iso()
            self.updated_at = utc_now_iso()
            return
        super().on_exit_waiting(ctx)

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
            from lean_constellation.flows.common.agent_steps import SourceCorpusBuilderAgentStep

            source_root = _source_corpus_workdir(ctx, repo_root)
            return ctx.create_step(
                SourceCorpusBuilderAgentStep(
                    step_id=new_repo_lifecycle_step_id("source_corpus_builder"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="source_corpus_builder",
                        agent_type="SourceCorpusBuilderAgent",
                        home_id="SourceCorpusBuilderAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="flow",
                        variables={"repo_key": input_model.repo_key},
                        prompt_override=_source_corpus_builder_prompt(
                            input_model,
                            logical_path=source_root.relative_to(repo_root).as_posix(),
                            reviewer_feedback=state.latest_source_corpus_reviewer_feedback,
                        ),
                        env_overrides=_agent_env("SourceCorpusBuilderAgent", "source_corpus_builder", "source_corpus_builder_submit"),
                        workdir_override=str(source_root),
                    ),
                )
            )
        if state.position.phase == "source_corpus_review":
            from lean_constellation.flows.common.agent_steps import SourceCorpusReviewerAgentStep

            return ctx.create_step(
                SourceCorpusReviewerAgentStep(
                    step_id=new_repo_lifecycle_step_id("source_corpus_reviewer"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="source_corpus_reviewer",
                        agent_type="SourceCorpusReviewerAgent",
                        home_id="SourceCorpusReviewerAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="flow",
                        variables={"repo_key": input_model.repo_key},
                        prompt_override=_source_corpus_reviewer_prompt(
                            input_model,
                            review_round=state.source_corpus_review_round,
                            builder_summary=(
                                state.source_corpus_candidate.preparation_summary
                                if state.source_corpus_candidate is not None
                                else "Existing SourceCorpus passed deterministic scan."
                            ),
                            previous_feedback=state.latest_source_corpus_reviewer_feedback,
                        ),
                        env_overrides=_agent_env("SourceCorpusReviewerAgent", "source_corpus_reviewer", "source_corpus_reviewer_submit"),
                        workdir_override=str(_source_corpus_workdir(ctx, repo_root)),
                    ),
                )
            )
        if state.position.phase in {"prepare_source_index_child", "prepare_root_interface_child"}:
            return ctx.create_step(
                PrepareNativeLifecycleChildStep(
                    step_id=new_repo_lifecycle_step_id(state.position.phase),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "dispatch_preparation_child":
            source_step_id = state.pending_child_source_step_id
            if source_step_id is None:
                return None
            flow_service = ctx.ark.flow_service
            if flow_service is None:
                return None
            source_step = flow_service.get_step(source_step_id)
            submission = source_step.submission
            if not isinstance(submission, ChildFlowDispatchSubmission):
                return None
            return ctx.create_step(
                DispatchStep(
                    step_id=new_repo_lifecycle_step_id(f"dispatch_{state.waiting_child_kind or 'preparation'}"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=DispatchStepState(
                        source_step_id=source_step_id,
                        source_submission_id=submission.submission_id,
                        requests=list(submission.requests),
                        continuation=submission.continuation,
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
        if state.position.phase == "completed":
            return None
        raise FlowStepValidationError(
            "unsupported_flow_phase: "
            f"NativeRepoPreparationFlow does not support phase {state.position.phase!r}."
        )

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
        elif ctx.step.step_type == "source_corpus_builder_agent_step":
            self._consume_source_corpus_builder_result(state, input_model, result, ctx.step.submission)
        elif ctx.step.step_type == "source_corpus_reviewer_agent_step":
            self._consume_source_corpus_reviewer_result(ctx, state, input_model, result, ctx.step.submission)
        elif isinstance(result, PrepareNativeLifecycleChildStepResult):
            if result.outcome == "prepared" and result.child_kind is not None:
                state.pending_child_source_step_id = ctx.step.step_id
                state.waiting_child_kind = result.child_kind
                state.position = FlowPosition(phase="dispatch_preparation_child")
            else:
                self._finish_native_preparation(
                    state,
                    input_model,
                    "blocked",
                    result.error.message if result.error else result.summary,
                    result.summary,
                )
        elif isinstance(result, HandoffGateStepResult):
            self._consume_handoff_gate_result(state, input_model, result)
        elif isinstance(result, PrepareCoordinatorDispatchStepResult):
            self._consume_prepare_dispatch_result(state, input_model, result, ctx.step.submission, ctx.step.step_id)
        elif isinstance(result, DispatchStepResult):
            if result.continuation == "wait_for_callback" and state.waiting_child_kind is not None:
                if result.outcome == "dispatched" and len(result.child_flow_ids) == 1:
                    state.waiting_dispatch_step_id = ctx.step.step_id
                    state.position = FlowPosition(
                        phase=(
                            "waiting_source_index_child"
                            if state.waiting_child_kind == "source_index"
                            else "waiting_root_interface_child"
                        )
                    )
                else:
                    self._finish_native_preparation(state, input_model, "blocked", result.summary or "Preparation child dispatch failed.", result.summary)
            else:
                self._consume_dispatch_result(state, input_model, result)
        super().on_step_terminal(ctx)
        if self.result is None and self.error is None and state.position.phase in {
            "waiting_source_index_child",
            "waiting_root_interface_child",
        }:
            self.status = FlowStatus.WAITING

    def after_step_terminal_stable(self, ctx: StableStepTerminalContext) -> None:
        if ctx.step.step_type == "validate_initialize_native_preparation_step":
            result = ctx.step.result
            if not isinstance(result, ValidateAndInitializeNativePreparationStepResult) or result.outcome != "initialized":
                return
            input_model = _require_native_preparation_input(self.input)
            if not result.pre_run_mutation_checkpoint_id:
                _mark_flow_failed_from_stable_snapshot(ctx, "native_source_processing_checkpoint_id_missing", [])
                return
            snapshot = ctx.app.snapshot_runtime.create_repo_stable_point_snapshot_with_id(
                _native_repo_root(input_model),
                snapshot_id=result.pre_run_mutation_checkpoint_id,
                checkpoint_kind="before_native_source_processing",
                label=f"before native source processing for {input_model.repo_key}",
                scope_ids=[ctx.flow.scope_id],
            )
            if not snapshot.ok:
                _mark_flow_failed_from_stable_snapshot(
                    ctx, "native_source_processing_stable_snapshot_failed", snapshot.issues
                )
            return
        if ctx.step.step_type != "prepare_coordinator_dispatch_step":
            return
        result = ctx.step.result
        if not isinstance(result, PrepareCoordinatorDispatchStepResult) or result.outcome != "prepared":
            return
        if not repo_flow_boundary_checkpoints_enabled(ctx.app):
            record_checkpoint_skip_summary(
                ctx,
                "Before-Coordinator checkpoint skipped because repo flow-boundary checkpoints are disabled.",
            )
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
            state.pre_run_mutation_checkpoint_id = result.pre_run_mutation_checkpoint_id
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
            state.position = FlowPosition(phase="source_corpus_review")
            return
        self._finish_native_preparation(state, input_model, "blocked", result.error.message if result.error else result.summary, result.summary)

    def _consume_source_corpus_builder_result(
        self,
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
                "SourceCorpusBuilderAgent did not produce a successful ready or blocked submission.",
                "Source corpus builder did not submit.",
            )
            return
        if isinstance(submission, SourceCorpusBuilderReadySubmission):
            state.source_corpus_candidate = submission
            state.position = FlowPosition(phase="source_corpus_review")
            return
        if isinstance(submission, SourceCorpusBuilderBlockedSubmission):
            self._finish_native_preparation(state, input_model, "blocked", submission.reason, submission.summary)
            return
        self._finish_native_preparation(state, input_model, "blocked", "Unsupported source corpus submission.", "Unsupported source corpus submission.")

    def _consume_source_corpus_reviewer_result(
        self,
        ctx: FlowStepContext,
        state: NativeRepoPreparationState,
        input_model: NativeRepoPreparationInput,
        result: object | None,
        submission: object | None,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or not isinstance(submission, SourceCorpusReviewSubmission):
            self._finish_native_preparation(
                state,
                input_model,
                "blocked",
                "SourceCorpusReviewerAgent did not produce a valid review submission.",
                "Source corpus reviewer did not submit.",
            )
            return
        if not submission.approved:
            state.source_corpus_review_round += 1
            state.latest_source_corpus_reviewer_feedback = submission.feedback
            if state.source_corpus_mode == "prepare" and state.source_corpus_review_round < 3:
                state.position = FlowPosition(phase="source_corpus")
                return
            reason = submission.feedback or "Source corpus fidelity review rejected the current candidate."
            if state.source_corpus_mode == "existing":
                reason = f"Existing SourceCorpus requires an explicit prepare/repair run: {reason}"
            self._finish_native_preparation(state, input_model, "blocked", reason, submission.summary)
            return

        if state.source_corpus_mode == "prepare":
            candidate = state.source_corpus_candidate
            material = getattr(ctx.app, "material", None)
            if candidate is None or material is None:
                self._finish_native_preparation(
                    state,
                    input_model,
                    "blocked",
                    "Reviewed SourceCorpus candidate or Material service is missing.",
                    "Source corpus finalize failed.",
                )
                return
            finalized = material.finalize_source_corpus_prepared(
                _native_repo_root(input_model),
                entry_path=candidate.entry_path,
                overview=candidate.overview,
                preparation_summary=candidate.preparation_summary,
                relpath=candidate.relpath,
            )
            if not finalized.ok:
                reason = finalized.issues[0].message if finalized.issues else "Source corpus manifest finalize failed."
                self._finish_native_preparation(state, input_model, "blocked", reason, "Source corpus manifest finalize failed.")
                return
        state.source_corpus_reviewed = True
        state.source_corpus_ready = True
        state.position = FlowPosition(phase="prepare_source_index_child")

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
    release_candidate_ready: bool = False
    catalog_decl_count: int = 0
    bound_interface_count: int = 0
    imported_modules_count: int = 0


class AdapterRepoPreparationResult(LeanRenderableFlowResult):
    result_type: Literal["adapter_repo_preparation"] = "adapter_repo_preparation"
    outcome: Literal["adapter_ready_for_release", "adapter_release_prepared", "blocked", "invalid_input"]
    repo_key: str
    catalog_decl_count: int = 0
    bound_interface_count: int = 0
    imported_modules_count: int = 0
    blocked_reason: str | None = None
    missing_interfaces: list[str] = Field(default_factory=list)
    suggested_next_action: str | None = None
    prepared_release: PreparedRepoReleaseView | None = None

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
            "release_id": self.prepared_release.release.release_id if self.prepared_release else None,
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
        if not isinstance(result, MarkAdapterProviderReadyStepResult) or result.outcome not in {
            "ready_for_release",
            "candidate_prepared",
        }:
            return
        flow_result = self.result
        if not isinstance(flow_result, AdapterRepoPreparationResult) or flow_result.outcome not in {
            "adapter_ready_for_release",
            "adapter_release_prepared",
        }:
            return
        input_model = _require_adapter_preparation_input(self.input)
        repo_root = _adapter_repo_root(input_model)
        if result.outcome == "candidate_prepared":
            if result.prepared_release is None:
                _mark_flow_failed_from_stable_snapshot(
                    ctx,
                    "adapter_release_finalize_failed",
                    [ValueError("Prepared Adapter release payload is missing.")],
                )
                return
            validation_snapshot = getattr(ctx.app, "validation_snapshot", None)
            if validation_snapshot is None:
                _mark_flow_failed_from_stable_snapshot(
                    ctx,
                    "adapter_release_finalize_failed",
                    [ValueError("Release finalizer service is missing.")],
                )
                return
            from lean_constellation.flows.coordinator.release_runtime import (
                check_repo_release_runtime_closeout,
            )

            runtime_closeout = check_repo_release_runtime_closeout(
                validation_snapshot.runtime,
                repo_root,
                owner_flow_id=self.flow_id,
                phase="commit",
            )
            if not runtime_closeout.ok or runtime_closeout.value is None or not runtime_closeout.value.passed:
                issues = runtime_closeout.issues if not runtime_closeout.ok else runtime_closeout.value.issues
                _mark_flow_failed_from_stable_snapshot(
                    ctx,
                    "repo_release_runtime_not_closed",
                    list(issues),
                )
                return
            committed = validation_snapshot.commit_prepared_release(
                repo_root,
                prepared=result.prepared_release,
            )
            if not committed.ok:
                _mark_flow_failed_from_stable_snapshot(
                    ctx,
                    "adapter_release_finalize_failed",
                    list(committed.issues),
                )
                return
        _record_stable_repo_snapshot(
            ctx,
            repo_root,
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
        if result.outcome in {"ready_for_release", "candidate_prepared"}:
            state.release_candidate_ready = True
            state.position = FlowPosition(phase="completed")
            self.result = AdapterRepoPreparationResult(
                outcome=(
                    "adapter_release_prepared"
                    if result.outcome == "candidate_prepared"
                    else "adapter_ready_for_release"
                ),
                repo_key=input_model.repo_key,
                catalog_decl_count=state.catalog_decl_count,
                bound_interface_count=state.bound_interface_count,
                imported_modules_count=state.imported_modules_count,
                prepared_release=result.prepared_release,
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
    flow_state_field: str | None = None,
) -> None:
    snapshot = ctx.app.snapshot_runtime.create_repo_stable_point_snapshot(
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
    if flow_state_field is not None:
        def patch_flow(flow) -> None:  # noqa: ANN001
            setattr(flow.state, flow_state_field, snapshot.value.snapshot_id)

        ctx.ark.flow_service.store.update_flow_record(ctx.flow.flow_id, patch_flow)


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


def _native_children_for_dispatch(
    ctx: FlowReadContext | FlowContext,
    parent_flow_id: str,
    dispatch_step_id: str | None,
):
    if dispatch_step_id is None:
        return []
    flow_service = ctx.ark.flow_service
    store = getattr(flow_service, "store", None) if flow_service is not None else None
    if store is not None and hasattr(store, "list_child_flows"):
        return list(
            store.list_child_flows(
                parent_flow_id=parent_flow_id,
                parent_dispatch_step_id=dispatch_step_id,
            )
        )
    if flow_service is None:
        return []
    return [
        flow
        for flow in flow_service.list_flows()
        if flow.parent_flow_id == parent_flow_id
        and flow.parent_dispatch_step_id == dispatch_step_id
    ]


def _agent_env(agent_type: str, app_view: str, submit_view: str) -> dict[str, str]:
    return {
        "LEAN_CONSTELLATION_AGENT_TYPE": agent_type,
        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": app_view,
        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": submit_view,
    }


def _source_corpus_workdir(ctx: FlowContext, repo_root: Path) -> Path:
    return ctx.app.foundation.layout.source_corpus_draft_root(
        FoundationContext(repo_root=repo_root)
    )


def _source_corpus_builder_prompt(
    input_model: NativeRepoPreparationInput, *, logical_path: str, reviewer_feedback: str | None
) -> str:
    return "\n".join(
        [
            f"Build the source corpus candidate for native repo {input_model.repo_key}.",
            "Current working directory: the active Source draft root.",
            "Use _work only for acquired containers, extraction scratch, and previews; write the self-contained final candidate elsewhere in this draft.",
            f"Configured draft path: {logical_path}; the canonical destination is recorded in preparation input.",
            "Read and apply $faithful-material-preservation, $pdf-faithful-transcription, $material-fidelity-check, and $source-corpus-draft-curation.",
            "Treat each source_material_inputs target as a structured clue to resolve and verify the material identity.",
            "Faithfully enforce included_scope and role as boundaries; do not add related material that the inputs did not request.",
            "Read the repository preparation input through tools and submit builder ready or blocked.",
            f"Latest independent reviewer feedback: {reviewer_feedback}" if reviewer_feedback else "This is the initial Builder pass.",
        ]
    )


def _source_corpus_reviewer_prompt(
    input_model: NativeRepoPreparationInput,
    *,
    review_round: int,
    builder_summary: str,
    previous_feedback: str | None,
) -> str:
    return "\n".join(
        [
            f"Independently review the complete current Source draft candidate for native repo {input_model.repo_key}.",
            f"Review round: {review_round + 1}.",
            f"Builder/scan summary for orientation only: {builder_summary}",
            f"Previous findings to regress after the fresh pass: {previous_feedback}" if previous_feedback else "No previous reviewer findings.",
            "Treat _work artifacts only as comparison evidence: downstream readers receive only the candidate outside _work. Submit one approved or rejected fresh full-current decision.",
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
            "Register finalized adapter declarations and bind required interfaces.",
            "Use the catalog-ready preflight as the sole submission gate; later deterministic steps own visible modules, projection refresh, and final provider readiness.",
            "Submit blocked only for a current catalog-preflight failure that cannot be repaired within this Agent's permissions.",
        ]
    )


REPO_LIFECYCLE_FLOW_TYPES: tuple[type[LeanBusinessFlow], ...] = (
    RequirementGroupRepoBootstrapFlow,
    NativeRepoPreparationFlow,
    AdapterRepoPreparationFlow,
)
