from pathlib import Path

from lean_constellation.domain.repo import RepoFormat, RepoPublicationState, RepoPublicationStatus
from lean_constellation.services.foundation import FoundationContext, WriteMode
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointKind,
    RepoCheckpointSnapshotManifest,
)
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo, _release
from tests.unit_services_helpers import make_runtime


def _write_publication(runtime, repo_root: Path, *, latest_release_id: str | None) -> None:
    path = runtime.repo_workspace.metadata._repo_publication_path(repo_root)
    assert runtime.foundation.store.write_json_atomic(
        path,
        RepoPublicationState(status=RepoPublicationStatus.STABLE, latest_release_id=latest_release_id),
        mode=WriteMode.OVERWRITE,
    ).ok


def _write_checkpoint(runtime, repo_root: Path, checkpoint_id: str) -> None:
    root = runtime.foundation.layout.snapshot_root(FoundationContext(repo_root=repo_root)) / "repo_checkpoints" / checkpoint_id
    manifest = RepoCheckpointSnapshotManifest(
        snapshot_id=checkpoint_id,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        created_at="2026-07-12T00:00:00Z",
        repo_root=str(repo_root),
        ark_runtime_snapshot_id="ark_snapshot_1",
        files_manifest_relpath="files.json",
        summary="Checkpoint fixture.",
    )
    assert runtime.foundation.store.write_json_atomic(root / "snapshot.json", manifest).ok


def test_native_provider_requires_release_and_checkpoint(tmp_path: Path) -> None:
    runtime, versions = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.metadata.set_repo_format(tmp_path, repo_format=RepoFormat.NATIVE, reason="native").ok
    release = _release("r1", versions)
    assert runtime.repo_workspace.release.create_release(tmp_path, release=release).ok
    _write_publication(runtime, tmp_path, latest_release_id="r1")

    missing_checkpoint = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert not missing_checkpoint.ok
    assert missing_checkpoint.issues[0].kind == "provider_native_checkpoint_missing"

    _write_checkpoint(runtime, tmp_path, release.repo_checkpoint_id)
    available = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert available.ok and available.value.passed is True


def test_native_without_stable_release_and_unknown_are_rejected(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    assert runtime.repo_workspace.metadata.set_repo_format(tmp_path, repo_format=RepoFormat.NATIVE, reason="native").ok
    _write_publication(runtime, tmp_path, latest_release_id=None)

    unavailable = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert unavailable.ok and unavailable.value.passed is False
    assert unavailable.value.issues[0].kind == "provider_native_stable_release_missing"

    other = tmp_path / "unknown"
    assert runtime.repo_workspace.metadata.ensure_repo_model(other).ok
    _write_publication(runtime, other, latest_release_id=None)
    unknown = runtime.repo_workspace.provider_availability.check_provider_available(other)
    assert unknown.ok and unknown.value.passed is False
    assert unknown.value.issues[0].kind == "provider_format_unknown"


def test_adapter_does_not_require_native_release(monkeypatch, tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    assert runtime.repo_workspace.metadata.set_repo_format(tmp_path, repo_format=RepoFormat.ADAPTER, reason="adapter").ok
    _write_publication(runtime, tmp_path, latest_release_id=None)
    monkeypatch.setattr(
        runtime.adapter,
        "check_adapter_ready",
        lambda repo_root: runtime.foundation.ok(
            runtime.foundation.gate_passed("adapter_ready", summary="Adapter ready fixture.")
        ),
    )

    available = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)

    assert available.ok and available.value.passed is True

    monkeypatch.setattr(
        runtime.adapter,
        "check_adapter_ready",
        lambda repo_root: runtime.foundation.ok(
            runtime.foundation.gate_failed(
                "adapter_ready",
                runtime.foundation.issue("adapter_catalog_incomplete", "Adapter catalog is incomplete."),
            )
        ),
    )
    blocked = runtime.repo_workspace.provider_availability.check_provider_available(tmp_path)
    assert blocked.ok and blocked.value.passed is False
    assert blocked.value.issues[0].kind == "provider_adapter_not_ready"
