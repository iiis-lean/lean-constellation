from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from lean_constellation.domain.repo import ProofAvailability, RepoPublicationStatus, RepoWorkMode
from lean_constellation.domain.repo_release import RepoRelease
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.validation_snapshot import PreparedRepoReleaseView
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


def _prepared(runtime, repo_root: Path, release: RepoRelease) -> PreparedRepoReleaseView:  # noqa: ANN001
    publication = runtime.repo_workspace.metadata.get_repo_publication(repo_root).value.publication.model_copy(
        update={"status": RepoPublicationStatus.STABLE, "latest_release_id": release.release_id}
    )
    return PreparedRepoReleaseView(
        release=release,
        publication=publication,
        candidate_digest=runtime.validation_snapshot.release_finalizer.compute_candidate_digest(repo_root),
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
        target_proof_availability=ProofAvailability.DECLARED,
        repo_checkpoint_id="checkpoint_r1",
        summary="Declared R1.",
    )
    committed_r1 = runtime.validation_snapshot.commit_prepared_release(
        repo_root, prepared=_prepared(runtime, repo_root, r1)
    )
    assert committed_r1.ok, committed_r1.issues

    assert runtime.repo_workspace.metadata.mark_repo_developing(repo_root).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        repo_root,
        target_proof_availability=ProofAvailability.PROVED,
        work_mode=RepoWorkMode.PROVED_FULL_GRAPH,
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
        target_proof_availability=ProofAvailability.PROVED,
        repo_checkpoint_id="checkpoint_r2",
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

    (repo_root / "Main.lean").write_text("this is not Lean\n", encoding="utf-8")
    restored = runtime.validation_snapshot.restore_repo_release(repo_root, release_id="release_r2")
    assert restored.ok, restored.issues
    assert "provedExtension" in (repo_root / "Main.lean").read_text(encoding="utf-8")
    assert runtime.external.lean_toolchain.run_lake_build(repo_root).ok
    historical = runtime.validation_snapshot.restore_repo_release(repo_root, release_id="release_r1", dry_run=True)
    assert not historical.ok
    assert historical.issues[0].kind == "historical_release_restore_not_supported"
    generic_historical = runtime.validation_snapshot.restore_repo_checkpoint_snapshot(
        repo_root, snapshot_id="checkpoint_r1", dry_run=True
    )
    assert not generic_historical.ok
    assert generic_historical.issues[0].kind == "historical_release_restore_not_supported"
