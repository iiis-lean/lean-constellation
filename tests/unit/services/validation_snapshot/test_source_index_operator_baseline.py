from __future__ import annotations

from pathlib import Path

from lean_constellation.services.validation_snapshot.source_index_checkpoint import (
    SourceIndexCheckpointAdapter,
)
from lean_constellation.services.material import SourceIndex
from tests.unit_services_helpers import make_runtime


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
        tmp_path / ".lean_constellation/source_index/operator_baseline.json"
    ).exists()
