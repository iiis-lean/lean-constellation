from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.real.lean_test_config import write_test_lean_toolchain
from lean_constellation.domain.repo import RepoCompletionMode, RepoPublicationStatus
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.validation_snapshot import PreparedRepoReleaseView
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


def _prepared(runtime, repo_root: Path, release: RepoRelease) -> PreparedRepoReleaseView:  # noqa: ANN001
    prepared_publication = runtime.repo_workspace.publication.prepare_publication(
        repo_root,
        release_id=release.release_id,
        semantic_manifest_digest=release.semantic_manifest_digest,
        generated_at=release.created_at,
    )
    assert prepared_publication.ok, prepared_publication.issues
    publication = runtime.repo_workspace.metadata.get_repo_publication(repo_root).value.publication.model_copy(
        update={"status": RepoPublicationStatus.STABLE, "latest_release_id": release.release_id}
    )
    git_state = runtime.repo_workspace.git_release.inspect_repo(repo_root)
    assert git_state.ok and git_state.value is not None
    return PreparedRepoReleaseView(
        release=release,
        publication=publication,
        candidate_digest=runtime.validation_snapshot.release_finalizer.compute_candidate_digest(repo_root),
        expected_git_head=git_state.value.head_commit,
        build=ToolchainCommandView(ok=True, command=["lake", "build"], summary="built", exit_code=0),
        gate=runtime.foundation.gate_passed("candidate_repo_release", summary="passed"),
        summary=f"prepared {release.release_id}",
    )


@pytest.mark.real
def test_declared_r1_to_proved_r2_release_restore_rebuilds_with_real_lake(tmp_path: Path) -> None:
    if shutil.which("lake") is None or shutil.which("lean") is None:
        pytest.skip("real release restore requires lake and lean")
    repo_root = tmp_path / "ReleaseRestoreReal"
    runtime, versions = _prepare_release_repo(repo_root)
    runtime.ark.flow_service = SimpleNamespace(list_flows=lambda **_filters: [])
    runtime.ark.step_service = SimpleNamespace(list_steps=lambda **_filters: [])
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    write_test_lean_toolchain(repo_root)
    (repo_root / "lakefile.toml").write_text(
        'name = "ReleaseRestoreReal"\nversion = "0.1.0"\ndefaultTargets = ["Main"]\n\n'
        '[[lean_lib]]\nname = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text("theorem releasedValue : 1 + 1 = 2 := by decide\n", encoding="utf-8")
    assert runtime.external.lean_toolchain.run_lake_build(repo_root).ok

    r1 = RepoRelease(
        release_id="release_r1",
        node_contract_versions=versions,
        completion_mode=RepoCompletionMode.GRAPH_DECLARED,
        semantic_manifest_digest=(
            runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
                repo_root
            )
        ),
        dependency_lock_digest=(
            runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
                repo_root
            )
        ),
        summary="Declared R1.",
    )
    committed_r1 = runtime.validation_snapshot.commit_prepared_release(
        repo_root, prepared=_prepared(runtime, repo_root, r1)
    )
    assert committed_r1.ok, committed_r1.issues

    assert runtime.repo_workspace.metadata.mark_repo_developing(repo_root).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        repo_root,
        completion_mode=RepoCompletionMode.GRAPH_PROVED,
    ).ok
    (repo_root / "Main.lean").write_text(
        "theorem releasedValue : 1 + 1 = 2 := by decide\n"
        "theorem provedExtension : True := by trivial\n",
        encoding="utf-8",
    )
    assert runtime.external.lean_toolchain.run_lake_build(repo_root).ok
    r2 = RepoRelease(
        release_id="release_r2",
        parent_release_id="release_r1",
        node_contract_versions=versions,
        completion_mode=RepoCompletionMode.GRAPH_PROVED,
        semantic_manifest_digest=(
            runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
                repo_root
            )
        ),
        dependency_lock_digest=(
            runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
                repo_root
            )
        ),
        summary="Proved R2.",
    )
    committed_r2 = runtime.validation_snapshot.commit_prepared_release(
        repo_root, prepared=_prepared(runtime, repo_root, r2)
    )
    assert committed_r2.ok, committed_r2.issues
    audit = runtime.validation_snapshot.audit_repo_release_storage(repo_root)
    assert audit.ok and audit.value is not None and audit.value.passed
    historical_cleanup = runtime.validation_snapshot.cleanup_unpublished_release_artifacts(
        repo_root, release_id="release_r1"
    )
    assert not historical_cleanup.ok
    assert historical_cleanup.issues[0].kind == "release_artifact_reachable"

    restore_preview = runtime.validation_snapshot.preview_repo_release_restore(
        repo_root, release_id="release_r1"
    )
    assert restore_preview.ok and restore_preview.value is not None
    restored = runtime.validation_snapshot.apply_repo_release_restore(
        repo_root,
        preview=restore_preview.value,
        expected_recovery_token=restore_preview.value.recovery_token,
    )
    assert restored.ok, restored.issues
    assert "provedExtension" not in (repo_root / "Main.lean").read_text(encoding="utf-8")
    assert runtime.external.lean_toolchain.run_lake_build(repo_root).ok
    historical = runtime.validation_snapshot.preview_repo_release_restore(
        repo_root,
        release_id="release_r2",
    )
    assert historical.ok and historical.value is not None
    assert historical.value.commit == committed_r2.value.git_release.commit
    assert historical.value.expected_head == committed_r1.value.git_release.commit
