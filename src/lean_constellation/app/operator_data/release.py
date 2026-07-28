"""Typed Release and LC-only Checkpoint facade for the Operator Data API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from lean_constellation.app.operator_data.common import (
    OperatorAccess,
    OperatorInputModel,
    OperatorLockPolicy,
    OperatorOperationSpec,
    OperatorGateView,
    operator_gate_view,
    project_operator_result,
)
from lean_constellation.app.operator_data.execution import OperatorExecutionService
from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.repo import RepoPublicationState
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.foundation import IssueSeverity, ServiceResult
from lean_constellation.services.repo_workspace.git_release import (
    GitReleaseCommitView,
    GitReleaseValidationView,
)
from lean_constellation.services.validation_snapshot import (
    AuditReport,
    CandidateReleaseGateView,
    ProviderRequirementReconciliationView,
    RepoCheckpointKind,
    RepoCheckpointSnapshotView,
    RepoReleaseStorageAuditView,
    RepoReadyGateView,
    SnapshotRestoreView,
)


READ_RELEASE = OperatorOperationSpec(
    name="release.read",
    access=OperatorAccess.READ,
    lock_policy=OperatorLockPolicy.NONE,
)
PREVIEW_RELEASE = OperatorOperationSpec(
    name="release.preview",
    access=OperatorAccess.PREVIEW,
    lock_policy=OperatorLockPolicy.NONE,
)
MUTATE_CHECKPOINT = OperatorOperationSpec(
    name="checkpoint.mutate",
    access=OperatorAccess.MUTATION,
    lock_policy=OperatorLockPolicy.OPERATOR,
    requires_stable_runtime=True,
)
SELF_MANAGED_RELEASE = OperatorOperationSpec(
    name="release.self_managed",
    access=OperatorAccess.MUTATION,
    lock_policy=OperatorLockPolicy.SELF_MANAGED,
    requires_stable_runtime=True,
)


class ReleaseCandidateInput(OperatorInputModel):
    base_release_id: str | None = None
    summary: str


class ReleaseIdInput(OperatorInputModel):
    release_id: str


class CheckpointKindInput(OperatorInputModel):
    checkpoint_kind: RepoCheckpointKind


class CheckpointCreateInput(CheckpointKindInput):
    label: str | None = None
    snapshot_id: str | None = None

    @field_validator("checkpoint_kind")
    @classmethod
    def _release_checkpoint_is_transaction_owned(
        cls, value: RepoCheckpointKind
    ) -> RepoCheckpointKind:
        if value is RepoCheckpointKind.REPO_RELEASE:
            raise ValueError("repo_release checkpoints are created only by publish_repo_release")
        return value


class CheckpointListInput(OperatorInputModel):
    checkpoint_kind: RepoCheckpointKind | None = None


class CheckpointIdInput(OperatorInputModel):
    snapshot_id: str


class CheckpointRestoreInput(CheckpointIdInput):
    dry_run: bool = False
    prune_extra_files: bool = False


class OperatorCheckpointView(StrictModel):
    """LC-only checkpoint view without archive paths or ARK identity."""

    snapshot_id: str
    checkpoint_kind: RepoCheckpointKind
    label: str | None = None
    file_count: int
    summary: str


class OperatorCheckpointRestoreView(StrictModel):
    """LC-only restore result without ARK identity."""

    snapshot_id: str
    dry_run: bool
    restored_files: list[str] = Field(default_factory=list)
    would_restore_files: list[str] = Field(default_factory=list)
    would_prune_files: list[str] = Field(default_factory=list)
    would_invalidate_paths: list[str] = Field(default_factory=list)
    pruned_files: list[str] = Field(default_factory=list)
    invalidated_paths: list[str] = Field(default_factory=list)
    summary: str


class OperatorReleaseFinalizeView(StrictModel):
    release: "OperatorRepoReleaseView"
    git_release: "OperatorGitReleaseView"
    checkpoint: OperatorCheckpointView | None = None
    publication: "OperatorReleasePublicationView"
    reconciliation: ProviderRequirementReconciliationView
    notification_pending: bool = False
    summary: str


class OperatorReleasePublishView(StrictModel):
    outcome: Literal["published", "blocked"]
    gate: OperatorGateView
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    finalized: OperatorReleaseFinalizeView | None = None
    summary: str


class OperatorRepoReleaseView(StrictModel):
    release: RepoRelease
    summary: str


class OperatorGitReleaseView(StrictModel):
    release_id: str
    commit: str
    tree: str
    summary: str


class OperatorReleasePublicationView(StrictModel):
    publication: RepoPublicationState


class OperatorRepoReadyView(StrictModel):
    root_scope_path: str
    target_proof_availability: str
    publication_status: str
    main_contract_version: int | None = None
    main_contract_version_status: str | None = None
    gate: OperatorGateView
    ready_to_submit: bool
    summary: str
    blocking_issue_kinds: list[str] = Field(default_factory=list)


class OperatorAuditFindingView(StrictModel):
    kind: str
    severity: IssueSeverity
    message: str
    suggested_action: str | None = None


class OperatorAuditReportView(StrictModel):
    audit_name: str
    passed: bool
    findings: list[OperatorAuditFindingView] = Field(default_factory=list)
    checked_items: list[str] = Field(default_factory=list)
    summary: str


class OperatorCandidateReleaseView(StrictModel):
    base_release_id: str | None = None
    candidate_node_contract_versions: dict[str, int] = Field(default_factory=dict)
    completion_mode: str
    gate: OperatorGateView
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    summary: str


class OperatorReleaseStorageAuditView(StrictModel):
    passed: bool
    latest_release_id: str | None = None
    reachable_release_ids: list[str] = Field(default_factory=list)
    orphan_release_ids: list[str] = Field(default_factory=list)
    orphan_checkpoint_ids: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    audit_digest: str
    summary: str


def _repo_release_view(value) -> OperatorRepoReleaseView:  # noqa: ANN001
    return OperatorRepoReleaseView(release=value.release, summary=value.summary)


def _repo_ready_view(value: RepoReadyGateView) -> OperatorRepoReadyView:
    return OperatorRepoReadyView(
        root_scope_path=value.root_scope_path,
        target_proof_availability=value.target_proof_availability.value,
        publication_status=value.publication_status.value,
        main_contract_version=value.main_contract_version,
        main_contract_version_status=(
            value.main_contract_version_status.value
            if value.main_contract_version_status is not None
            else None
        ),
        gate=operator_gate_view(value.gate),
        ready_to_submit=value.ready_to_submit,
        summary=value.summary,
        blocking_issue_kinds=value.blocking_issue_kinds,
    )


def _audit_report_view(value: AuditReport) -> OperatorAuditReportView:
    return OperatorAuditReportView(
        audit_name=value.audit_name,
        passed=value.passed,
        findings=[
            OperatorAuditFindingView(
                kind=finding.kind,
                severity=finding.severity,
                message=finding.message,
                suggested_action=finding.suggested_action,
            )
            for finding in value.findings
        ],
        checked_items=value.checked_items,
        summary=value.summary,
    )


def _candidate_release_view(value: CandidateReleaseGateView) -> OperatorCandidateReleaseView:
    return OperatorCandidateReleaseView(
        base_release_id=value.base_release_id,
        candidate_node_contract_versions=value.candidate_node_contract_versions,
        completion_mode=value.completion_mode.value,
        gate=operator_gate_view(value.gate),
        blocking_issue_kinds=value.blocking_issue_kinds,
        summary=value.summary,
    )


def _release_storage_audit_view(
    value: RepoReleaseStorageAuditView,
) -> OperatorReleaseStorageAuditView:
    return OperatorReleaseStorageAuditView(
        passed=value.passed,
        latest_release_id=value.latest_release_id,
        reachable_release_ids=value.reachable_release_ids,
        orphan_release_ids=value.orphan_release_ids,
        orphan_checkpoint_ids=value.orphan_checkpoint_ids,
        issues=value.issues,
        audit_digest=value.audit_digest,
        summary=value.summary,
    )


class _PathFreeExecutor:
    """Common envelope projection for every Release/Checkpoint operation."""

    def __init__(self, delegate: OperatorExecutionService[Any]) -> None:
        self.delegate = delegate

    def execute(self, *args: Any, **kwargs: Any) -> ServiceResult[Any]:
        return project_operator_result(self.delegate.execute(*args, **kwargs))


class ReleaseCheckpointOperatorApi:
    """Release transaction and LC-only checkpoint operations."""

    def __init__(self, executor: OperatorExecutionService[Any]) -> None:
        self.executor = _PathFreeExecutor(executor)

    def get_repo_ready_view(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_RELEASE,
            lambda ctx: ctx.runtime.validation_snapshot.get_repo_ready_view(ctx.repo_root),
        ), _repo_ready_view)

    def run_full_audit(self, repo_key: str) -> ServiceResult[Any]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_RELEASE,
            lambda ctx: ctx.runtime.validation_snapshot.run_full_audit(ctx.repo_root),
        ), _audit_report_view)

    def preview_repo_release(
        self, repo_key: str, input_model: ReleaseCandidateInput
    ) -> ServiceResult[CandidateReleaseGateView]:
        return project_operator_result(self.executor.execute(
            repo_key,
            PREVIEW_RELEASE,
            lambda ctx: ctx.runtime.validation_snapshot.preview_candidate_release(
                ctx.repo_root,
                base_release_id=input_model.base_release_id,
                summary=input_model.summary,
            ),
        ), _candidate_release_view)

    def publish_repo_release(
        self, repo_key: str, input_model: ReleaseCandidateInput
    ) -> ServiceResult[OperatorReleasePublishView]:
        def publish(ctx):  # noqa: ANN001, ANN202
            prepared = ctx.runtime.validation_snapshot.prepare_candidate_release(
                ctx.repo_root,
                base_release_id=input_model.base_release_id,
                summary=input_model.summary,
            )
            if not prepared.ok or prepared.value is None:
                return ctx.runtime.foundation.fail(prepared.issues)
            preparation = prepared.value
            if preparation.outcome == "blocked" or preparation.prepared_release is None:
                return ctx.runtime.foundation.ok(
                    OperatorReleasePublishView(
                        outcome="blocked",
                        gate=operator_gate_view(preparation.gate),
                        blocking_issue_kinds=preparation.blocking_issue_kinds,
                        summary=preparation.summary,
                    ),
                    warnings=prepared.issues,
                )
            committed = ctx.runtime.validation_snapshot.commit_prepared_release(
                ctx.repo_root,
                prepared=preparation.prepared_release,
            )
            if not committed.ok or committed.value is None:
                return ctx.runtime.foundation.fail(committed.issues)
            sanitized = self._sanitize_finalize(ctx.runtime.foundation, committed.value)
            if not sanitized.ok or sanitized.value is None:
                return sanitized
            return ctx.runtime.foundation.ok(
                OperatorReleasePublishView(
                    outcome="published",
                    gate=operator_gate_view(preparation.gate),
                    finalized=sanitized.value,
                    summary=committed.value.summary,
                ),
                warnings=[*prepared.issues, *committed.issues],
            )

        return project_operator_result(
            self.executor.execute(repo_key, SELF_MANAGED_RELEASE, publish)
        )

    def list_repo_releases(self, repo_key: str) -> ServiceResult[list[OperatorRepoReleaseView]]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_RELEASE,
            lambda ctx: ctx.runtime.repo_workspace.release.list_releases(ctx.repo_root),
        ), lambda values: [_repo_release_view(value) for value in values])

    def get_repo_release(
        self, repo_key: str, input_model: ReleaseIdInput
    ) -> ServiceResult[OperatorRepoReleaseView]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_RELEASE,
            lambda ctx: ctx.runtime.repo_workspace.release.get_release(
                ctx.repo_root, release_id=input_model.release_id
            ),
        ), _repo_release_view)

    def get_latest_repo_release(self, repo_key: str) -> ServiceResult[OperatorRepoReleaseView | None]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_RELEASE,
            lambda ctx: ctx.runtime.repo_workspace.release.get_latest_release(ctx.repo_root),
        ), _repo_release_view)

    def audit_repo_release_storage(
        self, repo_key: str
    ) -> ServiceResult[RepoReleaseStorageAuditView]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_RELEASE,
            lambda ctx: ctx.runtime.validation_snapshot.audit_repo_release_storage(ctx.repo_root),
        ), _release_storage_audit_view)

    def check_checkpoint_gate(
        self, repo_key: str, input_model: CheckpointKindInput
    ) -> ServiceResult[OperatorGateView]:
        return project_operator_result(self.executor.execute(
            repo_key,
            READ_RELEASE,
            lambda ctx: ctx.runtime.validation_snapshot.check_repo_checkpoint_business_gate(
                ctx.repo_root,
                checkpoint_kind=input_model.checkpoint_kind,
            ),
        ), operator_gate_view)

    def create_checkpoint(
        self, repo_key: str, input_model: CheckpointCreateInput
    ) -> ServiceResult[OperatorCheckpointView]:
        def create(ctx):  # noqa: ANN001, ANN202
            created = ctx.runtime.validation_snapshot.create_repo_checkpoint_archive(
                ctx.repo_root,
                checkpoint_kind=input_model.checkpoint_kind,
                label=input_model.label,
                snapshot_id=input_model.snapshot_id,
            )
            return self._sanitize_checkpoint_result(ctx.runtime.foundation, created)

        return project_operator_result(
            self.executor.execute(repo_key, MUTATE_CHECKPOINT, create)
        )

    def list_checkpoints(
        self, repo_key: str, input_model: CheckpointListInput
    ) -> ServiceResult[list[OperatorCheckpointView]]:
        def list_lc_only(ctx):  # noqa: ANN001, ANN202
            listed = ctx.runtime.validation_snapshot.list_repo_checkpoint_snapshots(
                ctx.repo_root,
                checkpoint_kind=input_model.checkpoint_kind,
            )
            if not listed.ok or listed.value is None:
                return ctx.runtime.foundation.fail(listed.issues)
            return ctx.runtime.foundation.ok(
                [
                    self._checkpoint_view(item)
                    for item in listed.value
                    if item.ark_runtime_snapshot_id is None
                ],
                warnings=listed.issues,
            )

        return project_operator_result(
            self.executor.execute(repo_key, READ_RELEASE, list_lc_only)
        )

    def validate_checkpoint(
        self, repo_key: str, input_model: CheckpointIdInput
    ) -> ServiceResult[OperatorCheckpointView]:
        def validate(ctx):  # noqa: ANN001, ANN202
            checked = ctx.runtime.validation_snapshot.validate_repo_checkpoint_snapshot(
                ctx.repo_root,
                snapshot_id=input_model.snapshot_id,
            )
            return self._sanitize_checkpoint_result(ctx.runtime.foundation, checked)

        return project_operator_result(
            self.executor.execute(repo_key, READ_RELEASE, validate)
        )

    def restore_checkpoint(
        self, repo_key: str, input_model: CheckpointRestoreInput
    ) -> ServiceResult[OperatorCheckpointRestoreView]:
        def restore(ctx):  # noqa: ANN001, ANN202
            checked = ctx.runtime.validation_snapshot.validate_repo_checkpoint_snapshot(
                ctx.repo_root,
                snapshot_id=input_model.snapshot_id,
            )
            if not checked.ok or checked.value is None:
                return ctx.runtime.foundation.fail(checked.issues)
            lc_only = self._require_lc_only(ctx.runtime.foundation, checked.value)
            if not lc_only.ok:
                return lc_only
            restored = ctx.runtime.validation_snapshot.restore_repo_checkpoint_snapshot(
                ctx.repo_root,
                snapshot_id=input_model.snapshot_id,
                dry_run=input_model.dry_run,
                prune_extra_files=input_model.prune_extra_files,
            )
            return self._sanitize_restore(ctx.runtime.foundation, restored)

        return project_operator_result(
            self.executor.execute(repo_key, MUTATE_CHECKPOINT, restore)
        )

    @staticmethod
    def _checkpoint_view(value: RepoCheckpointSnapshotView) -> OperatorCheckpointView:
        return OperatorCheckpointView(
            snapshot_id=value.snapshot_id,
            checkpoint_kind=value.checkpoint_kind,
            label=value.label,
            file_count=value.file_count,
            summary=value.summary,
        )

    @classmethod
    def _sanitize_checkpoint_result(cls, result, value):  # noqa: ANN001, ANN206
        if not value.ok or value.value is None:
            return result.fail(value.issues)
        lc_only = cls._require_lc_only(result, value.value)
        if not lc_only.ok:
            return lc_only
        return result.ok(cls._checkpoint_view(value.value), warnings=value.issues)

    @staticmethod
    def _require_lc_only(result, value: RepoCheckpointSnapshotView):  # noqa: ANN001, ANN205
        if value.ark_runtime_snapshot_id is not None:
            return result.fail(
                result.issue(
                    "operator_checkpoint_contains_ark_runtime",
                    "Operator checkpoint operations accept only LC project-data checkpoints.",
                    object_ref=value.snapshot_id,
                )
            )
        return result.ok(value)

    @staticmethod
    def _restore_view(value: SnapshotRestoreView) -> OperatorCheckpointRestoreView:
        return OperatorCheckpointRestoreView(
            snapshot_id=value.snapshot_id,
            dry_run=value.dry_run,
            restored_files=value.restored_files,
            would_restore_files=value.would_restore_files,
            would_prune_files=value.would_prune_files,
            would_invalidate_paths=value.would_invalidate_paths,
            pruned_files=value.pruned_files,
            invalidated_paths=value.invalidated_paths,
            summary=value.summary,
        )

    @staticmethod
    def _git_release_view(
        value: GitReleaseCommitView | GitReleaseValidationView,
    ) -> OperatorGitReleaseView:
        return OperatorGitReleaseView(
            release_id=value.release_id,
            commit=value.commit,
            tree=value.tree,
            summary=value.summary,
        )

    @classmethod
    def _sanitize_restore(cls, result, value):  # noqa: ANN001, ANN206
        if not value.ok or value.value is None:
            return result.fail(value.issues)
        if value.value.ark_runtime_snapshot_id is not None:
            return result.fail(
                result.issue(
                    "operator_checkpoint_contains_ark_runtime",
                    "Operator restore accepts only LC project-data checkpoints.",
                    object_ref=value.value.snapshot_id,
                )
            )
        return result.ok(cls._restore_view(value.value), warnings=value.issues)

    @classmethod
    def _sanitize_finalize(cls, result, value):  # noqa: ANN001, ANN206
        checkpoint = None
        if value.checkpoint is not None:
            lc_only = cls._require_lc_only(result, value.checkpoint)
            if not lc_only.ok:
                return lc_only
            checkpoint = cls._checkpoint_view(value.checkpoint)
        return result.ok(
            OperatorReleaseFinalizeView(
                release=_repo_release_view(value.release),
                git_release=cls._git_release_view(value.git_release),
                checkpoint=checkpoint,
                publication=OperatorReleasePublicationView(
                    publication=value.publication.publication
                ),
                reconciliation=value.reconciliation,
                notification_pending=value.notification_pending,
                summary=value.summary,
            )
        )


__all__ = [
    "CheckpointCreateInput",
    "CheckpointIdInput",
    "CheckpointKindInput",
    "CheckpointListInput",
    "CheckpointRestoreInput",
    "OperatorCheckpointRestoreView",
    "OperatorCheckpointView",
    "OperatorGitReleaseView",
    "OperatorAuditReportView",
    "OperatorCandidateReleaseView",
    "OperatorReleasePublicationView",
    "OperatorReleaseFinalizeView",
    "OperatorReleasePublishView",
    "OperatorReleaseStorageAuditView",
    "OperatorRepoReadyView",
    "OperatorRepoReleaseView",
    "ReleaseCandidateInput",
    "ReleaseCheckpointOperatorApi",
    "ReleaseIdInput",
]
