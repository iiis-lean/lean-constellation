"""Reviewer mark and stage review gate support."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclReviewMarkRecord,
    DeclRoundStatus,
    DeclStage,
    StageReviewResultView,
)
from lean_constellation.services.decl_graph.strategy_round import StrategyRoundComponent
from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class ReviewGateComponent:
    """Build per-decl review marks and aggregate stage review results."""

    _THEOREM_LIKE_KINDS = {"theorem", "lemma"}

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        graph_store: GraphStoreComponent,
        strategy_round: StrategyRoundComponent,
        decl_catalog: DeclCatalogComponent,
    ) -> None:
        self.runtime = runtime
        self.graph_store = graph_store
        self.strategy_round = strategy_round
        self.decl_catalog = decl_catalog

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
    ) -> ServiceResult[DeclReviewMarkRecord]:
        return self.build_decl_review_mark(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            decl_name=decl_name,
            passed=passed,
            summary=summary,
            issue_kind=issue_kind,
            suggested_fix=suggested_fix,
        )

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
    ) -> ServiceResult[DeclReviewMarkRecord]:
        stage = DeclStage(stage)
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("review_summary_required", "Review summary is required.", field="summary")
            )
        if not passed and not (issue_kind and issue_kind.strip()):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("review_issue_kind_required", "Failed review mark requires issue_kind.", field="issue_kind")
            )
        required = self._required_decl_names(repo_root, node_path=node_path, round_id=round_id, stage=stage)
        if not required.ok or required.value is None:
            return self.runtime.foundation.fail(required.issues)
        if decl_name not in required.value:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("review_decl_not_required", "Declaration is not required for this stage review.", object_ref=decl_name)
            )
        return self.runtime.foundation.ok(
            DeclReviewMarkRecord(
                round_id=round_id,
                node_path=node_path,
                stage=stage,
                decl_name=decl_name,
                passed=passed,
                summary=summary,
                issue_kind=issue_kind.strip() if issue_kind else None,
                suggested_fix=suggested_fix.strip() if suggested_fix else None,
            )
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
        stage = DeclStage(stage)
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("stage_review_summary_required", "Stage review summary is required.", field="summary")
            )
        return self.aggregate_stage_review_marks(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            stage=stage,
            summary=summary,
            marks=[],
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
    ) -> ServiceResult[StageReviewResultView]:
        stage = DeclStage(stage)
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("stage_review_summary_required", "Stage review summary is required.", field="summary")
            )
        required = self._required_decl_names(repo_root, node_path=node_path, round_id=round_id, stage=stage)
        if not required.ok or required.value is None:
            return self.runtime.foundation.fail(required.issues)
        mark_by_decl = {mark.decl_name: mark for mark in marks}
        missing = sorted(name for name in required.value if name not in mark_by_decl)
        failed = sorted(name for name in required.value if name in mark_by_decl and not mark_by_decl[name].passed)
        reviewed = sorted(name for name in required.value if name in mark_by_decl)
        feedback = [mark_by_decl[name] for name in failed]
        passed = not missing and not failed
        return self.runtime.foundation.ok(
            StageReviewResultView(
                round_id=round_id,
                node_path=node_path,
                stage=stage,
                passed=passed,
                reviewed_decl_names=reviewed,
                failed_decl_names=failed,
                missing_decl_names=missing,
                feedback=feedback,
                summary=summary.strip(),
            )
        )

    def _required_decl_names(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: DeclStage,
    ) -> ServiceResult[list[str]]:
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        if round_record.value.status != DeclRoundStatus.RUNNING:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_not_running",
                    "Stage review requires a running decl round.",
                    object_ref=round_id,
                    current=round_record.value.status.value,
                    expected=DeclRoundStatus.RUNNING.value,
                )
            )
        names: list[str] = []
        for ref in round_record.value.revision_refs:
            revision = self.decl_catalog.get_decl_revision(repo_root, node_path=node_path, name=ref.decl_name, revision=ref.revision)
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            if revision.value.change is not None and revision.value.change.kind == DeclChangeKind.DELETE:
                continue
            if stage in {DeclStage.PROOF_NL, DeclStage.PROOF_FORMAL}:
                decl = self.decl_catalog.get_decl(repo_root, node_path=node_path, name=ref.decl_name)
                if not decl.ok or decl.value is None:
                    return self.runtime.foundation.fail(decl.issues)
                if decl.value.kind not in self._THEOREM_LIKE_KINDS:
                    continue
            names.append(ref.decl_name)
        return self.runtime.foundation.ok(sorted(set(names)))
