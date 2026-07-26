"""Deterministic lifecycle steps shared by native continuation runs."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, FlowStepValidationError, StepTerminalReceipt
from pydantic import Field

from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.services.repo_workspace.repo_lifecycle_lock import RepoLifecycleLockBusyError


class PrepareNativeRunMutationResult(LeanRenderableStepResult):
    result_type: Literal["prepare_native_run_mutation"] = "prepare_native_run_mutation"
    outcome: Literal["prepared", "blocked"]
    checkpoint_id: str | None = None
    started_stable: bool = False
    previous_completion_mode: str | None = None
    reason: str | None = None


class PrepareNativeRunMutationStep(BaseStep):
    step_type: ClassVar[str] = "prepare_native_run_mutation_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = PrepareNativeRunMutationResult
    Results = {"prepare_native_run_mutation": PrepareNativeRunMutationResult}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        from lean_constellation.flows.repo_lifecycle.continuation import NativeRepoContinuationInput

        flow = ctx.ark.flow_service.get_flow(ctx.flow_id)
        input_model = flow.input
        if not isinstance(input_model, NativeRepoContinuationInput):
            raise TypeError("continuation input is invalid")
        conflicts = [candidate for candidate in ctx.ark.flow_service.list_flows(scope_id=ctx.scope_id)
                     if candidate.flow_id != ctx.flow_id
                     and candidate.status.value not in {"completed", "failed"}]
        if conflicts:
            reason = f"Conflicting repo lifecycle Flow is active: {conflicts[0].flow_id}."
            return ctx.complete_step(PrepareNativeRunMutationResult(outcome="blocked", reason=reason, summary=reason))
        gate = ctx.app.repo_workspace.run.validate_repo_run_transition(
            Path(input_model.repo_root), run_spec=input_model.run_spec,
            start_kind="continuation", base_release_id=input_model.base_release_id,
        )
        if not gate.ok or gate.value is None:
            raise FlowStepValidationError(
                "; ".join(issue.message for issue in gate.issues) or "Repo run transition preflight failed."
            )
        if not gate.value.passed:
            reason = "; ".join(issue.message for issue in gate.value.issues)
            return ctx.complete_step(PrepareNativeRunMutationResult(outcome="blocked", reason=reason, summary=reason))
        config = ctx.app.repo_workspace.metadata.get_repo_config(Path(input_model.repo_root))
        if not config.ok or config.value is None:
            return ctx.complete_step(PrepareNativeRunMutationResult(
                outcome="blocked", reason="Repo config is unavailable.", summary="Repo config is unavailable."
            ))
        return ctx.complete_step(PrepareNativeRunMutationResult(
            outcome="prepared", checkpoint_id=f"repo-{uuid.uuid4().hex}",
            started_stable=ctx.app.repo_workspace.metadata.get_repo_publication(Path(input_model.repo_root)).value.publication.status.value == "stable",
            previous_completion_mode=config.value.config.completion_mode.value,
            summary="Prepared native continuation mutation checkpoint.",
        ))


class ApplyNativeRunResult(LeanRenderableStepResult):
    result_type: Literal["apply_native_run"] = "apply_native_run"
    outcome: Literal["applied", "blocked"]
    transitioned: bool = False
    resolved_source_files: list[str] = Field(default_factory=list)
    reason: str | None = None
    config_change_summary: str | None = None


class ApplyNativeRunStep(BaseStep):
    step_type: ClassVar[str] = "apply_native_run_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ApplyNativeRunResult
    Results = {"apply_native_run": ApplyNativeRunResult}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        from lean_constellation.flows.repo_lifecycle.continuation import NativeRepoContinuationInput

        flow = ctx.ark.flow_service.get_flow(ctx.flow_id)
        input_model = flow.input
        if not isinstance(input_model, NativeRepoContinuationInput):
            raise TypeError("continuation input is invalid")
        root = Path(input_model.repo_root)
        checkpoint_id = getattr(flow.state, "pre_run_mutation_checkpoint_id", None)
        if not checkpoint_id:
            return ctx.complete_step(ApplyNativeRunResult(outcome="blocked", reason="checkpoint id missing", summary="Mutation checkpoint is missing."))
        checkpoint = ctx.app.source_index_checkpoint.validate_source_index_baseline_checkpoint(
            root, checkpoint_id=checkpoint_id
        )
        if not checkpoint.ok:
            raise FlowStepValidationError(checkpoint.issues[0].message)
        try:
            with ctx.app.repo_workspace.lifecycle_lock.locked(root):
                return self._run_locked(ctx, flow, input_model, root)
        except RepoLifecycleLockBusyError as exc:
            raise FlowStepValidationError(str(exc)) from exc

    def _run_locked(self, ctx: StepRunContext, flow, input_model, root: Path) -> StepTerminalReceipt:  # noqa: ANN001
        publication = ctx.app.repo_workspace.metadata.get_repo_publication(root)
        if not publication.ok or publication.value is None:
            return ctx.complete_step(ApplyNativeRunResult(outcome="blocked", reason="publication unavailable", summary="Publication unavailable."))
        transitioned = publication.value.publication.status.value == "stable"
        if transitioned:
            changed = ctx.app.repo_workspace.metadata.mark_repo_developing(root)
            if not changed.ok:
                return ctx.complete_step(ApplyNativeRunResult(outcome="blocked", reason=changed.issues[0].message, summary=changed.issues[0].message))
        applied = ctx.app.repo_workspace.run.apply_repo_run_config(
            root, run_spec=input_model.run_spec, expected_base_release_id=input_model.base_release_id
        )
        if not applied.ok:
            return ctx.complete_step(ApplyNativeRunResult(outcome="blocked", reason=applied.issues[0].message, summary=applied.issues[0].message))
        appended = ctx.app.repo_workspace.preparation.append_preparation_interfaces(
            root, interfaces=input_model.run_spec.additional_required_interfaces
        )
        if not appended.ok:
            return ctx.complete_step(ApplyNativeRunResult(outcome="blocked", reason=appended.issues[0].message, summary=appended.issues[0].message))
        resolved = ctx.app.material.resolve_source_scope(root, source_scope=input_model.run_spec.source_scope)
        if not resolved.ok or resolved.value is None:
            return ctx.complete_step(ApplyNativeRunResult(outcome="blocked", reason=resolved.issues[0].message, summary=resolved.issues[0].message))
        return ctx.complete_step(ApplyNativeRunResult(
            outcome="applied", transitioned=transitioned,
            resolved_source_files=list(resolved.value.resolved_file_paths),
            config_change_summary=(
                f"completion_mode={input_model.run_spec.completion_mode.value}"
            ),
            summary="Applied native continuation config and scope."
        ))


class ContinuationHandoffGateResult(LeanRenderableStepResult):
    result_type: Literal["continuation_handoff_gate"] = "continuation_handoff_gate"
    outcome: Literal["passed", "blocked", "invalid_input"]
    reason: str | None = None


class ContinuationHandoffGateStep(BaseStep):
    step_type: ClassVar[str] = "continuation_handoff_gate_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ContinuationHandoffGateResult
    Results = {"continuation_handoff_gate": ContinuationHandoffGateResult}

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        from lean_constellation.flows.repo_lifecycle.continuation import NativeRepoContinuationInput

        flow = ctx.ark.flow_service.get_flow(ctx.flow_id)
        input_model = flow.input
        if not isinstance(input_model, NativeRepoContinuationInput):
            raise TypeError("continuation input is invalid")
        gate = ctx.app.validation_snapshot.readiness_gate.check_native_handoff_gate(Path(input_model.repo_root))
        if not gate.ok or gate.value is None:
            reason = "; ".join(issue.message for issue in gate.issues) or "Native handoff gate failed."
            return ctx.complete_step(ContinuationHandoffGateResult(
                outcome="invalid_input", reason=reason, summary=reason
            ))
        if not gate.value.passed:
            reason = gate.value.summary or "; ".join(issue.message for issue in gate.value.issues)
            invalid = any(issue.kind in {"native_handoff_repo_format_invalid", "native_handoff_source_corpus_missing"} for issue in gate.value.issues)
            return ctx.complete_step(ContinuationHandoffGateResult(
                outcome="invalid_input" if invalid else "blocked", reason=reason, summary=reason
            ))
        return ctx.complete_step(ContinuationHandoffGateResult(
            outcome="passed", summary=gate.value.summary or "Native continuation handoff gate passed."
        ))


RUN_STEP_TYPES = (PrepareNativeRunMutationStep, ApplyNativeRunStep, ContinuationHandoffGateStep)

__all__ = ["ApplyNativeRunResult", "ApplyNativeRunStep", "ContinuationHandoffGateResult", "ContinuationHandoffGateStep", "PrepareNativeRunMutationResult", "PrepareNativeRunMutationStep", "RUN_STEP_TYPES"]
