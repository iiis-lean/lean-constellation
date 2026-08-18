from __future__ import annotations

from pathlib import Path

from lean_constellation.app.operator_data.repo_material import RepoMaterialOperatorApi
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.validation_snapshot.source_index_checkpoint import (
    SourceIndexCheckpointAdapter,
)
from lean_constellation.services.material import SourceIndex
from tests.unit_services_helpers import make_runtime


def _write_prepared_source(runtime, repo_root: Path) -> None:  # noqa: ANN001
    source = repo_root / ".lean_constellation/source"
    source.mkdir(parents=True)
    (source / "README.md").write_text(
        "# Corpus\n\n"
        "Source provenance: local fixture.\n"
        "Reading order: start here, then read chapter.md.\n"
        "Main material: chapter.md contains the theorem.\n"
        "Known gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    (source / "chapter.md").write_text("Definition A.\nTheorem B.\n", encoding="utf-8")
    prepared = runtime.material.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="Operator baseline fixture.",
        preparation_summary="Prepared fixture.",
    )
    assert prepared.ok, prepared.issues


def test_operator_baseline_validation_is_read_only(tmp_path: Path, monkeypatch) -> None:
    runtime = make_runtime()
    _write_prepared_source(runtime, tmp_path)
    scope = runtime.material.resolve_source_scope(
        tmp_path,
        source_scope=SourceScope(mode="selected", selectors=["chapter.md"]),
    )
    assert scope.ok and scope.value is not None, scope.issues
    adapter = SourceIndexCheckpointAdapter(runtime)
    baseline = adapter.persist_operator_source_index_baseline(
        tmp_path,
        resolved_file_scope=scope.value.resolved_file_paths,
        source_manifest_digest=scope.value.manifest_digest,
        expected_baseline_digest=runtime.material.source_index.missing_source_index_digest(),
    )
    assert baseline.ok and baseline.value is not None, baseline.issues
    opened = runtime.material.open_source_index_update(
        tmp_path,
        resolved_scope=scope.value,
        index_policy="auto",
        expected_baseline_digest=baseline.value.baseline_digest,
        retry_baseline_index=baseline.value.baseline_index,
    )
    assert opened.ok and opened.value is not None, opened.issues
    current = runtime.material.source_index.get_source_index_model(tmp_path)
    assert current.ok and current.value is not None, current.issues
    current_digest = runtime.material.source_index.canonical_source_index_digest(current.value)
    manifest_path = runtime.material.source_corpus._manifest_path(tmp_path)  # noqa: SLF001
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)
    real_write = runtime.foundation.store.write_json_atomic

    def reject_manifest_write(path, value, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        assert Path(path) != manifest_path, "baseline validation attempted to rewrite frozen SourceCorpus truth"
        return real_write(path, value, *args, **kwargs)

    monkeypatch.setattr(runtime.foundation.store, "write_json_atomic", reject_manifest_write)

    checked = RepoMaterialOperatorApi._load_and_check_baseline(  # noqa: SLF001
        runtime,
        tmp_path,
        current_digest,
    )

    assert checked.ok and checked.value is not None, checked.issues
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before


def test_operator_baseline_persists_complete_missing_and_existing_payloads(tmp_path: Path) -> None:
    runtime = make_runtime()
    adapter = SourceIndexCheckpointAdapter(runtime)
    missing_digest = runtime.material.source_index.missing_source_index_digest()

    persisted = adapter.persist_operator_source_index_baseline(
        tmp_path,
        resolved_file_scope=["chapter.md"],
        source_manifest_digest="manifest-digest",
        expected_baseline_digest=missing_digest,
    )
    assert persisted.ok and persisted.value is not None, persisted.issues
    assert persisted.value.baseline_index is None
    assert persisted.value.locator == (
        ".lean_constellation/work/recovery/source_index/operator_baseline.json"
    )
    assert (tmp_path / persisted.value.locator).is_file()

    restarted = SourceIndexCheckpointAdapter(runtime).load_operator_source_index_baseline(tmp_path)
    assert restarted.ok and restarted.value is not None
    assert restarted.value.model_dump(mode="json") == persisted.value.model_dump(mode="json")

    cleared = adapter.clear_operator_source_index_baseline(
        tmp_path,
        expected_locator=persisted.value.locator,
        expected_baseline_digest=missing_digest,
    )
    assert cleared.ok
    assert not (tmp_path / persisted.value.locator).exists()

    committed_index = SourceIndex(status="committed", summary="Committed baseline.")
    assert runtime.material.source_index._save_model(tmp_path, committed_index).ok  # noqa: SLF001
    committed_digest = runtime.material.source_index.canonical_source_index_digest(
        committed_index
    )
    complete = adapter.persist_operator_source_index_baseline(
        tmp_path,
        resolved_file_scope=["next.md"],
        source_manifest_digest="next-manifest",
        expected_baseline_digest=committed_digest,
    )
    assert complete.ok and complete.value is not None
    assert complete.value.baseline_index == committed_index


def test_operator_baseline_rejects_stale_digest_without_writing(tmp_path: Path) -> None:
    runtime = make_runtime()
    adapter = SourceIndexCheckpointAdapter(runtime)

    result = adapter.persist_operator_source_index_baseline(
        tmp_path,
        resolved_file_scope=["chapter.md"],
        source_manifest_digest="manifest-digest",
        expected_baseline_digest="stale",
    )

    assert not result.ok
    assert result.issues[0].kind == "source_index_baseline_digest_mismatch"
    assert not (
        tmp_path
        / ".lean_constellation/work/recovery/source_index/operator_baseline.json"
    ).exists()
