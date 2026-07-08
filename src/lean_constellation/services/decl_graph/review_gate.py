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
        issue_categories: list[str] | None = None,
        required_changes: list[str] | None = None,
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
            issue_categories=issue_categories,
            required_changes=required_changes,
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
        issue_categories: list[str] | None = None,
        required_changes: list[str] | None = None,
    ) -> ServiceResult[DeclReviewMarkRecord]:
        stage = DeclStage(stage)
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("review_summary_required", "Review summary is required.", field="summary")
            )
        normalized_categories = self._normalize_text_list(issue_categories)
        normalized_changes = self._normalize_text_list(required_changes)
        if issue_kind and issue_kind.strip() and issue_kind.strip() not in normalized_categories:
            normalized_categories.insert(0, issue_kind.strip())
        if suggested_fix and suggested_fix.strip() and suggested_fix.strip() not in normalized_changes:
            normalized_changes.insert(0, suggested_fix.strip())
        if not passed and not normalized_categories:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("review_issue_kind_required", "Failed review mark requires issue_kind.", field="issue_kind")
            )
        if not passed and not normalized_changes:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("review_required_changes_required", "Failed review mark requires actionable required changes.", field="required_changes")
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
                issue_kind=normalized_categories[0] if normalized_categories else None,
                suggested_fix=normalized_changes[0] if normalized_changes else None,
                issue_categories=normalized_categories,
                required_changes=normalized_changes,
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
        expected_decl_names: list[str] | None = None,
    ) -> ServiceResult[StageReviewResultView]:
        stage = DeclStage(stage)
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("stage_review_summary_required", "Stage review summary is required.", field="summary")
            )
        required = self._required_decl_names(repo_root, node_path=node_path, round_id=round_id, stage=stage)
        if not required.ok or required.value is None:
            return self.runtime.foundation.fail(required.issues)
        expected = sorted(set(expected_decl_names or required.value))
        unexpected_expected = [name for name in expected if name not in required.value]
        if unexpected_expected:
            return self.runtime.foundation.fail(
                [
                    self.runtime.foundation.issue(
                        "review_expected_decl_not_required",
                        "Expected review batch includes declarations that are not required for this stage.",
                        object_ref=name,
                    )
                    for name in unexpected_expected
                ]
            )
        invalid_context_issues = []
        mark_by_decl: dict[str, DeclReviewMarkRecord] = {}
        for mark in marks:
            if mark.round_id != round_id:
                invalid_context_issues.append(
                    self.runtime.foundation.issue("review_mark_round_mismatch", "Review mark round does not match current submit context.", object_ref=mark.decl_name, current=mark.round_id, expected=round_id)
                )
                continue
            if mark.node_path != node_path:
                invalid_context_issues.append(
                    self.runtime.foundation.issue("review_mark_node_mismatch", "Review mark node does not match current submit context.", object_ref=mark.decl_name, current=mark.node_path, expected=node_path)
                )
                continue
            if mark.stage != stage:
                invalid_context_issues.append(
                    self.runtime.foundation.issue("review_mark_stage_mismatch", "Review mark stage does not match current submit context.", object_ref=mark.decl_name, current=mark.stage.value, expected=stage.value)
                )
                continue
            if mark.decl_name not in expected:
                invalid_context_issues.append(
                    self.runtime.foundation.issue("review_mark_decl_not_expected", "Review mark declaration is not in the current expected batch.", object_ref=mark.decl_name)
                )
                continue
            if mark.decl_name in mark_by_decl:
                invalid_context_issues.append(
                    self.runtime.foundation.issue("review_mark_duplicate", "Each expected declaration must have exactly one current review mark.", object_ref=mark.decl_name)
                )
                continue
            if not mark.passed and not mark.required_changes:
                invalid_context_issues.append(
                    self.runtime.foundation.issue("review_mark_required_changes_missing", "Rejected review marks require actionable required changes.", object_ref=mark.decl_name)
                )
                continue
            mark_by_decl[mark.decl_name] = mark
        missing = sorted(name for name in expected if name not in mark_by_decl)
        if missing or invalid_context_issues:
            issues = list(invalid_context_issues)
            if missing:
                issues.append(
                    self.runtime.foundation.issue(
                        "review_marks_missing",
                        "Stage review submit requires one current mark for every expected declaration.",
                        expected=", ".join(missing),
                    )
                )
            return self.runtime.foundation.fail(issues)
        failed = sorted(name for name in expected if not mark_by_decl[name].passed)
        reviewed = sorted(expected)
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

    @staticmethod
    def _normalize_text_list(value: list[str] | None) -> list[str]:
        normalized: list[str] = []
        for item in value or []:
            text = item.strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

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
