"""DeclGraph round deterministic steps and business AgentStep results."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, FlowStepValidationError, StepTerminalReceipt
from agent_runtime_kit.flow.standard_steps.agent_step import AgentStepState
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclReviewMarkRecord,
    DeclRevision,
    DeclRoundResultKind,
    DeclRoundStatus,
    DeclStage,
    DeclState,
    DeclStrategyStatus,
)


DeclStageName = Literal["statement_nl", "statement_formal", "proof_nl", "proof_formal"]
RoundTerminalCode = Literal[
    "worker_blocked",
    "review_retry_exhausted",
    "stage_gate_failed",
    "round_local_audit_failed",
    "final_audit_failed",
    "invalid_round_state",
    "projection_sync_failed",
    "internal_service_error",
]


class RoundTerminalReason(StrictModel):
    code: RoundTerminalCode
    message: str
    stage: DeclStageName | None = None
    affected_decl_names: list[str] = Field(default_factory=list)
    suggested_plan_action: str | None = None


class RoundStageRuntimeSummary(StrictModel):
    stage: DeclStageName
    outcome: Literal["passed", "skipped", "blocked", "failed", "retry_worker"]
    target_decl_names: list[str] = Field(default_factory=list)
    retry_count: int = 0
    summary: str


class DeclStageTargetMetadata(StrictModel):
    decl_name: str
    change_kind: str | None = None
    objective: str | None = None
    start_before_state: str | None = None
    end_after_state: str | None = None
    require_target_state_satisfied: bool = True
    current_state: str
    current_revision: int
    known_statement_deps: list[str] = Field(default_factory=list)
    known_proof_deps: list[str] = Field(default_factory=list)


class RoundStartValidationStepResult(LeanRenderableStepResult):
    result_type: Literal["decl_round_start_validation"] = "decl_round_start_validation"
    outcome: Literal["valid", "invalid"]
    repo_key: str | None = None
    node_path: str | None = None
    strategy_id: str | None = None
    round_id: str | None = None
    round_index: int | None = None
    change_count: int = 0
    create_count: int = 0
    update_count: int = 0
    delete_count: int = 0
    theorem_like_count: int = 0
    error: RoundTerminalReason | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "strategy_id": self.strategy_id,
            "round_id": self.round_id,
            "round_index": self.round_index,
            "change_count": self.change_count,
            "create_count": self.create_count,
            "update_count": self.update_count,
            "delete_count": self.delete_count,
            "theorem_like_count": self.theorem_like_count,
            "error": self.error.message if self.error else None,
        }


class DeleteAndNormalizeStepResult(LeanRenderableStepResult):
    result_type: Literal["decl_round_delete_normalize"] = "decl_round_delete_normalize"
    outcome: Literal["normalized", "blocked", "failed"]
    deleted_count: int = 0
    created_count: int = 0
    updated_count: int = 0
    reset_count: int = 0
    projection_updates: int = 0
    error: RoundTerminalReason | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "deleted_count": self.deleted_count,
            "created_count": self.created_count,
            "updated_count": self.updated_count,
            "reset_count": self.reset_count,
            "projection_updates": self.projection_updates,
            "error": self.error.message if self.error else None,
        }


class PrepareStageTargetsStepResult(LeanRenderableStepResult):
    result_type: Literal["decl_round_prepare_stage_targets"] = "decl_round_prepare_stage_targets"
    outcome: Literal["targets_ready", "skipped", "blocked", "failed"]
    stage: DeclStageName
    target_decl_names: list[str] = Field(default_factory=list)
    target_metadata: list[DeclStageTargetMetadata] = Field(default_factory=list)
    skipped_reason: str | None = None
    prepared_file_count: int = 0
    error: RoundTerminalReason | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "stage": self.stage,
            "target_decl_names": list(self.target_decl_names),
            "target_metadata": [item.model_dump(mode="json") for item in self.target_metadata],
            "skipped_reason": self.skipped_reason,
            "prepared_file_count": self.prepared_file_count,
            "error": self.error.message if self.error else None,
        }


class DeclStageWorkerStepResult(LeanRenderableStepResult):
    result_type: Literal["decl_stage_worker"] = "decl_stage_worker"
    outcome: Literal["completed", "blocked", "incomplete"]
    stage: DeclStageName | None = None
    round_id: str | None = None
    completed_decl_names: list[str] = Field(default_factory=list)
    affected_decl_names: list[str] = Field(default_factory=list)
    reason: str | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "stage": self.stage,
            "round_id": self.round_id,
            "completed_decl_names": list(self.completed_decl_names),
            "affected_decl_names": list(self.affected_decl_names),
            "reason": self.reason,
            "incomplete_reason": self.incomplete_reason,
        }


class DeclStageReviewerStepResult(LeanRenderableStepResult):
    result_type: Literal["decl_stage_reviewer"] = "decl_stage_reviewer"
    outcome: Literal["passed", "rejected", "incomplete"]
    stage: DeclStageName | None = None
    round_id: str | None = None
    accepted: bool | None = None
    retry_required: bool | None = None
    reviewed_decl_names: list[str] = Field(default_factory=list)
    failed_decl_names: list[str] = Field(default_factory=list)
    missing_decl_names: list[str] = Field(default_factory=list)
    feedback: list[DeclReviewMarkRecord] = Field(default_factory=list)
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "stage": self.stage,
            "round_id": self.round_id,
            "accepted": self.accepted,
            "retry_required": self.retry_required,
            "reviewed_decl_names": list(self.reviewed_decl_names),
            "failed_decl_names": list(self.failed_decl_names),
            "missing_decl_names": list(self.missing_decl_names),
            "feedback": [item.model_dump(mode="json") for item in self.feedback],
            "incomplete_reason": self.incomplete_reason,
        }


class DeclStageReviewerStepState(AgentStepState):
    state_type: Literal["decl_stage_reviewer_agent_step"] = "decl_stage_reviewer_agent_step"
    review_marks: list[DeclReviewMarkRecord] = Field(default_factory=list)


class StageGateAndAuditStepResult(LeanRenderableStepResult):
    result_type: Literal["decl_round_stage_gate_audit"] = "decl_round_stage_gate_audit"
    outcome: Literal["stage_passed", "retry_worker", "blocked", "failed"]
    stage: DeclStageName
    advanced_decl_names: list[str] = Field(default_factory=list)
    rejected_decl_names: list[str] = Field(default_factory=list)
    retry_count: int = 0
    retry_remaining: int = 0
    audit_summary: str | None = None
    feedback_summary: str | None = None
    error: RoundTerminalReason | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "stage": self.stage,
            "advanced_decl_names": list(self.advanced_decl_names),
            "rejected_decl_names": list(self.rejected_decl_names),
            "retry_count": self.retry_count,
            "retry_remaining": self.retry_remaining,
            "audit_summary": self.audit_summary,
            "feedback_summary": self.feedback_summary,
            "error": self.error.message if self.error else None,
        }


class RoundFinalAuditStepResult(LeanRenderableStepResult):
    result_type: Literal["decl_round_final_audit"] = "decl_round_final_audit"
    outcome: Literal["passed", "blocked", "failed"]
    reached_target_decl_names: list[str] = Field(default_factory=list)
    missing_target_decl_names: list[str] = Field(default_factory=list)
    readiness_summary: str | None = None
    projection_summary: str | None = None
    error: RoundTerminalReason | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "reached_target_decl_names": list(self.reached_target_decl_names),
            "missing_target_decl_names": list(self.missing_target_decl_names),
            "readiness_summary": self.readiness_summary,
            "projection_summary": self.projection_summary,
            "error": self.error.message if self.error else None,
        }


class BuildRoundResultStepResult(LeanRenderableStepResult):
    result_type: Literal["decl_round_build_result"] = "decl_round_build_result"
    outcome: Literal["built"] = "built"
    flow_outcome: Literal["completed", "blocked", "failed"]

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "flow_outcome": self.flow_outcome,
        }


class RoundStartValidationStep(BaseStep):
    step_type: ClassVar[str] = "decl_round_start_validation_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RoundStartValidationStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "decl_round_start_validation": RoundStartValidationStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_decl_round_flow(ctx)
        input_model = _require_decl_round_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(_invalid_start(input_model, "DeclGraphRoundFlow requires repo_path in Flow input."))

        graph = _decl_graph(ctx)
        strategy = graph.get_strategy(repo_root, node_path=input_model.node_path, strategy_id=input_model.strategy_id)
        if not strategy.ok or strategy.value is None:
            return ctx.complete_step(_invalid_start(input_model, _first_issue_message(strategy.issues, "Strategy lookup failed.")))
        if strategy.value.status != DeclStrategyStatus.OPEN:
            return ctx.complete_step(_invalid_start(input_model, f"Strategy is not open: {strategy.value.status.value}."))

        round_record = graph.get_round(repo_root, node_path=input_model.node_path, round_id=input_model.round_id)
        if not round_record.ok or round_record.value is None:
            return ctx.complete_step(_invalid_start(input_model, _first_issue_message(round_record.issues, "Round lookup failed.")))
        if round_record.value.strategy_id != input_model.strategy_id:
            return ctx.complete_step(_invalid_start(input_model, "Round does not belong to the requested strategy."))
        if input_model.round_index is not None and round_record.value.round_index != input_model.round_index:
            return ctx.complete_step(_invalid_start(input_model, "Round index does not match current round truth."))

        if input_model.contract_version is not None:
            node_view = _node(ctx).node_tree.get_node(repo_root, path=input_model.node_path)
            if not node_view.ok or node_view.value is None:
                return ctx.complete_step(_invalid_start(input_model, _first_issue_message(node_view.issues, "Node lookup failed.")))
            if node_view.value.current_contract_version != input_model.contract_version:
                return ctx.complete_step(
                    _invalid_start(
                        input_model,
                        f"Contract version is stale: current={node_view.value.current_contract_version}, input={input_model.contract_version}.",
                    )
                )

        if round_record.value.status == DeclRoundStatus.DRAFT:
            draft_gate = graph.validate_round_draft(repo_root, node_path=input_model.node_path, round_id=input_model.round_id)
            if not draft_gate.ok or draft_gate.value is None:
                return ctx.complete_step(_invalid_start(input_model, _first_issue_message(draft_gate.issues, "Round draft validation failed.")))
            if not getattr(draft_gate.value, "passed", False):
                return ctx.complete_step(_invalid_start(input_model, draft_gate.value.summary or "Round draft validation rejected the round."))
            started = graph.start_round(repo_root, node_path=input_model.node_path, round_id=input_model.round_id)
            if not started.ok or started.value is None:
                return ctx.complete_step(_invalid_start(input_model, _first_issue_message(started.issues, "Round start failed.")))
            round_record_value = started.value
        elif round_record.value.status == DeclRoundStatus.RUNNING:
            round_record_value = round_record.value
        else:
            return ctx.complete_step(_invalid_start(input_model, f"Round is not draft or running: {round_record.value.status.value}."))

        counts = _round_change_counts(ctx, repo_root, input_model.node_path, input_model.round_id)
        if counts["change_count"] == 0:
            return ctx.complete_step(_invalid_start(input_model, "Round has no changes."))
        return ctx.complete_step(
            RoundStartValidationStepResult(
                outcome="valid",
                repo_key=input_model.repo_key,
                node_path=input_model.node_path,
                strategy_id=input_model.strategy_id,
                round_id=input_model.round_id,
                round_index=round_record_value.round_index,
                change_count=counts["change_count"],
                create_count=counts["create_count"],
                update_count=counts["update_count"],
                delete_count=counts["delete_count"],
                theorem_like_count=counts["theorem_like_count"],
                summary=f"Decl round {input_model.round_id} is running with {counts['change_count']} changes.",
            )
        )


class DeleteAndNormalizeStep(BaseStep):
    step_type: ClassVar[str] = "decl_round_delete_normalize_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = DeleteAndNormalizeStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "decl_round_delete_normalize": DeleteAndNormalizeStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_decl_round_flow(ctx)
        input_model = _require_decl_round_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(_delete_normalize_failed("DeclGraphRoundFlow requires repo_path in Flow input."))
        revisions = _round_revisions(ctx, repo_root, input_model.node_path, input_model.round_id)
        if not revisions.ok or revisions.value is None:
            return ctx.complete_step(_delete_normalize_failed(_first_issue_message(revisions.issues, "Cannot load round revisions.")))

        deleted_count = 0
        created_count = 0
        updated_count = 0
        reset_count = 0
        projection_updates = 0
        projection = _lean_projection(ctx)
        for revision in revisions.value:
            change = revision.change
            if change is None:
                return ctx.complete_step(_delete_normalize_failed(f"Round revision {revision.decl_name}@{revision.revision} has no change metadata."))
            if change.kind == DeclChangeKind.DELETE:
                removed = projection.remove_decl_file_for_delete(repo_root, node_path=input_model.node_path, decl_name=revision.decl_name)
                if not removed.ok or removed.value is None:
                    return ctx.complete_step(_delete_normalize_failed(_first_issue_message(removed.issues, "Delete projection sync failed.")))
                deleted_count += 1
                projection_updates += int(bool(getattr(removed.value, "changed", False)))
            elif change.kind == DeclChangeKind.CREATE:
                synced = projection.sync_decl_file_after_revision_reset(repo_root, node_path=input_model.node_path, decl_name=revision.decl_name)
                if not synced.ok or synced.value is None:
                    return ctx.complete_step(_delete_normalize_failed(_first_issue_message(synced.issues, "Create projection sync failed.")))
                created_count += 1
                reset_count += 1
                projection_updates += int(bool(getattr(synced.value, "changed", False)))
            elif change.kind == DeclChangeKind.UPDATE:
                synced = projection.sync_decl_file_after_revision_reset(repo_root, node_path=input_model.node_path, decl_name=revision.decl_name)
                if not synced.ok or synced.value is None:
                    return ctx.complete_step(_delete_normalize_failed(_first_issue_message(synced.issues, "Update projection sync failed.")))
                updated_count += 1
                reset_count += 1
                projection_updates += int(bool(getattr(synced.value, "changed", False)))

        refreshed = projection.refresh_node_projection(repo_root, node_path=input_model.node_path)
        if not refreshed.ok or refreshed.value is None:
            return ctx.complete_step(_delete_normalize_failed(_first_issue_message(refreshed.issues, "Node projection refresh failed.")))
        projection_updates += len(getattr(refreshed.value, "actions", []) or [])

        audit = _validation_snapshot(ctx).run_delete_sanity_audit(repo_root, node_path=input_model.node_path, round_id=input_model.round_id)
        if not audit.ok or audit.value is None:
            return ctx.complete_step(_delete_normalize_failed(_first_issue_message(audit.issues, "Delete sanity audit failed.")))
        if not audit.value.passed:
            return ctx.complete_step(
                DeleteAndNormalizeStepResult(
                    outcome="blocked",
                    deleted_count=deleted_count,
                    created_count=created_count,
                    updated_count=updated_count,
                    reset_count=reset_count,
                    projection_updates=projection_updates,
                    error=RoundTerminalReason(
                        code="projection_sync_failed",
                        message=audit.value.summary,
                        affected_decl_names=[
                            revision.decl_name
                            for revision in revisions.value
                            if revision.change is not None and revision.change.kind == DeclChangeKind.DELETE
                        ],
                        suggested_plan_action="Re-open planning and repair the delete closure before running this round.",
                    ),
                    summary=audit.value.summary,
                )
            )
        return ctx.complete_step(
            DeleteAndNormalizeStepResult(
                outcome="normalized",
                deleted_count=deleted_count,
                created_count=created_count,
                updated_count=updated_count,
                reset_count=reset_count,
                projection_updates=projection_updates,
                summary=f"Round delete/normalize completed for {len(revisions.value)} revisions.",
            )
        )


class PrepareStageTargetsStepState(BaseStepState):
    state_type: Literal["decl_round_prepare_stage_targets"] = "decl_round_prepare_stage_targets"
    stage: DeclStageName


class PrepareStageTargetsStep(BaseStep):
    step_type: ClassVar[str] = "decl_round_prepare_stage_targets_step"
    State: ClassVar[type[BaseStepState]] = PrepareStageTargetsStepState
    Result: ClassVar[type[BaseStepResult]] = PrepareStageTargetsStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "decl_round_prepare_stage_targets": PrepareStageTargetsStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_decl_round_flow(ctx)
        input_model = _require_decl_round_input(flow.input)
        state = _prepare_stage_state(self.state)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(_prepare_failed(state.stage, "DeclGraphRoundFlow requires repo_path in Flow input."))
        targets = _stage_targets(ctx, repo_root, input_model.node_path, input_model.round_id, state.stage)
        if not targets.ok or targets.value is None:
            return ctx.complete_step(_prepare_failed(state.stage, _first_issue_message(targets.issues, "Cannot compute stage targets.")))
        target_names = [target.decl_name for target in targets.value]
        if not target_names:
            return ctx.complete_step(
                PrepareStageTargetsStepResult(
                    outcome="skipped",
                    stage=state.stage,
                    skipped_reason=f"No declarations require {state.stage}.",
                    summary=f"Skipped {state.stage}: no targets.",
                )
        )
        prepared_file_count = 0
        if state.stage == "statement_formal":
            for decl_name in target_names:
                prepared = _lean_projection(ctx).prepare_statement_formal_stage_file(repo_root, node_path=input_model.node_path, decl_name=decl_name)
                if not prepared.ok or prepared.value is None:
                    return ctx.complete_step(_prepare_failed(state.stage, _first_issue_message(prepared.issues, "Statement formal file preparation failed."), target_names))
                prepared_file_count += 1
        elif state.stage == "proof_formal":
            for decl_name in target_names:
                prepared = _lean_projection(ctx).prepare_proof_formal_stage_file(repo_root, node_path=input_model.node_path, decl_name=decl_name)
                if not prepared.ok or prepared.value is None:
                    return ctx.complete_step(_prepare_failed(state.stage, _first_issue_message(prepared.issues, "Proof formal file preparation failed."), target_names))
                prepared_file_count += 1
        return ctx.complete_step(
            PrepareStageTargetsStepResult(
                outcome="targets_ready",
                stage=state.stage,
                target_decl_names=target_names,
                target_metadata=targets.value,
                prepared_file_count=prepared_file_count,
                summary=f"Prepared {len(target_names)} targets for {state.stage}.",
            )
        )


class StageGateAndAuditStepState(BaseStepState):
    state_type: Literal["decl_round_stage_gate_audit"] = "decl_round_stage_gate_audit"
    stage: DeclStageName
    target_decl_names: list[str] = Field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2


class StageGateAndAuditStep(BaseStep):
    step_type: ClassVar[str] = "decl_round_stage_gate_audit_step"
    State: ClassVar[type[BaseStepState]] = StageGateAndAuditStepState
    Result: ClassVar[type[BaseStepResult]] = StageGateAndAuditStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "decl_round_stage_gate_audit": StageGateAndAuditStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_decl_round_flow(ctx)
        input_model = _require_decl_round_input(flow.input)
        state = _stage_gate_state(self.state)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(_stage_gate_failed(state.stage, "DeclGraphRoundFlow requires repo_path in Flow input."))
        reviewer_result = getattr(flow.state, "latest_reviewer_result", None)
        if not isinstance(reviewer_result, DeclStageReviewerStepResult):
            return ctx.complete_step(_stage_gate_failed(state.stage, "Stage gate requires the latest reviewer result."))
        if reviewer_result.outcome == "incomplete":
            return ctx.complete_step(_stage_gate_failed(state.stage, reviewer_result.incomplete_reason or "Reviewer did not submit a result."))
        if reviewer_result.outcome == "rejected":
            retry_count = state.retry_count + 1
            retry_remaining = max(state.max_retries - retry_count, 0)
            if state.retry_count < state.max_retries:
                return ctx.complete_step(
                    StageGateAndAuditStepResult(
                        outcome="retry_worker",
                        stage=state.stage,
                        rejected_decl_names=list(state.target_decl_names),
                        retry_count=retry_count,
                        retry_remaining=retry_remaining,
                        feedback_summary=reviewer_result.summary,
                        summary=f"{state.stage} review rejected; retry {retry_count} is available.",
                    )
                )
            return ctx.complete_step(
                StageGateAndAuditStepResult(
                    outcome="failed",
                    stage=state.stage,
                    rejected_decl_names=list(state.target_decl_names),
                    retry_count=retry_count,
                    retry_remaining=0,
                    feedback_summary=reviewer_result.summary,
                    error=RoundTerminalReason(
                        code="review_retry_exhausted",
                        message=reviewer_result.summary or f"{state.stage} review retry budget exhausted.",
                        stage=state.stage,
                        affected_decl_names=list(state.target_decl_names),
                        suggested_plan_action="Review stage feedback and open a new round if this execution route is still viable.",
                    ),
                    summary=f"{state.stage} review retry budget exhausted.",
                )
            )

        consistency = _formal_consistency_for_stage(ctx, repo_root, input_model.node_path, state.stage, state.target_decl_names)
        if consistency is not None:
            return ctx.complete_step(consistency)
        audit = _validation_snapshot(ctx).run_round_local_audit(repo_root, node_path=input_model.node_path, round_id=input_model.round_id, stage=state.stage)
        if not audit.ok or audit.value is None:
            return ctx.complete_step(_stage_gate_failed(state.stage, _first_issue_message(audit.issues, "Round-local audit failed."), state.target_decl_names))
        if not audit.value.passed:
            return ctx.complete_step(
                StageGateAndAuditStepResult(
                    outcome="blocked",
                    stage=state.stage,
                    rejected_decl_names=list(state.target_decl_names),
                    retry_count=state.retry_count,
                    retry_remaining=max(state.max_retries - state.retry_count, 0),
                    audit_summary=audit.value.summary,
                    error=RoundTerminalReason(
                        code="round_local_audit_failed",
                        message=audit.value.summary,
                        stage=state.stage,
                        affected_decl_names=list(state.target_decl_names),
                        suggested_plan_action="Inspect round-local audit findings and split or repair the round.",
                    ),
                    summary=audit.value.summary,
                )
            )
        return ctx.complete_step(
            StageGateAndAuditStepResult(
                outcome="stage_passed",
                stage=state.stage,
                advanced_decl_names=list(state.target_decl_names),
                retry_count=state.retry_count,
                retry_remaining=max(state.max_retries - state.retry_count, 0),
                audit_summary=audit.value.summary,
                summary=f"{state.stage} passed for {len(state.target_decl_names)} declarations.",
            )
        )


class RoundFinalAuditStep(BaseStep):
    step_type: ClassVar[str] = "decl_round_final_audit_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RoundFinalAuditStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "decl_round_final_audit": RoundFinalAuditStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_decl_round_flow(ctx)
        input_model = _require_decl_round_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(_final_audit_failed("DeclGraphRoundFlow requires repo_path in Flow input."))
        revisions = _round_revisions(ctx, repo_root, input_model.node_path, input_model.round_id)
        if not revisions.ok or revisions.value is None:
            return ctx.complete_step(_final_audit_failed(_first_issue_message(revisions.issues, "Cannot load round revisions.")))
        reached: list[str] = []
        missing: list[str] = []
        unsatisfied: list[str] = []
        round_revisions = {revision.decl_name: revision for revision in revisions.value}
        for revision in revisions.value:
            change = revision.change
            if change is None:
                missing.append(revision.decl_name)
                continue
            if change.kind == DeclChangeKind.DELETE:
                reached.append(revision.decl_name)
                continue
            if change.end_after_state is None:
                missing.append(revision.decl_name)
                continue
            if _state_reaches(revision.state, change.end_after_state):
                reached.append(revision.decl_name)
            else:
                missing.append(revision.decl_name)
                continue
            if change.require_target_state_satisfied:
                target = _proof_availability_for_target_state(change.end_after_state)
                satisfied, _reason = _round_revision_satisfies_proof_policy(
                    ctx,
                    repo_root,
                    node_path=input_model.node_path,
                    round_revisions=round_revisions,
                    revision=revision,
                    target_proof_availability=target,
                )
                if not satisfied:
                    unsatisfied.append(revision.decl_name)
        if missing:
            return ctx.complete_step(
                RoundFinalAuditStepResult(
                    outcome="failed",
                    reached_target_decl_names=sorted(reached),
                    missing_target_decl_names=sorted(missing),
                    error=RoundTerminalReason(
                        code="final_audit_failed",
                        message=f"{len(missing)} declarations did not reach their target state.",
                        affected_decl_names=sorted(missing),
                        suggested_plan_action="Inspect failed stage results and repair or re-plan this round.",
                    ),
                    summary=f"Round final audit failed: {len(missing)} declarations did not reach their target state.",
                )
            )
        if unsatisfied:
            return ctx.complete_step(
                RoundFinalAuditStepResult(
                    outcome="failed",
                    reached_target_decl_names=sorted(reached),
                    missing_target_decl_names=sorted(unsatisfied),
                    error=RoundTerminalReason(
                        code="final_audit_failed",
                        message=f"{len(unsatisfied)} declarations reached target state but did not satisfy proof policy.",
                        affected_decl_names=sorted(unsatisfied),
                        suggested_plan_action=(
                            "Inspect dependencies and either prove missing dependencies or plan an intermediate round with "
                            "require_target_state_satisfied=false when appropriate."
                        ),
                    ),
                    summary=f"Round final audit failed: {len(unsatisfied)} declarations did not satisfy proof policy.",
                )
            )
        projection = _lean_projection(ctx).refresh_node_projection(repo_root, node_path=input_model.node_path)
        if not projection.ok or projection.value is None:
            return ctx.complete_step(_final_audit_failed(_first_issue_message(projection.issues, "Projection refresh failed.")))
        return ctx.complete_step(
            RoundFinalAuditStepResult(
                outcome="passed",
                reached_target_decl_names=sorted(reached),
                readiness_summary="Round target states and required proof-policy satisfaction checks passed.",
                projection_summary=projection.value.summary,
                summary="Decl round final audit passed.",
            )
        )


class BuildRoundResultStepState(BaseStepState):
    state_type: Literal["decl_round_build_result"] = "decl_round_build_result"
    flow_outcome: Literal["completed", "blocked", "failed"]


class BuildRoundResultStep(BaseStep):
    step_type: ClassVar[str] = "decl_round_build_result_step"
    State: ClassVar[type[BaseStepState]] = BuildRoundResultStepState
    Result: ClassVar[type[BaseStepResult]] = BuildRoundResultStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "decl_round_build_result": BuildRoundResultStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        state = _build_result_state(self.state)
        flow = _load_decl_round_flow(ctx)
        input_model = _require_decl_round_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is not None:
            round_record = _decl_graph(ctx).get_round(repo_root, node_path=input_model.node_path, round_id=input_model.round_id)
            if not round_record.ok or round_record.value is None:
                raise FlowStepValidationError(_first_issue_message(round_record.issues, "Failed to load DeclGraph round."))
            if state.flow_outcome == "completed":
                revisions = _decl_graph(ctx).list_round_revisions(repo_root, node_path=input_model.node_path, round_id=input_model.round_id)
                if not revisions.ok or revisions.value is None:
                    raise FlowStepValidationError(_first_issue_message(revisions.issues, "Failed to load round revisions."))
                for revision in revisions.value:
                    if revision.change is not None and revision.change.kind == DeclChangeKind.DELETE:
                        continue
                    if revision.version_status != "open":
                        continue
                    committed = _decl_graph(ctx).commit_decl_revision(
                        repo_root,
                        node_path=input_model.node_path,
                        name=revision.decl_name,
                        revision=revision.revision,
                        state=revision.state,
                    )
                    if not committed.ok:
                        raise FlowStepValidationError(_first_issue_message(committed.issues, "Failed to commit round revision."))
            for change_id in round_record.value.change_ids:
                if change_id in round_record.value.change_summaries:
                    continue
                summarized = _decl_graph(ctx).write_decl_change_summary(
                    repo_root,
                    node_path=input_model.node_path,
                    round_id=input_model.round_id,
                    change_id=change_id,
                    summary=f"DeclGraphRoundFlow {state.flow_outcome} for change {change_id}.",
                )
                if not summarized.ok:
                    raise FlowStepValidationError(_first_issue_message(summarized.issues, "Failed to write decl change summary."))
            if not round_record.value.summary:
                summarized_round = _decl_graph(ctx).write_round_summary(
                    repo_root,
                    node_path=input_model.node_path,
                    round_id=input_model.round_id,
                    summary=f"DeclGraphRoundFlow finished with {state.flow_outcome}.",
                )
                if not summarized_round.ok:
                    raise FlowStepValidationError(_first_issue_message(summarized_round.issues, "Failed to write round summary."))
            result_kind = {
                "completed": DeclRoundResultKind.SUCCESS,
                "blocked": DeclRoundResultKind.BLOCKED,
                "failed": DeclRoundResultKind.FAILED,
            }[state.flow_outcome]
            marked = _decl_graph(ctx).mark_round_terminal(
                repo_root,
                node_path=input_model.node_path,
                round_id=input_model.round_id,
                result_kind=result_kind,
                reason=f"DeclGraphRoundFlow finished with {state.flow_outcome}.",
            )
            if not marked.ok:
                raise FlowStepValidationError(_first_issue_message(marked.issues, "Failed to mark DeclGraph round terminal."))
        return ctx.complete_step(
            BuildRoundResultStepResult(
                flow_outcome=state.flow_outcome,
                summary=f"Built DeclGraph round Flow result: {state.flow_outcome}.",
            )
        )


def new_decl_round_step_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _load_decl_round_flow(ctx: StepRunContext):
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    flow = flow_service.get_flow(ctx.flow_id)
    if flow.flow_type != "decl_graph_round":
        raise FlowStepValidationError(f"expected decl_graph_round flow, got {flow.flow_type}")
    return flow


def _require_decl_round_input(input_model):
    from lean_constellation.flows.content_node_task.decl_round.flow import DeclGraphRoundInput

    if not isinstance(input_model, DeclGraphRoundInput):
        raise FlowStepValidationError("decl_graph_round flow has invalid input")
    return input_model


def _repo_root(input_model) -> Path | None:
    raw = getattr(input_model, "repo_path", None)
    if not raw:
        return None
    return Path(raw)


def _decl_graph(ctx: StepRunContext):
    service = getattr(ctx.app, "decl_graph", None)
    if service is None:
        raise FlowStepValidationError("Lean decl_graph service is not registered in app services.")
    return service


def _lean_projection(ctx: StepRunContext):
    service = getattr(ctx.app, "lean_projection", None)
    if service is None:
        raise FlowStepValidationError("Lean projection service is not registered in app services.")
    return service


def _validation_snapshot(ctx: StepRunContext):
    service = getattr(ctx.app, "validation_snapshot", None)
    if service is None:
        raise FlowStepValidationError("Lean validation_snapshot service is not registered in app services.")
    return service


def _node(ctx: StepRunContext):
    service = getattr(ctx.app, "node", None)
    if service is None:
        raise FlowStepValidationError("Lean node service is not registered in app services.")
    return service


def _first_issue_message(issues: list[object], fallback: str) -> str:
    if issues:
        issue = issues[0]
        return str(getattr(issue, "message", None) or getattr(issue, "summary", None) or fallback)
    return fallback


def _invalid_start(input_model, message: str) -> RoundStartValidationStepResult:
    return RoundStartValidationStepResult(
        outcome="invalid",
        repo_key=getattr(input_model, "repo_key", None),
        node_path=getattr(input_model, "node_path", None),
        strategy_id=getattr(input_model, "strategy_id", None),
        round_id=getattr(input_model, "round_id", None),
        round_index=getattr(input_model, "round_index", None),
        error=RoundTerminalReason(code="invalid_round_state", message=message, suggested_plan_action="Return to ContentPlanAgent and repair round planning."),
        summary=message,
    )


def _delete_normalize_failed(message: str) -> DeleteAndNormalizeStepResult:
    return DeleteAndNormalizeStepResult(
        outcome="failed",
        error=RoundTerminalReason(code="projection_sync_failed", message=message),
        summary=message,
    )


def _prepare_failed(stage: DeclStageName, message: str, targets: list[str] | None = None) -> PrepareStageTargetsStepResult:
    return PrepareStageTargetsStepResult(
        outcome="failed",
        stage=stage,
        target_decl_names=list(targets or []),
        error=RoundTerminalReason(code="stage_gate_failed", message=message, stage=stage, affected_decl_names=list(targets or [])),
        summary=message,
    )


def _stage_gate_failed(stage: DeclStageName, message: str, targets: list[str] | None = None) -> StageGateAndAuditStepResult:
    return StageGateAndAuditStepResult(
        outcome="failed",
        stage=stage,
        rejected_decl_names=list(targets or []),
        error=RoundTerminalReason(code="stage_gate_failed", message=message, stage=stage, affected_decl_names=list(targets or [])),
        summary=message,
    )


def _final_audit_failed(message: str) -> RoundFinalAuditStepResult:
    return RoundFinalAuditStepResult(
        outcome="failed",
        error=RoundTerminalReason(code="final_audit_failed", message=message),
        summary=message,
    )


def _round_change_counts(ctx: StepRunContext, repo_root: Path, node_path: str, round_id: str) -> dict[str, int]:
    counts = {"change_count": 0, "create_count": 0, "update_count": 0, "delete_count": 0, "theorem_like_count": 0}
    graph = _decl_graph(ctx)
    revisions = graph.list_round_revisions(repo_root, node_path=node_path, round_id=round_id)
    if not revisions.ok or revisions.value is None:
        return counts
    for revision in revisions.value:
        change = revision.change
        if change is None:
            continue
        counts["change_count"] += 1
        if change.kind == DeclChangeKind.CREATE:
            counts["create_count"] += 1
        elif change.kind == DeclChangeKind.UPDATE:
            counts["update_count"] += 1
        elif change.kind == DeclChangeKind.DELETE:
            counts["delete_count"] += 1
        decl = graph.get_decl(repo_root, node_path=node_path, name=revision.decl_name)
        if decl.ok and decl.value is not None and _is_theorem_like(decl.value.kind):
            counts["theorem_like_count"] += 1
    return counts


def _round_revisions(ctx: StepRunContext, repo_root: Path, node_path: str, round_id: str):
    graph = _decl_graph(ctx)
    return graph.list_round_revisions(repo_root, node_path=node_path, round_id=round_id)


def _stage_targets(ctx: StepRunContext, repo_root: Path, node_path: str, round_id: str, stage: DeclStageName):
    graph = _decl_graph(ctx)
    revisions = _round_revisions(ctx, repo_root, node_path, round_id)
    if not revisions.ok or revisions.value is None:
        return graph.runtime.foundation.fail(revisions.issues)
    targets: list[DeclStageTargetMetadata] = []
    for revision in revisions.value:
        change = revision.change
        if change is None:
            continue
        if change.kind == DeclChangeKind.DELETE:
            continue
        if change.end_after_state is None:
            continue
        decl = graph.get_decl(repo_root, node_path=node_path, name=revision.decl_name)
        if not decl.ok or decl.value is None:
            return graph.runtime.foundation.fail(decl.issues)
        if _stage_required(stage, decl.value.kind, revision, change.end_after_state):
            targets.append(_decl_stage_target_metadata(revision))
    return graph.runtime.foundation.ok(sorted(targets, key=lambda item: item.decl_name))


def _decl_stage_target_metadata(revision: DeclRevision) -> DeclStageTargetMetadata:
    change = revision.change
    return DeclStageTargetMetadata(
        decl_name=revision.decl_name,
        change_kind=change.kind.value if change is not None else None,
        objective=change.objective if change is not None else None,
        start_before_state=change.start_before_state.value if change is not None and change.start_before_state is not None else None,
        end_after_state=change.end_after_state.value if change is not None and change.end_after_state is not None else None,
        require_target_state_satisfied=change.require_target_state_satisfied if change is not None else True,
        current_state=revision.state.value,
        current_revision=revision.revision,
        known_statement_deps=list(revision.statement_deps),
        known_proof_deps=list(revision.proof_deps),
    )


def _stage_required(stage: DeclStageName, kind: str, revision, end_after_state: DeclState) -> bool:
    if stage == "statement_nl":
        return not revision.statement_nl
    if stage == "statement_formal":
        return _state_rank(end_after_state) >= _state_rank(DeclState.DECLARED) and not _state_reaches(revision.state, DeclState.DECLARED)
    if stage == "proof_nl":
        return end_after_state == DeclState.PROVED and _is_theorem_like(kind) and not revision.proof_nl
    if stage == "proof_formal":
        return end_after_state == DeclState.PROVED and _is_theorem_like(kind) and not _state_reaches(revision.state, DeclState.PROVED)
    return False


def _formal_consistency_for_stage(
    ctx: StepRunContext,
    repo_root: Path,
    node_path: str,
    stage: DeclStageName,
    targets: list[str],
) -> StageGateAndAuditStepResult | None:
    if stage not in {"statement_formal", "proof_formal"}:
        return None
    check_stage = "statement" if stage == "statement_formal" else "proof"
    for decl_name in targets:
        gate = _validation_snapshot(ctx).check_formal_stage_consistency(repo_root, node_path=node_path, decl_name=decl_name, stage=check_stage)
        if not gate.ok or gate.value is None:
            return _stage_gate_failed(stage, _first_issue_message(gate.issues, "Formal stage consistency check failed."), targets)
        if not gate.value.passed:
            return StageGateAndAuditStepResult(
                outcome="failed",
                stage=stage,
                rejected_decl_names=list(targets),
                error=RoundTerminalReason(
                    code="stage_gate_failed",
                    message=gate.value.summary,
                    stage=stage,
                    affected_decl_names=[decl_name],
                    suggested_plan_action="Return to the formal worker with consistency findings or re-plan the round.",
                ),
                summary=gate.value.summary,
            )
    return None


def _is_theorem_like(kind: str) -> bool:
    return kind in {"theorem", "lemma", "proposition", "corollary"}


def _state_rank(state: DeclState) -> int:
    return {
        DeclState.OBSOLETE: -1,
        DeclState.PLANNED: 0,
        DeclState.SPECIFIED: 1,
        DeclState.DECLARED: 2,
        DeclState.PROOF_PLANNED: 3,
        DeclState.PROVED: 4,
    }[DeclState(state)]


def _state_reaches(current: DeclState, target: DeclState) -> bool:
    return _state_rank(current) >= _state_rank(target)


def _proof_availability_for_target_state(target: DeclState) -> ProofAvailability:
    return ProofAvailability.PROVED if target == DeclState.PROVED else ProofAvailability.DECLARED


def _round_revision_satisfies_proof_policy(
    ctx: StepRunContext,
    repo_root: Path,
    *,
    node_path: str,
    round_revisions: dict[str, DeclRevision],
    revision: DeclRevision,
    target_proof_availability: ProofAvailability,
    stack: list[str] | None = None,
) -> tuple[bool, str | None]:
    stack = stack or []
    decl_name = revision.decl_name
    stack_key = f"{Path(repo_root).name}:{node_path}:{decl_name}"
    if stack_key in stack:
        return False, f"Dependency cycle detected: {' -> '.join([*stack, stack_key])}."
    decl_result = _decl_graph(ctx).get_decl(repo_root, node_path=node_path, name=decl_name)
    if not decl_result.ok or decl_result.value is None:
        return False, _first_issue_message(decl_result.issues, f"Declaration {decl_name} is missing.")
    decl = decl_result.value
    required_state = _required_state_for_proof_availability(decl.kind, target_proof_availability)
    if not _state_reaches(revision.state, required_state):
        return False, f"{decl_name} is {revision.state.value}, expected at least {required_state.value}."
    stage = _required_formal_stage_for_proof_availability(decl.kind, target_proof_availability)
    check = revision.proof_lean_check if stage == "proof" else revision.statement_lean_check
    if not _lean_check_passed(check):
        return False, f"{decl_name} does not have an acceptable {stage} Lean check."

    requirements = _decl_graph(ctx).dependency_ref_requirements_for_proof_policy(
        decl,
        revision,
        target_proof_availability=target_proof_availability,
    )
    for dep_ref, dep_target in requirements:
        dep_label = _decl_ref_label(dep_ref, fallback_node_path=node_path)
        resolved = _resolve_dependency_ref(ctx, repo_root, ref=dep_ref, fallback_node_path=node_path, local_target=dep_target)
        if resolved is None:
            return False, f"Dependency {dep_label} could not be resolved or its provider is not stable."
        dep_root, dep_node, effective_target = resolved
        round_dep = round_revisions.get(dep_ref.name) if dep_root == repo_root and dep_node == node_path else None
        if round_dep is not None:
            satisfied, reason = _round_revision_satisfies_proof_policy(
                ctx,
                repo_root,
                node_path=node_path,
                round_revisions=round_revisions,
                revision=round_dep,
                target_proof_availability=effective_target,
                stack=[*stack, stack_key],
            )
            if not satisfied:
                return False, reason
            continue
        dep_report = _decl_graph(ctx).check_decl_proof_policy_satisfied(
            dep_root,
            node_path=dep_node,
            decl_name=dep_ref.name,
            target_proof_availability=effective_target,
        )
        if not dep_report.ok or dep_report.value is None:
            return False, _first_issue_message(dep_report.issues, f"Dependency {dep_label} proof policy check failed.")
        if not dep_report.value.proof_policy_satisfied:
            return False, dep_report.value.summary
    return True, None


def _resolve_dependency_ref(
    ctx: StepRunContext,
    repo_root: Path,
    *,
    ref: DeclRef,
    fallback_node_path: str,
    local_target: ProofAvailability,
) -> tuple[Path, str, ProofAvailability] | None:
    if ref.repo:
        foundation = getattr(ctx.app, "foundation", None)
        repo_workspace = getattr(ctx.app, "repo_workspace", None)
        if foundation is None or repo_workspace is None:
            return None
        provider_key = foundation.layout.ensure_safe_key(ref.repo)
        provider_root = Path(repo_root).parent / provider_key
        publication = repo_workspace.metadata.get_repo_publication(provider_root)
        if not publication.ok or publication.value is None or publication.value.publication.status.value != "stable":
            return None
        config = repo_workspace.metadata.get_repo_config(provider_root)
        if not config.ok or config.value is None:
            return None
        return provider_root, ref.node, config.value.config.target_proof_availability
    dep_node = ref.node
    if dep_node == "Main" and fallback_node_path != "Main":
        dep_node = fallback_node_path
    return Path(repo_root), dep_node, local_target


def _decl_ref_label(ref: DeclRef, *, fallback_node_path: str) -> str:
    node = ref.node
    if ref.repo is None and node == "Main" and fallback_node_path != "Main":
        node = fallback_node_path
    if ref.repo:
        return f"{ref.repo}:{node}:{ref.name}"
    return ref.name if node == fallback_node_path else f"{node}:{ref.name}"


def _required_state_for_proof_availability(kind: str, target: ProofAvailability) -> DeclState:
    if target == ProofAvailability.DECLARED:
        return DeclState.DECLARED
    return DeclState.PROVED if _is_theorem_like(kind) else DeclState.DECLARED


def _required_formal_stage_for_proof_availability(kind: str, target: ProofAvailability) -> str:
    if target == ProofAvailability.PROVED and _is_theorem_like(kind):
        return "proof"
    return "statement"


def _lean_check_passed(check: dict[str, str] | None) -> bool:
    if check is None:
        return False
    if _truthy(check.get("contains_axiom")) or _truthy(check.get("contains_admit")) or _truthy(check.get("contains_opaque")) or _truthy(check.get("contains_unsafe")):
        return False
    if _truthy(check.get("contains_sorry")) and not _truthy(check.get("allow_sorry")):
        return False
    status = (check.get("status") or "").strip().lower()
    if status:
        return status == "passed"
    passed = check.get("passed")
    if passed is not None:
        return _truthy(passed)
    return False


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "passed"}


def _is_theorem_like(kind: str) -> bool:
    return kind.strip().lower() in {"theorem", "lemma"}


def _prepare_stage_state(state: BaseStepState) -> PrepareStageTargetsStepState:
    if not isinstance(state, PrepareStageTargetsStepState):
        raise FlowStepValidationError("PrepareStageTargetsStep has invalid state")
    return state


def _stage_gate_state(state: BaseStepState) -> StageGateAndAuditStepState:
    if not isinstance(state, StageGateAndAuditStepState):
        raise FlowStepValidationError("StageGateAndAuditStep has invalid state")
    return state


def _build_result_state(state: BaseStepState) -> BuildRoundResultStepState:
    if not isinstance(state, BuildRoundResultStepState):
        raise FlowStepValidationError("BuildRoundResultStep has invalid state")
    return state


DECL_ROUND_STEP_TYPES: tuple[type[BaseStep], ...] = (
    RoundStartValidationStep,
    DeleteAndNormalizeStep,
    PrepareStageTargetsStep,
    StageGateAndAuditStep,
    RoundFinalAuditStep,
    BuildRoundResultStep,
)
