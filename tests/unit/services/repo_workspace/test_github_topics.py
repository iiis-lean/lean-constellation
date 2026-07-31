from __future__ import annotations

import json
from pathlib import Path

from lean_constellation.domain.publication import RepoPublicationPresentation
from lean_constellation.services.external_clients.process import (
    ExternalCommandResult,
)
from lean_constellation.services.repo_workspace.github_topics import (
    RepoGitHubTopicsComponent,
)
from tests.unit_services_helpers import make_runtime


class FakeGitHubTopicsRunner:
    def __init__(
        self,
        *,
        topics: list[str] | None = None,
        apply_updates: bool = True,
        fail_view: bool = False,
    ) -> None:
        self.topics = list(topics or [])
        self.apply_updates = apply_updates
        self.fail_view = fail_view
        self.calls: list[list[str]] = []

    def run(
        self,
        command,
        *,
        cwd: Path,
        timeout_seconds: int,
        stdout_excerpt_chars: int,
        stderr_excerpt_chars: int,
        env=None,
        input_text=None,
    ) -> ExternalCommandResult:
        del timeout_seconds, stdout_excerpt_chars, stderr_excerpt_chars, env
        del input_text
        command = list(command)
        self.calls.append(command)
        if command[:4] == ["git", "remote", "get-url", "origin"]:
            return self._result(
                command,
                cwd,
                stdout="https://github.com/iiis-lean/Example.git\n",
            )
        if command[:4] == [
            "gh",
            "repo",
            "view",
            "iiis-lean/Example",
        ]:
            if self.fail_view:
                return self._result(
                    command,
                    cwd,
                    ok=False,
                    stderr="authentication failed",
                )
            return self._result(
                command,
                cwd,
                stdout=json.dumps(
                    {
                        "nameWithOwner": "iiis-lean/Example",
                        "repositoryTopics": [
                            {"name": topic} for topic in self.topics
                        ],
                    }
                ),
            )
        if command[:5] == [
            "gh",
            "api",
            "--method",
            "PUT",
            "repos/iiis-lean/Example/topics",
        ]:
            if self.apply_updates:
                self.topics = [
                    value.removeprefix("names[]=")
                    for index, value in enumerate(command)
                    if index > 0
                    and command[index - 1] == "-f"
                    and value.startswith("names[]=")
                ]
            return self._result(command, cwd, stdout='{"names":[]}')
        raise AssertionError(f"Unexpected command: {command}")

    @staticmethod
    def _result(
        command: list[str],
        cwd: Path,
        *,
        ok: bool = True,
        stdout: str | None = None,
        stderr: str | None = None,
    ) -> ExternalCommandResult:
        return ExternalCommandResult(
            ok=ok,
            command=command,
            cwd=str(cwd),
            exit_code=0 if ok else 1,
            stdout_excerpt=stdout,
            stderr_excerpt=stderr,
            issue_code=None if ok else "command_failed",
            summary="ok" if ok else "failed",
        )


def _runtime_with_topics(
    tmp_path: Path,
    runner: FakeGitHubTopicsRunner,
    *,
    topics: list[str] | None = None,
):
    runtime = make_runtime()
    repo_root = tmp_path / "Example"
    repo_root.mkdir()
    configured = runtime.repo_workspace.publication.set_presentation(
        repo_root,
        presentation=RepoPublicationPresentation(
            topics=(
                topics
                if topics is not None
                else [
                    "lean4",
                    "mathlib",
                    "formalization",
                    "lean-constellation",
                ]
            )
        ),
    )
    assert configured.ok, configured.issues
    runtime.repo_workspace.github_topics = RepoGitHubTopicsComponent(
        runtime,
        runner=runner,
    )
    return runtime, repo_root


def test_github_topics_preview_and_apply_exact_presentation_topics(
    tmp_path: Path,
) -> None:
    runner = FakeGitHubTopicsRunner(topics=["lean4", "stale"])
    runtime, repo_root = _runtime_with_topics(tmp_path, runner)

    preview = runtime.repo_workspace.github_topics.preview(repo_root)

    assert preview.ok and preview.value is not None, preview.issues
    assert preview.value.repository == "iiis-lean/Example"
    assert preview.value.current_topics == ["lean4", "stale"]
    assert preview.value.topics_to_add == [
        "mathlib",
        "formalization",
        "lean-constellation",
    ]
    assert preview.value.topics_to_remove == ["stale"]
    applied = runtime.repo_workspace.github_topics.apply(
        repo_root,
        expected_recovery_token=preview.value.recovery_token,
    )

    assert applied.ok and applied.value is not None, applied.issues
    assert applied.value.changed is True
    assert applied.value.verified is True
    assert applied.value.topics == [
        "lean4",
        "mathlib",
        "formalization",
        "lean-constellation",
    ]
    api_call = next(call for call in runner.calls if call[:2] == ["gh", "api"])
    assert api_call == [
        "gh",
        "api",
        "--method",
        "PUT",
        "repos/iiis-lean/Example/topics",
        "-f",
        "names[]=lean4",
        "-f",
        "names[]=mathlib",
        "-f",
        "names[]=formalization",
        "-f",
        "names[]=lean-constellation",
    ]


def test_github_topics_apply_rejects_stale_token(tmp_path: Path) -> None:
    runner = FakeGitHubTopicsRunner(topics=["lean4"])
    runtime, repo_root = _runtime_with_topics(tmp_path, runner)

    rejected = runtime.repo_workspace.github_topics.apply(
        repo_root,
        expected_recovery_token="0" * 64,
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "github_topics_token_mismatch"
    assert not any(call[:2] == ["gh", "api"] for call in runner.calls)


def test_github_topics_apply_fails_closed_on_verification_mismatch(
    tmp_path: Path,
) -> None:
    runner = FakeGitHubTopicsRunner(
        topics=["lean4"],
        apply_updates=False,
    )
    runtime, repo_root = _runtime_with_topics(tmp_path, runner)
    preview = runtime.repo_workspace.github_topics.preview(repo_root)
    assert preview.ok and preview.value is not None

    rejected = runtime.repo_workspace.github_topics.apply(
        repo_root,
        expected_recovery_token=preview.value.recovery_token,
    )

    assert not rejected.ok
    assert rejected.issues[0].kind == "github_topics_verification_failed"


def test_github_topics_preview_reports_read_failure(tmp_path: Path) -> None:
    runner = FakeGitHubTopicsRunner(fail_view=True)
    runtime, repo_root = _runtime_with_topics(tmp_path, runner)

    rejected = runtime.repo_workspace.github_topics.preview(repo_root)

    assert not rejected.ok
    assert rejected.issues[0].kind == "github_topics_read_failed"


def test_github_topics_preview_rejects_empty_presentation_topics(
    tmp_path: Path,
) -> None:
    runner = FakeGitHubTopicsRunner()
    runtime, repo_root = _runtime_with_topics(tmp_path, runner, topics=[])

    rejected = runtime.repo_workspace.github_topics.preview(repo_root)

    assert not rejected.ok
    assert rejected.issues[0].kind == "github_topics_not_configured"
    assert runner.calls == []
