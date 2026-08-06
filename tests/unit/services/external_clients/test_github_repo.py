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
                            "language": "Lean",
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
                        "primaryLanguage": {"name": "Lean"},
                        "languages": [{"name": "Lean"}, {"name": "Shell"}],
                        "repositoryTopics": [{"name": "formalization"}],
                        "licenseInfo": {"spdxId": "MIT", "name": "MIT License"},
                    }
                ),
            )
        if command[:2] == ["gh", "api"] and command[2].endswith("/commits"):
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps([{"sha": "a" * 40}, {"sha": "b" * 40}]),
            )
        if command[:2] == ["gh", "api"] and "/commits/" in command[2]:
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps({"sha": "a" * 40}),
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


class SignalProbeRunner:
    def __init__(
        self,
        *,
        full_name: str,
        primary_language: str | None,
        topics: list[str],
        paths: list[str],
    ) -> None:
        self.full_name = full_name
        self.primary_language = primary_language
        self.topics = topics
        self.paths = paths

    def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
        command = list(command)
        if command[:3] == ["gh", "repo", "view"]:
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps(
                    {
                        "nameWithOwner": self.full_name,
                        "url": f"https://github.com/{self.full_name}",
                        "defaultBranchRef": {"name": "main"},
                        "primaryLanguage": (
                            {"name": self.primary_language}
                            if self.primary_language
                            else None
                        ),
                        "languages": (
                            [{"name": self.primary_language}]
                            if self.primary_language
                            else []
                        ),
                        "repositoryTopics": [
                            {"name": topic} for topic in self.topics
                        ],
                    }
                ),
            )
        if command[:2] == ["gh", "api"] and "/commits/" in command[2]:
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps({"sha": "c" * 40}),
            )
        if command[:2] == ["gh", "api"] and "/git/trees/" in command[2]:
            return ExternalCommandResult(
                ok=True,
                command=command,
                cwd=str(cwd),
                exit_code=0,
                stdout_excerpt=json.dumps(
                    {
                        "sha": "tree-sha",
                        "truncated": False,
                        "tree": [
                            {"path": path, "type": "blob", "sha": f"sha-{index}"}
                            for index, path in enumerate(self.paths)
                        ],
                    }
                ),
            )
        return ExternalCommandResult(
            ok=True,
            command=command,
            cwd=str(cwd),
            exit_code=0,
            stdout_excerpt="{}",
        )


def test_normalize_github_url_formats() -> None:
    client = GitHubRepoClient()

    assert client.normalize_github_url("owner/repo") == "https://github.com/owner/repo"
    assert client.normalize_github_url("https://github.com/owner/repo.git") == "https://github.com/owner/repo"
    assert client.normalize_github_url("git@github.com:owner/repo.git") == "https://github.com/owner/repo"


def test_search_and_inspect_use_gh_json() -> None:
    runner = FakeRunner()
    client = GitHubRepoClient(runner=runner)

    search = client.search_repositories("lean fixed point", limit=5)
    client.search_repositories("broad formalization", limit=1000)
    inspected = client.inspect_repository("owner/repo")

    assert search.ok is True
    assert search.candidates[0].full_name == "leanprover-community/mathlib4"
    assert search.candidates[0].primary_language == "Lean"
    assert inspected.full_name == "owner/repo"
    assert inspected.default_branch == "main"
    assert inspected.languages == ["Lean", "Shell"]
    assert inspected.license_spdx_id == "MIT"
    broad_search = next(
        call
        for call in runner.calls
        if call[:4] == ["gh", "search", "repos", "broad formalization"]
    )
    assert broad_search[broad_search.index("--limit") + 1] == "100"


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


def test_remote_commit_history_returns_ordered_immutable_candidates() -> None:
    runner = FakeRunner()
    client = GitHubRepoClient(runner=runner)

    result = client.list_repository_commits(
        "owner/repo",
        path="lean/lean-toolchain",
        limit=2,
    )

    assert result.issue_code is None
    assert result.commits == ["a" * 40, "b" * 40]
    assert result.path == "lean/lean-toolchain"
    assert any(
        call[:3] == ["gh", "api", "repos/owner/repo/commits"]
        and "path=lean/lean-toolchain" in call
        and "per_page=2" in call
        for call in runner.calls
    )


def test_remote_lean_probe_extracts_lake_evidence_without_checkout() -> None:
    runner = FakeRunner()
    client = GitHubRepoClient(runner=runner)

    probe = client.probe_github_lean_repo_candidate("owner/repo", revision="main")
    nested = client.probe_github_lean_repo_candidate("owner/repo", revision="main", subdir="nested")

    assert probe.is_lean_project is True
    assert probe.package_name == "Foo"
    assert probe.has_lakefile is True
    assert probe.has_lean_toolchain is True
    assert probe.resolved_revision == "a" * 40
    assert probe.adapter_candidate is True
    assert "metadata:language=Lean" in probe.lean_signals
    assert probe.candidate_subdirs == ["", "nested"]
    assert "Foo" in probe.likely_import_modules
    assert "Lean project" in (probe.readme_evidence or "")
    assert nested.package_name == "Nested"
    assert nested.selected_subdir == "nested"
    assert not any(call[:2] == ["git", "clone"] for call in runner.calls)


@pytest.mark.parametrize(
    ("full_name", "primary_language", "topics", "paths", "expected_signal", "adapter_candidate"),
    [
        ("owner/language", "Lean", [], [], "metadata:language=Lean", True),
        ("owner/topics", "Python", ["formalization"], [], "metadata:topic=formalization", True),
        ("owner/toolchain", "Python", [], ["lean-toolchain"], "path:lean-toolchain", True),
        ("owner/source-tree", "Python", [], ["Proof/Main.lean"], "tree:lean_files=1", True),
        ("owner/not-lean", "Python", [], ["main.py"], None, False),
        (
            "leanprover-community/mathlib4",
            "Lean",
            ["mathlib"],
            ["lakefile.lean", "Mathlib/Algebra.lean"],
            "metadata:language=Lean",
            False,
        ),
    ],
)
def test_remote_lean_probe_uses_multiple_signals_and_excludes_mathlib(
    full_name: str,
    primary_language: str | None,
    topics: list[str],
    paths: list[str],
    expected_signal: str | None,
    adapter_candidate: bool,
) -> None:
    probe = GitHubRepoClient(
        runner=SignalProbeRunner(
            full_name=full_name,
            primary_language=primary_language,
            topics=topics,
            paths=paths,
        )
    ).probe_github_lean_repo_candidate(full_name)

    assert probe.is_lean_project is (expected_signal is not None)
    if expected_signal is not None:
        assert expected_signal in probe.lean_signals
    assert probe.adapter_candidate is adapter_candidate
    assert probe.is_mathlib_repository is full_name.startswith("leanprover-community/mathlib")


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
            if command[:2] == ["gh", "api"] and "/commits/" in command[2]:
                return ExternalCommandResult(
                    ok=True,
                    command=command,
                    cwd=str(cwd),
                    exit_code=0,
                    stdout_excerpt=json.dumps({"sha": "b" * 40}),
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


def test_remote_lean_probe_keeps_structure_but_withholds_unresolved_commit() -> None:
    class RevisionFailureRunner(FakeRunner):
        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            command = list(command)
            if command[:2] == ["gh", "api"] and "/commits/" in command[2]:
                return ExternalCommandResult(
                    ok=False,
                    command=command,
                    cwd=str(cwd),
                    exit_code=1,
                    summary="commit lookup failed",
                    issue_code="commit_lookup_failed",
                )
            return super().run(
                command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                stdout_excerpt_chars=stdout_excerpt_chars,
                stderr_excerpt_chars=stderr_excerpt_chars,
            )

    probe = GitHubRepoClient(
        runner=RevisionFailureRunner()
    ).probe_github_lean_repo_candidate("owner/repo")

    assert probe.is_lean_project is True
    assert probe.resolved_revision is None
    assert any("commit_lookup_failed" in risk for risk in probe.known_risks)


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
    with pytest.raises(ValueError, match="probe limits"):
        GitHubRepoClient(runner=FakeRunner()).probe_github_lean_repo_candidate(
            "owner/repo",
            max_tree_entries=0,
        )


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
