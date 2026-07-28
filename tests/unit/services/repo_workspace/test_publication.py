from __future__ import annotations

import json
from pathlib import Path

from lean_constellation.domain.publication import (
    PushPolicy,
    RemoteProfile,
    RepoPublicationOverride,
    RepoPortability,
    WorkspacePublicationPolicy,
)
from lean_constellation.domain.repo import WorkspaceConfig
from tests.unit_services_helpers import make_runtime
from tests.unit.services.repo_workspace.test_repo_release import _prepare_release_repo


def test_managed_gitignore_preserves_user_content_and_is_idempotent(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("custom-cache/\n", encoding="utf-8")

    first = runtime.repo_workspace.publication.refresh_managed_gitignore(tmp_path)
    first_bytes = gitignore.read_bytes()
    second = runtime.repo_workspace.publication.refresh_managed_gitignore(tmp_path)

    assert first.ok and first.value
    assert second.ok and not second.value
    assert gitignore.read_bytes() == first_bytes
    text = first_bytes.decode("utf-8")
    assert "custom-cache/" in text
    assert "/.agent_runtime/" in text
    assert "!/.env.example" in text


def test_publication_manifest_excludes_runtime_and_contains_no_absolute_paths(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    (tmp_path / "Main.lean").write_text("theorem ok : True := by trivial\n")
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime" / "server.json").write_text(
        json.dumps({"repo_root": str(tmp_path)})
    )

    manifest = runtime.repo_workspace.publication.build_manifest(tmp_path)

    assert manifest.ok and manifest.value is not None
    by_path = {entry.path: entry for entry in manifest.value.entries}
    assert by_path["Main.lean"].disposition == "include"
    assert by_path[".runtime/server.json"].disposition == "exclude"
    payload = manifest.value.model_dump_json()
    assert str(tmp_path) not in payload


def test_publication_documents_are_portable_and_managed_readme_is_preserved(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)
    assert runtime.repo_workspace.metadata.ensure_repo_model(tmp_path).ok
    assert runtime.repo_workspace.metadata.set_repo_summary(
        tmp_path, summary="Formalizes a public result."
    ).ok
    readme = tmp_path / "README.md"
    readme.write_text("User preface.\n", encoding="utf-8")

    prepared = runtime.repo_workspace.publication.prepare_publication(tmp_path)

    assert prepared.ok and prepared.value is not None, prepared.issues
    assert "User preface." in readme.read_text(encoding="utf-8")
    assert "PublicResult" in readme.read_text(encoding="utf-8")
    api = json.loads(
        (tmp_path / "docs/lean-constellation/public-api.json").read_text()
    )
    assert {item["name"] for item in api["declarations"]} == {
        "ProofHelper",
        "PublicResult",
        "Support",
    }
    for path in (
        tmp_path / "README.md",
        tmp_path / "docs/lean-constellation/public-api.json",
        tmp_path / "docs/lean-constellation/provenance.json",
        tmp_path / ".lean_constellation/publication/manifest.json",
    ):
        assert str(tmp_path) not in path.read_text(encoding="utf-8")


def test_repo_publication_override_wins_over_workspace_defaults(
    tmp_path: Path,
) -> None:
    runtime, _ = _prepare_release_repo(tmp_path)

    resolved = runtime.repo_workspace.publication.resolve_policy(
        tmp_path,
        repo_override=RepoPublicationOverride(
            push_policy=PushPolicy.ON_RELEASE,
            canonical_fetch_url="https://example.invalid/example.git",
        ),
    )

    assert resolved.ok and resolved.value is not None
    assert resolved.value.policy.push_policy == PushPolicy.ON_RELEASE
    assert (
        resolved.value.policy.canonical_fetch_url
        == "https://example.invalid/example.git"
    )
    assert resolved.value.portability == RepoPortability.PORTABLE
    assert resolved.value.source_by_field["push_policy"] == "repo_override"


def test_workspace_remote_profile_derives_repo_neutral_canonical_urls(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "Mathematics"
    repo = workspace / "Uniform"
    repo.mkdir(parents=True)
    runtime = make_runtime(
        workspace_config=WorkspaceConfig(
            publication=WorkspacePublicationPolicy(
                repo_remote_profile="canonical",
                repo_remote_name_template="lc-{repo_key}",
                remote_profiles={
                    "canonical": RemoteProfile(
                        fetch_url_template=(
                            "https://git.example/{organization}/{repo_name}.git"
                        ),
                        push_url_template=(
                            "ssh://git@git.example/{organization}/{repo_name}.git"
                        ),
                        values={"organization": "formalizations"},
                    )
                },
            )
        )
    )
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo).ok

    resolved = runtime.repo_workspace.publication.resolve_policy(repo)

    assert resolved.ok and resolved.value is not None, resolved.issues
    assert resolved.value.policy.canonical_fetch_url == (
        "https://git.example/formalizations/lc-Uniform.git"
    )
    assert resolved.value.policy.canonical_push_url == (
        "ssh://git@git.example/formalizations/lc-Uniform.git"
    )
    assert (
        resolved.value.source_by_field["canonical_fetch_url"]
        == "workspace_profile:canonical"
    )
