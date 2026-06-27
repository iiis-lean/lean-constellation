from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from lean_constellation.services.external_clients import GitHubRepoClient, GitHubRepoClientConfig


def _require_github_live() -> None:
    if os.environ.get("LEAN_CONSTELLATION_REAL_GITHUB") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_REAL_GITHUB=1 to run live GitHub client tests.")
    if shutil.which("gh") is None:
        pytest.skip("GitHub CLI `gh` is required for live GitHub client tests.")
    if shutil.which("git") is None:
        pytest.skip("`git` is required for live GitHub client tests.")


@pytest.mark.real
def test_github_live_search_inspect_checkout_and_probe(tmp_path: Path) -> None:
    _require_github_live()
    repo = os.environ.get("LEAN_CONSTELLATION_REAL_GITHUB_REPO", "leanprover/lean4-samples")
    revision = os.environ.get("LEAN_CONSTELLATION_REAL_GITHUB_REVISION")
    search_query = os.environ.get("LEAN_CONSTELLATION_REAL_GITHUB_QUERY", repo)
    client = GitHubRepoClient(GitHubRepoClientConfig(timeout_seconds=120, clone_depth=1))

    search = client.search_repositories(search_query, limit=1)
    assert search.ok, search.summary
    assert search.candidates
    assert search.candidates[0].full_name
    assert search.candidates[0].html_url.startswith("https://github.com/")

    inspected = client.inspect_repository(repo)
    assert inspected.full_name
    assert inspected.html_url.startswith("https://github.com/")
    assert inspected.clone_url and inspected.clone_url.endswith(".git")

    checkout = client.checkout_repository(inspected.html_url, tmp_path / "checkout", revision=revision)
    assert checkout.ok, checkout.summary
    assert checkout.resolved_revision

    probe = client.probe_lean_repo(Path(checkout.checkout_path))
    assert probe.has_lakefile, probe.summary
    assert probe.lakefile_paths


@pytest.mark.real
def test_github_probe_real_filesystem_variants(tmp_path: Path) -> None:
    client = GitHubRepoClient()
    missing = tmp_path / "missing-lakefile"
    nested = tmp_path / "nested-project"
    nested_pkg = nested / "Pkg"
    missing.mkdir()
    (nested_pkg / "Main").mkdir(parents=True)
    (nested_pkg / "lakefile.toml").write_text('name = "Pkg"\nversion = "0.1.0"\n', encoding="utf-8")
    (nested_pkg / "Pkg.lean").write_text("def x := 1\n", encoding="utf-8")
    (nested_pkg / "Main" / "Basic.lean").write_text("def y := 2\n", encoding="utf-8")

    missing_probe = client.probe_lean_repo(missing)
    nested_probe = client.probe_lean_repo(nested)

    assert missing_probe.has_lakefile is False
    assert missing_probe.has_entry_module is False
    assert nested_probe.has_lakefile is True
    assert nested_probe.candidate_subdirs == ["Pkg"]
    assert nested_probe.entry_module_paths == ["Pkg/Main/Basic.lean", "Pkg/Pkg.lean"]
