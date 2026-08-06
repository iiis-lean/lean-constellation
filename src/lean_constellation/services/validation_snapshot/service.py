"""ValidationSnapshotService composition and public wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.services.validation_snapshot.admin_repair import AdminRepairComponent
from lean_constellation.services.validation_snapshot.audit import AuditComponent, AuditReport, DeclGraphAuditProvider
from lean_constellation.services.validation_snapshot.consistency_check import ConsistencyCheckComponent, FormalStageConsistencyProvider
from lean_constellation.services.validation_snapshot.readiness_gate import (
    ContentNodeCompletionGateView,
    ContentReadinessProvider,
    ContentReadyGateView,
    ReadinessGateComponent,
    RepoReadyGateView,
    ScopeReadyGateView,
)
from lean_constellation.services.validation_snapshot.release_finalizer import (
    CandidateReleaseGateView,
    CandidateReleasePreparationView,
    PreparedRepoReleaseView,
    ProviderRequirementReconciliationView,
    RepoReleaseFinalizeView,
    RepoReleaseFinalizerComponent,
    RepoReleaseStorageAuditView,
)
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointKind,
    RepoCheckpointSnapshotView,
    SnapshotRestoreComponent,
    SnapshotRestoreView,
)
from lean_constellation.services.foundation import GateReport, MutationSummaryView, ServiceResult
from lean_constellation.services.repo_workspace.git_release import (
    GitReleaseRestorePreview,
    GitReleaseRestoreView,
)

if TYPE_CHECKING:
    from lean_constellation.services.adapter import AdapterService
    from lean_constellation.services.lean_projection import LeanProjectionService
    from lean_constellation.services.material import MaterialService
    from lean_constellation.services.node import NodeService
    from lean_constellation.services.repo_workspace import RepoWorkspaceService
    from lean_constellation.services.runtime import LeanRuntimeServices


class ValidationSnapshotService:
    """Composition root for validation, audit, snapshot, and admin repair services."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        consistency: ConsistencyCheckComponent | None = None,
        readiness_gate: ReadinessGateComponent | None = None,
        snapshot_restore: SnapshotRestoreComponent | None = None,
        audit: AuditComponent | None = None,
        admin_repair: AdminRepairComponent | None = None,
        content_readiness_provider: ContentReadinessProvider | None = None,
        formal_stage_provider: FormalStageConsistencyProvider | None = None,
        decl_graph_audit_provider: DeclGraphAuditProvider | None = None,
    ) -> None:
        self.runtime = runtime
        repo_workspace = self.runtime.repo_workspace
        material = self.runtime.material
        lean_projection = self.runtime.lean_projection
        node = self.runtime.node
        adapter = self.runtime.adapter
        self.consistency = consistency or ConsistencyCheckComponent(
            runtime,
            material=material,
            node=node,
            adapter=adapter,
            lean_projection=lean_projection,
            formal_stage_provider=formal_stage_provider,
        )
        self.readiness_gate = readiness_gate or ReadinessGateComponent(
            runtime,
            repo_workspace=repo_workspace,
            material=material,
            node=node,
            adapter=adapter,
            lean_projection=lean_projection,
            consistency=self.consistency,
            content_readiness_provider=content_readiness_provider,
        )
        self.audit = audit or AuditComponent(
            runtime,
            consistency=self.consistency,
            readiness_gate=self.readiness_gate,
            decl_graph_provider=decl_graph_audit_provider,
        )
        self.snapshot_restore = snapshot_restore or SnapshotRestoreComponent(
            runtime,
            readiness_gate=self.readiness_gate,
        )
        self.release_finalizer = RepoReleaseFinalizerComponent(runtime)
        self.admin_repair = admin_repair or AdminRepairComponent(
            runtime,
            repo_workspace=repo_workspace,
            node=node,
            lean_projection=lean_projection,
            audit=self.audit,
        )

    @property
    def repo_workspace(self) -> "RepoWorkspaceService":
        return self.runtime.repo_workspace

    @property
    def material(self) -> "MaterialService":
        return self.runtime.material

    @property
    def lean_projection(self) -> "LeanProjectionService":
        return self.runtime.lean_projection

    @property
    def node(self) -> "NodeService":
        return self.runtime.node

    @property
    def adapter(self) -> "AdapterService":
        return self.runtime.adapter

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        return self.readiness_gate.check_content_node_ready(repo_root, node_path=node_path)

    def check_content_node_completion(
        self,
        repo_root: Path,
        *,
        node_path: str,
        contract_version: int | None = None,
    ) -> ServiceResult[ContentNodeCompletionGateView]:
        return self.readiness_gate.check_content_node_completion(
            repo_root,
            node_path=node_path,
            contract_version=contract_version,
        )

    def get_content_ready_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[ContentReadyGateView]:
        return self.readiness_gate.get_content_ready_view(repo_root, node_path=node_path)

    def check_scope_commit(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[GateReport]:
        return self.readiness_gate.check_scope_commit(repo_root, scope_path=scope_path, summary=summary)

    def get_scope_ready_view(self, repo_root: Path, *, scope_path: str) -> ServiceResult[ScopeReadyGateView]:
        return self.readiness_gate.get_scope_ready_view(repo_root, scope_path=scope_path)

    def check_repo_ready(self, repo_root: Path, *, summary: str) -> ServiceResult[GateReport]:
        return self.readiness_gate.check_repo_ready(repo_root, summary=summary)

    def get_repo_ready_view(self, repo_root: Path) -> ServiceResult[RepoReadyGateView]:
        return self.readiness_gate.get_repo_ready_view(repo_root)

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> ServiceResult[AuditReport]:
        return self.audit.run_round_local_audit(repo_root, node_path=node_path, round_id=round_id, stage=stage)

    def run_delete_sanity_audit(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[AuditReport]:
        return self.audit.run_delete_sanity_audit(repo_root, node_path=node_path, round_id=round_id)

    def check_formal_stage_consistency(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[GateReport]:
        return self.consistency.check_formal_stage_consistency(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            stage=stage,
        )

    def check_repo_checkpoint_business_gate(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
    ) -> ServiceResult[GateReport]:
        return self.snapshot_restore.check_checkpoint_business_gate(
            Path(repo_root), RepoCheckpointKind(checkpoint_kind)
        )

    def create_repo_checkpoint_archive(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        label: str | None = None,
        snapshot_id: str | None = None,
        ark_runtime_snapshot_id: str | None = None,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        return self.snapshot_restore.create_repo_checkpoint_archive(
            repo_root,
            checkpoint_kind=checkpoint_kind,
            label=label,
            snapshot_id=snapshot_id,
            ark_runtime_snapshot_id=ark_runtime_snapshot_id,
        )

    def restore_repo_checkpoint_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        dry_run: bool = False,
        prune_extra_files: bool = False,
    ) -> ServiceResult[SnapshotRestoreView]:
        return self.snapshot_restore.restore_repo_checkpoint_snapshot(
            repo_root,
            snapshot_id=snapshot_id,
            dry_run=dry_run,
            prune_extra_files=prune_extra_files,
        )

    def validate_repo_checkpoint_snapshot(self, repo_root: Path, *, snapshot_id: str):  # noqa: ANN201
        return self.snapshot_restore.validate_repo_checkpoint_snapshot(repo_root, snapshot_id=snapshot_id)

    def list_repo_checkpoint_snapshots(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str | None = None,
    ) -> ServiceResult[list[RepoCheckpointSnapshotView]]:
        return self.snapshot_restore.list_repo_checkpoint_snapshots(repo_root, checkpoint_kind=checkpoint_kind)

    def run_full_audit(self, repo_root: Path) -> ServiceResult[AuditReport]:
        return self.admin_repair.run_full_audit(repo_root)

    def prepare_candidate_release(
        self,
        repo_root: Path,
        *,
        base_release_id: str | None,
        summary: str,
        audited: CandidateReleaseGateView | None = None,
    ) -> ServiceResult[CandidateReleasePreparationView]:
        return self.release_finalizer.prepare_candidate_release(
            repo_root,
            base_release_id=base_release_id,
            summary=summary,
            audited=audited,
        )

    def preview_candidate_release(
        self,
        repo_root: Path,
        *,
        base_release_id: str | None,
        summary: str,
    ) -> ServiceResult[CandidateReleaseGateView]:
        return self.release_finalizer.preview_candidate_release(
            repo_root,
            base_release_id=base_release_id,
            summary=summary,
        )

    def commit_prepared_release(
        self, repo_root: Path, *, prepared: PreparedRepoReleaseView
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        return self.release_finalizer.commit_prepared_release(repo_root, prepared=prepared)

    def reconcile_provider_requirements(
        self, repo_root: Path, *, release_id: str
    ) -> ServiceResult[ProviderRequirementReconciliationView]:
        return self.release_finalizer.reconcile_provider_requirements(repo_root, release_id=release_id)

    def audit_repo_release_storage(self, repo_root: Path) -> ServiceResult[RepoReleaseStorageAuditView]:
        return self.release_finalizer.audit_repo_release_storage(repo_root)

    def cleanup_repo_release_orphans(
        self, repo_root: Path, *, expected_audit_digest: str
    ) -> ServiceResult[MutationSummaryView]:
        return self.release_finalizer.cleanup_repo_release_orphans(
            repo_root, expected_audit_digest=expected_audit_digest
        )

    def cleanup_unpublished_release_artifacts(
        self,
        repo_root: Path,
        *,
        release_id: str | None = None,
        checkpoint_id: str | None = None,
        staging_id: str | None = None,
    ) -> ServiceResult[MutationSummaryView]:
        return self.release_finalizer.cleanup_unpublished_release_artifacts(
            repo_root,
            release_id=release_id,
            checkpoint_id=checkpoint_id,
            staging_id=staging_id,
        )

    def preview_repo_release_restore(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[GitReleaseRestorePreview]:
        return self.release_finalizer.preview_repo_release_restore(
            repo_root,
            release_id=release_id,
        )

    def apply_repo_release_restore(
        self,
        repo_root: Path,
        *,
        preview: GitReleaseRestorePreview,
        expected_recovery_token: str,
    ) -> ServiceResult[GitReleaseRestoreView]:
        return self.release_finalizer.apply_repo_release_restore(
            repo_root,
            preview=preview,
            expected_recovery_token=expected_recovery_token,
        )
