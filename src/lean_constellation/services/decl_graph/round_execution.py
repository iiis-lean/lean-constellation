"""Pure Service algorithms for DeclGraph round stage gates and closeout."""

from __future__ import annotations

import shutil
import tempfile
from time import perf_counter
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclReadinessBlocker,
    DeclRevisionRef,
    DeclRoundResultKind,
    DeclRoundStatus,
    DeclStage,
    DeclState,
)
from lean_constellation.services.foundation import FoundationContext, ServiceResult
from lean_constellation.services.foundation.module_layout import local_projection_path

if TYPE_CHECKING:
    from lean_constellation.services.decl_graph.service import DeclGraphService
    from lean_constellation.services.runtime import LeanRuntimeServices


DeclStageName = Literal["statement_nl", "statement_formal", "proof_nl", "proof_formal"]
RoundFlowOutcome = Literal["completed", "blocked", "failed"]


class RoundStageReview(StrictModel):
    outcome: Literal["passed", "rejected", "incomplete"]
    round_id: str | None = None
    node_path: str | None = None
    stage: DeclStageName | None = None
    reviewed_decl_names: list[str] = Field(default_factory=list)
    failed_decl_names: list[str] = Field(default_factory=list)
    missing_decl_names: list[str] = Field(default_factory=list)
    summary: str | None = None
    incomplete_reason: str | None = None


class RoundStageGateView(StrictModel):
    outcome: Literal["stage_passed", "retry_worker", "blocked", "failed"]
    stage: DeclStageName
    advanced_decl_names: list[str] = Field(default_factory=list)
    rejected_decl_names: list[str] = Field(default_factory=list)
    retry_count: int = 0
    retry_remaining: int = 0
    audit_summary: str | None = None
    feedback_summary: str | None = None
    issue_code: str | None = None
    issue_message: str | None = None
    affected_decl_names: list[str] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    summary: str


class RoundTargetStateFailure(StrictModel):
    revision_ref: DeclRevisionRef
    current_state: DeclState
    required_state: DeclState


class RoundReadinessFailure(StrictModel):
    revision_ref: DeclRevisionRef
    blocker: DeclReadinessBlocker


class RoundFinalAuditResult(StrictModel):
    passed: bool
    reached_target_revision_refs: list[DeclRevisionRef] = Field(default_factory=list)
    target_state_failures: list[RoundTargetStateFailure] = Field(default_factory=list)
    readiness_failures: list[RoundReadinessFailure] = Field(default_factory=list)
    summary: str


class RoundExecutionRecordResult(StrictModel):
    outcome: RoundFlowOutcome
    round_id: str
    status: Literal["awaiting_closeout"]
    summary: str


class RoundCloseoutResult(StrictModel):
    changed: bool
    result_kind: DeclRoundResultKind
    committed_revision_refs: list[DeclRevisionRef] = Field(default_factory=list)
    projection_outcome: Literal["refreshed", "deferred", "not_requested"]
    projection_summary: str | None = None
    round_id: str
    closeout_complete: bool = True
    summary: str


class DeclDraftSpec(StrictModel):
    name: str
    kind: str
    objective: str
    summary: str
    public: bool = False
    target_state: DeclState = DeclState.DECLARED
    require_target_state_satisfied: bool = True
    anticipated_statement_dep_names: list[str] = Field(default_factory=list)
    anticipated_proof_dep_names: list[str] = Field(default_factory=list)


class RoundDraftCreatedResult(StrictModel):
    round_id: str
    node_path: str
    strategy_id: str
    round_index: int
    status: Literal["draft"] = "draft"
    revision_refs: list[DeclRevisionRef] = Field(default_factory=list)
    summary: str


class DeclRoundExecutionComponent:
    """One business algorithm shared by Operator and Agent Flow callers."""

    def __init__(self, runtime: LeanRuntimeServices, graph: DeclGraphService) -> None:
        self.runtime = runtime
        self.graph = graph

    def create_round_with_decl_drafts(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        objective: str,
        declarations: list[DeclDraftSpec],
    ) -> ServiceResult[RoundDraftCreatedResult]:
        if not declarations:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_draft_batch_empty", "At least one declaration draft is required.", field="declarations")
            )
        graph_root = self.graph.graph_store.graph_root(repo_root, node_path=node_path)
        snapshot = _RoundTreesSnapshot([graph_root])
        try:
            round_record = self.graph.create_round_draft(
                repo_root,
                node_path=node_path,
                strategy_id=strategy_id,
                objective=objective,
            )
            if not round_record.ok or round_record.value is None:
                raise _CloseoutFailure(list(round_record.issues))
            created: list[DeclRevisionRef] = []
            for declaration in declarations:
                result = self.graph.create_decl(
                    repo_root,
                    node_path=node_path,
                    round_id=round_record.value.round_id,
                    **declaration.model_dump(),
                )
                if not result.ok or result.value is None or result.value.target_revision is None:
                    raise _CloseoutFailure(list(result.issues))
                created.append(
                    DeclRevisionRef(
                        change_id=result.value.change_id,
                        decl_name=result.value.decl_name,
                        revision=result.value.target_revision,
                    )
                )
            return self.runtime.foundation.ok(
                RoundDraftCreatedResult(
                    round_id=round_record.value.round_id,
                    node_path=node_path,
                    strategy_id=strategy_id,
                    round_index=round_record.value.round_index,
                    revision_refs=created,
                    summary=f"Created round {round_record.value.round_id} with {len(created)} declaration drafts.",
                )
            )
        except _CloseoutFailure as failure:
            rollback_failures = snapshot.restore()
            issues = [
                self.runtime.foundation.issue(
                    "round_decl_transaction_failed",
                    "Round and declaration drafts were not created as a complete transaction.",
                    object_ref=node_path,
                ),
                *failure.issues,
            ]
            if rollback_failures:
                issues.append(
                    self.runtime.foundation.issue(
                        "round_decl_transaction_rollback_failed",
                        "Round draft rollback did not fully restore graph truth.",
                        details={"failures": "; ".join(rollback_failures)},
                    )
                )
            return self.runtime.foundation.fail(issues)
        finally:
            snapshot.close()

    def gate_and_advance(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStageName,
        target_decl_names: list[str],
        review: RoundStageReview,
        retry_count: int = 0,
        max_retries: int = 2,
    ) -> ServiceResult[RoundStageGateView]:
        timings_ms: dict[str, float] = {}

        def with_timings(view: RoundStageGateView) -> RoundStageGateView:
            return view.model_copy(update={"timings_ms": dict(timings_ms)})

        review_started = perf_counter()
        context_issue = self._review_context_issue(
            review,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            targets=target_decl_names,
        )
        timings_ms["review_context"] = round((perf_counter() - review_started) * 1000, 3)

        if context_issue is not None:
            return self.runtime.foundation.ok(with_timings(self._failed(stage, context_issue, target_decl_names)))
        if review.outcome == "incomplete":
            return self.runtime.foundation.ok(
                with_timings(self._failed(stage, review.incomplete_reason or "Reviewer did not submit a result.", target_decl_names))
            )
        if review.outcome == "rejected":
            rejected = sorted(set(review.failed_decl_names) | set(review.missing_decl_names)) or list(target_decl_names)
            next_retry = retry_count + 1
            if retry_count < max_retries:
                return self.runtime.foundation.ok(
                    with_timings(RoundStageGateView(
                        outcome="retry_worker",
                        stage=stage,
                        rejected_decl_names=rejected,
                        retry_count=next_retry,
                        retry_remaining=max(max_retries - next_retry, 0),
                        feedback_summary=review.summary,
                        summary=f"{stage} review rejected; retry {next_retry} is available.",
                    ))
                )
            return self.runtime.foundation.ok(
                with_timings(RoundStageGateView(
                    outcome="failed",
                    stage=stage,
                    rejected_decl_names=rejected,
                    retry_count=next_retry,
                    retry_remaining=0,
                    feedback_summary=review.summary,
                    issue_code="review_retry_exhausted",
                    issue_message=review.summary or f"{stage} review retry budget exhausted.",
                    affected_decl_names=rejected,
                    summary=f"{stage} review retry budget exhausted.",
                ))
            )

        validation_started = perf_counter()
        validation = self._validate_stage(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            targets=target_decl_names,
        )
        timings_ms["stage_candidate_validation"] = round((perf_counter() - validation_started) * 1000, 3)
        if validation.value is not None:
            for name, duration in validation.value.timings_ms.items():
                timings_ms[f"stage_candidate_validation.{name}"] = duration
        if not validation.ok or validation.value is None:
            return self.runtime.foundation.ok(
                with_timings(self._failed(stage, self._issue_message(validation.issues, "Stage validation failed."), target_decl_names))
            )
        if not validation.value.passed:
            return self.runtime.foundation.ok(
                with_timings(self._failed(
                    stage,
                    self._issue_message(validation.value.issues, validation.value.summary),
                    target_decl_names,
                ))
            )
        audit_started = perf_counter()
        audit = self.runtime.validation_snapshot.run_round_local_audit(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
        )
        timings_ms["round_local_audit"] = round((perf_counter() - audit_started) * 1000, 3)
        if not audit.ok or audit.value is None:
            return self.runtime.foundation.fail(audit.issues)
        if not audit.value.passed:
            return self.runtime.foundation.ok(
                with_timings(RoundStageGateView(
                    outcome="blocked",
                    stage=stage,
                    rejected_decl_names=list(target_decl_names),
                    retry_count=retry_count,
                    retry_remaining=max(max_retries - retry_count, 0),
                    audit_summary=audit.value.summary,
                    issue_code="round_local_audit_failed",
                    issue_message=audit.value.summary,
                    affected_decl_names=list(target_decl_names),
                    summary=audit.value.summary,
                ))
            )
        mutation_started = perf_counter()
        advanced = self.graph.advance_stage_state(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            decl_names=list(target_decl_names),
        )
        timings_ms["stage_state_mutation"] = round((perf_counter() - mutation_started) * 1000, 3)
        if not advanced.ok or advanced.value is None:
            return self.runtime.foundation.fail(advanced.issues)
        return self.runtime.foundation.ok(
            with_timings(RoundStageGateView(
                outcome="stage_passed",
                stage=stage,
                advanced_decl_names=list(advanced.value),
                retry_count=retry_count,
                retry_remaining=max(max_retries - retry_count, 0),
                audit_summary=audit.value.summary,
                summary=f"{stage} passed for {len(target_decl_names)} declarations.",
            ))
        )

    def final_audit(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[RoundFinalAuditResult]:
        round_record = self.graph.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        reached: list[DeclRevisionRef] = []
        state_failures: list[RoundTargetStateFailure] = []
        readiness_failures: list[RoundReadinessFailure] = []
        for revision_ref in round_record.value.revision_refs:
            revision_result = self.graph.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=revision_ref.decl_name,
                revision=revision_ref.revision,
            )
            if not revision_result.ok or revision_result.value is None:
                return self.runtime.foundation.fail(revision_result.issues)
            revision = revision_result.value
            change = revision.change
            if change is None or change.target_state is None:
                state_failures.append(
                    RoundTargetStateFailure(
                        revision_ref=revision_ref,
                        current_state=revision.state,
                        required_state=DeclState.DECLARED,
                    )
                )
                continue
            if change.kind == DeclChangeKind.DELETE:
                reached.append(revision_ref)
                continue
            if not self._state_reaches(revision.state, change.target_state):
                state_failures.append(
                    RoundTargetStateFailure(
                        revision_ref=revision_ref,
                        current_state=revision.state,
                        required_state=change.target_state,
                    )
                )
                continue
            if change.require_target_state_satisfied:
                target = ProofAvailability.PROVED if change.target_state == DeclState.PROVED else ProofAvailability.DECLARED
                readiness = self.graph.check_round_decl_ready(
                    repo_root,
                    node_path=node_path,
                    round_id=round_id,
                    decl_name=revision_ref.decl_name,
                    required_availability=target,
                )
                if not readiness.ok or readiness.value is None:
                    return self.runtime.foundation.fail(readiness.issues)
                if not readiness.value.ready:
                    assert readiness.value.blocker is not None
                    readiness_failures.append(
                        RoundReadinessFailure(
                            revision_ref=revision_ref,
                            blocker=readiness.value.blocker,
                        )
                    )
                    continue
            reached.append(revision_ref)
        failed = bool(state_failures or readiness_failures)
        return self.runtime.foundation.ok(
            RoundFinalAuditResult(
                passed=not failed,
                reached_target_revision_refs=reached,
                target_state_failures=state_failures,
                readiness_failures=readiness_failures,
                summary=(
                    "Decl round final audit passed."
                    if not failed
                    else (
                        "Round final audit failed: "
                        f"{len(state_failures)} target-state failures and "
                        f"{len(readiness_failures)} readiness failures."
                    )
                ),
            )
        )

    def record_round_execution_result(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        outcome: RoundFlowOutcome,
        reason: str | None = None,
    ) -> ServiceResult[RoundExecutionRecordResult]:
        result_kind = {
            "completed": DeclRoundResultKind.SUCCESS,
            "blocked": DeclRoundResultKind.BLOCKED,
            "failed": DeclRoundResultKind.FAILED,
        }[outcome]
        if outcome == "completed":
            audit = self.final_audit(repo_root, node_path=node_path, round_id=round_id)
            if not audit.ok or audit.value is None:
                return self.runtime.foundation.fail(audit.issues)
            if not audit.value.passed:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "round_final_audit_failed",
                        audit.value.summary,
                        object_ref=round_id,
                    )
                )
        recorded = self.graph.strategy_round.record_round_execution_result(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            result_kind=result_kind,
            reason=reason,
        )
        if not recorded.ok or recorded.value is None:
            return self.runtime.foundation.fail(recorded.issues)
        return self.runtime.foundation.ok(
            RoundExecutionRecordResult(
                outcome=outcome,
                round_id=round_id,
                status="awaiting_closeout",
                summary=(
                    f"Recorded {outcome} execution for DeclGraph round {round_id}; "
                    "ContentPlan closeout is required."
                ),
            )
        )

    def closeout_round_by_plan(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        result_kind: DeclRoundResultKind | str,
        reason: str | None = None,
        acknowledged_by: str,
    ) -> ServiceResult[RoundCloseoutResult]:
        graph_root = self.graph.graph_store.graph_root(repo_root, node_path=node_path)
        projection_root = local_projection_path(
            repo_root,
            self.runtime.foundation.layout.node_projection_dir(FoundationContext(repo_root=repo_root), node_path),
        )
        snapshot = _RoundTreesSnapshot([graph_root, projection_root])
        try:
            return self._closeout_round_by_plan(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                result_kind=DeclRoundResultKind(result_kind),
                reason=reason,
                acknowledged_by=acknowledged_by,
            )
        except _CloseoutFailure as failure:
            rollback_failures = snapshot.restore()
            issues = list(failure.issues)
            if rollback_failures:
                issues.append(
                    self.runtime.foundation.issue(
                        "round_closeout_rollback_failed",
                        "Round closeout rollback did not fully restore project truth.",
                        details={"failures": "; ".join(rollback_failures)},
                    )
                )
            return self.runtime.foundation.fail(issues)
        finally:
            snapshot.close()

    def _closeout_round_by_plan(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        result_kind: DeclRoundResultKind,
        reason: str | None = None,
        acknowledged_by: str,
    ) -> ServiceResult[RoundCloseoutResult]:
        round_record = self.graph.get_round(repo_root, node_path=node_path, round_id=round_id)
        self._require(round_record)
        assert round_record.value is not None
        current_round = round_record.value
        normalized_reason = reason.strip() if reason and reason.strip() else None
        if (
            result_kind in {DeclRoundResultKind.BLOCKED, DeclRoundResultKind.FAILED}
            and normalized_reason is None
        ):
            raise _CloseoutFailure(
                [
                    self.runtime.foundation.issue(
                        "round_terminal_reason_required",
                        "Blocked or failed round result requires a reason.",
                        object_ref=round_id,
                        field="reason",
                    )
                ]
            )
        if current_round.status == DeclRoundStatus.COMMITTED:
            persisted = self.graph.strategy_round.persist_round_closeout(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                result_kind=result_kind,
                reason=normalized_reason,
                acknowledged_by=acknowledged_by,
            )
            self._require(persisted)
            assert persisted.value is not None
            _, changed = persisted.value
            return self.runtime.foundation.ok(
                RoundCloseoutResult(
                    changed=changed,
                    result_kind=result_kind,
                    committed_revision_refs=[],
                    projection_outcome="not_requested",
                    round_id=round_id,
                    summary="Declaration round closeout was already acknowledged.",
                )
            )
        if current_round.status == DeclRoundStatus.DRAFT:
            if result_kind == DeclRoundResultKind.SUCCESS:
                raise _CloseoutFailure(
                    [
                        self.runtime.foundation.issue(
                            "draft_round_success_invalid",
                            "A declaration round that never executed cannot be closed as success.",
                            object_ref=round_id,
                        )
                    ]
                )
        elif current_round.status == DeclRoundStatus.AWAITING_CLOSEOUT:
            allowed_results = {
                DeclRoundResultKind.SUCCESS: {
                    DeclRoundResultKind.SUCCESS,
                    DeclRoundResultKind.BLOCKED,
                    DeclRoundResultKind.FAILED,
                },
                DeclRoundResultKind.BLOCKED: {
                    DeclRoundResultKind.BLOCKED,
                    DeclRoundResultKind.FAILED,
                },
                DeclRoundResultKind.FAILED: {DeclRoundResultKind.FAILED},
            }
            execution_result = current_round.execution_result_kind
            if execution_result is None or result_kind not in allowed_results[execution_result]:
                raise _CloseoutFailure(
                    [
                        self.runtime.foundation.issue(
                            "round_closeout_result_incompatible",
                            "ContentPlan cannot upgrade the recorded round execution outcome.",
                            object_ref=round_id,
                            current=execution_result.value if execution_result is not None else None,
                            expected=result_kind.value,
                        )
                    ]
                )
        else:
            raise _CloseoutFailure(
                [
                    self.runtime.foundation.issue(
                        "round_not_ready_for_closeout",
                        "Declaration round is not ready for ContentPlan closeout.",
                        object_ref=round_id,
                        current=current_round.status.value,
                    )
                ]
            )
        revisions = self.graph.list_round_revisions(
            repo_root,
            node_path=node_path,
            round_id=round_id,
        )
        self._require(revisions)
        round_refs = {ref.decl_name: ref for ref in current_round.revision_refs}
        missing_summaries = [
            round_refs[decl_name].change_id
            for decl_name, revision in revisions.value or []
            if revision.change is None or not revision.change.summary
        ]
        if missing_summaries:
            raise _CloseoutFailure(
                [
                    self.runtime.foundation.issue(
                        "decl_change_summary_missing",
                        "Every round change must have its own summary before closeout.",
                        object_ref=round_id,
                        current=", ".join(missing_summaries),
                    )
                ]
            )
        if not current_round.summary:
            raise _CloseoutFailure(
                [
                    self.runtime.foundation.issue(
                        "round_summary_missing",
                        "Round summary is required before closeout.",
                        object_ref=round_id,
                    )
                ]
            )
        committed_refs: list[DeclRevisionRef] = []
        projection_summary: str | None = None
        projection_outcome: Literal["refreshed", "deferred", "not_requested"] = "not_requested"
        for decl_name, revision in revisions.value or []:
            decl = self.graph.get_decl(repo_root, node_path=node_path, name=decl_name)
            self._require(decl)
            assert decl.value is not None
            expected_ref = round_refs.get(decl_name)
            if expected_ref is None or decl.value.current_revision != expected_ref.revision:
                raise _CloseoutFailure(
                    [
                        self.runtime.foundation.issue(
                            "round_revision_not_current",
                            "Round closeout requires every affected revision to remain current.",
                            object_ref=decl_name,
                            current=str(decl.value.current_revision),
                            expected=str(expected_ref.revision if expected_ref is not None else None),
                        )
                    ]
                )
            if revision.status != "open":
                raise _CloseoutFailure(
                    [
                        self.runtime.foundation.issue(
                            "round_revision_not_open",
                            "Round closeout requires every affected revision to remain open.",
                            object_ref=f"{decl_name}@{revision.revision}",
                            current=str(revision.status),
                            expected="open",
                        )
                    ]
                )
            committed = self.graph.commit_decl_revision(
                repo_root,
                node_path=node_path,
                name=decl_name,
                revision=revision.revision,
                state=revision.state,
                apply_delete_lifecycle=result_kind == DeclRoundResultKind.SUCCESS,
            )
            self._require(committed)
            committed_refs.append(round_refs[decl_name])
        if result_kind == DeclRoundResultKind.SUCCESS:
            public_decls = self.graph.list_content_public_decls(repo_root, node_path=node_path)
            self._require(public_decls)
            deferred_public_names = sorted(
                decl.ref.name
                for decl in public_decls.value or []
                if not decl.ready and not decl.stale
            )
            if deferred_public_names:
                projection_outcome = "deferred"
                projection_summary = (
                    "Deferred node interface projection until public declarations satisfy the repo proof policy: "
                    + ", ".join(deferred_public_names)
                    + "."
                )
            else:
                projection = self.runtime.lean_projection.refresh_node_projection(repo_root, node_path=node_path)
                self._require(projection)
                projection_outcome = "refreshed"
                projection_summary = projection.value.summary if projection.value is not None else None
        persisted = self.graph.strategy_round.persist_round_closeout(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                result_kind=result_kind,
                reason=normalized_reason,
                acknowledged_by=acknowledged_by,
        )
        self._require(persisted)
        assert persisted.value is not None
        _, changed = persisted.value
        return self.runtime.foundation.ok(
            RoundCloseoutResult(
                changed=changed,
                result_kind=result_kind,
                committed_revision_refs=committed_refs,
                projection_outcome=projection_outcome,
                projection_summary=projection_summary,
                round_id=round_id,
                summary=f"ContentPlan closed declaration round as {result_kind.value}.",
            )
        )

    def _validate_stage(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStageName,
        targets: list[str],
    ):
        return self.graph.validate_round_stage_candidates(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=DeclStage(stage),
            target_decl_names=targets,
        )

    @staticmethod
    def _review_context_issue(
        review: RoundStageReview,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStageName,
        targets: list[str],
    ) -> str | None:
        issues: list[str] = []
        if review.round_id != round_id:
            issues.append(f"round_id={review.round_id!r}, expected {round_id!r}")
        if review.node_path != node_path:
            issues.append(f"node_path={review.node_path!r}, expected {node_path!r}")
        if review.stage != stage:
            issues.append(f"stage={review.stage!r}, expected {stage!r}")
        target_set = set(targets)
        reviewed = set(review.reviewed_decl_names)
        failed = set(review.failed_decl_names)
        missing = set(review.missing_decl_names)
        unexpected = sorted((reviewed | failed | missing) - target_set)
        if unexpected:
            issues.append(f"review result references declarations outside current target batch: {', '.join(unexpected)}")
        if review.outcome == "passed":
            if reviewed != target_set:
                issues.append("passed review result must review exactly the current target batch")
            if failed or missing:
                issues.append("passed review result must not contain failed or missing declarations")
        if not issues:
            return None
        return "Reviewer result context mismatch: " + "; ".join(issues)

    @staticmethod
    def _state_reaches(current: DeclState, target: DeclState) -> bool:
        rank = {
            DeclState.OBSOLETE: -1,
            DeclState.PLANNED: 0,
            DeclState.SPECIFIED: 1,
            DeclState.DECLARED: 2,
            DeclState.PROOF_PLANNED: 3,
            DeclState.PROVED: 4,
        }
        return rank[DeclState(current)] >= rank[DeclState(target)]

    @staticmethod
    def _issue_message(issues: list, fallback: str) -> str:  # noqa: ANN001
        if not issues:
            return fallback
        return str(getattr(issues[0], "message", None) or getattr(issues[0], "summary", None) or fallback)

    @staticmethod
    def _failed(stage: DeclStageName, message: str, targets: list[str]) -> RoundStageGateView:
        return RoundStageGateView(
            outcome="failed",
            stage=stage,
            rejected_decl_names=list(targets),
            issue_code="stage_gate_failed",
            issue_message=message,
            affected_decl_names=list(targets),
            summary=message,
        )

    @staticmethod
    def _require(result: ServiceResult) -> None:
        if not result.ok or result.value is None:
            raise _CloseoutFailure(list(result.issues))


class _CloseoutFailure(Exception):
    def __init__(self, issues: list) -> None:  # noqa: ANN001
        super().__init__("round closeout failed")
        self.issues = issues


class _RoundTreesSnapshot:
    def __init__(self, paths: list[Path]) -> None:
        self.temp_root = Path(tempfile.mkdtemp(prefix="lean-constellation-round-closeout-"))
        self.states: list[tuple[Path, bool, Path]] = []
        for index, path in enumerate(dict.fromkeys(Path(path) for path in paths)):
            backup = self.temp_root / str(index)
            existed = path.exists()
            if existed:
                shutil.copytree(path, backup) if path.is_dir() else shutil.copy2(path, backup)
            self.states.append((path, existed, backup))

    def restore(self) -> list[str]:
        failures: list[str] = []
        for path, existed, backup in reversed(self.states):
            try:
                shutil.rmtree(path) if path.is_dir() else path.unlink(missing_ok=True)
                if existed:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(backup, path) if backup.is_dir() else shutil.copy2(backup, path)
            except OSError as exc:
                failures.append(f"{path}: {exc}")
        return failures

    def close(self) -> None:
        shutil.rmtree(self.temp_root, ignore_errors=True)


__all__ = [
    "DeclRoundExecutionComponent",
    "DeclStageName",
    "DeclDraftSpec",
    "RoundCloseoutResult",
    "RoundDraftCreatedResult",
    "RoundFinalAuditResult",
    "RoundReadinessFailure",
    "RoundStageGateView",
    "RoundStageReview",
    "RoundTargetStateFailure",
]
