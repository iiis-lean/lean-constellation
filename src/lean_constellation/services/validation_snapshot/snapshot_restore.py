"""Repo stable-point checkpoint snapshot and restore helpers."""

from __future__ import annotations

import hashlib
import os
import shutil
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.repo import RepoModel, RepoPublicationState
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.foundation import (
    FoundationContext,
    GateReport,
    MutationSummaryView,
    ServiceResult,
)
from lean_constellation.services.validation_snapshot.readiness_gate import ReadinessGateComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class RepoCheckpointKind(StrEnum):
    REPO_RELEASE = "repo_release"
    REQUIREMENT_BOOTSTRAP_TERMINAL = "requirement_bootstrap_terminal"
    ADAPTER_PREPARATION_TERMINAL = "adapter_preparation_terminal"
    BEFORE_NATIVE_SOURCE_PROCESSING = "before_native_source_processing"
    BEFORE_NATIVE_RUN_MUTATION = "before_native_run_mutation"
    BEFORE_NATIVE_COORDINATOR_DISPATCH = "before_native_coordinator_dispatch"
    COORDINATOR_REQUIREMENT_WAITING = "coordinator_requirement_waiting"
    BEFORE_CONTENT_TASK_DISPATCH = "before_content_task_dispatch"
    AFTER_CONTENT_TASK_BATCH_TERMINAL = "after_content_task_batch_terminal"
    BEFORE_RESOURCE_REQUEST_DISPATCH = "before_resource_request_dispatch"
    AFTER_RESOURCE_REQUEST_TERMINAL = "after_resource_request_terminal"
    AFTER_CONTENT_PREPARATION_TERMINAL = "after_content_preparation_terminal"
    AFTER_CONTENT_DECL_ROUND_TERMINAL = "after_content_decl_round_terminal"
    MANUAL_TEST_STABLE_POINT = "manual_test_stable_point"


class RepoCheckpointPolicy(StrictModel):
    checkpoint_kind: RepoCheckpointKind
    gate_name: str
    summary: str


class SnapshotFileEntry(StrictModel):
    source_relpath: str
    archive_relpath: str
    file_size: int
    sha256: str | None = None


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
    ark_runtime_snapshot_id: str | None
    files_manifest_relpath: str
    summary: str


class RepoCheckpointSnapshotView(StrictModel):
    snapshot_id: str
    checkpoint_kind: RepoCheckpointKind
    label: str | None = None
    root: str
    ark_runtime_snapshot_id: str | None
    file_count: int
    summary: str


class SnapshotRestoreView(StrictModel):
    snapshot_id: str
    dry_run: bool
    restored_files: list[str] = Field(default_factory=list)
    would_restore_files: list[str] = Field(default_factory=list)
    would_prune_files: list[str] = Field(default_factory=list)
    would_invalidate_paths: list[str] = Field(default_factory=list)
    pruned_files: list[str] = Field(default_factory=list)
    invalidated_paths: list[str] = Field(default_factory=list)
    ark_runtime_snapshot_id: str | None
    summary: str


class SnapshotRestoreComponent:
    """Create, list, and restore repo stable-point checkpoint snapshots."""

    _EXCLUDED_TOP_LEVEL = {".git", ".lake", ".agent_runtime", "__pycache__", ".pytest_cache"}
    _EXCLUDED_CONSTELLATION_CHILDREN = {"snapshots", ".locks"}

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        readiness_gate: ReadinessGateComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.readiness_gate = readiness_gate or ReadinessGateComponent(runtime)

    @classmethod
    def checkpoint_policies(cls) -> dict[RepoCheckpointKind, RepoCheckpointPolicy]:
        return {
            RepoCheckpointKind.REPO_RELEASE: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.REPO_RELEASE,
                gate_name="repo_release_stable_point",
                summary="The native repository release transaction is at its stable publication hook.",
            ),
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
            RepoCheckpointKind.BEFORE_NATIVE_SOURCE_PROCESSING: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_SOURCE_PROCESSING,
                gate_name="before_native_source_processing_stable_point",
                summary="Native preparation is initialized before source, index, and interface Agent work.",
            ),
            RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
                gate_name="before_native_run_mutation_stable_point",
                summary="Native repo truth is stable before the first mutation of this run.",
            ),
            RepoCheckpointKind.BEFORE_NATIVE_COORDINATOR_DISPATCH: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_COORDINATOR_DISPATCH,
                gate_name="before_native_coordinator_dispatch_stable_point",
                summary="Native preparation is ready to hand off to the Coordinator before dispatch.",
            ),
            RepoCheckpointKind.COORDINATOR_REQUIREMENT_WAITING: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.COORDINATOR_REQUIREMENT_WAITING,
                gate_name="coordinator_requirement_waiting_stable_point",
                summary="Coordinator has submitted a repo requirement and is waiting for the provider repo.",
            ),
            RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
                gate_name="before_content_task_dispatch_stable_point",
                summary="Coordinator is about to dispatch a content task batch.",
            ),
            RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL,
                gate_name="after_content_task_batch_terminal_stable_point",
                summary="A content task batch has reached terminal flow states.",
            ),
            RepoCheckpointKind.BEFORE_RESOURCE_REQUEST_DISPATCH: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.BEFORE_RESOURCE_REQUEST_DISPATCH,
                gate_name="before_resource_request_dispatch_stable_point",
                summary="Coordinator is about to dispatch a resource request.",
            ),
            RepoCheckpointKind.AFTER_RESOURCE_REQUEST_TERMINAL: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.AFTER_RESOURCE_REQUEST_TERMINAL,
                gate_name="after_resource_request_terminal_stable_point",
                summary="A resource request has reached a terminal flow state.",
            ),
            RepoCheckpointKind.AFTER_CONTENT_PREPARATION_TERMINAL: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.AFTER_CONTENT_PREPARATION_TERMINAL,
                gate_name="after_content_preparation_terminal_stable_point",
                summary="A ContentNodeTask preparation recon is terminal before its PlanAgent callback.",
            ),
            RepoCheckpointKind.AFTER_CONTENT_DECL_ROUND_TERMINAL: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.AFTER_CONTENT_DECL_ROUND_TERMINAL,
                gate_name="after_content_decl_round_terminal_stable_point",
                summary="A ContentNodeTask Decl round is terminal before its PlanAgent callback.",
            ),
            RepoCheckpointKind.MANUAL_TEST_STABLE_POINT: RepoCheckpointPolicy(
                checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
                gate_name="manual_test_stable_point",
                summary="Admin test control requested a manual stable-point checkpoint.",
            ),
        }

    def check_checkpoint_business_gate(
        self, repo_root: Path, kind: RepoCheckpointKind
    ) -> ServiceResult[GateReport]:
        policy = self.checkpoint_policies()[kind]
        if kind == RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL:
            bootstrap = self._check_preparation_input_exists(Path(repo_root), policy.gate_name)
            return self.runtime.foundation.ok(bootstrap)
        elif kind == RepoCheckpointKind.ADAPTER_PREPARATION_TERMINAL:
            adapter = self.readiness_gate.check_adapter_ready(Path(repo_root))
            if not adapter.ok or adapter.value is None:
                return self.runtime.foundation.fail(adapter.issues)
            return self.runtime.foundation.ok(adapter.value)
        elif kind == RepoCheckpointKind.BEFORE_NATIVE_COORDINATOR_DISPATCH:
            native = self.readiness_gate.check_native_handoff_gate(Path(repo_root))
            if not native.ok or native.value is None:
                return self.runtime.foundation.fail(native.issues)
            return self.runtime.foundation.ok(native.value)
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                f"{kind.value}_registry_gate",
                summary="Checkpoint business registry gate passed; flow-specific checks remain in the runtime wrapper.",
            )
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
        """Archive project truth without inspecting or mutating ARK runtime state."""
        repo_root = Path(repo_root)
        kind = RepoCheckpointKind(checkpoint_kind)
        gate = self.check_checkpoint_business_gate(repo_root, kind)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        if snapshot_id is None:
            snapshot_id_result = self.runtime.foundation.store.allocate_uuid(
                lambda candidate: self._snapshot_dir(repo_root, candidate).exists(),
                prefix="repo_cp",
            )
            if not snapshot_id_result.ok or snapshot_id_result.value is None:
                return self.runtime.foundation.fail(snapshot_id_result.issues)
            snapshot_id = snapshot_id_result.value
        else:
            try:
                snapshot_id = self.runtime.foundation.layout.ensure_safe_key(snapshot_id.strip())
            except ValueError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "repo_checkpoint_snapshot_id_invalid",
                        str(exc),
                        object_ref=snapshot_id,
                    )
                )
        snapshot_root = self._snapshot_dir(repo_root, snapshot_id)
        if snapshot_root.exists():
            return self._load_existing_snapshot_view(repo_root, snapshot_id, expected_kind=kind)
        files_root = snapshot_root / "files"
        lc_archive = files_root / "lean_constellation"
        project_archive = files_root / "project"

        try:
            snapshot_root.mkdir(parents=True, exist_ok=False)
            entries: list[SnapshotFileEntry] = []
            entries.extend(self._copy_constellation_truth(repo_root, lc_archive))
            entries.extend(self._copy_project_files(repo_root, project_archive))
        except OSError as exc:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
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
            ark_runtime_snapshot_id=ark_runtime_snapshot_id,
            files_manifest_relpath="files_manifest.json",
            summary=gate.value.summary or f"Created repo checkpoint snapshot {snapshot_id}.",
        )
        write_files = self.runtime.foundation.store.write_json_atomic(snapshot_root / "files_manifest.json", files_manifest)
        if not write_files.ok:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            return self.runtime.foundation.fail(write_files.issues)
        write_manifest = self.runtime.foundation.store.write_json_atomic(snapshot_root / "snapshot.json", manifest)
        if not write_manifest.ok:
            shutil.rmtree(snapshot_root, ignore_errors=True)
            return self.runtime.foundation.fail(write_manifest.issues)
        return self.runtime.foundation.ok(
            RepoCheckpointSnapshotView(
                snapshot_id=snapshot_id,
                checkpoint_kind=kind,
                label=manifest.label,
                root=str(snapshot_root),
                ark_runtime_snapshot_id=ark_runtime_snapshot_id,
                file_count=len(entries),
                summary=f"Created {kind.value} checkpoint snapshot with {len(entries)} files.",
            )
        )

    def create_repo_release_checkpoint(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        release: RepoRelease,
        publication: RepoPublicationState,
        repo_model: RepoModel,
        expected_candidate_digest: str,
        label: str | None = None,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        """Stage and atomically publish a release checkpoint containing final stable truth."""
        repo_root = Path(repo_root)
        try:
            snapshot_id = self.runtime.foundation.layout.ensure_safe_key(snapshot_id.strip())
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_checkpoint_snapshot_id_invalid", str(exc), object_ref=snapshot_id
            ))
        final_root = self._snapshot_dir(repo_root, snapshot_id)
        if final_root.exists():
            return self._load_existing_release_checkpoint_view(
                repo_root,
                snapshot_id,
                release=release,
                publication=publication,
                repo_model=repo_model,
            )
        current_digest = self.runtime.validation_snapshot.release_finalizer.compute_candidate_digest(repo_root)
        if current_digest != expected_candidate_digest:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "release_candidate_drift",
                "Candidate truth changed before release checkpoint staging.",
                current=current_digest,
                expected=expected_candidate_digest,
            ))
        staging_root = self._snapshot_root(repo_root) / ".staging" / snapshot_id
        if staging_root.exists():
            try:
                shutil.rmtree(staging_root)
            except OSError as exc:
                return self.runtime.foundation.fail(self.runtime.foundation.issue(
                    "repo_release_checkpoint_staging_cleanup_failed",
                    f"Failed to remove stale release checkpoint staging: {exc}",
                    object_ref=snapshot_id,
                ))
        files_root = staging_root / "files"
        lc_archive = files_root / "lean_constellation"
        project_archive = files_root / "project"
        try:
            staging_root.mkdir(parents=True, exist_ok=False)
            self._copy_constellation_truth(repo_root, lc_archive)
            self._copy_project_files(repo_root, project_archive)

            ctx = FoundationContext(repo_root=repo_root, caller="release_checkpoint.overlay")
            overlays = (
                (self.runtime.foundation.layout.repo_metadata_path(ctx), repo_model),
                (self.runtime.repo_workspace.metadata._repo_publication_path(repo_root), publication),
                (self.runtime.foundation.layout.release_path(ctx, release.release_id), release),
            )
            for live_path, value in overlays:
                relative = live_path.relative_to(repo_root / ".lean_constellation")
                written = self.runtime.foundation.store.write_json_atomic(lc_archive / relative, value)
                if not written.ok:
                    raise OSError("; ".join(issue.kind for issue in written.issues))

            if self.runtime.validation_snapshot.release_finalizer.compute_candidate_digest(repo_root) != expected_candidate_digest:
                raise RuntimeError("release_candidate_drift")
            entries = self._entries_for_staged_archive(repo_root, files_root)
            files_manifest = SnapshotFilesManifest(
                entries=entries,
                excluded_top_level=sorted(self._EXCLUDED_TOP_LEVEL),
                summary=f"Captured {len(entries)} files for the final release checkpoint.",
            )
            preflight = self._preflight_archive_root(repo_root, files_root, files_manifest)
            if not preflight.ok:
                raise OSError("; ".join(issue.kind for issue in preflight.issues))

            manifest = RepoCheckpointSnapshotManifest(
                snapshot_id=snapshot_id,
                checkpoint_kind=RepoCheckpointKind.REPO_RELEASE,
                label=label.strip() if label else None,
                created_at=utc_now_iso(),
                repo_root=str(repo_root),
                ark_runtime_snapshot_id=None,
                files_manifest_relpath="files_manifest.json",
                summary=f"Created final stable truth checkpoint for release {release.release_id}.",
            )
            for path, value in (
                (staging_root / "files_manifest.json", files_manifest),
                (staging_root / "snapshot.json", manifest),
            ):
                written = self.runtime.foundation.store.write_json_atomic(path, value)
                if not written.ok:
                    raise OSError("; ".join(issue.kind for issue in written.issues))
            files_readback = self.runtime.foundation.store.read_json(
                staging_root / "files_manifest.json", SnapshotFilesManifest
            )
            manifest_readback = self.runtime.foundation.store.read_json(
                staging_root / "snapshot.json", RepoCheckpointSnapshotManifest
            )
            if (
                not files_readback.ok
                or files_readback.value != files_manifest
                or not manifest_readback.ok
                or manifest_readback.value != manifest
            ):
                raise OSError("release checkpoint manifest readback mismatch")
            self._fsync_tree(staging_root)
            final_root.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_root, final_root)
            self.runtime.foundation.store._fsync_parent(final_root)
        except (OSError, RuntimeError) as exc:
            cleanup_errors: list[str] = []
            for cleanup_root in (staging_root, final_root):
                if not cleanup_root.exists():
                    continue
                try:
                    shutil.rmtree(cleanup_root)
                except OSError as cleanup_exc:
                    cleanup_errors.append(f"{cleanup_root}: {cleanup_exc}")
                if cleanup_root.exists():
                    cleanup_errors.append(f"{cleanup_root}: still exists")
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_release_checkpoint_publish_failed",
                f"Failed to publish the final release checkpoint: {exc}",
                object_ref=snapshot_id,
                details={"cleanup_errors": "; ".join(cleanup_errors)},
            ))
        return self.runtime.foundation.ok(RepoCheckpointSnapshotView(
            snapshot_id=snapshot_id,
            checkpoint_kind=RepoCheckpointKind.REPO_RELEASE,
            label=manifest.label,
            root=str(final_root),
            ark_runtime_snapshot_id=manifest.ark_runtime_snapshot_id,
            file_count=len(entries),
            summary=manifest.summary,
        ))

    def _load_existing_snapshot_view(
        self,
        repo_root: Path,
        snapshot_id: str,
        *,
        expected_kind: RepoCheckpointKind,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        manifest = self._load_manifest(repo_root, snapshot_id)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_checkpoint_snapshot_id_conflict",
                    "The requested checkpoint id already exists but is not a complete checkpoint.",
                    object_ref=snapshot_id,
                )
            )
        if manifest.value.checkpoint_kind != expected_kind or Path(manifest.value.repo_root) != Path(repo_root):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_checkpoint_snapshot_id_conflict",
                    "The requested checkpoint id belongs to a different repository or checkpoint kind.",
                    object_ref=snapshot_id,
                )
            )
        files = self._load_files_manifest(repo_root, snapshot_id)
        if not files.ok or files.value is None:
            return self.runtime.foundation.fail(files.issues)
        archive = self._preflight_restore_archive_files(repo_root, snapshot_id, files.value)
        if not archive.ok:
            return self.runtime.foundation.fail(archive.issues)
        return self.runtime.foundation.ok(
            RepoCheckpointSnapshotView(
                snapshot_id=manifest.value.snapshot_id,
                checkpoint_kind=manifest.value.checkpoint_kind,
                label=manifest.value.label,
                root=str(self._snapshot_dir(repo_root, snapshot_id)),
                ark_runtime_snapshot_id=manifest.value.ark_runtime_snapshot_id,
                file_count=len(files.value.entries),
                summary=manifest.value.summary,
            )
        )

    def _load_existing_release_checkpoint_view(
        self,
        repo_root: Path,
        snapshot_id: str,
        *,
        release: RepoRelease,
        publication: RepoPublicationState,
        repo_model: RepoModel,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        loaded = self._load_existing_snapshot_view(
            repo_root, snapshot_id, expected_kind=RepoCheckpointKind.REPO_RELEASE
        )
        if not loaded.ok or loaded.value is None:
            return loaded
        archive = self._snapshot_dir(repo_root, snapshot_id) / "files" / "lean_constellation"
        archived_release = self.runtime.foundation.store.read_json(
            archive / "releases" / f"{release.release_id}.json", RepoRelease
        )
        archived_publication = self.runtime.foundation.store.read_json(
            archive / "repo_publication.json", RepoPublicationState
        )
        archived_model = self.runtime.foundation.store.read_json(archive / "repo.json", RepoModel)
        conflicts: list[str] = []
        if not archived_release.ok or archived_release.value != release:
            conflicts.append("release")
        if not archived_publication.ok or archived_publication.value != publication:
            conflicts.append("publication")
        if not archived_model.ok or archived_model.value != repo_model:
            conflicts.append("repo_model")
        if conflicts:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_release_checkpoint_id_conflict",
                "Existing release checkpoint does not contain the exact prepared final truth overlay.",
                object_ref=snapshot_id,
                details={"conflicts": ", ".join(conflicts)},
            ))
        return loaded

    def restore_repo_checkpoint_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        dry_run: bool = False,
        prune_extra_files: bool = False,
        allow_release_internal: bool = False,
    ) -> ServiceResult[SnapshotRestoreView]:
        """Restore only Lean Constellation project truth from a checkpoint archive."""
        repo_root = Path(repo_root)
        manifest = self._load_manifest(repo_root, snapshot_id)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        if manifest.value.checkpoint_kind == RepoCheckpointKind.REPO_RELEASE and not allow_release_internal:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_release_restore_requires_typed_route",
                "Repo release checkpoints must be restored through restore_repo_release current-latest validation.",
                object_ref=snapshot_id,
            ))
        files = self._load_files_manifest(repo_root, snapshot_id)
        if not files.ok or files.value is None:
            return self.runtime.foundation.fail(files.issues)
        would_restore = [entry.source_relpath for entry in files.value.entries]
        archive_preflight = self._preflight_restore_archive_files(repo_root, snapshot_id, files.value)
        if not archive_preflight.ok:
            return self.runtime.foundation.fail(archive_preflight.issues)
        if dry_run:
            would_prune = self._extra_files_for_restore(repo_root, files.value) if prune_extra_files else []
            would_invalidate = self._lake_build_paths_to_invalidate(repo_root)
            return self.runtime.foundation.ok(
                SnapshotRestoreView(
                    snapshot_id=snapshot_id,
                    dry_run=True,
                    would_restore_files=would_restore,
                    would_prune_files=would_prune,
                    would_invalidate_paths=would_invalidate,
                    ark_runtime_snapshot_id=manifest.value.ark_runtime_snapshot_id,
                    summary=f"Dry-run restore would restore {len(would_restore)} files.",
                )
            )

        restored: list[str] = []
        pruned: list[str] = []
        invalidated: list[str] = []
        try:
            if prune_extra_files:
                pruned = self._prune_extra_files_for_restore(repo_root, files.value)
            for entry in files.value.entries:
                source_archive = self._resolve_managed_relative_path(
                    self._snapshot_dir(repo_root, snapshot_id) / "files",
                    entry.archive_relpath,
                )
                target = self._resolve_managed_relative_path(repo_root, entry.source_relpath)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_archive, target)
                restored.append(entry.source_relpath)
            invalidated = self._invalidate_lake_build_artifacts(repo_root)
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_checkpoint_restore_failed",
                    f"Failed to restore repo checkpoint snapshot: {exc}",
                    object_ref=snapshot_id,
                )
            )
        rebuilt = self.rebuild_after_restore(repo_root)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(
            SnapshotRestoreView(
                snapshot_id=snapshot_id,
                dry_run=False,
                restored_files=restored,
                pruned_files=pruned,
                invalidated_paths=invalidated,
                ark_runtime_snapshot_id=manifest.value.ark_runtime_snapshot_id,
                summary=(
                    f"Restored {len(restored)} files from repo checkpoint snapshot"
                    f"{f' and pruned {len(pruned)} extra files' if prune_extra_files else ''}; "
                    f"invalidated {len(invalidated)} stale Lake build paths; "
                    f"{rebuilt.value.summary if rebuilt.value else 'rebuilt derived indexes after restore.'}"
                ),
            )
        )

    def validate_repo_checkpoint_snapshot(
        self, repo_root: Path, *, snapshot_id: str
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        repo_root = Path(repo_root)
        manifest = self._load_manifest(repo_root, snapshot_id)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        if manifest.value.snapshot_id != snapshot_id or Path(manifest.value.repo_root).resolve() != repo_root.resolve():
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_checkpoint_snapshot_identity_mismatch",
                "Checkpoint manifest identity or repository root does not match the requested snapshot.",
                object_ref=snapshot_id,
            ))
        files = self.runtime.foundation.store.read_json(
            self._snapshot_dir(repo_root, snapshot_id) / manifest.value.files_manifest_relpath,
            SnapshotFilesManifest,
        )
        if not files.ok or files.value is None:
            return self.runtime.foundation.fail(files.issues)
        archive = self._preflight_restore_archive_files(repo_root, snapshot_id, files.value)
        if not archive.ok:
            return self.runtime.foundation.fail(archive.issues)
        return self.runtime.foundation.ok(RepoCheckpointSnapshotView(
            snapshot_id=snapshot_id, checkpoint_kind=manifest.value.checkpoint_kind,
            label=manifest.value.label, root=str(self._snapshot_dir(repo_root, snapshot_id)),
            ark_runtime_snapshot_id=manifest.value.ark_runtime_snapshot_id,
            file_count=len(files.value.entries), summary=manifest.value.summary,
        ))

    def rebuild_after_restore(self, repo_root: Path) -> ServiceResult[MutationSummaryView]:
        ctx = FoundationContext(repo_root=Path(repo_root), caller="validation_snapshot.rebuild_after_restore")
        node_index = self.runtime.node.node_tree.node_store.rebuild_index(repo_root)
        if not node_index.ok:
            return self.runtime.foundation.fail(node_index.issues)
        metadata = self.runtime.foundation.index.list_index_metadata(ctx)
        if not metadata.ok or metadata.value is None:
            return self.runtime.foundation.fail(metadata.issues)
        rebuilt: list[str] = ["node:index"]
        warnings = []
        for item in metadata.value:
            result = self.runtime.foundation.index.rebuild_index(ctx, item.index_name, reason="restore")
            if result.ok and result.value is not None:
                rebuilt.append(item.index_name)
                warnings.extend(result.issues)
            else:
                return self.runtime.foundation.fail(result.issues)
        nodes = self.runtime.node.node_tree.node_store.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        for node in nodes.value:
            if node.lifecycle.value != "active" or node.kind.value != "content":
                continue
            graph_root = self.runtime.node.node_tree.node_store.decl_graph_dir(repo_root, node_id=node.node_id)
            if not graph_root.exists():
                continue
            decl_graph = self.runtime.decl_graph.rebuild_decl_graph_index(repo_root, node_path=node.path)
            if not decl_graph.ok:
                return self.runtime.foundation.fail(decl_graph.issues)
            rebuilt.append(f"decl_graph:{node.node_id}")
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
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
            return self.runtime.foundation.ok([])
        kind = RepoCheckpointKind(checkpoint_kind) if checkpoint_kind is not None else None
        views: list[tuple[str, RepoCheckpointSnapshotView]] = []
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            loaded = self.runtime.foundation.store.read_json(child / "snapshot.json", RepoCheckpointSnapshotManifest)
            if not loaded.ok or loaded.value is None:
                continue
            if kind is not None and loaded.value.checkpoint_kind != kind:
                continue
            files = self.runtime.foundation.store.read_json(child / loaded.value.files_manifest_relpath, SnapshotFilesManifest)
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
        return self.runtime.foundation.ok([view for _, view in views])

    def _check_preparation_input_exists(self, repo_root: Path, gate_name: str) -> GateReport:
        path = self.runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
        if not path.exists():
            return self.runtime.foundation.gate_failed(
                gate_name,
                self.runtime.foundation.issue("preparation_input_missing", "preparation_input.json is missing.", object_ref=str(path)),
                summary="Preparation input is missing.",
            )
        return self.runtime.foundation.gate_passed(gate_name, summary="Preparation input exists.")

    def _snapshot_root(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.snapshot_root(FoundationContext(repo_root=repo_root)) / "repo_checkpoints"

    def _snapshot_dir(self, repo_root: Path, snapshot_id: str) -> Path:
        return self._snapshot_root(repo_root) / self.runtime.foundation.layout.ensure_safe_key(snapshot_id)

    def _copy_constellation_truth(self, repo_root: Path, archive_root: Path) -> list[SnapshotFileEntry]:
        source = self.runtime.foundation.layout.constellation_root(FoundationContext(repo_root=repo_root))
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

    def _entries_for_staged_archive(self, repo_root: Path, files_root: Path) -> list[SnapshotFileEntry]:
        entries: list[SnapshotFileEntry] = []
        for archive_file in sorted(path for path in files_root.rglob("*") if path.is_file()):
            archive_relpath = archive_file.relative_to(files_root).as_posix()
            parts = Path(archive_relpath).parts
            if parts[0] == "lean_constellation":
                source_relpath = (Path(".lean_constellation") / Path(*parts[1:])).as_posix()
            elif parts[0] == "project":
                source_relpath = Path(*parts[1:]).as_posix()
            else:
                raise OSError(f"Unexpected release checkpoint archive root: {parts[0]}")
            self._resolve_managed_relative_path(repo_root, source_relpath)
            entries.append(SnapshotFileEntry(
                source_relpath=source_relpath,
                archive_relpath=archive_relpath,
                file_size=archive_file.stat().st_size,
                sha256=self._sha256_file(archive_file),
            ))
        return entries

    def _preflight_archive_root(
        self,
        repo_root: Path,
        files_root: Path,
        files_manifest: SnapshotFilesManifest,
    ) -> ServiceResult[MutationSummaryView]:
        missing: list[str] = []
        mismatched: list[str] = []
        for entry in files_manifest.entries:
            try:
                archive = self._resolve_managed_relative_path(files_root, entry.archive_relpath)
                self._resolve_managed_relative_path(repo_root, entry.source_relpath)
            except ValueError:
                mismatched.append(entry.archive_relpath)
                continue
            if not archive.is_file():
                missing.append(entry.archive_relpath)
            elif archive.stat().st_size != entry.file_size or (
                entry.sha256 is not None and self._sha256_file(archive) != entry.sha256
            ):
                mismatched.append(entry.archive_relpath)
        if missing or mismatched:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_checkpoint_archive_invalid",
                "Staged release checkpoint archive failed integrity preflight.",
                details={"missing": ", ".join(missing), "mismatched": ", ".join(mismatched)},
            ))
        return self.runtime.foundation.ok(self.runtime.foundation.mutation_view(
            object_ref=str(files_root), changed=False, summary="Staged archive integrity passed."
        ))

    @staticmethod
    def _fsync_tree(root: Path) -> None:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
        for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def _prune_extra_files_for_restore(self, repo_root: Path, files_manifest: SnapshotFilesManifest) -> list[str]:
        pruned: list[str] = []
        for relpath in self._extra_files_for_restore(repo_root, files_manifest):
            target = repo_root / relpath
            if not target.is_file():
                continue
            target.unlink()
            pruned.append(relpath)
        self._remove_empty_snapshot_managed_dirs(repo_root)
        return pruned

    def _extra_files_for_restore(self, repo_root: Path, files_manifest: SnapshotFilesManifest) -> list[str]:
        expected = {entry.source_relpath for entry in files_manifest.entries}
        return sorted(
            relpath
            for relpath in self._current_snapshot_managed_files(repo_root)
            if relpath not in expected
        )

    def _current_snapshot_managed_files(self, repo_root: Path) -> list[str]:
        managed: list[str] = []
        constellation_root = self.runtime.foundation.layout.constellation_root(FoundationContext(repo_root=repo_root))
        if constellation_root.exists():
            for child in constellation_root.iterdir():
                if child.name in self._EXCLUDED_CONSTELLATION_CHILDREN:
                    continue
                managed.extend(self._list_files(child, repo_root))
        for child in repo_root.iterdir():
            if child.name in self._EXCLUDED_TOP_LEVEL or child.name == ".lean_constellation":
                continue
            managed.extend(self._list_files(child, repo_root))
        return managed

    def _list_files(self, path: Path, repo_root: Path) -> list[str]:
        if path.is_file():
            return [path.relative_to(repo_root).as_posix()]
        if not path.is_dir():
            return []
        files: list[str] = []
        for child in path.iterdir():
            if child.name in self._EXCLUDED_TOP_LEVEL:
                continue
            files.extend(self._list_files(child, repo_root))
        return files

    def _remove_empty_snapshot_managed_dirs(self, repo_root: Path) -> None:
        roots: list[Path] = []
        constellation_root = self.runtime.foundation.layout.constellation_root(FoundationContext(repo_root=repo_root))
        if constellation_root.exists():
            roots.extend(child for child in constellation_root.iterdir() if child.name not in self._EXCLUDED_CONSTELLATION_CHILDREN)
        roots.extend(
            child
            for child in repo_root.iterdir()
            if child.name not in self._EXCLUDED_TOP_LEVEL and child.name != ".lean_constellation"
        )
        for root in roots:
            self._remove_empty_dirs(root, stop_at=repo_root)

    def _remove_empty_dirs(self, path: Path, *, stop_at: Path) -> None:
        if not path.is_dir() or path == stop_at:
            return
        for child in list(path.iterdir()):
            self._remove_empty_dirs(child, stop_at=stop_at)
        try:
            path.rmdir()
        except OSError:
            return

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
                file_size=target.stat().st_size,
                sha256=self._sha256_file(target),
            )
        )
        return entries

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _lake_build_paths_to_invalidate(self, repo_root: Path) -> list[str]:
        build_root = repo_root / ".lake" / "build"
        return [".lake/build"] if build_root.exists() or build_root.is_symlink() else []

    def _invalidate_lake_build_artifacts(self, repo_root: Path) -> list[str]:
        build_root = repo_root / ".lake" / "build"
        if build_root.is_symlink() or build_root.is_file():
            build_root.unlink()
            return [".lake/build"]
        if build_root.is_dir():
            shutil.rmtree(build_root)
            return [".lake/build"]
        return []

    def _load_manifest(self, repo_root: Path, snapshot_id: str) -> ServiceResult[RepoCheckpointSnapshotManifest]:
        return self.runtime.foundation.store.read_json(self._snapshot_dir(repo_root, snapshot_id) / "snapshot.json", RepoCheckpointSnapshotManifest)

    def _load_files_manifest(self, repo_root: Path, snapshot_id: str) -> ServiceResult[SnapshotFilesManifest]:
        return self.runtime.foundation.store.read_json(self._snapshot_dir(repo_root, snapshot_id) / "files_manifest.json", SnapshotFilesManifest)

    def _preflight_restore_archive_files(
        self,
        repo_root: Path,
        snapshot_id: str,
        files_manifest: SnapshotFilesManifest,
    ) -> ServiceResult[MutationSummaryView]:
        missing: list[str] = []
        mismatched: list[dict[str, object]] = []
        unsafe: list[dict[str, str]] = []
        snapshot_files_root = self._snapshot_dir(repo_root, snapshot_id) / "files"
        for entry in files_manifest.entries:
            try:
                archive_file = self._resolve_managed_relative_path(snapshot_files_root, entry.archive_relpath)
                self._resolve_managed_relative_path(repo_root, entry.source_relpath)
            except ValueError as exc:
                unsafe.append(
                    {
                        "source_relpath": entry.source_relpath,
                        "archive_relpath": entry.archive_relpath,
                        "error": str(exc),
                    }
                )
                continue
            if not archive_file.is_file():
                missing.append(entry.archive_relpath)
                continue
            actual_size = archive_file.stat().st_size
            if actual_size != entry.file_size:
                mismatched.append(
                    {
                        "archive_relpath": entry.archive_relpath,
                        "expected_size": entry.file_size,
                        "actual_size": actual_size,
                    }
                )
                continue
            if entry.sha256 is not None:
                actual_sha256 = self._sha256_file(archive_file)
                if actual_sha256 != entry.sha256:
                    mismatched.append(
                        {
                            "archive_relpath": entry.archive_relpath,
                            "expected_sha256": entry.sha256,
                            "actual_sha256": actual_sha256,
                        }
                    )
        if unsafe:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_checkpoint_archive_path_unsafe",
                    "Repo checkpoint manifest contains paths outside the managed archive or repo root.",
                    object_ref=snapshot_id,
                    details={"unsafe_entries": unsafe},
                )
            )
        if missing:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_checkpoint_archive_file_missing",
                    "Repo checkpoint archive is missing files required for restore.",
                    object_ref=snapshot_id,
                    details={"missing_archive_relpaths": missing},
                )
            )
        if mismatched:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_checkpoint_archive_file_mismatch",
                    "Repo checkpoint archive contains files whose size or checksum does not match the manifest.",
                    object_ref=snapshot_id,
                    details={"mismatched_archive_files": mismatched},
                )
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=snapshot_id,
                changed=False,
                summary=f"Restore archive preflight passed for {len(files_manifest.entries)} files.",
            )
        )

    @staticmethod
    def _resolve_managed_relative_path(root: Path, relpath: str) -> Path:
        relative = Path(relpath)
        if not relpath or relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"unsafe relative path: {relpath!r}")
        resolved_root = root.resolve(strict=False)
        resolved_path = (root / relative).resolve(strict=False)
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"path escapes managed root: {relpath!r}") from exc
        return resolved_path
