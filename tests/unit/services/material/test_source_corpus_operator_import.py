from __future__ import annotations

from inspect import signature
from pathlib import Path

from lean_constellation.services.material import source_corpus as source_corpus_module

from tests.unit_services_helpers import make_runtime


def _write_corpus(root: Path, *, theorem: str) -> None:
    root.mkdir(parents=True)
    (root / "README.md").write_text(
        "# Corpus\n\n"
        "Source provenance: local fixture.\n"
        "Reading order: start here, then read chapter.md.\n"
        "Main material: chapter.md contains the theorem.\n"
        "Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    (root / "chapter.md").write_text(f"{theorem}\n", encoding="utf-8")


def test_local_dir_import_promotes_only_after_gate_and_requires_expected_digest_for_replace(
    tmp_path: Path,
) -> None:
    runtime = make_runtime()
    repo_root = tmp_path / "repo"
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_corpus(first, theorem="Theorem one.")
    _write_corpus(second, theorem="Theorem two.")

    imported = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=first,
        entry_path="README.md",
        overview="First corpus.",
        preparation_summary="Prepared first corpus.",
    )
    assert imported.ok and imported.value is not None, imported.issues
    assert "created_from_mode" not in signature(runtime.material.import_local_source_corpus).parameters
    assert imported.value.prepared.manifest.created_from_mode == "operator_local_dir"
    assert (repo_root / ".lean_constellation/source/chapter.md").read_text() == "Theorem one.\n"
    persisted = runtime.material.source_corpus.get_source_corpus_manifest(repo_root)
    assert persisted.ok and persisted.value is not None
    assert persisted.value.created_from_mode == "operator_local_dir"

    missing_expected = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=second,
        entry_path="README.md",
        overview="Second corpus.",
        preparation_summary="Prepared second corpus.",
        replace_existing=True,
    )
    assert not missing_expected.ok
    assert missing_expected.issues[0].kind == "source_corpus_expected_manifest_digest_required"

    stale = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=second,
        entry_path="README.md",
        overview="Second corpus.",
        preparation_summary="Prepared second corpus.",
        replace_existing=True,
        expected_manifest_digest="stale",
    )
    assert not stale.ok
    assert stale.issues[0].kind == "source_corpus_manifest_digest_mismatch"

    replaced = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=second,
        entry_path="README.md",
        overview="Second corpus.",
        preparation_summary="Prepared second corpus.",
        replace_existing=True,
        expected_manifest_digest=imported.value.manifest_digest,
    )
    assert replaced.ok and replaced.value is not None, replaced.issues
    assert replaced.value.replaced_existing
    assert replaced.value.prepared.manifest.created_from_mode == "operator_local_dir"
    assert (repo_root / ".lean_constellation/source/chapter.md").read_text() == "Theorem two.\n"
    persisted = runtime.material.source_corpus.get_source_corpus_manifest(repo_root)
    assert persisted.ok and persisted.value is not None
    assert persisted.value.created_from_mode == "operator_local_dir"


def test_local_dir_import_gate_or_manifest_failure_preserves_canonical_truth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    repo_root = tmp_path / "repo"
    good = tmp_path / "good"
    invalid = tmp_path / "invalid"
    replacement = tmp_path / "replacement"
    _write_corpus(good, theorem="Stable theorem.")
    invalid.mkdir()
    (invalid / "README.md").write_bytes(b"\xff\n")
    _write_corpus(replacement, theorem="Replacement theorem.")
    imported = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=good,
        entry_path="README.md",
        overview="Stable corpus.",
        preparation_summary="Prepared stable corpus.",
    )
    assert imported.ok and imported.value is not None
    canonical = repo_root / ".lean_constellation/source/chapter.md"
    manifest = repo_root / ".lean_constellation/source_corpus/manifest.json"
    before_manifest = manifest.read_bytes()

    gate_failed = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=invalid,
        entry_path="README.md",
        overview="Invalid corpus.",
        preparation_summary="Should fail.",
        replace_existing=True,
        expected_manifest_digest=imported.value.manifest_digest,
    )
    assert not gate_failed.ok
    assert canonical.read_text() == "Stable theorem.\n"
    assert manifest.read_bytes() == before_manifest

    real_write = runtime.foundation.store.write_json_atomic

    def fail_manifest(path, value, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if Path(path) == manifest:
            return runtime.foundation.fail(
                runtime.foundation.issue("injected_manifest_failure", "injected")
            )
        return real_write(path, value, *args, **kwargs)

    monkeypatch.setattr(runtime.foundation.store, "write_json_atomic", fail_manifest)
    write_failed = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=replacement,
        entry_path="README.md",
        overview="Replacement corpus.",
        preparation_summary="Should roll back.",
        replace_existing=True,
        expected_manifest_digest=imported.value.manifest_digest,
    )
    assert not write_failed.ok
    assert canonical.read_text() == "Stable theorem.\n"
    assert manifest.read_bytes() == before_manifest
    assert not (repo_root / ".lean_constellation/.source_corpus_staging").exists()


def test_local_dir_import_preserves_backup_when_backup_restore_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    repo_root = tmp_path / "repo"
    first = tmp_path / "first"
    replacement = tmp_path / "replacement"
    _write_corpus(first, theorem="Stable theorem.")
    _write_corpus(replacement, theorem="Replacement theorem.")
    imported = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=first,
        entry_path="README.md",
        overview="Stable corpus.",
        preparation_summary="Prepared stable corpus.",
    )
    assert imported.ok and imported.value is not None
    manifest = repo_root / ".lean_constellation/source_corpus/manifest.json"
    real_write = runtime.foundation.store.write_json_atomic

    def fail_manifest(path, value, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if Path(path) == manifest:
            return runtime.foundation.fail(
                runtime.foundation.issue("injected_manifest_failure", "injected")
            )
        return real_write(path, value, *args, **kwargs)

    real_replace = source_corpus_module.os.replace

    def fail_backup_restore(src, dst):  # noqa: ANN001, ANN202
        if Path(src).name == "previous" and Path(dst).name == "source":
            raise OSError("injected backup restore failure")
        return real_replace(src, dst)

    monkeypatch.setattr(runtime.foundation.store, "write_json_atomic", fail_manifest)
    monkeypatch.setattr(source_corpus_module.os, "replace", fail_backup_restore)
    failed = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=replacement,
        entry_path="README.md",
        overview="Replacement corpus.",
        preparation_summary="Should preserve recovery artifacts.",
        replace_existing=True,
        expected_manifest_digest=imported.value.manifest_digest,
    )

    assert not failed.ok
    assert any(
        issue.kind == "source_corpus_import_rollback_backup_restore_failed"
        for issue in failed.issues
    )
    transactions = list(
        (repo_root / ".lean_constellation/.source_corpus_staging").glob("source_import_*")
    )
    assert len(transactions) == 1
    assert (transactions[0] / "previous/chapter.md").read_text() == "Stable theorem.\n"


def test_local_dir_import_preserves_manifest_recovery_when_manifest_restore_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = make_runtime()
    repo_root = tmp_path / "repo"
    first = tmp_path / "first"
    replacement = tmp_path / "replacement"
    _write_corpus(first, theorem="Stable theorem.")
    _write_corpus(replacement, theorem="Replacement theorem.")
    imported = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=first,
        entry_path="README.md",
        overview="Stable corpus.",
        preparation_summary="Prepared stable corpus.",
    )
    assert imported.ok and imported.value is not None
    canonical = repo_root / ".lean_constellation/source/chapter.md"
    manifest = repo_root / ".lean_constellation/source_corpus/manifest.json"
    before_manifest = manifest.read_bytes()
    real_write = runtime.foundation.store.write_json_atomic

    def corrupt_then_fail(path, value, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if Path(path) == manifest:
            manifest.write_text("corrupt\n", encoding="utf-8")
            return runtime.foundation.fail(
                runtime.foundation.issue("injected_manifest_failure", "injected")
            )
        return real_write(path, value, *args, **kwargs)

    real_replace = source_corpus_module.os.replace

    def fail_manifest_restore(src, dst):  # noqa: ANN001, ANN202
        if Path(src).name == "previous_manifest.json" and Path(dst) == manifest:
            raise OSError("injected manifest restore failure")
        return real_replace(src, dst)

    monkeypatch.setattr(runtime.foundation.store, "write_json_atomic", corrupt_then_fail)
    monkeypatch.setattr(source_corpus_module.os, "replace", fail_manifest_restore)
    failed = runtime.material.import_local_source_corpus(
        repo_root,
        source_dir=replacement,
        entry_path="README.md",
        overview="Replacement corpus.",
        preparation_summary="Should preserve manifest recovery.",
        replace_existing=True,
        expected_manifest_digest=imported.value.manifest_digest,
    )

    assert not failed.ok
    assert any(
        issue.kind == "source_corpus_import_rollback_manifest_restore_failed"
        for issue in failed.issues
    )
    assert canonical.read_text() == "Stable theorem.\n"
    transactions = list(
        (repo_root / ".lean_constellation/.source_corpus_staging").glob("source_import_*")
    )
    assert len(transactions) == 1
    assert (transactions[0] / "previous_manifest.json").read_bytes() == before_manifest
