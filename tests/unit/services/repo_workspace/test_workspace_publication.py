from __future__ import annotations

import subprocess
from pathlib import Path

from lean_constellation.domain.publication import (
    RemoteProfile,
    RepoPublicationOverride,
    WorkspacePublicationPolicy,
)
from lean_constellation.domain.repo import RepoPublicationStatus, WorkspaceConfig
from tests.unit_services_helpers import make_runtime, publish_native_provider_release


def _configure_clone_url(runtime, repo: Path, url: str) -> None:  # noqa: ANN001
    publication = runtime.repo_workspace.metadata.get_repo_publication(repo)
    assert publication.ok and publication.value is not None
    publication.value.publication.status = RepoPublicationStatus.DEVELOPING
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo),
        publication.value.publication,
    ).ok
    assert runtime.repo_workspace.metadata.update_repo_config(
        repo,
        publication=RepoPublicationOverride(canonical_fetch_url=url),
    ).ok
    publication.value.publication.status = RepoPublicationStatus.STABLE
    assert runtime.foundation.store.write_json_atomic(
        runtime.repo_workspace.metadata._repo_publication_path(repo),
        publication.value.publication,
    ).ok


def test_single_repo_workspace_does_not_create_superproject(
    tmp_path: Path,
) -> None:
    runtime = make_runtime()
    repo = tmp_path / "OnlyRepo"
    release = publish_native_provider_release(runtime, repo)

    preview = runtime.repo_workspace.workspace_publication.preview(tmp_path)
    assert preview.ok and preview.value is not None, preview.issues
    assert not preview.value.superproject_required
    applied = runtime.repo_workspace.workspace_publication.apply(
        tmp_path,
        preview=preview.value,
        expected_recovery_token=preview.value.recovery_token,
    )

    assert applied.ok and applied.value is not None, applied.issues
    assert not applied.value.superproject_created
    assert applied.value.child_release_ids == {
        "OnlyRepo": release.release_id
    }
    assert not (tmp_path / "lean-constellation-workspace").exists()


def test_multi_repo_workspace_commits_exact_gitlinks(tmp_path: Path) -> None:
    runtime = make_runtime()
    provider = tmp_path / "Provider"
    consumer = tmp_path / "Consumer"
    provider_release = publish_native_provider_release(
        runtime, provider, release_id="provider_r1"
    )
    consumer_release = publish_native_provider_release(
        runtime, consumer, release_id="consumer_r1"
    )
    _configure_clone_url(runtime, provider, "https://example.invalid/Provider.git")
    _configure_clone_url(runtime, consumer, "https://example.invalid/Consumer.git")
    output = tmp_path / "published-workspace"

    preview = runtime.repo_workspace.workspace_publication.preview(
        tmp_path,
        repo_keys=["Provider", "Consumer"],
        output_root=output,
    )
    assert preview.ok and preview.value is not None, preview.issues
    applied = runtime.repo_workspace.workspace_publication.apply(
        tmp_path,
        preview=preview.value,
        expected_recovery_token=preview.value.recovery_token,
    )

    assert applied.ok and applied.value is not None, applied.issues
    assert applied.value.superproject_created
    tree = subprocess.run(
        ["git", "-C", str(output), "ls-tree", "-r", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    provider_commit = runtime.repo_workspace.git_release.resolve_release_commit(
        provider, release_id=provider_release.release_id
    ).value
    consumer_commit = runtime.repo_workspace.git_release.resolve_release_commit(
        consumer, release_id=consumer_release.release_id
    ).value
    assert f"160000 commit {provider_commit}\trepos/Provider" in tree
    assert f"160000 commit {consumer_commit}\trepos/Consumer" in tree
    assert "https://example.invalid/Provider.git" in (
        output / ".gitmodules"
    ).read_text(encoding="utf-8")
    repeated_preview = runtime.repo_workspace.workspace_publication.preview(
        tmp_path,
        repo_keys=["Provider", "Consumer"],
        output_root=output,
    )
    assert repeated_preview.ok and repeated_preview.value is not None
    repeated = runtime.repo_workspace.workspace_publication.apply(
        tmp_path,
        preview=repeated_preview.value,
        expected_recovery_token=repeated_preview.value.recovery_token,
    )
    assert repeated.ok and repeated.value is not None
    assert repeated.value.changed is False
    assert repeated.value.superproject_commit == applied.value.superproject_commit


def test_workspace_publication_can_push_children_and_superproject(
    tmp_path: Path,
) -> None:
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    for name in ("Provider", "Consumer", "Bundle"):
        subprocess.run(
            ["git", "init", "--bare", str(remotes / f"{name}.git")],
            check=True,
            capture_output=True,
            text=True,
        )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = make_runtime(
        workspace_config=WorkspaceConfig(
            publication=WorkspacePublicationPolicy(
                superproject_remote_profile="local",
                superproject_remote_name="Bundle",
                remote_profiles={
                    "local": RemoteProfile(
                        fetch_url_template=(
                            remotes.as_uri() + "/{repo_name}.git"
                        )
                    )
                },
            )
        )
    )
    provider = workspace / "Provider"
    consumer = workspace / "Consumer"
    provider_release = publish_native_provider_release(
        runtime, provider, release_id="provider_r1"
    )
    consumer_release = publish_native_provider_release(
        runtime, consumer, release_id="consumer_r1"
    )
    _configure_clone_url(
        runtime,
        provider,
        (remotes / "Provider.git").as_uri(),
    )
    _configure_clone_url(
        runtime,
        consumer,
        (remotes / "Consumer.git").as_uri(),
    )
    output = workspace / "published-workspace"

    preview = runtime.repo_workspace.workspace_publication.preview(
        workspace,
        repo_keys=["Provider", "Consumer"],
        output_root=output,
        push_children=True,
        push_superproject=True,
    )
    assert preview.ok and preview.value is not None, preview.issues
    applied = runtime.repo_workspace.workspace_publication.apply(
        workspace,
        preview=preview.value,
        expected_recovery_token=preview.value.recovery_token,
    )

    assert applied.ok and applied.value is not None, applied.issues
    assert applied.value.pushed_child_repo_keys == ["Consumer", "Provider"]
    assert applied.value.superproject_remote_verified
    expected_children = {
        "Provider": provider_release.release_id,
        "Consumer": consumer_release.release_id,
    }
    for repo_key, release_id in expected_children.items():
        remote_ref = subprocess.run(
            [
                "git",
                "--git-dir",
                str(remotes / f"{repo_key}.git"),
                "rev-parse",
                f"refs/lean-constellation/releases/{release_id}",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert remote_ref == applied.value.child_commits[repo_key]
    super_ref = subprocess.run(
        [
            "git",
            "--git-dir",
            str(remotes / "Bundle.git"),
            "rev-parse",
            f"refs/lean-constellation/workspaces/{workspace.name}",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert super_ref == applied.value.superproject_commit
