from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointKind,
    RepoCheckpointSnapshotManifest,
    SnapshotRestoreComponent,
)
from tests.unit_services_helpers import make_runtime


@pytest.mark.parametrize("ark_runtime_snapshot_id", [None, "ark_external_snapshot"])
def test_lc_checkpoint_create_and_restore_never_require_ark_provider(
    tmp_path: Path,
    ark_runtime_snapshot_id: str | None,
) -> None:
    runtime = make_runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    component = SnapshotRestoreComponent(runtime)
    original = "theorem checkpoint_truth : True := by trivial\n"
    (tmp_path / "Main.lean").write_text(original, encoding="utf-8")

    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        snapshot_id="core_checkpoint",
        ark_runtime_snapshot_id=ark_runtime_snapshot_id,
    )

    assert created.ok and created.value is not None
    assert created.value.ark_runtime_snapshot_id == ark_runtime_snapshot_id
    assert not hasattr(component, "runtime_stability_provider")
    assert not hasattr(component, "ark_snapshot_provider")
    manifest = runtime.foundation.store.read_json(
        Path(created.value.root) / "snapshot.json",
        RepoCheckpointSnapshotManifest,
    )
    assert manifest.ok and manifest.value is not None
    assert manifest.value.ark_runtime_snapshot_id == ark_runtime_snapshot_id

    (tmp_path / "Main.lean").write_text("-- modified\n", encoding="utf-8")
    restored = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )

    assert restored.ok and restored.value is not None
    assert restored.value.ark_runtime_snapshot_id == ark_runtime_snapshot_id
    assert (tmp_path / "Main.lean").read_text(encoding="utf-8") == original


def test_lc_checkpoint_manifest_requires_explicit_optional_ark_field(tmp_path: Path) -> None:
    runtime = make_runtime()
    component = SnapshotRestoreComponent(runtime)

    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        snapshot_id="lc_only_checkpoint",
        ark_runtime_snapshot_id=None,
    )

    assert created.ok and created.value is not None
    manifest = runtime.foundation.store.read_json(
        Path(created.value.root) / "snapshot.json",
        RepoCheckpointSnapshotManifest,
    )
    assert manifest.ok and manifest.value is not None
    assert manifest.value.model_dump()["ark_runtime_snapshot_id"] is None
