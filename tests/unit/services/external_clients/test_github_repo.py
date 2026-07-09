from __future__ import annotations

import json
import base64
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
        if command[:2] == ["gh", "api"] and "/git/trees/" in command[2]:
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps(
                    {
                        "sha": "tree123",
                        "truncated": False,
                        "tree": [
                            {"path": "lakefile.lean", "type": "blob", "sha": "lake"},
                            {"path": "lean-toolchain", "type": "blob", "sha": "toolchain"},
                            {"path": "Foo.lean", "type": "blob", "sha": "foo"},
                            {"path": "README.md", "type": "blob", "sha": "readme"},
                            {"path": "nested/lakefile.toml", "type": "blob", "sha": "nested-lake"},
                            {"path": "nested/Nested.lean", "type": "blob", "sha": "nested"},
                        ],
                    }
                ),
            )
        if command[:2] == ["gh", "api"] and "/contents/" in command[2]:
            path = command[2].split("/contents/", 1)[1]
            contents = {
                "lakefile.lean": "import Lake\nopen Lake DSL\npackage Foo where\n",
                "lean-toolchain": "leanprover/lean4:v4.12.0\n",
                "README.md": "# Foo\nLean project with Lake proofs.\n",
                "nested/lakefile.toml": 'name = "Nested"\n',
            }
            text = contents.get(path, "def x := 1\n")
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps(
                    {
                        "type": "file",
                        "encoding": "base64",
                        "size": len(text),
                        "content": base64.b64encode(text.encode()).decode(),
                    }
                ),
            )
        if command[:3] == ["gh", "search", "code"]:
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps(
                    [
                        {
                            "path": "Foo.lean",
                            "url": "https://github.com/owner/repo/blob/main/Foo.lean",
                            "repository": {"fullName": "owner/repo"},
                            "textMatches": [{"fragment": "theorem foo : True := by trivial"}],
                        }
                    ]
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


def test_remote_tree_file_and_code_search_use_github_api() -> None:
    runner = FakeRunner()
    client = GitHubRepoClient(runner=runner)

    tree = client.list_repository_tree("owner/repo", revision="main", path_prefix="nested", limit=10)
    file_view = client.read_repository_file("owner/repo", "lakefile.lean", revision="main", max_chars=10)
    code = client.search_code("theorem", repo="owner/repo", limit=5)

    assert tree.entries[0].path == "nested/lakefile.toml"
    assert tree.path_prefix == "nested"
    assert file_view.content_excerpt == "import Lak"
    assert file_view.truncated is True
    assert code.ok is True
    assert code.matches[0].repository == "owner/repo"
    assert any(call[:2] == ["gh", "api"] and "/git/trees/main" in call[2] and "--method" in call and "GET" in call for call in runner.calls)
    assert any(call[:2] == ["gh", "api"] and "/contents/lakefile.lean" in call[2] and "--method" in call and "GET" in call for call in runner.calls)
    assert any(call[:3] == ["gh", "search", "code"] and "--repo" in call for call in runner.calls)


def test_remote_file_reports_path_traversal_issue() -> None:
    client = GitHubRepoClient(runner=FakeRunner())

    result = client.read_repository_file("owner/repo", "../lakefile.lean")

    assert result.issue_code == "invalid_github_path"
    assert "cannot contain" in (result.summary or "")


def test_remote_lean_probe_extracts_lake_evidence_without_checkout() -> None:
    runner = FakeRunner()
    client = GitHubRepoClient(runner=runner)

    probe = client.probe_github_lean_repo_candidate("owner/repo", revision="main")
    nested = client.probe_github_lean_repo_candidate("owner/repo", revision="main", subdir="nested")

    assert probe.is_lean_project is True
    assert probe.package_name == "Foo"
    assert probe.has_lakefile is True
    assert probe.has_lean_toolchain is True
    assert probe.candidate_subdirs == ["", "nested"]
    assert "Foo" in probe.likely_import_modules
    assert "Lean project" in (probe.readme_evidence or "")
    assert nested.package_name == "Nested"
    assert nested.selected_subdir == "nested"
    assert not any(call[:2] == ["git", "clone"] for call in runner.calls)


def test_remote_lean_probe_handles_no_lakefile_and_truncated_tree() -> None:
    class ProbeRunner(FakeRunner):
        def __init__(self, *, truncated: bool) -> None:
            super().__init__()
            self.truncated = truncated

        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            command = list(command)
            self.calls.append(command)
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
                            "defaultBranchRef": {"name": "main"},
                        }
                    ),
                )
            if command[:2] == ["gh", "api"] and "/git/trees/" in command[2]:
                return ExternalCommandResult(
                    ok=True,
                    command=command,
                    cwd=str(cwd),
                    exit_code=0,
                    stdout_excerpt=json.dumps(
                        {
                            "sha": "tree123",
                            "truncated": self.truncated,
                            "tree": [{"path": "README.md", "type": "blob", "sha": "readme"}],
                        }
                    ),
                )
            if command[:2] == ["gh", "api"] and "/contents/README.md" in command[2]:
                text = "A repository without Lean project files.\n"
                return ExternalCommandResult(
                    ok=True,
                    command=command,
                    cwd=str(cwd),
                    exit_code=0,
                    stdout_excerpt=json.dumps(
                        {
                            "type": "file",
                            "encoding": "base64",
                            "size": len(text),
                            "content": base64.b64encode(text.encode()).decode(),
                        }
                    ),
                )
            return ExternalCommandResult(ok=True, command=command, cwd=str(cwd), exit_code=0, stdout_excerpt="{}")

    no_lakefile = GitHubRepoClient(runner=ProbeRunner(truncated=False)).probe_github_lean_repo_candidate("owner/repo")
    truncated = GitHubRepoClient(runner=ProbeRunner(truncated=True)).probe_github_lean_repo_candidate("owner/repo")

    assert no_lakefile.is_lean_project is False
    assert no_lakefile.has_lakefile is False
    assert truncated.truncated is True
    assert any("truncated" in risk for risk in truncated.known_risks)


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
