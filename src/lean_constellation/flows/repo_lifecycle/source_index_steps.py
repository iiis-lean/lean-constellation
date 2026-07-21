"""Deterministic steps for the reusable scoped SourceIndex build flow."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal, Protocol

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, FlowStepValidationError, StepTerminalReceipt
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.material import SourceIndex


class SourceIndexBaselineCheckpointView(StrictModel):
    checkpoint_id: str
    repo_root: str
    baseline_digest: str
    summary: str


class SourceIndexBaselineCheckpointAdapter(Protocol):
    """Narrow runtime contract required by SourceIndexBuildFlow.

    The shared runtime integration owns the concrete repo-checkpoint archive and
    must guarantee that materialization occurs before SourceIndex mutation.
    """

    def validate_source_index_baseline_checkpoint(
        self, repo_root: Path, *, checkpoint_id: str
    ) -> ServiceResult[SourceIndexBaselineCheckpointView]: ...

    def materialize_source_index_baseline_checkpoint(
        self,
        repo_root: Path,
        *,
        checkpoint_id: str,
        scope_ids: list[str],
        label: str,
    ) -> ServiceResult[SourceIndexBaselineCheckpointView]: ...

    def load_source_index_baseline(
        self, repo_root: Path, *, checkpoint_id: str
    ) -> ServiceResult[SourceIndex | None]: ...


class SourceIndexFlowStepError(StrictModel):
    code: str
    message: str
    issue_kinds: list[str] = Field(default_factory=list)


class ValidateSourceIndexRunStepResult(LeanRenderableStepResult):
    result_type: Literal["validate_source_index_run"] = "validate_source_index_run"
    outcome: Literal["passed", "invalid_input", "blocked"]
    error: SourceIndexFlowStepError | None = None


class ValidateSourceIndexRecoveryStepResult(LeanRenderableStepResult):
    result_type: Literal["validate_source_index_recovery"] = "validate_source_index_recovery"
    outcome: Literal["passed", "blocked"]
    baseline_digest: str | None = None
    resolved_file_paths: list[str] = Field(default_factory=list)
    readable_file_paths: list[str] = Field(default_factory=list)
    artifact_file_paths: list[str] = Field(default_factory=list)
    manifest_digest: str | None = None
    new_file_paths: list[str] = Field(default_factory=list)
    already_committed_file_paths: list[str] = Field(default_factory=list)
    uncommitted_file_paths: list[str] = Field(default_factory=list)
    review_round: int = 0
    latest_builder_summary: str | None = None
    reviewer_feedback: str | None = None
    error: SourceIndexFlowStepError | None = None


class ValidateSourceIndexRecoveryStep(BaseStep):
    """Re-audit a recovery CAS before any new AgentStep is created."""

    step_type: ClassVar[str] = "validate_source_index_recovery_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ValidateSourceIndexRecoveryStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "validate_source_index_recovery": ValidateSourceIndexRecoveryStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_source_index_flow(ctx)
        recovery = flow.input.recovery
        if recovery is None or flow.parent_flow_id is None:
            return ctx.complete_step(
                _blocked_recovery(
                    "source_index_recovery_contract_missing",
                    "SourceIndex recovery requires a successor parent and recovery contract.",
                )
            )
        try:
            successor_parent = ctx.ark.flow_service.get_flow(flow.parent_flow_id)
        except Exception:  # noqa: BLE001 - deterministic fail-closed validation.
            return ctx.complete_step(
                _blocked_recovery(
                    "source_index_recovery_parent_missing",
                    "The SourceIndex recovery successor parent cannot be loaded.",
                )
            )
        parent_recovery = getattr(getattr(successor_parent, "input", None), "recovery", None)
        if parent_recovery != recovery:
            return ctx.complete_step(
                _blocked_recovery(
                    "source_index_recovery_parent_contract_mismatch",
                    "The SourceIndex child recovery contract differs from its successor parent.",
                )
            )
        preview = ctx.app.repo_workspace.native_source_index_recovery.revalidate_successor(
            Path(flow.input.repo_root),
            repo_key=flow.input.repo_key,
            failed_parent_flow_id=recovery.failed_parent_flow_id,
            successor_parent_flow_id=successor_parent.flow_id,
            successor_child_flow_id=flow.flow_id,
            running_validation_step_id=ctx.step_id,
        )
        if not preview.ok or preview.value is None:
            return ctx.complete_step(
                ValidateSourceIndexRecoveryStepResult(
                    outcome="blocked",
                    error=_service_error("source_index_recovery_revalidation_failed", preview),
                    summary="SourceIndex recovery invariants changed before execution.",
                )
            )
        if preview.value != recovery:
            return ctx.complete_step(
                _blocked_recovery(
                    "source_index_recovery_token_mismatch",
                    "SourceIndex recovery preview no longer matches the persisted contract.",
                )
            )
        return ctx.complete_step(
            ValidateSourceIndexRecoveryStepResult(
                outcome="passed",
                baseline_digest=recovery.baseline_digest,
                resolved_file_paths=list(recovery.resolved_file_paths),
                readable_file_paths=list(recovery.readable_file_paths),
                artifact_file_paths=list(recovery.artifact_file_paths),
                manifest_digest=recovery.manifest_digest,
                new_file_paths=list(recovery.new_file_paths),
                already_committed_file_paths=list(recovery.already_committed_file_paths),
                uncommitted_file_paths=list(recovery.uncommitted_file_paths),
                review_round=recovery.review_round,
                latest_builder_summary=recovery.latest_builder_summary,
                reviewer_feedback=recovery.reviewer_feedback,
                summary="SourceIndex recovery contract revalidated against the preserved rejected draft.",
            )
        )


class ResolveSourceScopeStepResult(LeanRenderableStepResult):
    result_type: Literal["resolve_source_scope"] = "resolve_source_scope"
    outcome: Literal["resolved", "invalid_input", "blocked"]
    resolved_file_paths: list[str] = Field(default_factory=list)
    readable_file_paths: list[str] = Field(default_factory=list)
    artifact_file_paths: list[str] = Field(default_factory=list)
    manifest_digest: str | None = None
    error: SourceIndexFlowStepError | None = None


class PrepareSourceIndexBaselineStepResult(LeanRenderableStepResult):
    result_type: Literal["prepare_source_index_baseline"] = "prepare_source_index_baseline"
    outcome: Literal["prepared", "reused", "blocked"]
    checkpoint_id: str | None = None
    baseline_digest: str | None = None
    requires_materialization: bool = False
    error: SourceIndexFlowStepError | None = None


class OpenSourceIndexUpdateStepResult(LeanRenderableStepResult):
    result_type: Literal["open_source_index_update"] = "open_source_index_update"
    outcome: Literal["opened", "already_open", "no_op", "invalid_input", "blocked"]
    baseline_digest: str | None = None
    active_file_scope: list[str] = Field(default_factory=list)
    new_file_paths: list[str] = Field(default_factory=list)
    already_committed_file_paths: list[str] = Field(default_factory=list)
    uncommitted_file_paths: list[str] = Field(default_factory=list)
    error: SourceIndexFlowStepError | None = None


class ValidateAndCommitSourceIndexUpdateStepResult(LeanRenderableStepResult):
    result_type: Literal["validate_commit_source_index_update"] = "validate_commit_source_index_update"
    outcome: Literal["committed", "blocked"]
    newly_committed_file_paths: list[str] = Field(default_factory=list)
    appended_block_ids: list[str] = Field(default_factory=list)
    appended_link_ids: list[str] = Field(default_factory=list)
    appended_ref_ids: list[str] = Field(default_factory=list)
    coverage_summary: str | None = None
    error: SourceIndexFlowStepError | None = None


class ValidateSourceIndexRunStep(BaseStep):
    step_type: ClassVar[str] = "validate_source_index_run_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ValidateSourceIndexRunStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "validate_source_index_run": ValidateSourceIndexRunStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_source_index_flow(ctx)
        input_model = flow.input
        repo_root = Path(input_model.repo_root)
        if not repo_root.exists() or not repo_root.is_dir():
            return ctx.complete_step(
                ValidateSourceIndexRunStepResult(
                    outcome="invalid_input",
                    error=SourceIndexFlowStepError(
                        code="repo_root_missing",
                        message=f"Repo root does not exist: {repo_root}",
                    ),
                    summary="SourceIndex run input is invalid.",
                )
            )
        if not getattr(ctx.app, "material", None):
            return ctx.complete_step(_blocked_validate("material_service_missing", "Material service is not registered."))
        if not getattr(ctx.app, "source_index_checkpoint", None):
            return ctx.complete_step(
                _blocked_validate(
                    "source_index_checkpoint_adapter_missing",
                    "SourceIndex baseline checkpoint adapter is not registered.",
                )
            )
        return ctx.complete_step(
            ValidateSourceIndexRunStepResult(outcome="passed", summary="SourceIndex run input is valid.")
        )


class ResolveSourceScopeStep(BaseStep):
    step_type: ClassVar[str] = "resolve_source_scope_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ResolveSourceScopeStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {"resolve_source_scope": ResolveSourceScopeStepResult}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_source_index_flow(ctx)
        result = ctx.app.material.resolve_source_scope(
            Path(flow.input.repo_root), source_scope=flow.input.source_scope
        )
        if not result.ok or result.value is None:
            return ctx.complete_step(
                ResolveSourceScopeStepResult(
                    outcome=_scope_failure_outcome(result),
                    error=_service_error("source_scope_resolution_failed", result),
                    summary="Source scope resolution failed.",
                )
            )
        value = result.value
        return ctx.complete_step(
            ResolveSourceScopeStepResult(
                outcome="resolved",
                resolved_file_paths=value.resolved_file_paths,
                readable_file_paths=value.readable_file_paths,
                artifact_file_paths=value.artifact_file_paths,
                manifest_digest=value.manifest_digest,
                summary=value.summary,
            )
        )


class PrepareSourceIndexBaselineStep(BaseStep):
    step_type: ClassVar[str] = "prepare_source_index_baseline_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = PrepareSourceIndexBaselineStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "prepare_source_index_baseline": PrepareSourceIndexBaselineStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_source_index_flow(ctx)
        checkpoint_id = flow.state.pre_update_checkpoint_id
        if not checkpoint_id:
            raise FlowStepValidationError("SourceIndexBuildFlow did not preallocate its checkpoint id")
        if flow.input.pre_update_checkpoint_id is None:
            return ctx.complete_step(
                PrepareSourceIndexBaselineStepResult(
                    outcome="prepared",
                    checkpoint_id=checkpoint_id,
                    requires_materialization=True,
                    summary="SourceIndex baseline checkpoint is ready for stable-hook materialization.",
                )
            )
        result = ctx.app.source_index_checkpoint.validate_source_index_baseline_checkpoint(
            Path(flow.input.repo_root), checkpoint_id=checkpoint_id
        )
        if not result.ok or result.value is None:
            return ctx.complete_step(
                PrepareSourceIndexBaselineStepResult(
                    outcome="blocked",
                    checkpoint_id=checkpoint_id,
                    error=_service_error("source_index_baseline_checkpoint_invalid", result),
                    summary="Provided SourceIndex baseline checkpoint is invalid.",
                )
            )
        return ctx.complete_step(
            PrepareSourceIndexBaselineStepResult(
                outcome="reused",
                checkpoint_id=checkpoint_id,
                baseline_digest=result.value.baseline_digest,
                summary=result.value.summary,
            )
        )


class OpenSourceIndexUpdateStep(BaseStep):
    step_type: ClassVar[str] = "open_source_index_update_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = OpenSourceIndexUpdateStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {"open_source_index_update": OpenSourceIndexUpdateStepResult}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_source_index_flow(ctx)
        state = flow.state
        checkpoint_id = state.pre_update_checkpoint_id
        if not checkpoint_id or not state.manifest_digest:
            raise FlowStepValidationError("SourceIndexBuildFlow baseline or resolved scope is missing")
        baseline = ctx.app.source_index_checkpoint.load_source_index_baseline(
            Path(flow.input.repo_root), checkpoint_id=checkpoint_id
        )
        if not baseline.ok:
            return ctx.complete_step(
                OpenSourceIndexUpdateStepResult(
                    outcome="blocked",
                    error=_service_error("source_index_baseline_load_failed", baseline),
                    summary="SourceIndex baseline could not be loaded.",
                )
            )
        baseline_digest = (
            ctx.app.material.source_index.missing_source_index_digest()
            if baseline.value is None
            else ctx.app.material.source_index.canonical_source_index_digest(baseline.value)
        )
        if state.baseline_digest is not None and state.baseline_digest != baseline_digest:
            return ctx.complete_step(
                OpenSourceIndexUpdateStepResult(
                    outcome="blocked",
                    error=SourceIndexFlowStepError(
                        code="source_index_baseline_digest_mismatch",
                        message="Checkpoint baseline differs from the Flow-persisted baseline digest.",
                    ),
                    summary="SourceIndex baseline digest mismatch.",
                )
            )
        from lean_constellation.services.material import ResolvedSourceScopeView

        resolved = ResolvedSourceScopeView(
            mode=flow.input.source_scope.mode,
            selectors=list(flow.input.source_scope.selectors),
            resolved_file_paths=list(state.resolved_file_paths),
            readable_file_paths=list(state.readable_file_paths),
            artifact_file_paths=list(state.artifact_file_paths),
            manifest_digest=state.manifest_digest,
            summary=f"Resolved {len(state.resolved_file_paths)} files for SourceIndex update.",
        )
        opened = ctx.app.material.open_source_index_update(
            Path(flow.input.repo_root),
            resolved_scope=resolved,
            index_policy=flow.input.index_policy,
            expected_baseline_digest=baseline_digest,
            retry_baseline_index=baseline.value,
        )
        if not opened.ok or opened.value is None:
            return ctx.complete_step(
                OpenSourceIndexUpdateStepResult(
                    outcome=_open_failure_outcome(opened),
                    error=_service_error("source_index_update_open_failed", opened),
                    summary="SourceIndex update could not be opened.",
                )
            )
        value = opened.value
        return ctx.complete_step(
            OpenSourceIndexUpdateStepResult(
                outcome=value.outcome,
                baseline_digest=baseline_digest,
                active_file_scope=value.active_file_scope,
                new_file_paths=value.new_file_paths,
                already_committed_file_paths=value.already_committed_file_paths,
                uncommitted_file_paths=value.uncommitted_file_paths,
                summary=value.summary,
            )
        )


class ValidateAndCommitSourceIndexUpdateStep(BaseStep):
    step_type: ClassVar[str] = "validate_commit_source_index_update_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ValidateAndCommitSourceIndexUpdateStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "validate_commit_source_index_update": ValidateAndCommitSourceIndexUpdateStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_source_index_flow(ctx)
        state = flow.state
        if not state.pre_update_checkpoint_id or not state.baseline_digest:
            raise FlowStepValidationError("SourceIndexBuildFlow validation baseline is missing")
        baseline = ctx.app.source_index_checkpoint.load_source_index_baseline(
            Path(flow.input.repo_root), checkpoint_id=state.pre_update_checkpoint_id
        )
        if not baseline.ok:
            return ctx.complete_step(_blocked_commit("source_index_baseline_load_failed", baseline))
        validated = ctx.app.material.validate_source_index_update(
            Path(flow.input.repo_root),
            baseline_index=baseline.value,
            expected_baseline_digest=state.baseline_digest,
            resolved_scope=list(state.resolved_file_paths),
            require_completed=True,
        )
        if not validated.ok or validated.value is None:
            return ctx.complete_step(_blocked_commit("source_index_update_validation_failed", validated))
        if not validated.value.gate.passed:
            return ctx.complete_step(
                ValidateAndCommitSourceIndexUpdateStepResult(
                    outcome="blocked",
                    error=SourceIndexFlowStepError(
                        code="source_index_update_gate_rejected",
                        message=validated.value.gate.summary or "SourceIndex update gate rejected the draft.",
                        issue_kinds=validated.value.gate_issue_kinds,
                    ),
                    summary=validated.value.gate.summary,
                )
            )
        committed = ctx.app.material.commit_source_index_update(
            Path(flow.input.repo_root), validated=validated.value
        )
        if not committed.ok or committed.value is None:
            return ctx.complete_step(_blocked_commit("source_index_update_commit_failed", committed))
        value = committed.value
        return ctx.complete_step(
            ValidateAndCommitSourceIndexUpdateStepResult(
                outcome="committed",
                newly_committed_file_paths=value.newly_committed_file_paths,
                appended_block_ids=value.appended_block_ids,
                appended_link_ids=value.appended_link_ids,
                appended_ref_ids=value.appended_ref_ids,
                coverage_summary=value.coverage.summary,
                summary=value.summary,
            )
        )


def _load_source_index_flow(ctx: StepRunContext):
    from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildFlow

    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    flow = flow_service.get_flow(ctx.flow_id)
    if not isinstance(flow, SourceIndexBuildFlow):
        raise FlowStepValidationError("step does not belong to SourceIndexBuildFlow")
    return flow


def _service_error(code: str, result: ServiceResult[object]) -> SourceIndexFlowStepError:
    return SourceIndexFlowStepError(
        code=code,
        message="; ".join(issue.message for issue in result.issues) or code,
        issue_kinds=[issue.kind for issue in result.issues],
    )


def _scope_failure_outcome(result: ServiceResult[object]) -> Literal["invalid_input", "blocked"]:
    invalid = {"source_scope_selector_unsafe", "source_scope_selector_unmatched"}
    return "invalid_input" if any(issue.kind in invalid for issue in result.issues) else "blocked"


def _open_failure_outcome(result: ServiceResult[object]) -> Literal["invalid_input", "blocked"]:
    invalid = {"source_index_policy_invalid", "source_index_scope_not_reusable"}
    return "invalid_input" if any(issue.kind in invalid for issue in result.issues) else "blocked"


def _blocked_validate(code: str, message: str) -> ValidateSourceIndexRunStepResult:
    return ValidateSourceIndexRunStepResult(
        outcome="blocked",
        error=SourceIndexFlowStepError(code=code, message=message),
        summary=message,
    )


def _blocked_recovery(code: str, message: str) -> ValidateSourceIndexRecoveryStepResult:
    return ValidateSourceIndexRecoveryStepResult(
        outcome="blocked",
        error=SourceIndexFlowStepError(code=code, message=message),
        summary=message,
    )


def _blocked_commit(code: str, result: ServiceResult[object]) -> ValidateAndCommitSourceIndexUpdateStepResult:
    return ValidateAndCommitSourceIndexUpdateStepResult(
        outcome="blocked",
        error=_service_error(code, result),
        summary="SourceIndex update could not be committed.",
    )


SOURCE_INDEX_BUILD_STEP_TYPES: tuple[type[BaseStep], ...] = (
    ValidateSourceIndexRecoveryStep,
    ValidateSourceIndexRunStep,
    ResolveSourceScopeStep,
    PrepareSourceIndexBaselineStep,
    OpenSourceIndexUpdateStep,
    ValidateAndCommitSourceIndexUpdateStep,
)


__all__ = [
    "OpenSourceIndexUpdateStep",
    "OpenSourceIndexUpdateStepResult",
    "PrepareSourceIndexBaselineStep",
    "PrepareSourceIndexBaselineStepResult",
    "ResolveSourceScopeStep",
    "ResolveSourceScopeStepResult",
    "SOURCE_INDEX_BUILD_STEP_TYPES",
    "SourceIndexBaselineCheckpointAdapter",
    "SourceIndexBaselineCheckpointView",
    "ValidateAndCommitSourceIndexUpdateStep",
    "ValidateAndCommitSourceIndexUpdateStepResult",
    "ValidateSourceIndexRecoveryStep",
    "ValidateSourceIndexRecoveryStepResult",
    "ValidateSourceIndexRunStep",
    "ValidateSourceIndexRunStepResult",
]
