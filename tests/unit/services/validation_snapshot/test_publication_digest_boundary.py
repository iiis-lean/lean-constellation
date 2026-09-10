from pathlib import Path

from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


def test_export_receipt_is_outside_release_semantic_digest(tmp_path: Path) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    finalizer = runtime.validation_snapshot.release_finalizer
    before = finalizer.compute_semantic_manifest_digest(tmp_path)

    (tmp_path / "lc-export.json").write_text(
        '{"schema_version":1,"release_id":"release_test"}\n',
        encoding="utf-8",
    )

    assert finalizer.compute_semantic_manifest_digest(tmp_path) == before
