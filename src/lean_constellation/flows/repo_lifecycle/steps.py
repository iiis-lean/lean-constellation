"""Repo lifecycle deterministic LogicStep implementations."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import (
    BaseStep,
    BaseStepResult,
    BaseStepState,
    ChildFlowDispatchSubmission,
    FlowRequest,
    FlowStepValidationError,
    StepTerminalReceipt,
)
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import (
    AdapterProviderRoute,
    AutoProviderRoute,
    NativeProviderRoute,
    ProviderRoute,
    RejectedUpstreamCandidate,
    RepoRequirementRef,
    SourceCorpusMode,
    UpstreamDependencyInput,
)
from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.repo_lifecycle.submissions import (
    NativeCoordinatorHandoffSubmission,
    RepoFormatAdapterChoiceSubmission,
    RepoFormatNativeChoiceSubmission,
)

if TYPE_CHECKING:
    from lean_constellation.flows.repo_lifecycle.flows import (
        AdapterRepoPreparationInput,
        NativeRepoPreparationInput,
        RequirementGroupRepoBootstrapInput,
    )


class RequirementBootstrapStepError(StrictModel):
    code: str
    message: str
    gate_summary: str | None = None
    suggested_fix: str | None = None


class BootstrapInputValidationStepResult(LeanRenderableStepResult):
    result_type: Literal["bootstrap_input_validation"] = "bootstrap_input_validation"
    outcome: Literal["passed", "needs_admin_repair"]
    requirement_count: int = 0
    source_corpus_mode: str | None = None
    error: RequirementBootstrapStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "requirement_count": self.requirement_count,
            "source_corpus_mode": self.source_corpus_mode,
            "error_code": self.error.code if self.error else None,
            "suggested_fix": self.error.suggested_fix if self.error else None,
        }


class ApplyRepoFormatChoiceStepResult(LeanRenderableStepResult):
    result_type: Literal["apply_repo_format_choice"] = "apply_repo_format_choice"
    outcome: Literal["adapter_initialized", "native_initialized", "needs_admin_repair"]
    repo_format: Literal["adapter", "native"] | None = None
    next_entry_flow: Literal["adapter_repo_preparation", "native_repo_preparation"] | None = None
    upstream_summary: str | None = None
    lake_check_summary: str | None = None
    snapshot_id: str | None = None
    error: RequirementBootstrapStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_format": self.repo_format,
            "next_entry_flow": self.next_entry_flow,
            "upstream_summary": self.upstream_summary,
            "lake_check_summary": self.lake_check_summary,
            "snapshot_id": self.snapshot_id,
            "error_code": self.error.code if self.error else None,
            "suggested_fix": self.error.suggested_fix if self.error else None,
        }


class RepoFormatDiscoveryStepResult(LeanRenderableStepResult):
    result_type: Literal["repo_format_discovery"] = "repo_format_discovery"
    outcome: Literal["adapter", "native", "incomplete"]
    selected_repo_format: Literal["adapter", "native"] | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "selected_repo_format": self.selected_repo_format,
            "incomplete_reason": self.incomplete_reason,
        }


class SourceCorpusPrepareStepResult(LeanRenderableStepResult):
    result_type: Literal["source_corpus_prepare"] = "source_corpus_prepare"
    outcome: Literal["prepared", "blocked", "incomplete"]
    relpath: str | None = None
    entry_path: str | None = None
    overview: str | None = None
    blocked_reason: str | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "relpath": self.relpath,
            "entry_path": self.entry_path,
            "blocked_reason": self.blocked_reason,
            "incomplete_reason": self.incomplete_reason,
        }


class SourceIndexBuilderStepResult(LeanRenderableStepResult):
    result_type: Literal["source_index_builder"] = "source_index_builder"
    outcome: Literal["submitted", "incomplete"]
    validation_summary: str | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "validation_summary": self.validation_summary,
            "incomplete_reason": self.incomplete_reason,
        }


class SourceIndexReviewerStepResult(LeanRenderableStepResult):
    result_type: Literal["source_index_reviewer"] = "source_index_reviewer"
    outcome: Literal["approved", "rejected", "incomplete"]
    feedback: str | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "feedback": self.feedback,
            "incomplete_reason": self.incomplete_reason,
        }


class RootInterfacePrepareStepResult(LeanRenderableStepResult):
    result_type: Literal["root_interface_prepare"] = "root_interface_prepare"
    outcome: Literal["ready", "incomplete"]
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {"outcome": self.outcome, "incomplete_reason": self.incomplete_reason}


class AdapterDeclCatalogStepResult(LeanRenderableStepResult):
    result_type: Literal["adapter_decl_catalog"] = "adapter_decl_catalog"
    outcome: Literal["ready", "blocked", "incomplete"]
    blocked_reason: str | None = None
    missing_interfaces: list[str] = Field(default_factory=list)
    suggested_next_action: str | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "blocked_reason": self.blocked_reason,
            "missing_interfaces": list(self.missing_interfaces),
            "suggested_next_action": self.suggested_next_action,
            "incomplete_reason": self.incomplete_reason,
        }


class ValidateBootstrapInputStep(BaseStep):
    step_type: ClassVar[str] = "validate_bootstrap_input_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = BootstrapInputValidationStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "bootstrap_input_validation": BootstrapInputValidationStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_requirement_bootstrap_flow(ctx)
        input_model = _require_bootstrap_input(flow.input)
        repo_workspace = _repo_workspace(ctx)
        refs = _parse_requirement_refs(input_model.requirement_refs)
        result = repo_workspace.validate_requirement_bootstrap_input(
            Path(input_model.repo_root),
            requirement_refs=refs or None,
        )
        if not result.ok or result.value is None:
            return ctx.complete_step(
                BootstrapInputValidationStepResult(
                    outcome="needs_admin_repair",
                    summary=_issue_summary(result.issues) or "Requirement bootstrap input validation failed.",
                    error=_error_from_issues(
                        result.issues,
                        fallback_code="invalid_preparation_input",
                        fallback_message="Requirement bootstrap input validation failed.",
                    ),
                )
            )
        view = result.value
        if not view.passed:
            return ctx.complete_step(
                BootstrapInputValidationStepResult(
                    outcome="needs_admin_repair",
                    requirement_count=view.requirement_count,
                    source_corpus_mode=str(view.source_corpus_mode) if view.source_corpus_mode else None,
                    summary=view.summary,
                    error=RequirementBootstrapStepError(
                        code=view.issue_code or "invalid_preparation_input",
                        message=view.summary,
                        suggested_fix=view.suggested_fix,
                    ),
                )
            )
        return ctx.complete_step(
            BootstrapInputValidationStepResult(
                outcome="passed",
                requirement_count=view.requirement_count,
                source_corpus_mode=str(view.source_corpus_mode) if view.source_corpus_mode else None,
                summary=view.summary,
            )
        )


class ApplyRepoFormatChoiceStep(BaseStep):
    step_type: ClassVar[str] = "apply_repo_format_choice_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ApplyRepoFormatChoiceStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "apply_repo_format_choice": ApplyRepoFormatChoiceStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_requirement_bootstrap_flow(ctx)
        input_model = _require_bootstrap_input(flow.input)
        route: ProviderRoute = input_model.resolved_provider_route
        if isinstance(route, AutoProviderRoute):
            submission = _latest_repo_format_submission(ctx, flow)
            if submission is None:
                return ctx.complete_step(
                    _repair_result(
                        "repo_format_choice_missing",
                        "Repo format choice submission is missing.",
                    )
                )
            if isinstance(submission, RepoFormatAdapterChoiceSubmission):
                if not submission.git_url.strip():
                    return ctx.complete_step(
                        _repair_result(
                            "adapter_choice_missing_upstream",
                            "Adapter choice must include git_url.",
                        )
                    )
                if not submission.revision:
                    return ctx.complete_step(
                        _repair_result(
                            "adapter_choice_revision_missing",
                            "Adapter choice must resolve an immutable Git commit before apply.",
                        )
                    )
                try:
                    route = AdapterProviderRoute(
                        git_url=submission.git_url,
                        revision=submission.revision,
                        subdir=submission.subdir,
                        package_name=submission.package_name,
                        likely_import_module=submission.likely_import_module,
                        evidence_summary=submission.evidence_summary,
                        known_risks=submission.known_risks,
                    )
                except ValueError as exc:
                    return ctx.complete_step(
                        _repair_result("adapter_choice_invalid", str(exc))
                    )
            elif isinstance(submission, RepoFormatNativeChoiceSubmission):
                try:
                    route = NativeProviderRoute(
                        evidence_summary=submission.summary
                        or "Repo format discovery selected a native provider.",
                        searched_targets=submission.searched_targets,
                        rejected_candidates=[
                            RejectedUpstreamCandidate.model_validate(
                                candidate.model_dump(mode="json")
                            )
                            for candidate in submission.rejected_candidates
                        ],
                    )
                except ValueError as exc:
                    return ctx.complete_step(
                        _repair_result("native_choice_invalid", str(exc))
                    )
            else:
                return ctx.complete_step(
                    _repair_result(
                        "unknown_repo_format_choice",
                        f"Unsupported repo format submission: {submission.submission_type}",
                    )
                )

        repo_workspace = _repo_workspace(ctx)
        repo_root = Path(input_model.repo_root)
        if isinstance(route, AdapterProviderRoute):
            prepared_input = repo_workspace.preparation.get_preparation_input(repo_root)
            if not prepared_input.ok or prepared_input.value is None:
                return ctx.complete_step(
                    _repair_result_from_issues(
                        prepared_input.issues,
                        fallback_code="adapter_preparation_input_missing",
                        fallback_message="Adapter repo preparation input is missing.",
                    )
                )
            rewritten_input = prepared_input.value.input.model_copy(
                update={
                    "source_corpus_mode": SourceCorpusMode.NONE,
                    "source_corpus_relpath": None,
                }
            )
            written_input = repo_workspace.preparation.write_preparation_input(repo_root, input=rewritten_input)
            if not written_input.ok:
                return ctx.complete_step(
                    _repair_result_from_issues(
                        written_input.issues,
                        fallback_code="adapter_preparation_input_rewrite_failed",
                        fallback_message="Adapter repo preparation input rewrite failed.",
                    )
                )
            upstream = UpstreamDependencyInput(
                git_url=route.git_url,
                revision=route.revision,
                subdir=route.subdir,
                package_name=route.package_name,
                module_name=route.likely_import_module,
                evidence_summary=route.evidence_summary,
                known_risks=route.known_risks,
            )
            initialized = repo_workspace.initialize_repo_as_adapter(
                repo_root,
                upstream=upstream,
                project_name=input_model.target_repo,
            )
            if not initialized.ok or initialized.value is None:
                return ctx.complete_step(
                    _repair_result_from_issues(
                        initialized.issues,
                        fallback_code="adapter_lake_setup_failed",
                        fallback_message="Adapter repo skeleton initialization failed.",
                    )
                )
            value = initialized.value
            return ctx.complete_step(
                ApplyRepoFormatChoiceStepResult(
                    outcome="adapter_initialized",
                    repo_format="adapter",
                    next_entry_flow="adapter_repo_preparation",
                    upstream_summary=value.upstream_summary,
                    lake_check_summary=value.lake_check_summary,
                    summary=value.summary,
                )
            )

        if isinstance(route, NativeProviderRoute):
            initialized = repo_workspace.initialize_repo_as_native(
                repo_root,
                project_name=input_model.target_repo,
            )
            if not initialized.ok or initialized.value is None:
                return ctx.complete_step(
                    _repair_result_from_issues(
                        initialized.issues,
                        fallback_code="native_lake_setup_failed",
                        fallback_message="Native repo skeleton initialization failed.",
                    )
                )
            value = initialized.value
            return ctx.complete_step(
                ApplyRepoFormatChoiceStepResult(
                    outcome="native_initialized",
                    repo_format="native",
                    next_entry_flow="native_repo_preparation",
                    lake_check_summary=value.lake_check_summary,
                    summary=value.summary,
                )
            )

        return ctx.complete_step(
            _repair_result("unknown_repo_format_choice", "Unsupported provider route.")
        )


class NativePreparationStepError(StrictModel):
    code: str
    message: str
    gate_summary: str | None = None
    suggested_fix: str | None = None


class ValidateAndInitializeNativePreparationStepResult(LeanRenderableStepResult):
    result_type: Literal["native_validate_initialize"] = "native_validate_initialize"
    outcome: Literal["initialized", "invalid_input", "blocked"]
    repo_key: str
    source_corpus_mode: Literal["existing", "prepare"] | None = None
    allow_interface_supplement: bool | None = None
    input_interface_count: int = 0
    main_goal_initialized: bool = False
    main_boundary_initialized: bool = False
    main_objective_initialized: bool = False
    protected_interfaces_synced: bool = False
    pre_run_mutation_checkpoint_id: str | None = None
    error: NativePreparationStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "source_corpus_mode": self.source_corpus_mode,
            "allow_interface_supplement": self.allow_interface_supplement,
            "input_interface_count": self.input_interface_count,
            "error_code": self.error.code if self.error else None,
            "suggested_fix": self.error.suggested_fix if self.error else None,
        }


class ExistingSourceCorpusScanStepResult(LeanRenderableStepResult):
    result_type: Literal["existing_source_corpus_scan"] = "existing_source_corpus_scan"
    outcome: Literal["ready", "blocked"]
    relpath: str
    entry_path: str | None = None
    file_count: int = 0
    text_file_count: int = 0
    binary_file_count: int = 0
    overview: str | None = None
    error: NativePreparationStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "relpath": self.relpath,
            "entry_path": self.entry_path,
            "file_count": self.file_count,
            "text_file_count": self.text_file_count,
            "binary_file_count": self.binary_file_count,
            "error_code": self.error.code if self.error else None,
        }


class HandoffGateStepResult(LeanRenderableStepResult):
    result_type: Literal["native_handoff_gate"] = "native_handoff_gate"
    outcome: Literal["passed", "blocked", "invalid_input"]
    checked_condition_count: int = 0
    missing_conditions: list[str] = Field(default_factory=list)
    error: NativePreparationStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "checked_condition_count": self.checked_condition_count,
            "missing_conditions": list(self.missing_conditions),
            "error_code": self.error.code if self.error else None,
        }


class PrepareCoordinatorDispatchStepResult(LeanRenderableStepResult):
    result_type: Literal["prepare_coordinator_dispatch"] = "prepare_coordinator_dispatch"
    outcome: Literal["prepared", "blocked"]
    request_count: int = 0
    continuation: Literal["terminal_handoff"] = "terminal_handoff"
    target_flow_type: Literal["native_repo_coordinator"] = "native_repo_coordinator"
    snapshot_id: str | None = None
    error: NativePreparationStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "request_count": self.request_count,
            "continuation": self.continuation,
            "target_flow_type": self.target_flow_type,
            "snapshot_id": self.snapshot_id,
            "error_code": self.error.code if self.error else None,
        }


class PrepareNativeLifecycleChildStepResult(LeanRenderableStepResult):
    result_type: Literal["prepare_native_lifecycle_child"] = "prepare_native_lifecycle_child"
    outcome: Literal["prepared", "blocked"]
    child_kind: Literal["source_index", "root_interface"] | None = None
    error: NativePreparationStepError | None = None


class PrepareNativeLifecycleChildStep(BaseStep):
    """Persist a standard callback dispatch request for a reusable preparation child."""

    step_type: ClassVar[str] = "prepare_native_lifecycle_child_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = PrepareNativeLifecycleChildStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "prepare_native_lifecycle_child": PrepareNativeLifecycleChildStepResult,
    }
    Submissions: ClassVar[dict[str, type[ChildFlowDispatchSubmission]]] = {
        "child_flow_dispatch": ChildFlowDispatchSubmission,
    }
    SubmitTools: ClassVar[set[str] | None] = {"prepare_native_lifecycle_child"}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_preparation_flow(ctx)
        input_model = _require_native_preparation_input(flow.input)
        state = flow.state
        run_spec = input_model.run_spec
        checkpoint_id = getattr(state, "pre_run_mutation_checkpoint_id", None)
        if not checkpoint_id:
            return ctx.complete_step(
                PrepareNativeLifecycleChildStepResult(
                    outcome="blocked",
                    error=NativePreparationStepError(
                        code="native_child_run_context_missing",
                        message="Native preparation child dispatch requires RepoRunSpec and a pre-mutation checkpoint.",
                    ),
                    summary="Native preparation child dispatch is missing run context.",
                )
            )
        repo_root = _native_repo_root(input_model)
        if state.position.phase == "prepare_source_index_child":
            child_kind: Literal["source_index", "root_interface"] = "source_index"
            recovery = input_model.recovery
            request = FlowRequest(
                flow_type="source_index_build",
                scope_id=ctx.scope_id,
                params={
                    "repo_key": input_model.repo_key,
                    "repo_root": str(repo_root),
                    "run_objective": run_spec.run_objective,
                    "source_scope": run_spec.source_scope.model_dump(mode="json"),
                    "index_policy": run_spec.index_policy,
                    "start_reason": "recovery" if recovery is not None else "initial",
                    "max_review_rounds": recovery.max_review_rounds if recovery is not None else 3,
                    "pre_update_checkpoint_id": checkpoint_id,
                    "recovery": recovery.model_dump(mode="json") if recovery is not None else None,
                },
            )
        elif state.position.phase == "prepare_root_interface_child":
            from lean_constellation.domain.repo_run import RepoRunContext

            source_result = getattr(state, "source_index_child_result", None)
            if source_result is None:
                return ctx.complete_step(
                    PrepareNativeLifecycleChildStepResult(
                        outcome="blocked",
                        error=NativePreparationStepError(
                            code="source_index_child_result_missing",
                            message="Root-interface child dispatch requires the terminal SourceIndex child result.",
                        ),
                        summary="SourceIndex child result is missing.",
                    )
                )
            child_kind = "root_interface"
            run_context = RepoRunContext(
                start_kind="initial",
                run_spec=run_spec,
                resolved_source_files=list(source_result.resolved_file_paths),
                source_index_delta_summary=source_result.summary,
            )
            request = FlowRequest(
                flow_type="root_interface_preparation",
                scope_id=ctx.scope_id,
                params={
                    "repo_key": input_model.repo_key,
                    "repo_root": str(repo_root),
                    "run_context": run_context.model_dump(mode="json"),
                    "source_index_delta": source_result.model_dump(mode="json"),
                    "start_reason": "initial",
                    "pre_run_mutation_checkpoint_id": checkpoint_id,
                },
            )
        else:
            raise FlowStepValidationError(
                f"PrepareNativeLifecycleChildStep cannot run in phase {state.position.phase}"
            )
        submission = ChildFlowDispatchSubmission(
            submission_id=new_submission_id(f"native_{child_kind}_child"),
            submission_type="child_flow_dispatch",
            tool_name="prepare_native_lifecycle_child",
            summary=f"Dispatch native preparation {child_kind} child Flow.",
            requests=[request],
            continuation="wait_for_callback",
        )
        ctx.accept_step_submission(submission)
        return ctx.complete_step(
            PrepareNativeLifecycleChildStepResult(
                outcome="prepared",
                child_kind=child_kind,
                summary=submission.summary,
            )
        )


class ValidateAndInitializeNativePreparationStep(BaseStep):
    step_type: ClassVar[str] = "validate_initialize_native_preparation_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ValidateAndInitializeNativePreparationStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "native_validate_initialize": ValidateAndInitializeNativePreparationStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_preparation_flow(ctx)
        input_model = _require_native_preparation_input(flow.input)
        repo_root = _native_repo_root(input_model)
        repo_workspace = _repo_workspace(ctx)
        preflight = repo_workspace.get_preparation_start_preflight(repo_root, expected_format="native")
        if not preflight.ok or preflight.value is None:
            return ctx.complete_step(
                _native_validate_result_from_issues(
                    input_model.repo_key,
                    preflight.issues,
                    outcome="invalid_input",
                    fallback_code="native_preparation_preflight_failed",
                    fallback_message="Native preparation preflight failed.",
                )
            )
        if not preflight.value.passed:
            invalid_kinds = {
                "preparation_input_missing",
                "preparation_input_invalid",
                "preparation_start_format_mismatch",
                "preparation_start_native_source_corpus_none",
                "invalid_source_corpus_mode",
            }
            outcome: Literal["invalid_input", "blocked"] = (
                "invalid_input"
                if any(getattr(issue, "kind", "") in invalid_kinds for issue in preflight.value.issues)
                else "blocked"
            )
            return ctx.complete_step(
                _native_validate_result_from_issues(
                    input_model.repo_key,
                    preflight.value.issues,
                    outcome=outcome,
                    fallback_code="native_preparation_preflight_failed",
                    fallback_message=preflight.value.summary,
                )
            )

        preparation = repo_workspace.preparation.get_preparation_input(repo_root)
        if not preparation.ok or preparation.value is None:
            return ctx.complete_step(
                _native_validate_result_from_issues(
                    input_model.repo_key,
                    preparation.issues,
                    outcome="invalid_input",
                    fallback_code="missing_preparation_input",
                    fallback_message="Preparation input is missing or invalid.",
                )
            )
        prep_input = preparation.value.input
        source_mode = prep_input.source_corpus_mode
        if source_mode == SourceCorpusMode.NONE:
            return ctx.complete_step(
                _native_validate_result(
                    input_model.repo_key,
                    outcome="invalid_input",
                    code="invalid_source_corpus_mode",
                    message="Native preparation requires source_corpus_mode to be existing or prepare.",
                )
            )

        transition = repo_workspace.run.validate_repo_run_transition(
            repo_root, run_spec=input_model.run_spec, start_kind="initial", base_release_id=None
        )
        if not transition.ok or transition.value is None or not transition.value.passed:
            issues = transition.value.issues if transition.value is not None else transition.issues
            return ctx.complete_step(
                _native_validate_result_from_issues(
                    input_model.repo_key, issues, outcome="blocked",
                    fallback_code="initial_repo_run_transition_invalid",
                    fallback_message="Initial RepoRunSpec transition is invalid.",
                )
            )
        applied = repo_workspace.run.apply_repo_run_config(
            repo_root, run_spec=input_model.run_spec, expected_base_release_id=None
        )
        if not applied.ok:
            return ctx.complete_step(
                _native_validate_result_from_issues(
                    input_model.repo_key, applied.issues, outcome="blocked",
                    fallback_code="initial_repo_run_config_apply_failed",
                    fallback_message="Initial RepoRunSpec config could not be applied.",
                )
            )

        node = _node(ctx)
        initialized = node.ensure_native_root_main_contract(repo_root)
        if not initialized.ok or initialized.value is None:
            return ctx.complete_step(
                _native_validate_result_from_issues(
                    input_model.repo_key,
                    initialized.issues,
                    outcome="blocked",
                    fallback_code="main_contract_init_failed",
                    fallback_message="Root Main contract initialization failed.",
                )
            )

        contract = initialized.value.contract
        return ctx.complete_step(
            ValidateAndInitializeNativePreparationStepResult(
                outcome="initialized",
                repo_key=input_model.repo_key,
                source_corpus_mode=source_mode.value if source_mode in {SourceCorpusMode.EXISTING, SourceCorpusMode.PREPARE} else None,
                allow_interface_supplement=prep_input.allow_interface_supplement,
                input_interface_count=len(prep_input.interface_inputs),
                main_goal_initialized=bool(contract.goal.strip()),
                main_boundary_initialized=bool(contract.boundary.strip()),
                main_objective_initialized=bool(contract.objective and contract.objective.strip()),
                protected_interfaces_synced=True,
                pre_run_mutation_checkpoint_id=f"repo-{uuid.uuid4().hex}",
                summary="Native preparation input and root Main contract initialized.",
            )
        )


class ExistingSourceCorpusScanStep(BaseStep):
    step_type: ClassVar[str] = "existing_source_corpus_scan_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ExistingSourceCorpusScanStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "existing_source_corpus_scan": ExistingSourceCorpusScanStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_preparation_flow(ctx)
        input_model = _require_native_preparation_input(flow.input)
        repo_root = _native_repo_root(input_model)
        preparation = _repo_workspace(ctx).preparation.get_preparation_input(repo_root)
        relpath = ".lean_constellation/source"
        if preparation.ok and preparation.value is not None:
            relpath = preparation.value.input.source_corpus_relpath or relpath
        material = _material(ctx)
        prepared = material.prepare_existing_source_corpus(repo_root, relpath=relpath)
        if not prepared.ok or prepared.value is None:
            return ctx.complete_step(
                ExistingSourceCorpusScanStepResult(
                    outcome="blocked",
                    relpath=relpath,
                    summary=_issue_summary(prepared.issues) or "Existing source corpus preflight failed.",
                    error=_native_error_from_issues(
                        prepared.issues,
                        fallback_code="source_corpus_preflight_failed",
                        fallback_message="Existing source corpus preflight failed.",
                    ),
                )
            )
        manifest = prepared.value
        text_count = sum(1 for item in manifest.files if getattr(item, "readable_text", False))
        return ctx.complete_step(
            ExistingSourceCorpusScanStepResult(
                outcome="ready",
                relpath=manifest.relpath,
                entry_path=manifest.entry_path,
                file_count=len(manifest.files),
                text_file_count=text_count,
                binary_file_count=len(manifest.files) - text_count,
                overview=manifest.overview,
                summary=manifest.summary,
            )
        )


class HandoffGateStep(BaseStep):
    step_type: ClassVar[str] = "native_handoff_gate_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = HandoffGateStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {"native_handoff_gate": HandoffGateStepResult}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_preparation_flow(ctx)
        input_model = _require_native_preparation_input(flow.input)
        repo_root = _native_repo_root(input_model)
        gate = _validation_snapshot(ctx).readiness_gate.check_native_handoff_gate(repo_root)
        if not gate.ok or gate.value is None:
            return ctx.complete_step(
                HandoffGateStepResult(
                    outcome="invalid_input",
                    summary=_issue_summary(gate.issues) or "Native handoff gate failed.",
                    error=_native_error_from_issues(
                        gate.issues,
                        fallback_code="handoff_gate_failed",
                        fallback_message="Native handoff gate failed.",
                    ),
                )
            )
        value = gate.value
        if not value.passed:
            invalid_kinds = {"native_handoff_repo_format_invalid", "native_handoff_source_corpus_missing"}
            outcome: Literal["blocked", "invalid_input"] = (
                "invalid_input"
                if any(getattr(issue, "kind", "") in invalid_kinds for issue in value.issues)
                else "blocked"
            )
            return ctx.complete_step(
                HandoffGateStepResult(
                    outcome=outcome,
                    checked_condition_count=1,
                    missing_conditions=[str(getattr(issue, "kind", issue)) for issue in value.issues],
                    summary=value.summary or "Native handoff gate failed.",
                    error=_native_error_from_issues(
                        value.issues,
                        fallback_code="handoff_gate_failed",
                        fallback_message=value.summary or "Native handoff gate failed.",
                    ),
                )
            )
        return ctx.complete_step(
            HandoffGateStepResult(
                outcome="passed",
                checked_condition_count=1,
                missing_conditions=[],
                summary=value.summary or "Native handoff gate passed.",
            )
        )


class PrepareCoordinatorDispatchStep(BaseStep):
    step_type: ClassVar[str] = "prepare_coordinator_dispatch_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = PrepareCoordinatorDispatchStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "prepare_coordinator_dispatch": PrepareCoordinatorDispatchStepResult,
    }
    Submissions: ClassVar[dict[str, type[NativeCoordinatorHandoffSubmission]]] = {
        "native_coordinator_handoff": NativeCoordinatorHandoffSubmission,
    }
    SubmitTools: ClassVar[set[str] | None] = {"prepare_native_coordinator_handoff"}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_preparation_flow(ctx)
        input_model = _require_native_preparation_input(flow.input)
        repo_root = _native_repo_root(input_model)
        from lean_constellation.domain.repo_run import RepoRunContext

        state = flow.state
        source_result = getattr(state, "source_index_child_result", None)
        root_result = getattr(state, "root_interface_child_result", None)
        run_context = RepoRunContext(
            start_kind="initial",
            run_spec=input_model.run_spec,
            resolved_source_files=list(source_result.resolved_file_paths) if source_result is not None else [],
            source_index_delta_summary=source_result.summary if source_result is not None else None,
            root_interface_delta_summary=root_result.summary if root_result is not None else None,
            config_change_summary=(
                f"completion_mode={input_model.run_spec.completion_mode.value}"
            ),
        )
        request = FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id=ctx.scope_id,
            params={
                "repo_key": input_model.repo_key,
                "repo_root": str(repo_root),
                "start_mode": "native_preparation_handoff",
                "start_reason": "Native preparation handoff.",
                "run_context": run_context.model_dump(mode="json"),
            },
        )
        summary = f"Native preparation for {input_model.repo_key} is ready for coordinator handoff."
        submission = NativeCoordinatorHandoffSubmission(
            submission_id=new_submission_id("native_handoff"),
            submission_type="native_coordinator_handoff",
            tool_name="prepare_native_coordinator_handoff",
            summary=summary,
            repo_key=input_model.repo_key,
            requests=[request],
            continuation="terminal_handoff",
            handoff_summary=summary,
        )
        ctx.accept_step_submission(submission)
        return ctx.complete_step(
            PrepareCoordinatorDispatchStepResult(
                outcome="prepared",
                request_count=1,
                summary=f"{summary} Checkpoint will be created after the step reaches a stable terminal state.",
            )
        )


class AdapterPreparationStepError(StrictModel):
    code: str
    message: str
    gate_summary: str | None = None
    suggested_fix: str | None = None


class AdapterInputValidationStepResult(LeanRenderableStepResult):
    result_type: Literal["adapter_input_validation"] = "adapter_input_validation"
    outcome: Literal["passed", "invalid_input", "blocked"]
    upstream_summary: str | None = None
    error: AdapterPreparationStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "upstream_summary": self.upstream_summary,
            "error_code": self.error.code if self.error else None,
            "suggested_fix": self.error.suggested_fix if self.error else None,
        }


class EnsureAdapterMainCatalogStepResult(LeanRenderableStepResult):
    result_type: Literal["ensure_adapter_main_catalog"] = "ensure_adapter_main_catalog"
    outcome: Literal["ready", "blocked"]
    root_contract_created: bool = False
    interface_count: int = 0
    active_decl_count: int = 0
    error: AdapterPreparationStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "interface_count": self.interface_count,
            "active_decl_count": self.active_decl_count,
            "error_code": self.error.code if self.error else None,
        }


class FinalizeAdapterReadyStepResult(LeanRenderableStepResult):
    result_type: Literal["finalize_adapter_ready"] = "finalize_adapter_ready"
    outcome: Literal["ready", "invalid_input", "blocked"]
    projection_refreshed: bool = False
    ready_gate_passed: bool = False
    catalog_decl_count: int = 0
    bound_interface_count: int = 0
    imported_modules_count: int = 0
    gate_summary: str | None = None
    error: AdapterPreparationStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "projection_refreshed": self.projection_refreshed,
            "ready_gate_passed": self.ready_gate_passed,
            "catalog_decl_count": self.catalog_decl_count,
            "bound_interface_count": self.bound_interface_count,
            "imported_modules_count": self.imported_modules_count,
            "error_code": self.error.code if self.error else None,
        }


class MarkAdapterProviderReadyStepResult(LeanRenderableStepResult):
    result_type: Literal["mark_adapter_provider_ready"] = "mark_adapter_provider_ready"
    outcome: Literal["marked_ready", "blocked"]
    provider_ready_marked: bool = False
    satisfied_requirement_count: int = 0
    repo_summary: str | None = None
    snapshot_id: str | None = None
    error: AdapterPreparationStepError | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "provider_ready_marked": self.provider_ready_marked,
            "satisfied_requirement_count": self.satisfied_requirement_count,
            "repo_summary": self.repo_summary,
            "snapshot_id": self.snapshot_id,
            "error_code": self.error.code if self.error else None,
        }


class ValidateAdapterPreparationInputStep(BaseStep):
    step_type: ClassVar[str] = "validate_adapter_preparation_input_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = AdapterInputValidationStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {"adapter_input_validation": AdapterInputValidationStepResult}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_adapter_preparation_flow(ctx)
        input_model = _require_adapter_preparation_input(flow.input)
        validated = _adapter(ctx).validate_adapter_preparation_input(_adapter_repo_root(input_model))
        if not validated.ok or validated.value is None:
            return ctx.complete_step(
                AdapterInputValidationStepResult(
                    outcome="invalid_input",
                    summary=_issue_summary(validated.issues) or "Adapter preparation input validation failed.",
                    error=_adapter_error_from_issues(
                        validated.issues,
                        fallback_code="invalid_adapter_preparation_input",
                        fallback_message="Adapter preparation input validation failed.",
                    ),
                )
            )
        value = validated.value
        if value.outcome != "passed":
            return ctx.complete_step(
                AdapterInputValidationStepResult(
                    outcome=value.outcome,
                    upstream_summary=value.upstream_summary,
                    summary=value.summary,
                    error=AdapterPreparationStepError(
                        code=value.issue_code or "adapter_preparation_input_not_ready",
                        message=value.summary,
                        suggested_fix=value.suggested_fix,
                    ),
                )
            )
        return ctx.complete_step(
            AdapterInputValidationStepResult(
                outcome="passed",
                upstream_summary=value.upstream_summary,
                summary=value.summary,
            )
        )


class EnsureAdapterMainCatalogStep(BaseStep):
    step_type: ClassVar[str] = "ensure_adapter_main_catalog_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = EnsureAdapterMainCatalogStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "ensure_adapter_main_catalog": EnsureAdapterMainCatalogStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_adapter_preparation_flow(ctx)
        input_model = _require_adapter_preparation_input(flow.input)
        repo_root = _adapter_repo_root(input_model)
        adapter = _adapter(ctx)
        ensured = adapter.ensure_flat_main_catalog(repo_root)
        if not ensured.ok or ensured.value is None:
            return ctx.complete_step(
                EnsureAdapterMainCatalogStepResult(
                    outcome="blocked",
                    summary=_issue_summary(ensured.issues) or "Adapter Main catalog initialization failed.",
                    error=_adapter_error_from_issues(
                        ensured.issues,
                        fallback_code="adapter_catalog_init_failed",
                        fallback_message="Adapter Main catalog initialization failed.",
                    ),
                )
            )
        input_view = adapter.inspect_adapter_input(repo_root)
        decls = adapter.list_adapter_decls(repo_root)
        return ctx.complete_step(
            EnsureAdapterMainCatalogStepResult(
                outcome="ready",
                root_contract_created=True,
                interface_count=input_view.value.interface_count if input_view.ok and input_view.value is not None else 0,
                active_decl_count=len(decls.value) if decls.ok and decls.value is not None else 0,
                summary=ensured.value.summary,
            )
        )


class FinalizeAdapterReadyStep(BaseStep):
    step_type: ClassVar[str] = "finalize_adapter_ready_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = FinalizeAdapterReadyStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {"finalize_adapter_ready": FinalizeAdapterReadyStepResult}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_adapter_preparation_flow(ctx)
        input_model = _require_adapter_preparation_input(flow.input)
        repo_root = _adapter_repo_root(input_model)
        adapter = _adapter(ctx)
        refreshed = adapter.refresh_adapter_projection(repo_root)
        if not refreshed.ok or refreshed.value is None:
            return ctx.complete_step(
                FinalizeAdapterReadyStepResult(
                    outcome="blocked",
                    summary=_issue_summary(refreshed.issues) or "Adapter projection refresh failed.",
                    error=_adapter_error_from_issues(
                        refreshed.issues,
                        fallback_code="adapter_projection_refresh_failed",
                        fallback_message="Adapter projection refresh failed.",
                    ),
                )
            )
        gate = adapter.check_adapter_ready(repo_root)
        modules = adapter.preview_adapter_import_modules(repo_root)
        decls = adapter.list_adapter_decls(repo_root)
        bindings = adapter.validate_adapter_interface_bindings(repo_root)
        bound_count = 0
        if bindings.ok and bindings.value is not None and bindings.value.passed:
            input_view = adapter.inspect_adapter_input(repo_root)
            bound_count = input_view.value.interface_count if input_view.ok and input_view.value is not None else 0
        if not gate.ok or gate.value is None:
            return ctx.complete_step(
                FinalizeAdapterReadyStepResult(
                    outcome="blocked",
                    projection_refreshed=True,
                    summary=_issue_summary(gate.issues) or "Adapter ready gate failed.",
                    error=_adapter_error_from_issues(
                        gate.issues,
                        fallback_code="adapter_ready_gate_failed",
                        fallback_message="Adapter ready gate failed.",
                    ),
                )
            )
        if not gate.value.passed:
            invalid_kinds = {"repo_format_not_adapter", "adapter_upstream_missing", "adapter_source_corpus_mode_invalid"}
            outcome: Literal["invalid_input", "blocked"] = (
                "invalid_input"
                if any(getattr(issue, "kind", "") in invalid_kinds for issue in gate.value.issues)
                else "blocked"
            )
            return ctx.complete_step(
                FinalizeAdapterReadyStepResult(
                    outcome=outcome,
                    projection_refreshed=True,
                    catalog_decl_count=len(decls.value) if decls.ok and decls.value is not None else 0,
                    bound_interface_count=bound_count,
                    imported_modules_count=modules.value.module_count if modules.ok and modules.value is not None else 0,
                    gate_summary=gate.value.summary,
                    summary=gate.value.summary or "Adapter ready gate failed.",
                    error=_adapter_error_from_issues(
                        gate.value.issues,
                        fallback_code="adapter_ready_gate_failed",
                        fallback_message=gate.value.summary or "Adapter ready gate failed.",
                    ),
                )
            )
        return ctx.complete_step(
            FinalizeAdapterReadyStepResult(
                outcome="ready",
                projection_refreshed=True,
                ready_gate_passed=True,
                catalog_decl_count=len(decls.value) if decls.ok and decls.value is not None else 0,
                bound_interface_count=bound_count,
                imported_modules_count=modules.value.module_count if modules.ok and modules.value is not None else 0,
                gate_summary=gate.value.summary,
                summary=gate.value.summary or "Adapter ready gate passed.",
            )
        )


class MarkAdapterProviderReadyStep(BaseStep):
    step_type: ClassVar[str] = "mark_adapter_provider_ready_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = MarkAdapterProviderReadyStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "mark_adapter_provider_ready": MarkAdapterProviderReadyStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_adapter_preparation_flow(ctx)
        input_model = _require_adapter_preparation_input(flow.input)
        repo_root = _adapter_repo_root(input_model)
        summary = f"Adapter provider repo {input_model.repo_key} is ready."
        marked = _repo_workspace(ctx).mark_provider_repo_ready(repo_root, summary=summary)
        if not marked.ok or marked.value is None:
            return ctx.complete_step(
                MarkAdapterProviderReadyStepResult(
                    outcome="blocked",
                    summary=_issue_summary(marked.issues) or "Adapter provider ready mark failed.",
                    error=_adapter_error_from_issues(
                        marked.issues,
                        fallback_code="provider_ready_mark_failed",
                        fallback_message="Adapter provider ready mark failed.",
                    ),
                )
            )
        return ctx.complete_step(
            MarkAdapterProviderReadyStepResult(
                outcome="marked_ready",
                provider_ready_marked=True,
                satisfied_requirement_count=marked.value.satisfied_requirement_count,
                repo_summary=marked.value.repo_summary,
                summary=marked.value.summary,
            )
        )


def new_repo_lifecycle_step_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _repo_workspace(ctx: StepRunContext):
    repo_workspace = getattr(ctx.app, "repo_workspace", None)
    if repo_workspace is None:
        raise FlowStepValidationError("Lean repo_workspace service is not registered in app services.")
    return repo_workspace


def _material(ctx: StepRunContext):
    material = getattr(ctx.app, "material", None)
    if material is None:
        raise FlowStepValidationError("Lean material service is not registered in app services.")
    return material


def _node(ctx: StepRunContext):
    node = getattr(ctx.app, "node", None)
    if node is None:
        raise FlowStepValidationError("Lean node service is not registered in app services.")
    return node


def _validation_snapshot(ctx: StepRunContext):
    validation_snapshot = getattr(ctx.app, "validation_snapshot", None)
    if validation_snapshot is None:
        raise FlowStepValidationError("Lean validation_snapshot service is not registered in app services.")
    return validation_snapshot


def _adapter(ctx: StepRunContext):
    adapter = getattr(ctx.app, "adapter", None)
    if adapter is None:
        raise FlowStepValidationError("Lean adapter service is not registered in app services.")
    return adapter


def _load_requirement_bootstrap_flow(ctx: StepRunContext):
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    flow = flow_service.get_flow(ctx.flow_id)
    if flow.flow_type != "requirement_group_repo_bootstrap":
        raise FlowStepValidationError(f"expected requirement_group_repo_bootstrap flow, got {flow.flow_type}")
    return flow


def _load_native_preparation_flow(ctx: StepRunContext):
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    flow = flow_service.get_flow(ctx.flow_id)
    if flow.flow_type != "native_repo_preparation":
        raise FlowStepValidationError(f"expected native_repo_preparation flow, got {flow.flow_type}")
    return flow


def _load_adapter_preparation_flow(ctx: StepRunContext):
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    flow = flow_service.get_flow(ctx.flow_id)
    if flow.flow_type != "adapter_repo_preparation":
        raise FlowStepValidationError(f"expected adapter_repo_preparation flow, got {flow.flow_type}")
    return flow


def _require_bootstrap_input(value: object) -> RequirementGroupRepoBootstrapInput:
    from lean_constellation.flows.repo_lifecycle.flows import RequirementGroupRepoBootstrapInput

    if not isinstance(value, RequirementGroupRepoBootstrapInput):
        raise FlowStepValidationError("RequirementGroupRepoBootstrapFlow has invalid input model.")
    return value


def _require_native_preparation_input(value: object) -> NativeRepoPreparationInput:
    from lean_constellation.flows.repo_lifecycle.flows import NativeRepoPreparationInput

    if not isinstance(value, NativeRepoPreparationInput):
        raise FlowStepValidationError("NativeRepoPreparationFlow has invalid input model.")
    return value


def _require_adapter_preparation_input(value: object) -> AdapterRepoPreparationInput:
    from lean_constellation.flows.repo_lifecycle.flows import AdapterRepoPreparationInput

    if not isinstance(value, AdapterRepoPreparationInput):
        raise FlowStepValidationError("AdapterRepoPreparationFlow has invalid input model.")
    return value


def _native_repo_root(input_model: NativeRepoPreparationInput) -> Path:
    repo_root = getattr(input_model, "repo_root", None)
    return Path(repo_root or input_model.repo_key)


def _adapter_repo_root(input_model: AdapterRepoPreparationInput) -> Path:
    repo_root = getattr(input_model, "repo_root", None)
    return Path(repo_root or input_model.repo_key)


def _parse_requirement_refs(values: list[str]) -> list[RepoRequirementRef]:
    refs: list[RepoRequirementRef] = []
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        if ":" in text:
            consumer_repo, requirement_name = text.split(":", 1)
        elif "/" in text:
            consumer_repo, requirement_name = text.split("/", 1)
        else:
            raise FlowStepValidationError(
                "requirement_refs entries must use 'consumer:requirement' or 'consumer/requirement' format."
            )
        refs.append(RepoRequirementRef(consumer_repo=consumer_repo, requirement_name=requirement_name))
    return refs


def _latest_repo_format_submission(ctx: StepRunContext, flow: object):
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    step_ids = list(getattr(flow, "step_ids", []))
    for step_id in reversed(step_ids):
        if step_id == ctx.step_id:
            continue
        step = flow_service.get_step(step_id)
        submission = step.submission
        if isinstance(submission, (RepoFormatAdapterChoiceSubmission, RepoFormatNativeChoiceSubmission)):
            return submission
    return None


def _repair_result(code: str, message: str, *, suggested_fix: str | None = None) -> ApplyRepoFormatChoiceStepResult:
    return ApplyRepoFormatChoiceStepResult(
        outcome="needs_admin_repair",
        summary=message,
        error=RequirementBootstrapStepError(code=code, message=message, suggested_fix=suggested_fix),
    )


def _repair_result_from_issues(
    issues: list[object],
    *,
    fallback_code: str,
    fallback_message: str,
) -> ApplyRepoFormatChoiceStepResult:
    error = _error_from_issues(issues, fallback_code=fallback_code, fallback_message=fallback_message)
    return ApplyRepoFormatChoiceStepResult(outcome="needs_admin_repair", summary=error.message, error=error)


def _error_from_issues(
    issues: list[object],
    *,
    fallback_code: str,
    fallback_message: str,
) -> RequirementBootstrapStepError:
    first = issues[0] if issues else None
    code = str(getattr(first, "kind", None) or getattr(first, "code", None) or fallback_code)
    message = str(getattr(first, "message", None) or fallback_message)
    suggested_fix = getattr(first, "suggested_action", None)
    return RequirementBootstrapStepError(
        code=code,
        message=message,
        gate_summary=_issue_summary(issues) or None,
        suggested_fix=str(suggested_fix) if suggested_fix else None,
    )


def _native_validate_result(
    repo_key: str,
    *,
    outcome: Literal["invalid_input", "blocked"],
    code: str,
    message: str,
    suggested_fix: str | None = None,
) -> ValidateAndInitializeNativePreparationStepResult:
    return ValidateAndInitializeNativePreparationStepResult(
        outcome=outcome,
        repo_key=repo_key,
        summary=message,
        error=NativePreparationStepError(code=code, message=message, suggested_fix=suggested_fix),
    )


def _native_validate_result_from_issues(
    repo_key: str,
    issues: list[object],
    *,
    outcome: Literal["invalid_input", "blocked"],
    fallback_code: str,
    fallback_message: str,
) -> ValidateAndInitializeNativePreparationStepResult:
    error = _native_error_from_issues(
        issues,
        fallback_code=fallback_code,
        fallback_message=fallback_message,
    )
    return ValidateAndInitializeNativePreparationStepResult(
        outcome=outcome,
        repo_key=repo_key,
        summary=error.message,
        error=error,
    )


def _native_error_from_issues(
    issues: list[object],
    *,
    fallback_code: str,
    fallback_message: str,
) -> NativePreparationStepError:
    first = issues[0] if issues else None
    code = str(getattr(first, "kind", None) or getattr(first, "code", None) or fallback_code)
    message = str(getattr(first, "message", None) or fallback_message)
    suggested_fix = getattr(first, "suggested_action", None)
    return NativePreparationStepError(
        code=code,
        message=message,
        gate_summary=_issue_summary(issues) or None,
        suggested_fix=str(suggested_fix) if suggested_fix else None,
    )


def _adapter_error_from_issues(
    issues: list[object],
    *,
    fallback_code: str,
    fallback_message: str,
) -> AdapterPreparationStepError:
    first = issues[0] if issues else None
    code = str(getattr(first, "kind", None) or getattr(first, "code", None) or fallback_code)
    message = str(getattr(first, "message", None) or fallback_message)
    suggested_fix = getattr(first, "suggested_action", None)
    return AdapterPreparationStepError(
        code=code,
        message=message,
        gate_summary=_issue_summary(issues) or None,
        suggested_fix=str(suggested_fix) if suggested_fix else None,
    )


def _issue_summary(issues: list[object]) -> str:
    return "; ".join(str(getattr(issue, "message", issue)) for issue in issues)


REPO_LIFECYCLE_STEP_TYPES: tuple[type[BaseStep], ...] = (
    ValidateBootstrapInputStep,
    ApplyRepoFormatChoiceStep,
    ValidateAndInitializeNativePreparationStep,
    ExistingSourceCorpusScanStep,
    HandoffGateStep,
    PrepareCoordinatorDispatchStep,
    PrepareNativeLifecycleChildStep,
    ValidateAdapterPreparationInputStep,
    EnsureAdapterMainCatalogStep,
    FinalizeAdapterReadyStep,
    MarkAdapterProviderReadyStep,
)


__all__ = [
    "ApplyRepoFormatChoiceStep",
    "ApplyRepoFormatChoiceStepResult",
    "AdapterDeclCatalogStepResult",
    "AdapterInputValidationStepResult",
    "AdapterPreparationStepError",
    "BootstrapInputValidationStepResult",
    "EnsureAdapterMainCatalogStep",
    "EnsureAdapterMainCatalogStepResult",
    "ExistingSourceCorpusScanStep",
    "ExistingSourceCorpusScanStepResult",
    "FinalizeAdapterReadyStep",
    "FinalizeAdapterReadyStepResult",
    "HandoffGateStep",
    "HandoffGateStepResult",
    "MarkAdapterProviderReadyStep",
    "MarkAdapterProviderReadyStepResult",
    "NativePreparationStepError",
    "PrepareCoordinatorDispatchStep",
    "PrepareCoordinatorDispatchStepResult",
    "PrepareNativeLifecycleChildStep",
    "PrepareNativeLifecycleChildStepResult",
    "REPO_LIFECYCLE_STEP_TYPES",
    "RepoFormatDiscoveryStepResult",
    "RequirementBootstrapStepError",
    "RootInterfacePrepareStepResult",
    "SourceCorpusPrepareStepResult",
    "SourceIndexBuilderStepResult",
    "SourceIndexReviewerStepResult",
    "ValidateBootstrapInputStep",
    "ValidateAndInitializeNativePreparationStep",
    "ValidateAndInitializeNativePreparationStepResult",
    "ValidateAdapterPreparationInputStep",
    "new_repo_lifecycle_step_id",
]
