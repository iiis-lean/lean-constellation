"""DeclGraphService composition and public wrappers."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.domain.repo_release import DeclAvailabilityIndex
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.dependency import DeclDependencyComponent
from lean_constellation.services.decl_graph.models import (
    DeclChangeView,
    DeclDeleteClosureView,
    DeclDependencyClosureView,
    DeclDependencyMutationReceipt,
    DeclFileRevisionView,
    DeclGraphIndex,
    DeclGraphRoundView,
    DeclGraphStoreView,
    DeclGraphStrategyView,
    DeclManagedProjectionEffect,
    DeclOriginMutationReceipt,
    DeclReviewMarkRecord,
    DeclReviewMarkView,
    DeclReadinessReport,
    Decl,
    DeclRevision,
    DeclStageWriteReceipt,
    DeclTextMutationReceipt,
    DeclView,
    DeclGraphRound,
    DeclRoundResultKind,
    DeclRoundDraftDiscardReceipt,
    DeclStage,
    DeclState,
    DeclGraphStrategy,
    StageReviewResultView,
)
from lean_constellation.services.decl_graph.review_gate import ReviewGateComponent
from lean_constellation.services.decl_graph.readiness import DeclReadinessComponent
from lean_constellation.services.decl_graph.strategy_round import StrategyRoundComponent
from lean_constellation.services.decl_graph.stage_mutation import StageMutationComponent
from lean_constellation.services.decl_graph.views import DeclGraphViewMapper
from lean_constellation.services.decl_graph.declared_api import DeclaredApiFingerprintComponent
from lean_constellation.services.decl_graph.ref_compatibility import DeclRefCompatibilityComponent
from lean_constellation.services.decl_graph.release_guard import DeclReleaseGuard
from lean_constellation.services.decl_graph.round_execution import (
    DeclRoundExecutionComponent,
    DeclStageName,
    RoundCloseoutResult,
    RoundExecutionRecordResult,
    RoundFinalAuditResult,
    RoundStageGateView,
    RoundStageReview,
    DeclDraftSpec,
    RoundDraftCreatedResult,
)
from lean_constellation.services.decl_graph.projection_transaction import (
    mutate_decl_truth_only,
    mutate_decl_with_projection,
)
from lean_constellation.services.decl_graph.stage_validation import (
    DeclStageValidationResult,
    validate_round_stage_candidates,
)
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.lean_projection.lean_check import LeanCheckView
from lean_constellation.services.node.export import DeclPublicView
from lean_constellation.services.validation_snapshot.audit import AuditReport

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices

T = TypeVar("T")


def _business_changed(before: DeclRevision, after: DeclRevision) -> bool:
    return before.model_dump(mode="json", exclude={"updated_at"}) != after.model_dump(
        mode="json",
        exclude={"updated_at"},
    )


def _managed_projection_effect(
    *,
    stage: str | None,
    changed: bool,
    changed_files: list[str],
    reread_required: bool,
) -> DeclManagedProjectionEffect | None:
    if not (changed or changed_files or reread_required):
        return None
    return DeclManagedProjectionEffect(
        stage=stage,
        changed_files=changed_files,
        reread_required=reread_required,
    )


def _stage_collection(
    revision: DeclRevision,
    *,
    section: Literal["Statement", "Proof"],
    collection: Literal["origins", "dependencies"],
) -> list[object]:
    stage = revision.statement if section == "Statement" else revision.proof
    if stage is None:
        return []
    if collection == "origins":
        return list(stage.nl.origin if stage.nl is not None else [])
    return list(stage.deps)


class DeclGraphService:
    """Composition root for Content node declaration graph services."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        graph_store: GraphStoreComponent | None = None,
        strategy_round: StrategyRoundComponent | None = None,
        decl_catalog: DeclCatalogComponent | None = None,
        stage_mutation: StageMutationComponent | None = None,
        review_gate: ReviewGateComponent | None = None,
        dependency: DeclDependencyComponent | None = None,
        readiness: DeclReadinessComponent | None = None,
        view_mapper: DeclGraphViewMapper | None = None,
        declared_api: DeclaredApiFingerprintComponent | None = None,
        ref_compatibility: DeclRefCompatibilityComponent | None = None,
        release_guard: DeclReleaseGuard | None = None,
        round_execution: DeclRoundExecutionComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.views = view_mapper or DeclGraphViewMapper()
        self.graph_store = graph_store or GraphStoreComponent(runtime)
        self.strategy_round = strategy_round or StrategyRoundComponent(runtime, self.graph_store)
        self.decl_catalog = decl_catalog or DeclCatalogComponent(runtime, self.graph_store, self.strategy_round)
        self.stage_mutation = stage_mutation or StageMutationComponent(
            runtime,
            self.graph_store,
            self.strategy_round,
            self.decl_catalog,
        )
        self.review_gate = review_gate or ReviewGateComponent(
            runtime,
            self.graph_store,
            self.strategy_round,
            self.decl_catalog,
        )
        self.dependency = dependency or DeclDependencyComponent(runtime, self.decl_catalog)
        self.declared_api = declared_api or DeclaredApiFingerprintComponent(runtime)
        self.release_guard = release_guard or DeclReleaseGuard(runtime)
        self.decl_catalog.release_guard = self.release_guard
        self.ref_compatibility = ref_compatibility or DeclRefCompatibilityComponent(runtime, self.declared_api)
        self.readiness = readiness or DeclReadinessComponent(runtime, self.decl_catalog, self.dependency)
        self.round_execution = round_execution or DeclRoundExecutionComponent(runtime, self)

    def create_round_with_decl_drafts(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        objective: str,
        declarations: list[DeclDraftSpec],
    ) -> ServiceResult[RoundDraftCreatedResult]:
        return self.round_execution.create_round_with_decl_drafts(
            repo_root,
            node_path=node_path,
            strategy_id=strategy_id,
            objective=objective,
            declarations=declarations,
        )

    def gate_and_advance_round_stage(
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
        return self.round_execution.gate_and_advance(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            target_decl_names=target_decl_names,
            review=review,
            retry_count=retry_count,
            max_retries=max_retries,
        )

    def validate_round_stage_candidates(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStage | str,
        target_decl_names: list[str],
    ) -> ServiceResult[DeclStageValidationResult]:
        return validate_round_stage_candidates(
            self.runtime,
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            target_decl_names=target_decl_names,
        )

    def audit_round_final(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
    ) -> ServiceResult[RoundFinalAuditResult]:
        return self.round_execution.final_audit(repo_root, node_path=node_path, round_id=round_id)

    def record_round_execution_result(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        outcome: str,
        reason: str | None = None,
    ) -> ServiceResult[RoundExecutionRecordResult]:
        return self.round_execution.record_round_execution_result(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            outcome=outcome,  # type: ignore[arg-type]
            reason=reason,
        )

    def reopen_failed_round_execution(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        failed_step_id: str,
    ) -> ServiceResult[DeclGraphRound]:
        return self.strategy_round.reopen_failed_round_execution(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            failed_step_id=failed_step_id,
        )

    def validate_failed_round_execution_restart(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        failed_step_id: str,
    ) -> ServiceResult[DeclGraphRound]:
        return self.strategy_round.validate_failed_round_execution_restart(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            failed_step_id=failed_step_id,
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
        return self.round_execution.closeout_round_by_plan(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            result_kind=result_kind,
            reason=reason,
            acknowledged_by=acknowledged_by,
        )

    def ensure_decl_graph(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStoreView]:
        return self.graph_store.ensure_graph(repo_root, node_path=node_path)

    def get_decl_graph_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphIndex]:
        return self.graph_store.get_index(repo_root, node_path=node_path)

    def get_decl_graph_store_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStoreView]:
        return self.graph_store.get_store_view(repo_root, node_path=node_path)

    def rebuild_decl_graph_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphIndex]:
        return self.graph_store.rebuild_index(repo_root, node_path=node_path)

    def ensure_open_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        objective: str,
        rationale: str | None = None,
    ) -> ServiceResult[DeclGraphStrategy]:
        return self.strategy_round.ensure_open_strategy(
            repo_root,
            node_path=node_path,
            objective=objective,
            rationale=rationale,
        )

    def ensure_open_strategy_view(
        self,
        repo_root: Path,
        *,
        node_path: str,
        objective: str,
        rationale: str | None = None,
    ) -> ServiceResult[DeclGraphStrategyView]:
        strategy = self.ensure_open_strategy(repo_root, node_path=node_path, objective=objective, rationale=rationale)
        if not strategy.ok or strategy.value is None:
            return self.runtime.foundation.fail(strategy.issues)
        return self.runtime.foundation.ok(self.views.strategy_view(strategy.value))

    def close_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        summary: str,
        reason: str | None = None,
        failed: bool = False,
    ) -> ServiceResult[DeclGraphStrategy]:
        return self.strategy_round.close_strategy(
            repo_root,
            node_path=node_path,
            strategy_id=strategy_id,
            summary=summary,
            reason=reason,
            failed=failed,
        )

    def close_strategy_view(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        summary: str,
        reason: str | None = None,
        failed: bool = False,
    ) -> ServiceResult[DeclGraphStrategyView]:
        strategy = self.close_strategy(
            repo_root,
            node_path=node_path,
            strategy_id=strategy_id,
            summary=summary,
            reason=reason,
            failed=failed,
        )
        if not strategy.ok or strategy.value is None:
            return self.runtime.foundation.fail(strategy.issues)
        return self.runtime.foundation.ok(self.views.strategy_view(strategy.value))

    def create_round_draft(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        objective: str,
    ) -> ServiceResult[DeclGraphRound]:
        return self.strategy_round.create_round_draft(
            repo_root,
            node_path=node_path,
            strategy_id=strategy_id,
            objective=objective,
        )

    def create_round_draft_view(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        objective: str,
    ) -> ServiceResult[DeclGraphRoundView]:
        round_record = self.create_round_draft(repo_root, node_path=node_path, strategy_id=strategy_id, objective=objective)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        return self.runtime.foundation.ok(self.views.round_view(round_record.value))

    def discard_round_draft(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        reason: str,
        discarded_by: str,
    ) -> ServiceResult[DeclRoundDraftDiscardReceipt]:
        return self.decl_catalog.discard_round_draft(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            reason=reason,
            discarded_by=discarded_by,
        )

    def start_round(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclGraphRound]:
        return self.strategy_round.start_round(repo_root, node_path=node_path, round_id=round_id)

    def write_decl_change_summary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        change_id: str,
        summary: str,
    ) -> ServiceResult[DeclGraphRound]:
        return self.decl_catalog.write_decl_change_summary(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            change_id=change_id,
            summary=summary,
        )

    def write_decl_change_summary_view(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        change_id: str,
        summary: str,
    ) -> ServiceResult[DeclGraphRoundView]:
        round_record = self.write_decl_change_summary(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            change_id=change_id,
            summary=summary,
        )
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        return self.runtime.foundation.ok(self.views.round_view(round_record.value))

    def write_round_summary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        summary: str,
    ) -> ServiceResult[DeclGraphRound]:
        return self.decl_catalog.write_round_summary(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            summary=summary,
        )

    def write_round_summary_view(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        summary: str,
    ) -> ServiceResult[DeclGraphRoundView]:
        round_record = self.write_round_summary(repo_root, node_path=node_path, round_id=round_id, summary=summary)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        return self.runtime.foundation.ok(self.views.round_view(round_record.value))

    def mark_round_terminal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        result_kind: DeclRoundResultKind | str,
        reason: str | None = None,
        acknowledged_by: str = "system",
    ) -> ServiceResult[DeclGraphRound]:
        closed = self.closeout_round_by_plan(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            result_kind=result_kind,
            reason=reason,
            acknowledged_by=acknowledged_by,
        )
        if not closed.ok:
            return self.runtime.foundation.fail(closed.issues)
        return self.strategy_round.get_round(
            repo_root,
            node_path=node_path,
            round_id=round_id,
        )

    def mark_round_terminal_view(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        result_kind: DeclRoundResultKind | str,
        reason: str | None = None,
        acknowledged_by: str = "system",
    ) -> ServiceResult[DeclGraphRoundView]:
        round_record = self.mark_round_terminal(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            result_kind=result_kind,
            reason=reason,
            acknowledged_by=acknowledged_by,
        )
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        return self.runtime.foundation.ok(self.views.round_view(round_record.value))

    def get_strategy(self, repo_root: Path, *, node_path: str, strategy_id: str) -> ServiceResult[DeclGraphStrategy]:
        return self.strategy_round.get_strategy(repo_root, node_path=node_path, strategy_id=strategy_id)

    def get_strategy_view(self, repo_root: Path, *, node_path: str, strategy_id: str) -> ServiceResult[DeclGraphStrategyView]:
        strategy = self.get_strategy(repo_root, node_path=node_path, strategy_id=strategy_id)
        if not strategy.ok or strategy.value is None:
            return self.runtime.foundation.fail(strategy.issues)
        return self.runtime.foundation.ok(self.views.strategy_view(strategy.value))

    def list_strategies(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclGraphStrategy]]:
        return self.strategy_round.list_strategies(repo_root, node_path=node_path)

    def list_strategy_views(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclGraphStrategyView]]:
        strategies = self.list_strategies(repo_root, node_path=node_path)
        if not strategies.ok or strategies.value is None:
            return self.runtime.foundation.fail(strategies.issues)
        return self.runtime.foundation.ok([self.views.strategy_view(strategy) for strategy in strategies.value])

    def get_round(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclGraphRound]:
        return self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)

    def get_round_view(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclGraphRoundView]:
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        return self.runtime.foundation.ok(self.views.round_view(round_record.value))

    def list_rounds(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclGraphRound]]:
        return self.strategy_round.list_rounds(repo_root, node_path=node_path)

    def list_round_views(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclGraphRoundView]]:
        rounds = self.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        return self.runtime.foundation.ok([self.views.round_view(round_record) for round_record in rounds.value])

    def create_decl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        kind: str,
        objective: str,
        summary: str,
        public: bool = False,
        target_state: DeclState | str = DeclState.DECLARED,
        require_target_state_satisfied: bool = True,
        anticipated_statement_dep_names: list[str] | None = None,
        anticipated_proof_dep_names: list[str] | None = None,
    ) -> ServiceResult[DeclChangeView]:
        return self.decl_catalog.create_decl(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            name=name,
            kind=kind,
            objective=objective,
            summary=summary,
            public=public,
            target_state=target_state,
            require_target_state_satisfied=require_target_state_satisfied,
            anticipated_statement_dep_names=anticipated_statement_dep_names,
            anticipated_proof_dep_names=anticipated_proof_dep_names,
        )

    def open_decl_update(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        objective: str,
        target_state: DeclState | str,
        base_revision: int | None = None,
        reset_to_state: DeclState | str | None = None,
        require_target_state_satisfied: bool = True,
        anticipated_statement_dep_names: list[str] | None = None,
        anticipated_proof_dep_names: list[str] | None = None,
    ) -> ServiceResult[DeclChangeView]:
        return self.decl_catalog.open_decl_update(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            name=name,
            objective=objective,
            target_state=target_state,
            base_revision=base_revision,
            reset_to_state=reset_to_state,
            require_target_state_satisfied=require_target_state_satisfied,
            anticipated_statement_dep_names=anticipated_statement_dep_names,
            anticipated_proof_dep_names=anticipated_proof_dep_names,
        )

    def mark_decl_delete(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        objective: str,
    ) -> ServiceResult[DeclChangeView]:
        return self.decl_catalog.mark_decl_delete(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            name=name,
            objective=objective,
        )

    def commit_decl_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int | None = None,
        state: DeclState | str | None = None,
        apply_delete_lifecycle: bool = True,
    ) -> ServiceResult[DeclRevision]:
        return self.decl_catalog.commit_decl_revision(
            repo_root,
            node_path=node_path,
            name=name,
            revision=revision,
            state=state,
            apply_delete_lifecycle=apply_delete_lifecycle,
        )

    def get_decl(self, repo_root: Path, *, node_path: str, name: str) -> ServiceResult[Decl]:
        return self.decl_catalog.get_decl(repo_root, node_path=node_path, name=name)

    def get_decl_view(self, repo_root: Path, *, node_path: str, name: str) -> ServiceResult[DeclView]:
        decl = self.get_decl(repo_root, node_path=node_path, name=name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        revision = self.get_decl_revision(repo_root, node_path=node_path, name=name, revision=decl.value.current_revision)
        revision_value = revision.value if revision.ok else None
        return self._add_release_status(
            repo_root,
            node_path=node_path,
            decl_name=name,
            view=self.views.decl_view(decl.value, revision_value),
        )

    def list_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[Decl]]:
        return self.decl_catalog.list_decls(repo_root, node_path=node_path)

    def list_decl_views(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclView]]:
        decls = self.list_decls(repo_root, node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        views: list[DeclView] = []
        for decl in decls.value:
            revision = self.get_decl_revision(repo_root, node_path=node_path, name=decl.name, revision=decl.current_revision)
            revision_value = revision.value if revision.ok else None
            enriched = self._add_release_status(
                repo_root,
                node_path=node_path,
                decl_name=decl.name,
                view=self.views.decl_view(decl, revision_value),
            )
            if not enriched.ok or enriched.value is None:
                return self.runtime.foundation.fail(enriched.issues)
            views.append(enriched.value)
        return self.runtime.foundation.ok(views)

    def get_decl_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int,
    ) -> ServiceResult[DeclRevision]:
        return self.decl_catalog.get_decl_revision(repo_root, node_path=node_path, name=name, revision=revision)

    def _add_release_status(self, repo_root: Path, *, node_path: str, decl_name: str, view):
        status = self.runtime.repo_workspace.release.get_decl_release_status(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )
        if not status.ok or status.value is None:
            return self.runtime.foundation.fail(status.issues)
        released_state = DeclState(status.value.released_state) if status.value.released_state is not None else None
        return self.runtime.foundation.ok(
            view.model_copy(
                update={
                    "released_state": released_state,
                    "release_protected": status.value.release_protected,
                }
            )
        )

    def get_decl_change(self, repo_root: Path, *, node_path: str, change_id: str) -> ServiceResult[DeclChangeView]:
        return self.decl_catalog.get_decl_change(repo_root, node_path=node_path, change_id=change_id)

    def list_round_changes(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[list[DeclChangeView]]:
        return self.decl_catalog.list_round_changes(repo_root, node_path=node_path, round_id=round_id)

    def list_round_revisions(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
    ) -> ServiceResult[list[tuple[str, DeclRevision]]]:
        return self.decl_catalog.list_round_revisions(repo_root, node_path=node_path, round_id=round_id)

    def compute_delete_closure(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[DeclDeleteClosureView]:
        return self.decl_catalog.compute_delete_closure(repo_root, node_path=node_path, decl_names=decl_names)

    def validate_round_draft(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[object]:
        return self.decl_catalog.validate_round_draft(repo_root, node_path=node_path, round_id=round_id)

    def write_statement_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
        origin: list[dict[str, object]] | None = None,
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclStageWriteReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.write_statement_nl(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                nl=nl,
                origin=origin,
                deps=deps,
            ),
            finalize=self._stage_write_receipt(decl_name=decl_name),
        )

    def write_statement_nl_typed(self, repo_root: Path, **kwargs) -> ServiceResult[DeclStageWriteReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=kwargs["node_path"],
            decl_name=kwargs["decl_name"],
            mutate=lambda: self.stage_mutation.write_statement_nl_typed(repo_root, **kwargs),
            finalize=self._stage_write_receipt(
                decl_name=kwargs["decl_name"],
            ),
        )

    def set_statement_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
    ) -> ServiceResult[DeclTextMutationReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.set_statement_nl(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                nl=nl,
            ),
            finalize=self._text_mutation_receipt(
                decl_name=decl_name,
                section="Statement",
            ),
        )

    def add_statement_origin(self, repo_root: Path, *, node_path: str, round_id: str, decl_name: str, origin) -> ServiceResult[DeclOriginMutationReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.add_statement_origin(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, origin=origin),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Statement",
                collection="origins",
                operation="add",
                requested=[origin],
            ),
        )

    def remove_statement_origin(self, repo_root: Path, *, node_path: str, round_id: str, decl_name: str, index: int) -> ServiceResult[DeclOriginMutationReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.remove_statement_origin(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, index=index),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Statement",
                collection="origins",
                operation="remove",
            ),
        )

    def clear_statement_origins(self, repo_root: Path, *, node_path: str, round_id: str, decl_name: str) -> ServiceResult[DeclOriginMutationReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.clear_statement_origins(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Statement",
                collection="origins",
                operation="clear",
            ),
        )

    def add_statement_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        dep,
        refresh_projection: bool = True,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self._mutate_stage(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            refresh_projection=refresh_projection,
            mutate=lambda: self.stage_mutation.add_statement_dep(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, dep=dep),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Statement",
                collection="dependencies",
                operation="add",
                requested=[dep],
            ),
        )

    def add_statement_dependencies(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        deps,
        refresh_projection: bool = True,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self._mutate_stage(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            refresh_projection=refresh_projection,
            mutate=lambda: self.stage_mutation.add_statement_dependencies(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                deps=deps,
            ),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Statement",
                collection="dependencies",
                operation="add",
                requested=list(deps),
            ),
        )

    def remove_statement_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        index: int,
        refresh_projection: bool = True,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self._mutate_stage(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            refresh_projection=refresh_projection,
            mutate=lambda: self.stage_mutation.remove_statement_dep(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, index=index),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Statement",
                collection="dependencies",
                operation="remove",
            ),
        )

    def clear_statement_deps(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        refresh_projection: bool = True,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self._mutate_stage(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            refresh_projection=refresh_projection,
            mutate=lambda: self.stage_mutation.clear_statement_deps(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Statement",
                collection="dependencies",
                operation="clear",
            ),
        )

    def write_statement_deps(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclStageWriteReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.write_statement_deps(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                deps=deps,
            ),
            finalize=self._stage_write_receipt(decl_name=decl_name),
        )

    def write_proof_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
        origin: list[dict[str, object]] | None = None,
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclStageWriteReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.write_proof_nl(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                nl=nl,
                origin=origin,
                deps=deps,
            ),
            finalize=self._stage_write_receipt(decl_name=decl_name),
        )

    def write_proof_nl_typed(self, repo_root: Path, **kwargs) -> ServiceResult[DeclStageWriteReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=kwargs["node_path"],
            decl_name=kwargs["decl_name"],
            mutate=lambda: self.stage_mutation.write_proof_nl_typed(repo_root, **kwargs),
            finalize=self._stage_write_receipt(
                decl_name=kwargs["decl_name"],
            ),
        )

    def set_proof_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
    ) -> ServiceResult[DeclTextMutationReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.set_proof_nl(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                nl=nl,
            ),
            finalize=self._text_mutation_receipt(
                decl_name=decl_name,
                section="Proof",
            ),
        )

    def add_proof_origin(self, repo_root: Path, *, node_path: str, round_id: str, decl_name: str, origin) -> ServiceResult[DeclOriginMutationReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.add_proof_origin(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, origin=origin),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Proof",
                collection="origins",
                operation="add",
                requested=[origin],
            ),
        )

    def remove_proof_origin(self, repo_root: Path, *, node_path: str, round_id: str, decl_name: str, index: int) -> ServiceResult[DeclOriginMutationReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.remove_proof_origin(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, index=index),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Proof",
                collection="origins",
                operation="remove",
            ),
        )

    def clear_proof_origins(self, repo_root: Path, *, node_path: str, round_id: str, decl_name: str) -> ServiceResult[DeclOriginMutationReceipt]:
        return self._mutate_stage_truth_only(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=lambda: self.stage_mutation.clear_proof_origins(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Proof",
                collection="origins",
                operation="clear",
            ),
        )

    def add_proof_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        dep,
        refresh_projection: bool = True,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self._mutate_stage(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            refresh_projection=refresh_projection,
            mutate=lambda: self.stage_mutation.add_proof_dep(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, dep=dep),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Proof",
                collection="dependencies",
                operation="add",
                requested=[dep],
            ),
        )

    def add_proof_dependencies(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        deps,
        refresh_projection: bool = True,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self._mutate_stage(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            refresh_projection=refresh_projection,
            mutate=lambda: self.stage_mutation.add_proof_dependencies(
                repo_root,
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                deps=deps,
            ),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Proof",
                collection="dependencies",
                operation="add",
                requested=list(deps),
            ),
        )

    def remove_proof_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        index: int,
        refresh_projection: bool = True,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self._mutate_stage(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            refresh_projection=refresh_projection,
            mutate=lambda: self.stage_mutation.remove_proof_dep(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, index=index),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Proof",
                collection="dependencies",
                operation="remove",
            ),
        )

    def clear_proof_deps(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        refresh_projection: bool = True,
    ) -> ServiceResult[DeclDependencyMutationReceipt]:
        return self._mutate_stage(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            refresh_projection=refresh_projection,
            mutate=lambda: self.stage_mutation.clear_proof_deps(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name),
            finalize=self._collection_mutation_receipt(
                decl_name=decl_name,
                section="Proof",
                collection="dependencies",
                operation="clear",
            ),
        )

    def advance_stage_state(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: str,
        decl_names: list[str],
    ) -> ServiceResult[list[str]]:
        return self.stage_mutation.advance_stage_state(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            decl_names=decl_names,
        )

    def record_decl_review(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStage | str,
        decl_name: str,
        passed: bool,
        summary: str,
        issue_kind: str | None = None,
        suggested_fix: str | None = None,
        issue_categories: list[str] | None = None,
        required_changes: list[str] | None = None,
        recommended_next_action: str | None = None,
    ) -> ServiceResult[DeclReviewMarkRecord]:
        return self.review_gate.record_decl_review(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            decl_name=decl_name,
            passed=passed,
            summary=summary,
            issue_kind=issue_kind,
            suggested_fix=suggested_fix,
            issue_categories=issue_categories,
            required_changes=required_changes,
            recommended_next_action=recommended_next_action,
        )

    def review_mark_view(self, mark: DeclReviewMarkRecord) -> DeclReviewMarkView:
        return self.views.review_mark_view(mark)

    def build_decl_review_mark(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStage | str,
        decl_name: str,
        passed: bool,
        summary: str,
        issue_kind: str | None = None,
        suggested_fix: str | None = None,
        issue_categories: list[str] | None = None,
        required_changes: list[str] | None = None,
        recommended_next_action: str | None = None,
    ) -> ServiceResult[DeclReviewMarkRecord]:
        return self.review_gate.build_decl_review_mark(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            decl_name=decl_name,
            passed=passed,
            summary=summary,
            issue_kind=issue_kind,
            suggested_fix=suggested_fix,
            issue_categories=issue_categories,
            required_changes=required_changes,
            recommended_next_action=recommended_next_action,
        )

    def submit_stage_review(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStage | str,
        summary: str,
    ) -> ServiceResult[StageReviewResultView]:
        return self.review_gate.submit_stage_review(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            summary=summary,
        )

    def aggregate_stage_review_marks(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStage | str,
        summary: str,
        marks: list[DeclReviewMarkRecord],
        expected_decl_names: list[str] | None = None,
    ) -> ServiceResult[StageReviewResultView]:
        return self.review_gate.aggregate_stage_review_marks(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            summary=summary,
            marks=marks,
            expected_decl_names=expected_decl_names,
        )

    def compute_dependency_closure(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[DeclDependencyClosureView]:
        return self.dependency.compute_dependency_closure(repo_root, node_path=node_path, decl_names=decl_names)

    def check_delete_preflight(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[object]:
        return self.dependency.check_delete_preflight(repo_root, node_path=node_path, decl_names=decl_names)

    def audit_round_dependencies(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[object]:
        return self.dependency.audit_round_dependencies(repo_root, node_path=node_path, round_id=round_id)

    def statement_dependency_names(self, revision: DeclRevision) -> list[str]:
        return self.dependency.statement_dependency_names(revision)

    def proof_dependency_names(self, revision: DeclRevision) -> list[str]:
        return self.dependency.proof_dependency_names(revision)

    def all_dependency_names(self, revision: DeclRevision) -> list[str]:
        return self.dependency.all_dependency_names(revision)

    def dependency_names_for_proof_policy(
        self,
        decl: Decl,
        revision: DeclRevision,
        *,
        target_proof_availability: ProofAvailability,
    ) -> list[str]:
        return self.dependency.dependency_names_for_proof_policy(
            decl,
            revision,
            target_proof_availability=target_proof_availability,
        )

    def dependency_requirements_for_proof_policy(
        self,
        decl: Decl,
        revision: DeclRevision,
        *,
        target_proof_availability: ProofAvailability,
    ) -> list[tuple[str, ProofAvailability]]:
        return self.dependency.dependency_requirements_for_proof_policy(
            decl,
            revision,
            target_proof_availability=target_proof_availability,
        )

    def dependency_ref_requirements_for_proof_policy(
        self,
        decl: Decl,
        revision: DeclRevision,
        *,
        target_proof_availability: ProofAvailability,
    ) -> list[tuple[DeclRef, ProofAvailability]]:
        return self.dependency.dependency_ref_requirements_for_proof_policy(
            decl,
            revision,
            target_proof_availability=target_proof_availability,
        )

    def check_decl_ready(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        policy: str | None = None,
    ) -> ServiceResult[DeclReadinessReport]:
        return self.readiness.check_decl_ready(repo_root, node_path=node_path, decl_name=decl_name, policy=policy)

    def check_decl_proof_policy_satisfied(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        target_proof_availability: ProofAvailability | str | None = None,
    ) -> ServiceResult[DeclReadinessReport]:
        return self.readiness.check_decl_proof_policy_satisfied(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            target_proof_availability=target_proof_availability,
        )

    def check_decl_proof_policy_batch(
        self,
        repo_root: Path,
        *,
        roots: Sequence[tuple[str, str, ProofAvailability]],
        provider_target_override: ProofAvailability | None = None,
        round_overlay: dict[str, tuple[Decl, DeclRevision]] | None = None,
    ) -> ServiceResult[list[DeclReadinessReport]]:
        return self.readiness.check_decl_proof_policy_batch(
            repo_root,
            roots=roots,
            provider_target_override=provider_target_override,
            round_overlay=round_overlay,
        )

    def build_release_decl_availability_index(
        self,
        repo_root: Path,
    ) -> ServiceResult[DeclAvailabilityIndex]:
        return self.readiness.build_release_decl_availability_index(repo_root)

    def check_round_decl_ready(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        required_availability: ProofAvailability | str,
    ) -> ServiceResult[DeclReadinessReport]:
        return self.readiness.check_round_decl_ready(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            required_availability=required_availability,
        )

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.readiness.list_content_public_decls(repo_root, node_path=node_path)

    def get_current_decl_revision(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[DeclFileRevisionView]:
        return self.readiness.get_current_decl_revision(repo_root, node_path=node_path, decl_name=decl_name)

    def save_statement_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
        lean_decl_name: str,
    ) -> ServiceResult[DeclRevision]:
        return self.readiness.save_statement_formal_capture(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            code=code,
            check=check,
            lean_decl_name=lean_decl_name,
        )

    def save_proof_formal_capture(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        code: str,
        check: LeanCheckView,
        lean_decl_name: str,
    ) -> ServiceResult[DeclRevision]:
        return self.readiness.save_proof_formal_capture(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            code=code,
            check=check,
            lean_decl_name=lean_decl_name,
        )

    def list_active_decl_names(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[str]]:
        return self.readiness.list_active_decl_names(repo_root, node_path=node_path)

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        return self.readiness.check_content_node_ready(repo_root, node_path=node_path)

    def check_formal_stage_consistency(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        return self.readiness.check_formal_stage_consistency(repo_root, node_path=node_path, decl_name=decl_name, stage=stage)

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> ServiceResult[AuditReport]:
        return self.readiness.run_round_local_audit(repo_root, node_path=node_path, round_id=round_id, stage=stage)

    def run_delete_sanity_audit(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[AuditReport]:
        return self.readiness.run_delete_sanity_audit(repo_root, node_path=node_path, round_id=round_id)

    def run_strict_proved_audit(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str] | None = None,
    ) -> ServiceResult[AuditReport]:
        return self.readiness.run_strict_proved_audit(repo_root, node_path=node_path, decl_names=decl_names)

    def _mutate_stage_with_projection(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        mutate: Callable[[], ServiceResult[DeclRevision]],
        finalize: Callable[
            [DeclRevision, DeclRevision, str | None, bool, list[str], bool],
            T,
        ],
    ) -> ServiceResult[T]:
        return mutate_decl_with_projection(
            self.runtime,
            repo_root=Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            mutate=mutate,
            finalize=finalize,
        )

    def _mutate_stage_truth_only(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        mutate: Callable[[], ServiceResult[DeclRevision]],
        finalize: Callable[
            [DeclRevision, DeclRevision, str | None, bool, list[str], bool],
            T,
        ],
    ) -> ServiceResult[T]:
        return mutate_decl_truth_only(
            self.runtime,
            repo_root=Path(repo_root),
            node_path=node_path,
            decl_name=decl_name,
            mutate=mutate,
            finalize=finalize,
        )

    def _mutate_stage(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        refresh_projection: bool,
        mutate: Callable[[], ServiceResult[DeclRevision]],
        finalize: Callable[
            [DeclRevision, DeclRevision, str | None, bool, list[str], bool],
            T,
        ],
    ) -> ServiceResult[T]:
        apply_mutation = (
            self._mutate_stage_with_projection
            if refresh_projection
            else self._mutate_stage_truth_only
        )
        return apply_mutation(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            mutate=mutate,
            finalize=finalize,
        )

    @staticmethod
    def _stage_write_receipt(
        *,
        decl_name: str,
    ):
        def finalize(
            before: DeclRevision,
            after: DeclRevision,
            projection_stage: str | None,
            projection_changed: bool,
            changed_files: list[str],
            reread_required: bool,
        ) -> DeclStageWriteReceipt:
            return DeclStageWriteReceipt(
                decl_name=decl_name,
                revision=after.revision,
                changed=_business_changed(before, after),
                managed_projection=_managed_projection_effect(
                    stage=projection_stage,
                    changed=projection_changed,
                    changed_files=changed_files,
                    reread_required=reread_required,
                ),
            )

        return finalize

    @staticmethod
    def _text_mutation_receipt(
        *,
        decl_name: str,
        section: Literal["Statement", "Proof"],
    ):
        def finalize(
            before: DeclRevision,
            after: DeclRevision,
            projection_stage: str | None,
            projection_changed: bool,
            changed_files: list[str],
            reread_required: bool,
        ) -> DeclTextMutationReceipt:
            before_stage = before.statement if section == "Statement" else before.proof
            after_stage = after.statement if section == "Statement" else after.proof
            before_text = (
                before_stage.nl.text
                if before_stage is not None and before_stage.nl is not None
                else None
            )
            after_text = (
                after_stage.nl.text
                if after_stage is not None and after_stage.nl is not None
                else None
            )
            return DeclTextMutationReceipt(
                target=f"current node / {decl_name} / {section} NL",
                changed=before_text != after_text,
                managed_projection=_managed_projection_effect(
                    stage=projection_stage,
                    changed=projection_changed,
                    changed_files=changed_files,
                    reread_required=reread_required,
                ),
            )

        return finalize

    @staticmethod
    def _collection_mutation_receipt(
        *,
        decl_name: str,
        section: Literal["Statement", "Proof"],
        collection: Literal["origins", "dependencies"],
        operation: Literal["add", "remove", "clear"],
        requested: list[object] | None = None,
    ):
        def finalize(
            before: DeclRevision,
            after: DeclRevision,
            projection_stage: str | None,
            projection_changed: bool,
            changed_files: list[str],
            reread_required: bool,
        ) -> DeclOriginMutationReceipt | DeclDependencyMutationReceipt:
            previous = _stage_collection(
                before,
                section=section,
                collection=collection,
            )
            current = _stage_collection(
                after,
                section=section,
                collection=collection,
            )
            added = [item for item in current if item not in previous]
            removed = [item for item in previous if item not in current]
            already_present = (
                [item for item in (requested or []) if item in previous]
                if operation == "add"
                else []
            )
            receipt_type = (
                DeclOriginMutationReceipt
                if collection == "origins"
                else DeclDependencyMutationReceipt
            )
            return receipt_type(
                target=f"current node / {decl_name} / {section} {collection}",
                operation=operation,
                changed=previous != current,
                added=added,
                removed=removed,
                already_present=already_present,
                managed_projection=_managed_projection_effect(
                    stage=projection_stage,
                    changed=projection_changed,
                    changed_files=changed_files,
                    reread_required=reread_required,
                ),
            )

        return finalize
