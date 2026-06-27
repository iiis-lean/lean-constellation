"""Repo stable-point checkpoint snapshot and restore helpers."""

from __future__ import annotations

import shutil
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation import (
    FoundationContext,
    FoundationService,
    GateReport,
    MutationSummaryView,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.validation_snapshot.readiness_gate import ReadinessGateComponent


class RepoCheckpointKind(StrEnum):
    REQUIREMENT_BOOTSTRAP_TERMINAL = "requirement_bootstrap_terminal"
    ADAPTER_PREPARATION_TERMINAL = "adapter_preparation_terminal"
    BEFORE_NATIVE_COORDINATOR_DISPATCH = "before_native_coordinator_dispatch"
    BEFORE_CONTENT_TASK_DISPATCH = "before_content_task_dispatch"
    AFTER_CONTENT_TASK_BATCH_TERMINAL = "after_content_task_batch_terminal"


class RepoCheckpointPolicy(StrictModel):
    checkpoint_kind: RepoCheckpointKind
    requires_runtime_stable: bool = True
    requires_ark_snapshot: bool = True
    include_node_scopes: bool = False
    gate_name: str
    summary: str


class SnapshotFileEntry(StrictModel):
    source_relpath: str
    archive_relpath: str
    file_size: int


class SnapshotFilesManifest(StrictModel):
    entries: list[SnapshotFileEntry] = Field(default_factory=list)
    excluded_top_level: list[str] = Field(default_factory=list)
    summary: str


class RepoCheckpointSnapshotManifest(StrictModel):
    snapshot_id: str
    checkpoint_kind: RepoCheckpointKind
    label: str | None = None
    created_at: str
    repo_root: str
    ark_runtime_snapshot_id: str
    node_paths: list[str] = Field(default_factory=list)
    files_manifest_relpath: str
    summary: str


class RepoCheckpointSnapshotView(StrictModel):
    snapshot_id: str
    checkpoint_kind: RepoCheckpointKind
    label: str | None = None
    root: str
    ark_runtime_snapshot_id: str
    file_count: int
    summary: str


class SnapshotRestoreView(StrictModel):
    snapshot_id: str
    dry_run: bool
    restored_files: list[str] = Field(default_factory=list)
    would_restore_files: list[str] = Field(default_factory=list)
    ark_runtime_snapshot_id: str
    leave_runtime_paused: bool = True
    summary: str


class RuntimeStabilityProvider(Protocol):
    """Provider that knows whether ARK Flow/Step state is stable for a checkpoint."""

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ) -> ServiceResult[GateReport]:
        ...


class ArkRuntimeSnapshotProvider(Protocol):
    """Provider that creates and restores ARK runtime snapshots."""

    def create_runtime_snapshot(
        self,
        repo_root: Path,
        *,
        scope_ids: list[str],
        label: str | None = None,
    ) -> ServiceResult[str]:
        ...

    def restore_runtime_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        leave_runtime_paused: bool = True,
    ) -> ServiceResult[MutationSummaryView]:
        ...


class _MissingRuntimeStabilityProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ) -> ServiceResult[GateReport]:
        del repo_root, node_paths
        return self.foundation.ok(
            self.foundation.gate_failed(
                "runtime_stability",
                self.foundation.issue(
                    "runtime_stability_provider_missing",
                    "No ARK runtime stability provider is configured for repo stable-point snapshots.",
                    object_ref=checkpoint_kind.value,
                    suggested_action="Inject a runtime stability provider before creating checkpoint snapshots.",
                ),
                summary="Runtime stable-point status cannot be verified.",
            )
        )


class _MissingArkRuntimeSnapshotProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def create_runtime_snapshot(
        self,
        repo_root: Path,
        *,
        scope_ids: list[str],
        label: str | None = None,
    ) -> ServiceResult[str]:
        del repo_root, scope_ids, label
        return self.foundation.fail(
            self.foundation.issue(
                "ark_snapshot_provider_missing",
                "No ARK runtime snapshot provider is configured.",
                suggested_action="Inject an ARK runtime snapshot provider before creating repo checkpoint snapshots.",
            )
        )

    def restore_runtime_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        leave_runtime_paused: bool = True,
    ) -> ServiceResult[MutationSummaryView]:
        del repo_root, leave_runtime_paused
        return self.foundation.fail(
            self.foundation.issue(
                "ark_snapshot_provider_missing",
                "No ARK runtime snapshot provider is configured.",
                object_ref=snapshot_id,
                suggested_action="Inject an ARK runtime snapshot provider before restoring repo checkpoint snapshots.",
            )
        )


class SnapshotRestoreComponent:
    """Create, list, and restore repo stable-point checkpoint snapshots."""

    _EXCLUDED_TOP_LEVEL = {".git", ".lake", ".agent_runtime", "__pycache__", ".pytest_cache"}
    _EXCLUDED_CONSTELLATION_CHILDREN = {"snapshots"}

    def __init__(
        self,
        *,
        foundation: FoundationService | None = None,
        readiness_gate: ReadinessGateComponent | None = None,
        runtime_stability_provider: RuntimeStabilityProvider | None = None,
        ark_snapshot_provider: ArkRuntimeSnapshotProvider | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.readiness_gate = readiness_gate or ReadinessGateComponent(foundation=self.foundation)
        self.runtime_stability_provider = runtime_stability_provider or _MissingRuntimeStabilityProvider(self.foundation)
        self.ark_snapshot_provider = ark_snapshot_provider or _MissingArkRuntimeSnapshotProvider(self.foundation)

    @classmethod
    def checkpoint_policies(cls) -> dict[RepoCheckpointKind, RepoCheckpointPolicy]:
        return {
            RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL,
                gate_name="requirement_bootstrap_terminal_stable_point",
                summary="Requirement bootstrap flow has reached a terminal stable point.",
            ),
            RepoCheckpointKind.ADAPTER_PREPARATION_TERMINAL: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.ADAPTER_PREPARATION_TERMINAL,
                gate_name="adapter_preparation_terminal_stable_point",
                summary="Adapter preparation has reached a terminal stable point.",
            ),
            RepoCheckpointKind.BEFORE_NATIVE_COORDINATOR_DISPATCH: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_COORDINATOR_DISPATCH,
                gate_name="before_native_coordinator_dispatch_stable_point",
                summary="Native preparation is ready to hand off to the Coordinator before dispatch.",
            ),
            RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
                gate_name="before_content_task_dispatch_stable_point",
                summary="Coordinator is about to dispatch a content task batch.",
                include_node_scopes=True,
            ),
            RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL,
                gate_name="after_content_task_batch_terminal_stable_point",
                summary="A content task batch has reached terminal flow states.",
                include_node_scopes=True,
            ),
        }

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        node_paths: list[str] | None = None,
    ) -> ServiceResult[GateReport]:
        kind = RepoCheckpointKind(checkpoint_kind)
        policy = self.checkpoint_policies()[kind]
        reports: list[GateReport] = []

        runtime = self.runtime_stability_provider.check_repo_stable_point(
            Path(repo_root),
            checkpoint_kind=kind,
            node_paths=node_paths or [],
        )
        if not runtime.ok or runtime.value is None:
            return self.foundation.fail(runtime.issues)
        reports.append(runtime.value)

        if kind == RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL:
            bootstrap = self._check_preparation_input_exists(Path(repo_root), policy.gate_name)
            reports.append(bootstrap)
        elif kind == RepoCheckpointKind.ADAPTER_PREPARATION_TERMINAL:
            adapter = self.readiness_gate.check_adapter_ready(Path(repo_root))
            if not adapter.ok or adapter.value is None:
                return self.foundation.fail(adapter.issues)
            reports.append(adapter.value)
        elif kind == RepoCheckpointKind.BEFORE_NATIVE_COORDINATOR_DISPATCH:
            native = self.readiness_gate.check_native_handoff_gate(Path(repo_root))
            if not native.ok or native.value is None:
                return self.foundation.fail(native.issues)
            reports.append(native.value)
        else:
            reports.append(
                self.foundation.gate_passed(
                    f"{kind.value}_registry_gate",
                    summary="First-round stable-point registry gate passed; deep Content task checks are delegated to later flow-specific gates.",
                )
            )
        return self.foundation.ok(self.foundation.merge_gate_reports(policy.gate_name, reports))

    def create_repo_stable_point_snapshot(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        label: str | None = None,
        node_paths: list[str] | None = None,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        repo_root = Path(repo_root)
        kind = RepoCheckpointKind(checkpoint_kind)
        gate = self.check_repo_stable_point(repo_root, checkpoint_kind=kind, node_paths=node_paths)
        if not gate.ok or gate.value is None:
            return self.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.foundation.fail(gate.value.issues)

        snapshot_id_result = self.foundation.store.allocate_uuid(
            lambda candidate: self._snapshot_dir(repo_root, candidate).exists(),
            prefix="repo_cp",
        )
        if not snapshot_id_result.ok or snapshot_id_result.value is None:
            return self.foundation.fail(snapshot_id_result.issues)
        snapshot_id = snapshot_id_result.value
        snapshot_root = self._snapshot_dir(repo_root, snapshot_id)
        files_root = snapshot_root / "files"
        lc_archive = files_root / "lean_constellation"
        project_archive = files_root / "project"

        scope_ids = self._scope_ids_for(kind, node_paths or [])
        ark = self.ark_snapshot_provider.create_runtime_snapshot(repo_root, scope_ids=scope_ids, label=label)
        if not ark.ok or ark.value is None:
            return self.foundation.fail(ark.issues)

        try:
            snapshot_root.mkdir(parents=True, exist_ok=False)
            entries: list[SnapshotFileEntry] = []
            entries.extend(self._copy_constellation_truth(repo_root, lc_archive))
            entries.extend(self._copy_project_files(repo_root, project_archive))
        except OSError as exc:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            return self.foundation.fail(
                self.foundation.issue(
                    "repo_checkpoint_snapshot_write_failed",
                    f"Failed to create repo checkpoint snapshot: {exc}",
                    object_ref=str(snapshot_root),
                )
            )

        files_manifest = SnapshotFilesManifest(
            entries=entries,
            excluded_top_level=sorted(self._EXCLUDED_TOP_LEVEL),
            summary=f"Captured {len(entries)} files for repo checkpoint snapshot.",
        )
        manifest = RepoCheckpointSnapshotManifest(
            snapshot_id=snapshot_id,
            checkpoint_kind=kind,
            label=label.strip() if label else None,
            created_at=utc_now_iso(),
            repo_root=str(repo_root),
            ark_runtime_snapshot_id=ark.value,
            node_paths=list(node_paths or []),
            files_manifest_relpath="files_manifest.json",
            summary=gate.value.summary or f"Created repo checkpoint snapshot {snapshot_id}.",
        )
        write_files = self.foundation.store.write_json_atomic(snapshot_root / "files_manifest.json", files_manifest)
        if not write_files.ok:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            return self.foundation.fail(write_files.issues)
        write_manifest = self.foundation.store.write_json_atomic(snapshot_root / "snapshot.json", manifest)
        if not write_manifest.ok:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            return self.foundation.fail(write_manifest.issues)
        return self.foundation.ok(
            RepoCheckpointSnapshotView(
                snapshot_id=snapshot_id,
                checkpoint_kind=kind,
                label=manifest.label,
                root=str(snapshot_root),
                ark_runtime_snapshot_id=ark.value,
                file_count=len(entries),
                summary=f"Created {kind.value} checkpoint snapshot with {len(entries)} files.",
            )
        )

    def restore_repo_checkpoint_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        dry_run: bool = False,
        leave_runtime_paused: bool = True,
    ) -> ServiceResult[SnapshotRestoreView]:
        repo_root = Path(repo_root)
        manifest = self._load_manifest(repo_root, snapshot_id)
        if not manifest.ok or manifest.value is None:
            return self.foundation.fail(manifest.issues)
        files = self._load_files_manifest(repo_root, snapshot_id)
        if not files.ok or files.value is None:
            return self.foundation.fail(files.issues)
        would_restore = [entry.source_relpath for entry in files.value.entries]
        archive_preflight = self._preflight_restore_archive_files(repo_root, snapshot_id, files.value)
        if not archive_preflight.ok:
            return self.foundation.fail(archive_preflight.issues)
        if dry_run:
            return self.foundation.ok(
                SnapshotRestoreView(
                    snapshot_id=snapshot_id,
                    dry_run=True,
                    would_restore_files=would_restore,
                    ark_runtime_snapshot_id=manifest.value.ark_runtime_snapshot_id,
                    leave_runtime_paused=leave_runtime_paused,
                    summary=f"Dry-run restore would restore {len(would_restore)} files.",
                )
            )

        ark = self.ark_snapshot_provider.restore_runtime_snapshot(
            repo_root,
            snapshot_id=manifest.value.ark_runtime_snapshot_id,
            leave_runtime_paused=leave_runtime_paused,
        )
        if not ark.ok:
            return self.foundation.fail(ark.issues)
        restored: list[str] = []
        try:
            for entry in files.value.entries:
                source_archive = self._snapshot_dir(repo_root, snapshot_id) / "files" / entry.archive_relpath
                target = repo_root / entry.source_relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_archive, target)
                restored.append(entry.source_relpath)
        except OSError as exc:
            return self.foundation.fail(
                self.foundation.issue(
                    "repo_checkpoint_restore_failed",
                    f"Failed to restore repo checkpoint snapshot: {exc}",
                    object_ref=snapshot_id,
                )
            )
        rebuilt = self.rebuild_after_restore(repo_root)
        if not rebuilt.ok:
            return self.foundation.fail(rebuilt.issues)
        return self.foundation.ok(
            SnapshotRestoreView(
                snapshot_id=snapshot_id,
                dry_run=False,
                restored_files=restored,
                ark_runtime_snapshot_id=manifest.value.ark_runtime_snapshot_id,
                leave_runtime_paused=leave_runtime_paused,
                summary=f"Restored {len(restored)} files from repo checkpoint snapshot; {rebuilt.value.summary if rebuilt.value else 'rebuilt derived indexes after restore.'}",
            )
        )

    def rebuild_after_restore(self, repo_root: Path) -> ServiceResult[MutationSummaryView]:
        ctx = FoundationContext(repo_root=Path(repo_root), caller="validation_snapshot.rebuild_after_restore")
        metadata = self.foundation.index.list_index_metadata(ctx)
        if not metadata.ok or metadata.value is None:
            return self.foundation.fail(metadata.issues)
        rebuilt: list[str] = []
        warnings = []
        for item in metadata.value:
            result = self.foundation.index.rebuild_index(ctx, item.index_name, reason="restore")
            if result.ok and result.value is not None:
                rebuilt.append(item.index_name)
                warnings.extend(result.issues)
            else:
                return self.foundation.fail(result.issues)
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref=str(repo_root),
                changed=bool(rebuilt),
                summary=f"Rebuilt {len(rebuilt)} derived indexes after restore.",
                changed_items=rebuilt,
                warnings=warnings,
            )
        )

    def list_repo_checkpoint_snapshots(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str | None = None,
    ) -> ServiceResult[list[RepoCheckpointSnapshotView]]:
        root = self._snapshot_root(Path(repo_root))
        if not root.exists():
            return self.foundation.ok([])
        kind = RepoCheckpointKind(checkpoint_kind) if checkpoint_kind is not None else None
        views: list[tuple[str, RepoCheckpointSnapshotView]] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            loaded = self.foundation.store.read_json(child / "snapshot.json", RepoCheckpointSnapshotManifest)
            if not loaded.ok or loaded.value is None:
                continue
            if kind is not None and loaded.value.checkpoint_kind != kind:
                continue
            files = self.foundation.store.read_json(child / loaded.value.files_manifest_relpath, SnapshotFilesManifest)
            file_count = len(files.value.entries) if files.ok and files.value is not None else 0
            views.append(
                (
                    loaded.value.created_at,
                    RepoCheckpointSnapshotView(
                        snapshot_id=loaded.value.snapshot_id,
                        checkpoint_kind=loaded.value.checkpoint_kind,
                        label=loaded.value.label,
                        root=str(child),
                        ark_runtime_snapshot_id=loaded.value.ark_runtime_snapshot_id,
                        file_count=file_count,
                        summary=loaded.value.summary,
                    ),
                )
            )
        views.sort(key=lambda item: item[0], reverse=True)
        return self.foundation.ok([view for _, view in views])

    def _check_preparation_input_exists(self, repo_root: Path, gate_name: str) -> GateReport:
        path = self.foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
        if not path.exists():
            return self.foundation.gate_failed(
                gate_name,
                self.foundation.issue("preparation_input_missing", "preparation_input.json is missing.", object_ref=str(path)),
                summary="Preparation input is missing.",
            )
        return self.foundation.gate_passed(gate_name, summary="Preparation input exists.")

    def _snapshot_root(self, repo_root: Path) -> Path:
        return self.foundation.layout.snapshot_root(FoundationContext(repo_root=repo_root)) / "repo_checkpoints"

    def _snapshot_dir(self, repo_root: Path, snapshot_id: str) -> Path:
        return self._snapshot_root(repo_root) / self.foundation.layout.ensure_safe_key(snapshot_id)

    def _scope_ids_for(self, checkpoint_kind: RepoCheckpointKind, node_paths: list[str]) -> list[str]:
        policy = self.checkpoint_policies()[checkpoint_kind]
        scope_ids = ["repo"]
        if policy.include_node_scopes:
            scope_ids.extend(f"node:{path}" for path in node_paths)
        return scope_ids

    def _copy_constellation_truth(self, repo_root: Path, archive_root: Path) -> list[SnapshotFileEntry]:
        source = self.foundation.layout.constellation_root(FoundationContext(repo_root=repo_root))
        if not source.exists():
            return []
        entries: list[SnapshotFileEntry] = []
        for child in source.iterdir():
            if child.name in self._EXCLUDED_CONSTELLATION_CHILDREN:
                continue
            entries.extend(self._copy_path(child, archive_root / child.name, source_prefix=repo_root, archive_prefix=archive_root.parent))
        return entries

    def _copy_project_files(self, repo_root: Path, archive_root: Path) -> list[SnapshotFileEntry]:
        entries: list[SnapshotFileEntry] = []
        for child in repo_root.iterdir():
            if child.name in self._EXCLUDED_TOP_LEVEL or child.name == ".lean_constellation":
                continue
            entries.extend(self._copy_path(child, archive_root / child.name, source_prefix=repo_root, archive_prefix=archive_root.parent))
        return entries

    def _copy_path(self, source: Path, target: Path, *, source_prefix: Path, archive_prefix: Path) -> list[SnapshotFileEntry]:
        entries: list[SnapshotFileEntry] = []
        if source.is_dir():
            for child in source.iterdir():
                if child.name in self._EXCLUDED_TOP_LEVEL:
                    continue
                entries.extend(self._copy_path(child, target / child.name, source_prefix=source_prefix, archive_prefix=archive_prefix))
            return entries
        if not source.is_file():
            return entries
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        entries.append(
            SnapshotFileEntry(
                source_relpath=source.relative_to(source_prefix).as_posix(),
                archive_relpath=target.relative_to(archive_prefix).as_posix(),
                file_size=source.stat().st_size,
            )
        )
        return entries

    def _load_manifest(self, repo_root: Path, snapshot_id: str) -> ServiceResult[RepoCheckpointSnapshotManifest]:
        return self.foundation.store.read_json(self._snapshot_dir(repo_root, snapshot_id) / "snapshot.json", RepoCheckpointSnapshotManifest)

    def _load_files_manifest(self, repo_root: Path, snapshot_id: str) -> ServiceResult[SnapshotFilesManifest]:
        return self.foundation.store.read_json(self._snapshot_dir(repo_root, snapshot_id) / "files_manifest.json", SnapshotFilesManifest)

    def _preflight_restore_archive_files(
        self,
        repo_root: Path,
        snapshot_id: str,
        files_manifest: SnapshotFilesManifest,
    ) -> ServiceResult[MutationSummaryView]:
        missing: list[str] = []
        snapshot_files_root = self._snapshot_dir(repo_root, snapshot_id) / "files"
        for entry in files_manifest.entries:
            archive_file = snapshot_files_root / entry.archive_relpath
            if not archive_file.is_file():
                missing.append(entry.archive_relpath)
        if missing:
            return self.foundation.fail(
                self.foundation.issue(
                    "repo_checkpoint_archive_file_missing",
                    "Repo checkpoint archive is missing files required for restore.",
                    object_ref=snapshot_id,
                    details={"missing_archive_relpaths": missing},
                )
            )
        return self.foundation.ok(
            self.foundation.mutation_view(
                object_ref=snapshot_id,
                changed=False,
                summary=f"Restore archive preflight passed for {len(files_manifest.entries)} files.",
            )
        )
