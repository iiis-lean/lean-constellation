"""Pure Service algorithms for DeclGraph round stage gates and closeout."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclRoundResultKind,
    DeclState,
    DeclGraphRoundView,
    DeclRevisionToolView,
)
from lean_constellation.services.decl_graph.proof_nl_validation import validate_proof_deps, validate_proof_nl_candidate
from lean_constellation.services.decl_graph.statement_nl_validation import validate_statement_nl_candidate
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
    summary: str


class RoundFinalAuditView(StrictModel):
    passed: bool
    reached_target_decl_names: list[str] = Field(default_factory=list)
    missing_target_decl_names: list[str] = Field(default_factory=list)
    issue_message: str | None = None
    summary: str


class RoundCloseoutView(StrictModel):
    outcome: RoundFlowOutcome
    committed_decl_names: list[str] = Field(default_factory=list)
    projection_summary: str | None = None
    round_id: str
    summary: str


class DeclDraftSpec(StrictModel):
    name: str
    kind: str
    objective: str
    summary: str
    public: bool = False
    target_state: DeclState = DeclState.DECLARED
    require_target_state_satisfied: bool = True


class RoundDraftBatchView(StrictModel):
    round: DeclGraphRoundView
    declarations: list[DeclRevisionToolView]
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
    ) -> ServiceResult[RoundDraftBatchView]:
        if not declarations:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_draft_batch_empty", "At least one declaration draft is required.", field="declarations")
            )
        graph_root = self.graph.graph_store.graph_root(repo_root, node_path=node_path)
        snapshot = _RoundTreesSnapshot([graph_root])
        try:
            round_record = self.graph.create_round_draft_view(
                repo_root,
                node_path=node_path,
                strategy_id=strategy_id,
                objective=objective,
            )
            if not round_record.ok or round_record.value is None:
                raise _CloseoutFailure(list(round_record.issues))
            created: list[DeclRevisionToolView] = []
            for declaration in declarations:
                result = self.graph.create_decl_revision_view(
                    repo_root,
                    node_path=node_path,
                    round_id=round_record.value.round_id,
                    **declaration.model_dump(),
                )
                if not result.ok or result.value is None:
                    raise _CloseoutFailure(list(result.issues))
                created.append(result.value)
            return self.runtime.foundation.ok(
                RoundDraftBatchView(
                    round=round_record.value,
                    declarations=created,
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
        context_issue = self._review_context_issue(
            review,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            targets=target_decl_names,
        )

        if context_issue is not None:
            return self.runtime.foundation.ok(self._failed(stage, context_issue, target_decl_names))
        if review.outcome == "incomplete":
            return self.runtime.foundation.ok(self._failed(stage, review.incomplete_reason or "Reviewer did not submit a result.", target_decl_names))
        if review.outcome == "rejected":
            rejected = sorted(set(review.failed_decl_names) | set(review.missing_decl_names)) or list(target_decl_names)
            next_retry = retry_count + 1
            if retry_count < max_retries:
                return self.runtime.foundation.ok(
                    RoundStageGateView(
                        outcome="retry_worker",
                        stage=stage,
                        rejected_decl_names=rejected,
                        retry_count=next_retry,
                        retry_remaining=max(max_retries - next_retry, 0),
                        feedback_summary=review.summary,
                        summary=f"{stage} review rejected; retry {next_retry} is available.",
                    )
                )
            return self.runtime.foundation.ok(
                RoundStageGateView(
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
                )
            )

        validation = self._validate_stage(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            targets=target_decl_names,
        )
        if not validation.ok:
            return self.runtime.foundation.ok(
                self._failed(stage, self._issue_message(validation.issues, "Stage validation failed."), target_decl_names)
            )
        audit = self.runtime.validation_snapshot.run_round_local_audit(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
        )
        if not audit.ok or audit.value is None:
            return self.runtime.foundation.fail(audit.issues)
        if not audit.value.passed:
            return self.runtime.foundation.ok(
                RoundStageGateView(
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
                )
            )
        advanced = self.graph.advance_stage_state(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            decl_names=list(target_decl_names),
        )
        if not advanced.ok or advanced.value is None:
            return self.runtime.foundation.fail(advanced.issues)
        return self.runtime.foundation.ok(
            RoundStageGateView(
                outcome="stage_passed",
                stage=stage,
                advanced_decl_names=list(advanced.value),
                retry_count=retry_count,
                retry_remaining=max(max_retries - retry_count, 0),
                audit_summary=audit.value.summary,
                summary=f"{stage} passed for {len(target_decl_names)} declarations.",
            )
        )

    def final_audit(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[RoundFinalAuditView]:
        revisions = self.graph.list_round_revisions(repo_root, node_path=node_path, round_id=round_id)
        if not revisions.ok or revisions.value is None:
            return self.runtime.foundation.fail(revisions.issues)
        reached: list[str] = []
        missing: list[str] = []
        unsatisfied: list[str] = []
        round_revisions = dict(revisions.value)
        for decl_name, revision in revisions.value:
            change = revision.change
            if change is None:
                missing.append(decl_name)
                continue
            if change.kind == DeclChangeKind.DELETE:
                reached.append(decl_name)
                continue
            if change.target_state is None or not self._state_reaches(revision.state, change.target_state):
                missing.append(decl_name)
                continue
            reached.append(decl_name)
            if change.require_target_state_satisfied:
                target = ProofAvailability.PROVED if change.target_state == DeclState.PROVED else ProofAvailability.DECLARED
                satisfied, _reason = self.round_revision_satisfies_proof_policy(
                    repo_root,
                    node_path=node_path,
                    round_revisions=round_revisions,
                    decl_name=decl_name,
                    revision=revision,
                    target_proof_availability=target,
                )
                if not satisfied:
                    unsatisfied.append(decl_name)
        failed = missing or unsatisfied
        affected = missing or unsatisfied
        message = None
        if missing:
            message = f"{len(missing)} declarations did not reach their target state."
        elif unsatisfied:
            message = f"{len(unsatisfied)} declarations reached target state but did not satisfy proof policy."
        return self.runtime.foundation.ok(
            RoundFinalAuditView(
                passed=not failed,
                reached_target_decl_names=sorted(reached),
                missing_target_decl_names=sorted(affected),
                issue_message=message,
                summary="Decl round final audit passed." if not failed else f"Round final audit failed: {message}",
            )
        )

    def build_round_result(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        outcome: RoundFlowOutcome,
    ) -> ServiceResult[RoundCloseoutView]:
        graph_root = self.graph.graph_store.graph_root(repo_root, node_path=node_path)
        projection_root = local_projection_path(
            repo_root,
            self.runtime.foundation.layout.node_projection_dir(FoundationContext(repo_root=repo_root), node_path),
        )
        snapshot = _RoundTreesSnapshot([graph_root, projection_root])
        try:
            return self._build_round_result(repo_root, node_path=node_path, round_id=round_id, outcome=outcome)
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

    def _build_round_result(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        outcome: RoundFlowOutcome,
    ) -> ServiceResult[RoundCloseoutView]:
        round_record = self.graph.get_round(repo_root, node_path=node_path, round_id=round_id)
        self._require(round_record)
        committed_names: list[str] = []
        projection_summary: str | None = None
        revisions = self.graph.list_round_revisions(repo_root, node_path=node_path, round_id=round_id)
        self._require(revisions)
        if outcome == "completed":
            audit = self.final_audit(repo_root, node_path=node_path, round_id=round_id)
            self._require(audit)
            if audit.value is None or not audit.value.passed:
                issue = self.runtime.foundation.issue(
                    "round_final_audit_failed",
                    audit.value.summary if audit.value is not None else "Round final audit failed.",
                    object_ref=round_id,
                )
                raise _CloseoutFailure([issue])
        for decl_name, revision in revisions.value or []:
            if revision.status != "open":
                continue
            committed = self.graph.commit_decl_revision(
                repo_root,
                node_path=node_path,
                name=decl_name,
                revision=revision.revision,
                state=revision.state,
                apply_delete_lifecycle=outcome == "completed",
            )
            self._require(committed)
            committed_names.append(decl_name)
        if outcome == "completed":
            public_decls = self.graph.list_content_public_decls(repo_root, node_path=node_path)
            self._require(public_decls)
            deferred_public_names = sorted(
                decl.ref.name
                for decl in public_decls.value or []
                if not decl.ready and not decl.stale
            )
            if deferred_public_names:
                projection_summary = (
                    "Deferred node interface projection until public declarations satisfy the repo proof policy: "
                    + ", ".join(deferred_public_names)
                    + "."
                )
            else:
                projection = self.runtime.lean_projection.refresh_node_projection(repo_root, node_path=node_path)
                self._require(projection)
                projection_summary = projection.value.summary if projection.value is not None else None
        current_round = self.graph.get_round(repo_root, node_path=node_path, round_id=round_id)
        self._require(current_round)
        assert current_round.value is not None
        for change_id in current_round.value.change_ids:
            if change_id in current_round.value.change_summaries:
                continue
            self._require(
                self.graph.write_decl_change_summary(
                    repo_root,
                    node_path=node_path,
                    round_id=round_id,
                    change_id=change_id,
                    summary=f"DeclGraphRoundFlow {outcome} for change {change_id}.",
                )
            )
        current_round = self.graph.get_round(repo_root, node_path=node_path, round_id=round_id)
        self._require(current_round)
        assert current_round.value is not None
        if not current_round.value.summary:
            self._require(
                self.graph.write_round_summary(
                    repo_root,
                    node_path=node_path,
                    round_id=round_id,
                    summary=f"DeclGraphRoundFlow finished with {outcome}.",
                )
            )
        result_kind = {
            "completed": DeclRoundResultKind.SUCCESS,
            "blocked": DeclRoundResultKind.BLOCKED,
            "failed": DeclRoundResultKind.FAILED,
        }[outcome]
        self._require(
            self.graph.mark_round_terminal(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                result_kind=result_kind,
                reason=f"DeclGraphRoundFlow finished with {outcome}.",
            )
        )
        return self.runtime.foundation.ok(
            RoundCloseoutView(
                outcome=outcome,
                committed_decl_names=sorted(committed_names),
                projection_summary=projection_summary,
                round_id=round_id,
                summary=f"Built DeclGraph round result: {outcome}.",
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
    ) -> ServiceResult[None]:
        for decl_name in targets:
            if stage == "statement_nl":
                checked = validate_statement_nl_candidate(
                    self.runtime,
                    repo_root,
                    node_path=node_path,
                    round_id=round_id,
                    decl_name=decl_name,
                )
            elif stage == "proof_nl":
                checked = validate_proof_nl_candidate(
                    self.runtime,
                    repo_root,
                    node_path=node_path,
                    round_id=round_id,
                    decl_name=decl_name,
                )
            else:
                formal_stage = "statement" if stage == "statement_formal" else "proof"
                checked = self.runtime.validation_snapshot.check_formal_stage_consistency(
                    repo_root,
                    node_path=node_path,
                    decl_name=decl_name,
                    stage=formal_stage,
                )
                if checked.ok and checked.value is not None and not checked.value.passed:
                    return self.runtime.foundation.fail(checked.value.issues)
                if checked.ok and stage == "proof_formal":
                    decl = self.graph.get_decl(repo_root, node_path=node_path, name=decl_name)
                    if not decl.ok or decl.value is None:
                        return self.runtime.foundation.fail(decl.issues)
                    revision = self.graph.get_decl_revision(
                        repo_root,
                        node_path=node_path,
                        name=decl_name,
                        revision=decl.value.current_revision,
                    )
                    if not revision.ok or revision.value is None:
                        return self.runtime.foundation.fail(revision.issues)
                    deps = list(revision.value.proof.deps) if revision.value.proof is not None else []
                    checked = validate_proof_deps(
                        self.runtime,
                        repo_root,
                        node_path=node_path,
                        round_id=round_id,
                        decl_name=decl_name,
                        deps=deps,
                    )
            if not checked.ok:
                return self.runtime.foundation.fail(checked.issues)
        return self.runtime.foundation.ok(None)

    def round_revision_satisfies_proof_policy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_revisions: dict[str, object],
        decl_name: str,
        revision,
        target_proof_availability: ProofAvailability,
        stack: list[str] | None = None,
    ) -> tuple[bool, str | None]:
        stack = stack or []
        stack_key = f"{Path(repo_root).name}:{node_path}:{decl_name}"
        if stack_key in stack:
            return False, f"Dependency cycle detected: {' -> '.join([*stack, stack_key])}."
        decl_result = self.graph.get_decl(repo_root, node_path=node_path, name=decl_name)
        if not decl_result.ok or decl_result.value is None:
            return False, self._issue_message(decl_result.issues, f"Declaration {decl_name} is missing.")
        decl = decl_result.value
        required_state = self._required_state(decl.kind, target_proof_availability)
        if not self._state_reaches(revision.state, required_state):
            return False, f"{decl_name} is {revision.state.value}, expected at least {required_state.value}."
        formal_stage = "proof" if target_proof_availability == ProofAvailability.PROVED and self._theorem_like(decl.kind) else "statement"
        check = revision.proof_lean_check if formal_stage == "proof" else revision.statement_lean_check
        if not self._lean_check_passed(check):
            return False, f"{decl_name} does not have an acceptable {formal_stage} Lean check."
        requirements = self.graph.dependency_ref_requirements_for_proof_policy(
            decl,
            revision,
            target_proof_availability=target_proof_availability,
        )
        for dep_ref, dep_target in requirements:
            dep_label = self._decl_ref_label(dep_ref, fallback_node_path=node_path)
            dep_node = dep_ref.node
            if dep_ref.repo is None and dep_node == "Main" and node_path != "Main":
                dep_node = node_path
            round_dep = round_revisions.get(dep_ref.name) if dep_ref.repo is None and dep_node == node_path else None
            if round_dep is not None and getattr(round_dep, "revision", None) == dep_ref.revision:
                satisfied, reason = self.round_revision_satisfies_proof_policy(
                    repo_root,
                    node_path=node_path,
                    round_revisions=round_revisions,
                    decl_name=dep_ref.name,
                    revision=round_dep,
                    target_proof_availability=dep_target,
                    stack=[*stack, stack_key],
                )
                if not satisfied:
                    return False, reason
                continue
            if (
                dep_ref.repo is None
                and dep_node == node_path
                and dep_ref.name not in round_revisions
            ):
                local_dep = self.graph.get_decl_revision(
                    repo_root,
                    node_path=node_path,
                    name=dep_ref.name,
                    revision=dep_ref.revision,
                )
                if (
                    local_dep.ok
                    and local_dep.value is not None
                    and local_dep.value.status == "committed"
                ):
                    satisfied, reason = self.round_revision_satisfies_proof_policy(
                        repo_root,
                        node_path=node_path,
                        round_revisions=round_revisions,
                        decl_name=dep_ref.name,
                        revision=local_dep.value,
                        target_proof_availability=dep_target,
                        stack=[*stack, stack_key],
                    )
                    if not satisfied:
                        return False, reason
                    continue
            resolved = self._resolve_dependency_ref(
                repo_root,
                ref=dep_ref,
                fallback_node_path=node_path,
                local_target=dep_target,
            )
            if not resolved.ok:
                return False, self._issue_message(resolved.issues, f"Dependency {dep_label} provider resolution failed.")
            if resolved.value is None:
                return False, f"Dependency {dep_label} could not be resolved or its provider is not stable."
            dep_root, dep_node, effective_target = resolved.value
            report = self.graph.check_decl_proof_policy_satisfied(
                dep_root,
                node_path=dep_node,
                decl_name=dep_ref.name,
                target_proof_availability=effective_target,
            )
            if not report.ok or report.value is None:
                return False, self._issue_message(report.issues, f"Dependency {dep_label} proof policy check failed.")
            if not report.value.proof_policy_satisfied:
                return False, report.value.summary
        return True, None

    def _resolve_dependency_ref(
        self,
        repo_root: Path,
        *,
        ref: DeclRef,
        fallback_node_path: str,
        local_target: ProofAvailability,
    ) -> ServiceResult[tuple[Path, str, ProofAvailability] | None]:
        if ref.repo:
            provider_key = self.runtime.foundation.layout.ensure_safe_key(ref.repo)
            provider_root = Path(repo_root).parent / provider_key
            config = self.runtime.repo_workspace.metadata.get_repo_config(provider_root)
            if not config.ok or config.value is None:
                return self.runtime.foundation.fail(config.issues)
            effective_target = config.value.config.target_proof_availability
            compatible = self.graph.ref_compatibility.resolve_public_decl_ref(
                repo_root,
                ref=ref,
                required_availability=effective_target,
            )
            if not compatible.ok:
                return self.runtime.foundation.fail(compatible.issues)
            if compatible.value is None or not compatible.value.compatible:
                return self.runtime.foundation.ok(None)
            return self.runtime.foundation.ok((provider_root, ref.node, effective_target))
        dep_node = ref.node
        if dep_node == "Main" and fallback_node_path != "Main":
            dep_node = fallback_node_path
        local_ref = ref.model_copy(update={"node": dep_node})
        compatible = self.graph.ref_compatibility.resolve_decl_ref(
            repo_root,
            ref=local_ref,
            required_availability=local_target,
        )
        if not compatible.ok or compatible.value is None or not compatible.value.compatible:
            return self.runtime.foundation.ok(None)
        return self.runtime.foundation.ok((Path(repo_root), dep_node, local_target))

    @staticmethod
    def _decl_ref_label(ref: DeclRef, *, fallback_node_path: str) -> str:
        node = ref.node
        if ref.repo is None and node == "Main" and fallback_node_path != "Main":
            node = fallback_node_path
        if ref.repo:
            return f"{ref.repo}:{node}:{ref.name}"
        return ref.name if node == fallback_node_path else f"{node}:{ref.name}"

    @staticmethod
    def _required_state(kind: str, target: ProofAvailability) -> DeclState:
        if target == ProofAvailability.DECLARED:
            return DeclState.DECLARED
        return DeclState.PROVED if DeclRoundExecutionComponent._theorem_like(kind) else DeclState.DECLARED

    @staticmethod
    def _theorem_like(kind: str) -> bool:
        return kind.strip().lower() in {"theorem", "lemma", "proposition", "corollary"}

    @staticmethod
    def _lean_check_passed(check: object) -> bool:
        if check is None:
            return False
        if hasattr(check, "model_dump"):
            check = check.model_dump(mode="json")
        if not isinstance(check, dict):
            return False
        for key in ("contains_axiom", "contains_admit", "contains_opaque", "contains_unsafe"):
            if DeclRoundExecutionComponent._truthy(check.get(key)):
                return False
        if DeclRoundExecutionComponent._truthy(check.get("contains_sorry")) and not DeclRoundExecutionComponent._truthy(check.get("allow_sorry")):
            return False
        status = str(check.get("status") or "").strip().lower()
        return status == "passed" if status else DeclRoundExecutionComponent._truthy(check.get("passed"))

    @staticmethod
    def _truthy(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "passed"}

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
    "RoundCloseoutView",
    "DeclDraftSpec",
    "RoundDraftBatchView",
    "RoundFinalAuditView",
    "RoundStageGateView",
    "RoundStageReview",
]
