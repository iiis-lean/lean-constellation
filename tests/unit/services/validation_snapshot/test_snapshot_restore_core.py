from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointKind,
    RepoCheckpointSnapshotManifest,
    SnapshotFilesManifest,
    SnapshotRestoreComponent,
)
from tests.unit_services_helpers import make_runtime


_SOURCE_DRAFT = ".lean_constellation/work/drafts/source_corpus/article.tex"
_RESOURCE_DRAFT = ".lean_constellation/work/drafts/resources/request_1/README.md"
_SOURCE_INDEX_RECOVERY = (
    ".lean_constellation/work/recovery/source_index/operator_baseline.json"
)
_REBUILDABLE_CACHE = ".lean_constellation/work/cache/mathlib_candidates.json"
_REBUILDABLE_PREVIEW = ".lean_constellation/work/previews/source_corpus/page.svg"
_TRANSACTION_STAGING = (
    ".lean_constellation/work/staging/source_corpus/source_import_1/corpus/article.tex"
)
_LOCAL_AUDIT = ".lean_constellation/work/audit/gate_gaps.jsonl"
_LOCAL_RECEIPT = ".lean_constellation/work/receipts/remote_publication/release.json"


def _write_file(repo_root: Path, relpath: str, content: str) -> Path:
    path = repo_root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _file_bytes_under(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


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


def test_checkpoint_excludes_and_does_not_restore_repo_runtime_artifacts(tmp_path: Path) -> None:
    runtime = make_runtime()
    assert runtime.repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    component = SnapshotRestoreComponent(runtime)
    runtime_artifact = tmp_path / ".runtime" / "toolkit" / "calls" / "call.json"
    runtime_artifact.parent.mkdir(parents=True)
    runtime_artifact.write_text('{"state":"before"}\n', encoding="utf-8")
    (tmp_path / "Main.lean").write_text("theorem checkpoint_truth : True := by trivial\n", encoding="utf-8")

    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        snapshot_id="runtime_excluded_checkpoint",
        ark_runtime_snapshot_id=None,
    )

    assert created.ok and created.value is not None
    manifest = runtime.foundation.store.read_json(
        Path(created.value.root) / "files_manifest.json",
        SnapshotFilesManifest,
    )
    assert manifest.ok and manifest.value is not None
    assert ".runtime" in manifest.value.excluded_top_level
    assert not any(entry.source_relpath.startswith(".runtime/") for entry in manifest.value.entries)

    runtime_artifact.write_text('{"state":"after"}\n', encoding="utf-8")
    restored = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )

    assert restored.ok and restored.value is not None
    assert runtime_artifact.read_text(encoding="utf-8") == '{"state":"after"}\n'
    assert not any(path.startswith(".runtime/") for path in restored.value.pruned_files)


@pytest.mark.parametrize("checkpoint_kind", list(RepoCheckpointKind))
def test_checkpoint_work_profile_matches_checkpoint_kind(
    tmp_path: Path,
    monkeypatch,
    checkpoint_kind: RepoCheckpointKind,
) -> None:  # noqa: ANN001
    runtime = make_runtime()
    component = SnapshotRestoreComponent(runtime)
    monkeypatch.setattr(
        component,
        "check_checkpoint_business_gate",
        lambda *_args, **_kwargs: runtime.foundation.ok(
            runtime.foundation.gate_passed("snapshot_profile_test")
        ),
    )
    _write_file(tmp_path, "Main.lean", "theorem stable : True := by trivial\n")
    _write_file(
        tmp_path,
        ".lean_constellation/source/article.tex",
        "stable source\n",
    )
    for relpath in (
        _SOURCE_DRAFT,
        _RESOURCE_DRAFT,
        _SOURCE_INDEX_RECOVERY,
        _REBUILDABLE_CACHE,
        _REBUILDABLE_PREVIEW,
        _TRANSACTION_STAGING,
        _LOCAL_AUDIT,
        _LOCAL_RECEIPT,
    ):
        _write_file(tmp_path, relpath, f"fixture:{relpath}\n")

    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=checkpoint_kind,
        snapshot_id=f"profile_{checkpoint_kind.value}",
    )

    assert created.ok and created.value is not None, created.issues
    loaded = runtime.foundation.store.read_json(
        Path(created.value.root) / "files_manifest.json",
        SnapshotFilesManifest,
    )
    assert loaded.ok and loaded.value is not None
    captured = {entry.source_relpath for entry in loaded.value.entries}
    assert {"Main.lean", ".lean_constellation/source/article.tex"} <= captured
    assert (_SOURCE_DRAFT in captured) is (
        checkpoint_kind
        in {
            RepoCheckpointKind.BEFORE_NATIVE_SOURCE_PROCESSING,
            RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        }
    )
    assert (_RESOURCE_DRAFT in captured) is (
        checkpoint_kind
        in {
            RepoCheckpointKind.BEFORE_RESOURCE_REQUEST_DISPATCH,
            RepoCheckpointKind.AFTER_RESOURCE_REQUEST_TERMINAL,
            RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        }
    )
    assert (_SOURCE_INDEX_RECOVERY in captured) is (
        checkpoint_kind
        in {
            RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
            RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        }
    )
    assert not {
        _REBUILDABLE_CACHE,
        _REBUILDABLE_PREVIEW,
        _TRANSACTION_STAGING,
        _LOCAL_AUDIT,
        _LOCAL_RECEIPT,
    } & captured


def test_restore_only_manages_captured_recoverable_work(tmp_path: Path) -> None:
    runtime = make_runtime()
    component = SnapshotRestoreComponent(runtime)
    source = _write_file(tmp_path, _SOURCE_DRAFT, "source before\n")
    resource = _write_file(tmp_path, _RESOURCE_DRAFT, "resource before\n")
    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_SOURCE_PROCESSING,
        snapshot_id="source_profile_checkpoint",
    )
    assert created.ok and created.value is not None, created.issues
    source.write_text("source after\n", encoding="utf-8")
    resource.write_text("resource after\n", encoding="utf-8")
    source_extra = _write_file(
        tmp_path,
        ".lean_constellation/work/drafts/source_corpus/extra.tex",
        "source extra\n",
    )
    audit = _write_file(tmp_path, _LOCAL_AUDIT, "preserve audit\n")

    restored = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
        prune_extra_files=True,
    )

    assert restored.ok and restored.value is not None, restored.issues
    assert source.read_text(encoding="utf-8") == "source before\n"
    assert not source_extra.exists()
    assert source_extra.relative_to(tmp_path).as_posix() in restored.value.pruned_files
    assert resource.read_text(encoding="utf-8") == "resource after\n"
    assert audit.read_text(encoding="utf-8") == "preserve audit\n"

    files_manifest_path = Path(created.value.root) / "files_manifest.json"
    files_manifest = runtime.foundation.store.read_json(
        files_manifest_path,
        SnapshotFilesManifest,
    )
    assert files_manifest.ok and files_manifest.value is not None
    source_entry = next(
        entry
        for entry in files_manifest.value.entries
        if entry.source_relpath == _SOURCE_DRAFT
    )
    source_entry.source_relpath = _RESOURCE_DRAFT
    assert runtime.foundation.store.write_json_atomic(
        files_manifest_path,
        files_manifest.value,
    ).ok
    invalid = component.validate_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )
    rejected = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )
    assert not invalid.ok
    assert invalid.issues[0].kind == "repo_checkpoint_snapshot_profile_mismatch"
    assert not rejected.ok
    assert rejected.issues[0].kind == "repo_checkpoint_snapshot_profile_mismatch"


def test_restore_invalidates_rebuildable_work_without_pruning_unmanaged_drafts(
    tmp_path: Path,
) -> None:
    runtime = make_runtime()
    component = SnapshotRestoreComponent(runtime)
    _write_file(tmp_path, "Main.lean", "theorem stable : True := by trivial\n")
    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REPO_RELEASE,
        snapshot_id="release_profile_checkpoint",
    )
    assert created.ok and created.value is not None, created.issues
    source = _write_file(tmp_path, _SOURCE_DRAFT, "preserve source draft\n")
    cache = _write_file(tmp_path, _REBUILDABLE_CACHE, "invalidate cache\n")
    preview = _write_file(tmp_path, _REBUILDABLE_PREVIEW, "invalidate preview\n")
    staging = _write_file(tmp_path, _TRANSACTION_STAGING, "preserve transaction\n")
    audit = _write_file(tmp_path, _LOCAL_AUDIT, "preserve audit\n")
    lake_build = _write_file(tmp_path, ".lake/build/stale.olean", "stale\n").parent
    expected_invalidation = [
        ".lake/build",
        ".lean_constellation/work/cache",
        ".lean_constellation/work/previews",
    ]

    dry_run = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
        dry_run=True,
        prune_extra_files=True,
    )
    assert dry_run.ok and dry_run.value is not None, dry_run.issues
    assert dry_run.value.would_prune_files == []
    assert dry_run.value.would_invalidate_paths == expected_invalidation

    restored = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
        prune_extra_files=True,
    )

    assert restored.ok and restored.value is not None, restored.issues
    assert restored.value.pruned_files == []
    assert restored.value.invalidated_paths == expected_invalidation
    assert not lake_build.exists()
    assert not cache.exists()
    assert not preview.exists()
    assert source.read_text(encoding="utf-8") == "preserve source draft\n"
    assert staging.read_text(encoding="utf-8") == "preserve transaction\n"
    assert audit.read_text(encoding="utf-8") == "preserve audit\n"


@pytest.mark.parametrize("manifest_name", ["snapshot.json", "files_manifest.json"])
@pytest.mark.parametrize("schema_version", [None, 0], ids=["missing", "mismatch"])
def test_checkpoint_manifests_write_current_versions_and_reject_noncurrent_schema(
    tmp_path: Path,
    manifest_name: str,
    schema_version: int | None,
) -> None:
    runtime = make_runtime()
    component = SnapshotRestoreComponent(runtime)
    (tmp_path / "Main.lean").write_text("theorem original : True := by trivial\n", encoding="utf-8")
    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        snapshot_id="versioned_checkpoint",
        ark_runtime_snapshot_id=None,
    )
    assert created.ok and created.value is not None
    snapshot_root = Path(created.value.root)
    manifest_path = snapshot_root / "snapshot.json"
    files_path = snapshot_root / "files_manifest.json"
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    files_payload = json.loads(files_path.read_text(encoding="utf-8"))
    assert manifest_payload["schema_version"] == 1
    assert files_payload["schema_version"] == 1

    target_path = snapshot_root / manifest_name
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    if schema_version is None:
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_version
    target_path.write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "Main.lean").write_text("-- modified after snapshot\n", encoding="utf-8")
    before = _file_bytes_under(tmp_path)

    validated = component.validate_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )
    restored = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )

    assert not validated.ok
    assert validated.issues[0].kind == "repo_checkpoint_snapshot_schema_version_invalid"
    assert not restored.ok
    assert restored.issues[0].kind == "repo_checkpoint_snapshot_schema_version_invalid"
    assert _file_bytes_under(tmp_path) == before


@pytest.mark.parametrize("manifest_name", ["snapshot.json", "files_manifest.json"])
@pytest.mark.parametrize("schema_version", [None, 0], ids=["missing", "mismatch"])
def test_snapshot_list_rejects_noncurrent_manifest_schema(
    tmp_path: Path,
    manifest_name: str,
    schema_version: int | None,
) -> None:
    runtime = make_runtime()
    component = SnapshotRestoreComponent(runtime)
    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
        snapshot_id="noncurrent_list_schema",
    )
    assert created.ok and created.value is not None, created.issues
    target_path = Path(created.value.root) / manifest_name
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    if schema_version is None:
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_version
    target_path.write_text(json.dumps(payload), encoding="utf-8")
    before = target_path.read_bytes()

    listed = component.list_repo_checkpoint_snapshots(tmp_path)

    assert not listed.ok
    assert listed.value is None
    assert listed.issues[0].kind == "repo_checkpoint_snapshot_schema_version_invalid"
    assert target_path.read_bytes() == before


def test_restore_invalidates_direct_rebuildable_symlinks_without_touching_targets(
    tmp_path: Path,
) -> None:
    runtime = make_runtime()
    component = SnapshotRestoreComponent(runtime)
    (tmp_path / "Main.lean").write_text("theorem original : True := by trivial\n", encoding="utf-8")
    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REPO_RELEASE,
        snapshot_id="direct_rebuildable_symlinks",
    )
    assert created.ok and created.value is not None, created.issues

    external_cache = tmp_path.parent / f"{tmp_path.name}_external_cache"
    external_previews = tmp_path.parent / f"{tmp_path.name}_external_previews"
    _write_file(external_cache, "cache.bin", "external cache\n")
    _write_file(external_previews, "preview.svg", "external preview\n")
    work_root = tmp_path / ".lean_constellation" / "work"
    work_root.mkdir(parents=True)
    cache_link = work_root / "cache"
    previews_link = work_root / "previews"
    cache_link.symlink_to(external_cache, target_is_directory=True)
    previews_link.symlink_to(external_previews, target_is_directory=True)

    dry_run = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
        dry_run=True,
    )
    restored = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )

    expected = [
        ".lean_constellation/work/cache",
        ".lean_constellation/work/previews",
    ]
    assert dry_run.ok and dry_run.value is not None, dry_run.issues
    assert dry_run.value.would_invalidate_paths == expected
    assert restored.ok and restored.value is not None, restored.issues
    assert restored.value.invalidated_paths == expected
    assert not cache_link.exists() and not cache_link.is_symlink()
    assert not previews_link.exists() and not previews_link.is_symlink()
    assert (external_cache / "cache.bin").read_text(encoding="utf-8") == "external cache\n"
    assert (external_previews / "preview.svg").read_text(encoding="utf-8") == "external preview\n"


def test_restore_rejects_symlinked_work_root_before_mutation(tmp_path: Path) -> None:
    runtime = make_runtime()
    component = SnapshotRestoreComponent(runtime)
    main = tmp_path / "Main.lean"
    main.write_text("theorem original : True := by trivial\n", encoding="utf-8")
    created = component.create_repo_checkpoint_archive(
        tmp_path,
        checkpoint_kind=RepoCheckpointKind.REPO_RELEASE,
        snapshot_id="symlinked_work_root",
    )
    assert created.ok and created.value is not None, created.issues

    main.write_text("-- modified after snapshot\n", encoding="utf-8")
    external_work = tmp_path.parent / f"{tmp_path.name}_external_work"
    _write_file(external_work, "cache/outside.bin", "outside work\n")
    work_root = tmp_path / ".lean_constellation" / "work"
    work_root.symlink_to(external_work, target_is_directory=True)
    before = _file_bytes_under(tmp_path)

    validated = component.validate_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )
    dry_run = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
        dry_run=True,
    )
    restored = component.restore_repo_checkpoint_snapshot(
        tmp_path,
        snapshot_id=created.value.snapshot_id,
    )

    for result in (validated, dry_run, restored):
        assert not result.ok
        assert result.issues[0].kind == "repo_checkpoint_invalidation_path_unsafe"
    assert _file_bytes_under(tmp_path) == before
    assert work_root.is_symlink()
    assert (external_work / "cache/outside.bin").read_text(encoding="utf-8") == "outside work\n"
