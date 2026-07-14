"""SourceIndex baseline access backed by repo checkpoint archives."""

from __future__ import annotations

import json
from pathlib import Path

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import RepoPreparationInput
from lean_constellation.flows.repo_lifecycle.source_index_steps import SourceIndexBaselineCheckpointView
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.material import SourceIndex
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointKind,
    RepoCheckpointSnapshotManifest,
)


class RootInterfaceBaselineCheckpointView(StrictModel):
    """Recoverable pre-run truth used to validate root-interface child input."""

    checkpoint_id: str
    repo_root: str
    preparation_input: RepoPreparationInput
    source_index: SourceIndex | None = None
    source_index_digest: str
    summary: str


class SourceIndexCheckpointAdapter:
    """Expose the narrow checkpoint protocol required by SourceIndexBuildFlow."""

    _BASELINE_KINDS = {
        RepoCheckpointKind.BEFORE_NATIVE_SOURCE_PROCESSING,
        RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
    }

    def __init__(self, runtime) -> None:  # noqa: ANN001
        self.runtime = runtime

    def validate_source_index_baseline_checkpoint(
        self,
        repo_root: Path,
        *,
        checkpoint_id: str,
    ) -> ServiceResult[SourceIndexBaselineCheckpointView]:
        snapshot = self._snapshot(repo_root, checkpoint_id)
        if not snapshot.ok or snapshot.value is None:
            return self.runtime.foundation.fail(snapshot.issues)
        if snapshot.value.checkpoint_kind not in self._BASELINE_KINDS:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_baseline_checkpoint_kind_mismatch",
                    "Checkpoint kind cannot serve as a pre-update SourceIndex baseline.",
                    object_ref=checkpoint_id,
                    current=snapshot.value.checkpoint_kind.value,
                    expected=", ".join(sorted(kind.value for kind in self._BASELINE_KINDS)),
                )
            )
        archived = self.load_source_index_baseline(repo_root, checkpoint_id=checkpoint_id)
        if not archived.ok:
            return self.runtime.foundation.fail(archived.issues)
        archived_digest = self._digest(archived.value)
        current = self.runtime.material.source_index.get_source_index_model(Path(repo_root))
        if current.ok and current.value is not None:
            current_index: SourceIndex | None = current.value
        elif any(issue.kind == "source_index_missing" for issue in current.issues):
            current_index = None
        else:
            return self.runtime.foundation.fail(current.issues)
        # A retry may observe the owning update already open. Its mutable
        # SourceIndex is expected to differ from the archived pre-update
        # baseline; ownership and retry-baseline equality are enforced by
        # ``open_source_index_update``. Before the update is open, however,
        # the archive must still match current truth exactly.
        current_digest = self._digest(current_index)
        update_open = bool(
            current_index is not None
            and current_index.status in {"draft", "updating"}
            and current_index.active_file_scope
        )
        if not update_open and archived_digest != current_digest:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_baseline_checkpoint_mismatch",
                    "Archived SourceIndex does not match the current pre-update baseline.",
                    object_ref=checkpoint_id,
                    current=current_digest,
                    expected=archived_digest,
                )
            )
        return self.runtime.foundation.ok(
            SourceIndexBaselineCheckpointView(
                checkpoint_id=checkpoint_id,
                repo_root=str(Path(repo_root)),
                baseline_digest=archived_digest,
                summary=f"Validated SourceIndex baseline checkpoint {checkpoint_id}.",
            )
        )

    def materialize_source_index_baseline_checkpoint(
        self,
        repo_root: Path,
        *,
        checkpoint_id: str,
        scope_ids: list[str],
        label: str,
    ) -> ServiceResult[SourceIndexBaselineCheckpointView]:
        snapshot_runtime = getattr(self.runtime.app, "snapshot_runtime", None)
        if snapshot_runtime is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "snapshot_runtime_missing",
                    "Application snapshot runtime is required to materialize a SourceIndex baseline.",
                )
            )
        created = snapshot_runtime.create_repo_stable_point_snapshot_with_id(
            Path(repo_root),
            snapshot_id=checkpoint_id,
            checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
            label=label,
            scope_ids=scope_ids,
        )
        if not created.ok or created.value is None:
            return self.runtime.foundation.fail(created.issues)
        return self.validate_source_index_baseline_checkpoint(repo_root, checkpoint_id=checkpoint_id)

    def load_source_index_baseline(
        self,
        repo_root: Path,
        *,
        checkpoint_id: str,
    ) -> ServiceResult[SourceIndex | None]:
        snapshot = self._snapshot(repo_root, checkpoint_id)
        if not snapshot.ok or snapshot.value is None:
            return self.runtime.foundation.fail(snapshot.issues)
        path = Path(snapshot.value.root) / "files" / "lean_constellation" / "source_index" / "index.json"
        if not path.exists():
            return self.runtime.foundation.ok(None)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return self.runtime.foundation.ok(SourceIndex.model_validate(payload))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_baseline_archive_invalid",
                    str(exc),
                    object_ref=checkpoint_id,
                )
            )

    def validate_root_interface_baseline_checkpoint(
        self,
        repo_root: Path,
        *,
        checkpoint_id: str,
        expected_kind: RepoCheckpointKind,
    ) -> ServiceResult[RootInterfaceBaselineCheckpointView]:
        """Validate and load the exact pre-run archive required by the Root flow."""

        repo_root = Path(repo_root)
        snapshot = self._snapshot(repo_root, checkpoint_id)
        if not snapshot.ok or snapshot.value is None:
            return self.runtime.foundation.fail(snapshot.issues)
        if snapshot.value.checkpoint_kind != expected_kind:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "root_interface_checkpoint_kind_mismatch",
                    "Root-interface preparation checkpoint kind does not match its invocation mode.",
                    object_ref=checkpoint_id,
                    current=snapshot.value.checkpoint_kind.value,
                    expected=expected_kind.value,
                )
            )

        snapshot_root = Path(snapshot.value.root)
        manifest = self.runtime.foundation.store.read_json(
            snapshot_root / "snapshot.json",
            RepoCheckpointSnapshotManifest,
        )
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        if (
            manifest.value.snapshot_id != checkpoint_id
            or manifest.value.checkpoint_kind != expected_kind
            or Path(manifest.value.repo_root) != repo_root
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "root_interface_checkpoint_manifest_mismatch",
                    "Checkpoint manifest does not identify the requested repository, id, and checkpoint kind.",
                    object_ref=checkpoint_id,
                )
            )

        snapshot_runtime = getattr(self.runtime.app, "snapshot_runtime", None)
        if snapshot_runtime is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "snapshot_runtime_missing",
                    "Application snapshot runtime is required to validate a root-interface baseline.",
                )
            )
        archive = snapshot_runtime.restore_repo_checkpoint_snapshot(
            repo_root,
            snapshot_id=checkpoint_id,
            dry_run=True,
        )
        if not archive.ok:
            return self.runtime.foundation.fail(archive.issues)

        preparation_path = snapshot_root / "files" / "lean_constellation" / "preparation_input.json"
        preparation = self.runtime.foundation.store.read_json(preparation_path, RepoPreparationInput)
        if not preparation.ok or preparation.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "root_interface_checkpoint_preparation_missing",
                    "Checkpoint does not contain a recoverable preparation input baseline.",
                    object_ref=checkpoint_id,
                    details={"issues": [issue.model_dump(mode="json") for issue in preparation.issues]},
                )
            )
        source_index = self.load_source_index_baseline(repo_root, checkpoint_id=checkpoint_id)
        if not source_index.ok:
            return self.runtime.foundation.fail(source_index.issues)
        return self.runtime.foundation.ok(
            RootInterfaceBaselineCheckpointView(
                checkpoint_id=checkpoint_id,
                repo_root=str(repo_root),
                preparation_input=preparation.value,
                source_index=source_index.value,
                source_index_digest=self._digest(source_index.value),
                summary=f"Validated recoverable root-interface baseline checkpoint {checkpoint_id}.",
            )
        )

    def _snapshot(self, repo_root: Path, checkpoint_id: str):  # noqa: ANN001
        snapshots = self.runtime.validation_snapshot.list_repo_checkpoint_snapshots(Path(repo_root))
        if not snapshots.ok or snapshots.value is None:
            return self.runtime.foundation.fail(snapshots.issues)
        matched = [item for item in snapshots.value if item.snapshot_id == checkpoint_id]
        if len(matched) != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_baseline_checkpoint_missing",
                    "SourceIndex baseline checkpoint does not exist in this repository.",
                    object_ref=checkpoint_id,
                )
            )
        return self.runtime.foundation.ok(matched[0])

    def _digest(self, index: SourceIndex | None) -> str:
        if index is None:
            return self.runtime.material.source_index.missing_source_index_digest()
        return self.runtime.material.source_index.canonical_source_index_digest(index)


__all__ = ["RootInterfaceBaselineCheckpointView", "SourceIndexCheckpointAdapter"]
