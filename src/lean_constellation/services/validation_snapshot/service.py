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
    LegacyStableAdoptionPreviewView,
    LegacyStableAdoptionView,
    PreparedRepoReleaseView,
    ProviderRequirementReconciliationView,
    RepoReleaseFinalizeView,
    RepoReleaseFinalizerComponent,
    RepoReleaseStorageAuditView,
)
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    ArkRuntimeSnapshotProvider,
    RepoCheckpointKind,
    RepoCheckpointSnapshotView,
    RuntimeStabilityProvider,
    SnapshotRestoreComponent,
    SnapshotRestoreView,
)
from lean_constellation.services.foundation import GateReport, MutationSummaryView, ServiceResult

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
        runtime_stability_provider: RuntimeStabilityProvider | None = None,
        ark_snapshot_provider: ArkRuntimeSnapshotProvider | None = None,
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
            runtime_stability_provider=runtime_stability_provider,
            ark_snapshot_provider=ark_snapshot_provider,
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

    def check_content_node_completion(self, repo_root: Path, *, node_path: str) -> ServiceResult[ContentNodeCompletionGateView]:
        return self.readiness_gate.check_content_node_completion(repo_root, node_path=node_path)

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

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        node_paths: list[str] | None = None,
        node_ids: list[str] | None = None,
    ) -> ServiceResult[GateReport]:
        return self.snapshot_restore.check_repo_stable_point(
            repo_root,
            checkpoint_kind=checkpoint_kind,
            node_paths=node_paths,
            node_ids=node_ids,
        )

    def create_repo_stable_point_snapshot(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        label: str | None = None,
        node_paths: list[str] | None = None,
        node_ids: list[str] | None = None,
        scope_ids: list[str] | None = None,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        return self.snapshot_restore.create_repo_stable_point_snapshot(
            repo_root,
            checkpoint_kind=checkpoint_kind,
            label=label,
            node_paths=node_paths,
            node_ids=node_ids,
            scope_ids=scope_ids,
        )

    def create_repo_stable_point_snapshot_with_id(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        checkpoint_kind: RepoCheckpointKind | str,
        label: str | None = None,
        scope_ids: list[str] | None = None,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        return self.snapshot_restore.create_repo_stable_point_snapshot(
            repo_root,
            checkpoint_kind=checkpoint_kind,
            label=label,
            scope_ids=scope_ids,
            snapshot_id=snapshot_id,
        )

    def restore_repo_checkpoint_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        dry_run: bool = False,
        leave_runtime_paused: bool = True,
        prune_extra_files: bool = False,
    ) -> ServiceResult[SnapshotRestoreView]:
        manifest = self.snapshot_restore._load_manifest(Path(repo_root), snapshot_id)
        if manifest.ok and manifest.value is not None and manifest.value.checkpoint_kind == RepoCheckpointKind.REPO_RELEASE:
            releases = self.runtime.repo_workspace.release.list_releases(Path(repo_root))
            if not releases.ok or releases.value is None:
                return self.runtime.foundation.fail(releases.issues)
            matches = [
                item.release.release_id
                for item in releases.value
                if item.release.repo_checkpoint_id == snapshot_id
            ]
            if len(matches) != 1:
                return self.runtime.foundation.fail(self.runtime.foundation.issue(
                    "repo_release_checkpoint_identity_invalid",
                    "Release checkpoint must be referenced by exactly one immutable RepoRelease.",
                    object_ref=snapshot_id,
                    details={"matches": ", ".join(matches)},
                ))
            return self.release_finalizer.restore_repo_release(
                Path(repo_root),
                release_id=matches[0],
                dry_run=dry_run,
                leave_runtime_paused=leave_runtime_paused,
            )
        return self.snapshot_restore.restore_repo_checkpoint_snapshot(
            repo_root,
            snapshot_id=snapshot_id,
            dry_run=dry_run,
            leave_runtime_paused=leave_runtime_paused,
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
        self, repo_root: Path, *, base_release_id: str | None, summary: str, owner_flow_id: str
    ) -> ServiceResult[CandidateReleasePreparationView]:
        return self.release_finalizer.prepare_candidate_release(
            repo_root, base_release_id=base_release_id, summary=summary, owner_flow_id=owner_flow_id
        )

    def preview_candidate_release(
        self,
        repo_root: Path,
        *,
        base_release_id: str | None,
        summary: str,
        owner_flow_id: str | None = None,
        submission_intent_preview: bool = False,
    ) -> ServiceResult[CandidateReleaseGateView]:
        return self.release_finalizer.preview_candidate_release(
            repo_root,
            base_release_id=base_release_id,
            summary=summary,
            owner_flow_id=owner_flow_id,
            submission_intent_preview=submission_intent_preview,
        )

    def commit_prepared_release(
        self, repo_root: Path, *, prepared: PreparedRepoReleaseView, owner_flow_id: str, scope_ids: list[str]
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        return self.release_finalizer.commit_prepared_release(
            repo_root, prepared=prepared, owner_flow_id=owner_flow_id, scope_ids=scope_ids
        )

    def preview_legacy_stable_adoption(
        self, repo_root: Path, *, summary: str
    ) -> ServiceResult[LegacyStableAdoptionPreviewView]:
        return self.release_finalizer.preview_legacy_stable_adoption(repo_root, summary=summary)

    def adopt_legacy_stable_repo(
        self,
        repo_root: Path,
        *,
        summary: str,
        dry_run: bool,
        scope_ids: list[str] | None = None,
    ) -> ServiceResult[LegacyStableAdoptionView]:
        return self.release_finalizer.adopt_legacy_stable_repo(
            repo_root, summary=summary, dry_run=dry_run, scope_ids=scope_ids
        )

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

    def restore_repo_release(
        self, repo_root: Path, *, release_id: str, dry_run: bool = False, leave_runtime_paused: bool = True
    ) -> ServiceResult[SnapshotRestoreView]:
        return self.release_finalizer.restore_repo_release(
            repo_root, release_id=release_id, dry_run=dry_run, leave_runtime_paused=leave_runtime_paused
        )
