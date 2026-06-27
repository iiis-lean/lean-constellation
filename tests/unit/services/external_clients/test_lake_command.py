from __future__ import annotations

import sys
from pathlib import Path

from lean_constellation.services.external_clients import ExternalCommandResult, LakeCommandClient, LakeCommandClientConfig
from lean_constellation.services.external_clients.process import SubprocessCommandRunner


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
        self.calls.append(list(command))
        return ExternalCommandResult(
            ok=True,
            command=list(command),
            cwd=str(cwd),
            exit_code=0,
            stdout_excerpt="ok",
            stderr_excerpt="",
            elapsed_ms=1,
            summary="done",
        )


def test_lake_update_and_build_construct_commands(tmp_path) -> None:
    runner = FakeRunner()
    client = LakeCommandClient(runner=runner)

    update = client.run_lake_update(tmp_path)
    build = client.run_lake_build(tmp_path, target="Main")

    assert update.ok is True
    assert build.ok is True
    assert runner.calls == [["lake", "update"], ["lake", "build", "Main"]]


def test_lake_update_preserves_failure_and_timeout_results(tmp_path) -> None:
    class ResultRunner:
        def __init__(self, result: ExternalCommandResult) -> None:
            self.result = result

        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            return self.result.model_copy(update={"command": list(command), "cwd": str(cwd)})

    failed = LakeCommandClient(
        runner=ResultRunner(
            ExternalCommandResult(
                ok=False,
                command=[],
                cwd="",
                exit_code=1,
                summary="lake update failed",
                issue_code="command_failed",
            )
        )
    ).run_lake_update(tmp_path)
    timed_out = LakeCommandClient(
        runner=ResultRunner(
            ExternalCommandResult(
                ok=False,
                command=[],
                cwd="",
                exit_code=None,
                timed_out=True,
                summary="timeout",
                issue_code="command_timeout",
            )
        )
    ).run_lake_update(tmp_path)

    assert failed.ok is False
    assert failed.issue_code == "command_failed"
    assert timed_out.ok is False
    assert timed_out.timed_out is True
    assert timed_out.issue_code == "command_timeout"


def test_lake_build_supports_default_target_and_targets_list(tmp_path) -> None:
    runner = FakeRunner()
    client = LakeCommandClient(runner=runner)

    client.run_lake_build(tmp_path)
    client.run_lake_build(tmp_path, targets=["A", "B"])

    assert runner.calls == [["lake", "build"], ["lake", "build", "A", "B"]]


def test_run_command_rejects_missing_repo_root(tmp_path) -> None:
    client = LakeCommandClient()

    result = client.run_command(tmp_path / "missing", ["lake", "build"])

    assert result.ok is False
    assert result.issue_code == "missing_repo_root"


def test_run_command_rejects_empty_argv_and_passes_excerpt_limits(tmp_path) -> None:
    class LimitRunner:
        def __init__(self) -> None:
            self.limits: tuple[int, int] | None = None

        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            self.limits = (stdout_excerpt_chars, stderr_excerpt_chars)
            return ExternalCommandResult(ok=True, command=list(command), cwd=str(cwd), exit_code=0)

    runner = LimitRunner()
    client = LakeCommandClient(LakeCommandClientConfig(stdout_excerpt_chars=7, stderr_excerpt_chars=9), runner=runner)

    empty = client.run_command(tmp_path, [])
    ok = client.run_command(tmp_path, ["lake", "build"])

    assert empty.ok is False
    assert empty.issue_code == "empty_command"
    assert ok.ok is True
    assert runner.limits == (7, 9)


def test_minimal_import_check_uses_lake_env(tmp_path) -> None:
    runner = FakeRunner()
    client = LakeCommandClient(LakeCommandClientConfig(use_lake_env_for_lean=True), runner=runner)

    result = client.run_minimal_import_check(tmp_path, "Mathlib")

    assert result.ok is True
    assert runner.calls[0][:4] == ["lake", "env", "lean", "--json"]


def test_minimal_import_check_direct_lean_failure_invalid_module_and_temp_cleanup(tmp_path) -> None:
    class FailingRunner(FakeRunner):
        def run(self, command, *, cwd: Path, timeout_seconds: int, stdout_excerpt_chars: int, stderr_excerpt_chars: int):
            self.calls.append(list(command))
            return ExternalCommandResult(
                ok=False,
                command=list(command),
                cwd=str(cwd),
                exit_code=1,
                stderr_excerpt="unknown module",
                issue_code="command_failed",
                summary="lean failed",
            )

    runner = FailingRunner()
    client = LakeCommandClient(LakeCommandClientConfig(use_lake_env_for_lean=False), runner=runner)

    failed = client.run_minimal_import_check(tmp_path, "Project.Module")
    temp_file = Path(runner.calls[0][-1])
    invalid = client.run_minimal_import_check(tmp_path, "Mathlib\n#eval 1")

    assert failed.ok is False
    assert failed.command[0:2] == ["lean", "--json"]
    assert failed.diagnostics_excerpt == "unknown module"
    assert temp_file.exists() is False
    assert invalid.ok is False
    assert invalid.issue_code == "invalid_import_module"
    assert len(runner.calls) == 1


def test_snippet_check_rejects_invalid_import_before_creating_command(tmp_path) -> None:
    runner = FakeRunner()
    client = LakeCommandClient(runner=runner)

    result = client.run_snippet_check(repo_root=tmp_path, imports=["Mathlib", "Bad\n#eval 1"], code="#check Nat")

    assert result.ok is False
    assert result.issue_code == "invalid_import_module"
    assert runner.calls == []


def test_summarize_command_result() -> None:
    client = LakeCommandClient()
    result = ExternalCommandResult(ok=False, command=["lake"], cwd="/repo", exit_code=1, stderr_excerpt="bad", issue_code="command_failed", summary="failed")

    view = client.summarize_command_result(result)

    assert view.ok is False
    assert view.stderr_excerpt == "bad"


def test_subprocess_runner_truncates_stdout_and_stderr(tmp_path) -> None:
    runner = SubprocessCommandRunner()

    result = runner.run(
        [
            sys.executable,
            "-c",
            "import sys; print('abcdef'); print('uvwxyz', file=sys.stderr); sys.exit(1)",
        ],
        cwd=tmp_path,
        timeout_seconds=10,
        stdout_excerpt_chars=3,
        stderr_excerpt_chars=4,
    )

    assert result.ok is False
    assert result.issue_code == "command_failed"
    assert result.stdout_excerpt == "abc\n...[truncated]"
    assert result.stderr_excerpt == "uvwx\n...[truncated]"
