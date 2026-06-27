"""ValidationSnapshotService composition and public wrappers."""

from __future__ import annotations

from pathlib import Path

from lean_constellation.services.adapter import AdapterService
from lean_constellation.services.foundation import FoundationService, GateReport, ServiceResult
from lean_constellation.services.lean_projection import LeanProjectionService
from lean_constellation.services.material import MaterialService
from lean_constellation.services.node import NodeService
from lean_constellation.services.repo_workspace import RepoWorkspaceService
from lean_constellation.services.validation_snapshot.admin_repair import AdminRepairComponent
from lean_constellation.services.validation_snapshot.audit import AuditComponent, AuditReport
from lean_constellation.services.validation_snapshot.consistency_check import ConsistencyCheckComponent
from lean_constellation.services.validation_snapshot.readiness_gate import ReadinessGateComponent
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    ArkRuntimeSnapshotProvider,
    RepoCheckpointKind,
    RepoCheckpointSnapshotView,
    RuntimeStabilityProvider,
    SnapshotRestoreComponent,
    SnapshotRestoreView,
)


class ValidationSnapshotService:
    """Composition root for validation, audit, snapshot, and admin repair services."""

    def __init__(
        self,
        *,
        foundation: FoundationService | None = None,
        repo_workspace: RepoWorkspaceService | None = None,
        material: MaterialService | None = None,
        node: NodeService | None = None,
        adapter: AdapterService | None = None,
        lean_projection: LeanProjectionService | None = None,
        consistency: ConsistencyCheckComponent | None = None,
        readiness_gate: ReadinessGateComponent | None = None,
        snapshot_restore: SnapshotRestoreComponent | None = None,
        audit: AuditComponent | None = None,
        admin_repair: AdminRepairComponent | None = None,
        runtime_stability_provider: RuntimeStabilityProvider | None = None,
        ark_snapshot_provider: ArkRuntimeSnapshotProvider | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.repo_workspace = repo_workspace or RepoWorkspaceService(foundation=self.foundation)
        self.material = material or MaterialService(foundation=self.foundation)
        self.lean_projection = lean_projection or LeanProjectionService(foundation=self.foundation)
        self.node = node or NodeService(
            foundation=self.foundation,
            repo_workspace=self.repo_workspace,
            material=self.material,
            node_projection=self.lean_projection.node_projection,
        )
        self.adapter = adapter or AdapterService(
            foundation=self.foundation,
            repo_workspace=self.repo_workspace,
            node=self.node,
            lean_projection=self.lean_projection,
        )
        self.consistency = consistency or ConsistencyCheckComponent(
            foundation=self.foundation,
            material=self.material,
            node=self.node,
            adapter=self.adapter,
            lean_projection=self.lean_projection,
        )
        self.readiness_gate = readiness_gate or ReadinessGateComponent(
            foundation=self.foundation,
            repo_workspace=self.repo_workspace,
            material=self.material,
            node=self.node,
            adapter=self.adapter,
            lean_projection=self.lean_projection,
            consistency=self.consistency,
        )
        self.audit = audit or AuditComponent(
            foundation=self.foundation,
            consistency=self.consistency,
            readiness_gate=self.readiness_gate,
        )
        self.snapshot_restore = snapshot_restore or SnapshotRestoreComponent(
            foundation=self.foundation,
            readiness_gate=self.readiness_gate,
            runtime_stability_provider=runtime_stability_provider,
            ark_snapshot_provider=ark_snapshot_provider,
        )
        self.admin_repair = admin_repair or AdminRepairComponent(
            foundation=self.foundation,
            repo_workspace=self.repo_workspace,
            node=self.node,
            lean_projection=self.lean_projection,
            audit=self.audit,
        )

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        return self.readiness_gate.check_content_node_ready(repo_root, node_path=node_path)

    def check_scope_commit(self, repo_root: Path, *, scope_path: str, summary: str) -> ServiceResult[GateReport]:
        return self.readiness_gate.check_scope_commit(repo_root, scope_path=scope_path, summary=summary)

    def check_repo_ready(self, repo_root: Path, *, summary: str) -> ServiceResult[GateReport]:
        return self.readiness_gate.check_repo_ready(repo_root, summary=summary)

    def run_round_local_audit(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> ServiceResult[AuditReport]:
        return self.audit.run_round_local_audit(repo_root, node_path=node_path, round_id=round_id, stage=stage)

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        node_paths: list[str] | None = None,
    ) -> ServiceResult[GateReport]:
        return self.snapshot_restore.check_repo_stable_point(repo_root, checkpoint_kind=checkpoint_kind, node_paths=node_paths)

    def create_repo_stable_point_snapshot(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        label: str | None = None,
        node_paths: list[str] | None = None,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        return self.snapshot_restore.create_repo_stable_point_snapshot(
            repo_root,
            checkpoint_kind=checkpoint_kind,
            label=label,
            node_paths=node_paths,
        )

    def restore_repo_checkpoint_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        dry_run: bool = False,
        leave_runtime_paused: bool = True,
    ) -> ServiceResult[SnapshotRestoreView]:
        return self.snapshot_restore.restore_repo_checkpoint_snapshot(
            repo_root,
            snapshot_id=snapshot_id,
            dry_run=dry_run,
            leave_runtime_paused=leave_runtime_paused,
        )

    def list_repo_checkpoint_snapshots(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str | None = None,
    ) -> ServiceResult[list[RepoCheckpointSnapshotView]]:
        return self.snapshot_restore.list_repo_checkpoint_snapshots(repo_root, checkpoint_kind=checkpoint_kind)

    def run_full_audit(self, repo_root: Path) -> ServiceResult[AuditReport]:
        return self.admin_repair.run_full_audit(repo_root)
