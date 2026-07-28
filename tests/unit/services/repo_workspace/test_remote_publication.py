from __future__ import annotations

import subprocess
from pathlib import Path

from lean_constellation.domain.publication import RepoPublicationOverride
from lean_constellation.domain.repo import RepoPublicationStatus
from tests.unit_services_helpers import make_runtime, publish_native_provider_release


def test_remote_publication_configures_and_verifies_exact_release(
    tmp_path: Path,
) -> None:
    runtime = make_runtime()
    repo = tmp_path / "Provider"
    remote = tmp_path / "Provider.git"
    release = publish_native_provider_release(
        runtime, repo, release_id="provider_r1"
    )
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    publication = runtime.repo_workspace.metadata.get_repo_publication(repo)
    assert publication.ok and publication.value is not None
    publication.value.publication.status = RepoPublicationStatus.DEVELOPING
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo),
        publication.value.publication,
    ).ok
    configured = runtime.repo_workspace.metadata.update_repo_config(
        repo,
        publication=RepoPublicationOverride(
            canonical_fetch_url=remote.as_uri(),
            canonical_push_url=remote.as_uri(),
        ),
    )
    assert configured.ok, configured.issues
    publication.value.publication.status = RepoPublicationStatus.STABLE
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo),
        publication.value.publication,
    ).ok

    preview = runtime.repo_workspace.remote_publication.preview(
        repo, release_id=release.release_id
    )
    assert preview.ok and preview.value is not None, preview.issues
    applied = runtime.repo_workspace.remote_publication.apply(
        repo,
        preview=preview.value,
        expected_recovery_token=preview.value.recovery_token,
        push=True,
    )

    assert applied.ok and applied.value is not None, applied.issues
    assert applied.value.status == "remote_verified"
    expected = runtime.repo_workspace.git_release.resolve_release_commit(
        repo, release_id=release.release_id
    ).value
    actual = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remote),
            "rev-parse",
            "refs/lean-constellation/releases/provider_r1",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual == expected


def test_remote_publication_preview_is_cas_bound(tmp_path: Path) -> None:
    runtime = make_runtime()
    repo = tmp_path / "Provider"
    remote = tmp_path / "Provider.git"
    release = publish_native_provider_release(
        runtime, repo, release_id="provider_r1"
    )
    publication = runtime.repo_workspace.metadata.get_repo_publication(repo)
    assert publication.ok and publication.value is not None
    publication.value.publication.status = RepoPublicationStatus.DEVELOPING
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo),
        publication.value.publication,
    ).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        repo,
        publication=RepoPublicationOverride(
            canonical_fetch_url=remote.as_uri()
        ),
    ).ok
    publication.value.publication.status = RepoPublicationStatus.STABLE
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo),
        publication.value.publication,
    ).ok
    preview = runtime.repo_workspace.remote_publication.preview(
        repo, release_id=release.release_id
    )
    assert preview.ok and preview.value is not None
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", "other"],
        check=True,
    )

    rejected = runtime.repo_workspace.remote_publication.apply(
        repo,
        preview=preview.value,
        expected_recovery_token=preview.value.recovery_token,
        push=False,
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "remote_publication_token_mismatch"
