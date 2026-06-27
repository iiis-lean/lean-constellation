from __future__ import annotations

import json
from pathlib import Path

import pytest

from lean_constellation.services.external_clients import ExternalCommandResult, GitHubRepoClient


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
        command = list(command)
        self.calls.append(command)
        if command[:3] == ["gh", "search", "repos"]:
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps(
                    [
                        {
                            "fullName": "leanprover-community/mathlib4",
                            "url": "https://github.com/leanprover-community/mathlib4",
                            "description": "mathlib",
                            "stargazersCount": 1,
                            "defaultBranch": "master",
                        }
                    ]
                ),
            )
        if command[:3] == ["gh", "repo", "view"]:
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps(
                    {
                        "nameWithOwner": "owner/repo",
                        "url": "https://github.com/owner/repo",
                        "description": "repo",
                        "stargazerCount": 2,
                        "defaultBranchRef": {"name": "main"},
                        "licenseInfo": {"spdxId": "MIT", "name": "MIT License"},
                    }
                ),
            )
        if command[:2] == ["git", "clone"]:
            Path(command[-1]).mkdir(parents=True, exist_ok=True)
            return ExternalCommandResult(ok=True, command=command, cwd=str(cwd), exit_code=0)
        if command[:2] == ["git", "rev-parse"]:
            return ExternalCommandResult(ok=True, command=command, cwd=str(cwd), exit_code=0, stdout_excerpt="abc123\n")
        return ExternalCommandResult(ok=True, command=command, cwd=str(cwd), exit_code=0)


def test_normalize_github_url_formats() -> None:
    client = GitHubRepoClient()

    assert client.normalize_github_url("owner/repo") == "https://github.com/owner/repo"
    assert client.normalize_github_url("https://github.com/owner/repo.git") == "https://github.com/owner/repo"
    assert client.normalize_github_url("git@github.com:owner/repo.git") == "https://github.com/owner/repo"


def test_search_and_inspect_use_gh_json() -> None:
    client = GitHubRepoClient(runner=FakeRunner())

    search = client.search_repositories("lean fixed point", limit=5)
    inspected = client.inspect_repository("owner/repo")

    assert search.ok is True
    assert search.candidates[0].full_name == "leanprover-community/mathlib4"
    assert inspected.full_name == "owner/repo"
    assert inspected.default_branch == "main"
    assert inspected.license_spdx_id == "MIT"


def test_search_reports_command_failure_invalid_json_and_bad_limit() -> None:
    class FailureRunner:
        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            return ExternalCommandResult(
                ok=False,
                command=list(command),
                cwd=str(cwd),
                exit_code=1,
                summary="gh search failed",
                issue_code="gh_failed",
            )

    class InvalidJsonRunner:
        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            return ExternalCommandResult(ok=True, command=list(command), cwd=str(cwd), exit_code=0, stdout_excerpt="{bad")

    failed = GitHubRepoClient(runner=FailureRunner()).search_repositories("lean", limit=1)
    invalid = GitHubRepoClient(runner=InvalidJsonRunner()).search_repositories("lean", limit=1)

    assert failed.ok is False
    assert failed.issue_code == "gh_failed"
    assert invalid.ok is False
    assert invalid.issue_code == "invalid_json"
    with pytest.raises(ValueError, match="limit"):
        GitHubRepoClient(runner=FakeRunner()).search_repositories("lean", limit=0)


def test_inspect_repository_falls_back_on_command_failure_and_invalid_json() -> None:
    class FailureRunner:
        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            return ExternalCommandResult(ok=False, command=list(command), cwd=str(cwd), exit_code=1, summary="repo view failed")

    class InvalidJsonRunner:
        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            return ExternalCommandResult(ok=True, command=list(command), cwd=str(cwd), exit_code=0, stdout_excerpt="{bad")

    failed = GitHubRepoClient(runner=FailureRunner()).inspect_repository("owner/repo")
    invalid = GitHubRepoClient(runner=InvalidJsonRunner()).inspect_repository("owner/repo")

    assert failed.full_name == "owner/repo"
    assert failed.clone_url == "https://github.com/owner/repo.git"
    assert failed.evidence_summary == "Repository inspect failed: repo view failed"
    assert invalid.full_name == "owner/repo"
    assert invalid.evidence_summary == "Repository inspect returned invalid JSON."


def test_checkout_rejects_existing_nonempty_dest(tmp_path) -> None:
    client = GitHubRepoClient(runner=FakeRunner())
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "file").write_text("x", encoding="utf-8")

    result = client.checkout_repository("owner/repo", dest)

    assert result.ok is False
    assert result.issue_code == "checkout_path_not_empty"


def test_checkout_revision_success_and_failures(tmp_path) -> None:
    class CheckoutRunner:
        def __init__(self, *, fail_clone: bool = False, fail_checkout: bool = False) -> None:
            self.fail_clone = fail_clone
            self.fail_checkout = fail_checkout
            self.calls: list[list[str]] = []

        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            command = list(command)
            self.calls.append(command)
            if command[:2] == ["git", "clone"]:
                if self.fail_clone:
                    return ExternalCommandResult(
                        ok=False,
                        command=command,
                        cwd=str(cwd),
                        exit_code=1,
                        summary="clone failed",
                        issue_code="clone_failed",
                    )
                Path(command[-1]).mkdir(parents=True, exist_ok=True)
                return ExternalCommandResult(ok=True, command=command, cwd=str(cwd), exit_code=0)
            if command[:2] == ["git", "checkout"]:
                if self.fail_checkout:
                    return ExternalCommandResult(
                        ok=False,
                        command=command,
                        cwd=str(cwd),
                        exit_code=1,
                        summary="checkout failed",
                        issue_code="checkout_failed",
                    )
                return ExternalCommandResult(ok=True, command=command, cwd=str(cwd), exit_code=0)
            if command[:2] == ["git", "rev-parse"]:
                return ExternalCommandResult(ok=True, command=command, cwd=str(cwd), exit_code=0, stdout_excerpt="def456\n")
            return ExternalCommandResult(ok=True, command=command, cwd=str(cwd), exit_code=0)

    success_runner = CheckoutRunner()
    success = GitHubRepoClient(runner=success_runner).checkout_repository("owner/repo", tmp_path / "success", revision="main")
    clone_failed = GitHubRepoClient(runner=CheckoutRunner(fail_clone=True)).checkout_repository("owner/repo", tmp_path / "clone")
    checkout_failed = GitHubRepoClient(runner=CheckoutRunner(fail_checkout=True)).checkout_repository(
        "owner/repo",
        tmp_path / "checkout",
        revision="bad",
    )

    assert success.ok is True
    assert success.requested_revision == "main"
    assert success.resolved_revision == "def456"
    assert ["git", "checkout", "main"] in success_runner.calls
    assert clone_failed.ok is False
    assert clone_failed.issue_code == "clone_failed"
    assert checkout_failed.ok is False
    assert checkout_failed.issue_code == "checkout_failed"


def test_checkout_and_probe_lean_repo(tmp_path) -> None:
    client = GitHubRepoClient(runner=FakeRunner())
    dest = tmp_path / "repo"

    checkout = client.checkout_repository("owner/repo", dest)
    (dest / "lakefile.lean").write_text("import Lake", encoding="utf-8")
    (dest / "lean-toolchain").write_text("leanprover/lean4:stable", encoding="utf-8")
    (dest / "Main.lean").write_text("def x := 1", encoding="utf-8")
    probe = client.probe_lean_repo(dest)

    assert checkout.ok is True
    assert checkout.resolved_revision == "abc123"
    assert probe.has_lakefile is True
    assert probe.has_lean_toolchain is True
    assert probe.has_entry_module is True
    assert probe.lakefile_paths == ["lakefile.lean"]
    assert probe.entry_module_paths == ["Main.lean"]


def test_probe_lean_repo_detects_missing_nested_and_package_entry_modules(tmp_path) -> None:
    client = GitHubRepoClient(runner=FakeRunner())
    empty = tmp_path / "empty"
    empty.mkdir()
    root = tmp_path / "workspace"
    nested = root / "pkg"
    (nested / "Main").mkdir(parents=True)
    (nested / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage «Pkg» where\n", encoding="utf-8")
    (nested / "Main" / "Basic.lean").write_text("def y := 1", encoding="utf-8")
    (nested / "Pkg.lean").write_text("import Pkg.Basic", encoding="utf-8")

    no_project = client.probe_lean_repo(empty)
    nested_project = client.probe_lean_repo(root)

    assert no_project.has_lakefile is False
    assert no_project.has_entry_module is False
    assert nested_project.has_lakefile is True
    assert nested_project.candidate_subdirs == ["pkg"]
    assert nested_project.entry_module_paths == ["pkg/Main/Basic.lean", "pkg/Pkg.lean"]
