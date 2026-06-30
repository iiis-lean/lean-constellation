from __future__ import annotations

from lean_constellation.app import (
    LeanAdminApi,
    SnapshotCreateInput,
    SnapshotRestoreInput,
    create_app_runtime_services,
    initialize_repo_runtime,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode


def test_admin_snapshot_create_and_restore_leaves_runtime_paused(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Repo"
    assert initialize_repo_runtime(runtime, repo_root).ok
    prep = RepoPreparationInput(
        goal="Prepare provider.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
    )
    assert runtime.repo_workspace.preparation.write_preparation_input(repo_root, input=prep).ok
    marker = repo_root / "Marker.txt"
    marker.write_text("before\n", encoding="utf-8")
    admin = LeanAdminApi(runtime)

    created = admin.create_snapshot(
        SnapshotCreateInput(repo_root=repo_root, checkpoint_kind="requirement_bootstrap_terminal", label="unit")
    )
    assert created.ok and created.value is not None
    marker.write_text("after\n", encoding="utf-8")

    restored = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=created.value.snapshot_id, leave_runtime_paused=True)
    )

    assert restored.ok and restored.value is not None
    assert marker.read_text(encoding="utf-8") == "before\n"
    assert restored.value.leave_runtime_paused is True
    assert runtime.ark.pause_controller.is_paused() is True
