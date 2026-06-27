"""Shared subprocess execution helpers for external clients."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from lean_constellation.domain.common import StrictModel


class ExternalCommandResult(StrictModel):
    ok: bool
    command: list[str]
    cwd: str
    exit_code: int | None
    timed_out: bool = False
    stdout_excerpt: str | None = None
    stderr_excerpt: str | None = None
    elapsed_ms: int | None = None
    issue_code: str | None = None
    summary: str | None = None


class CommandRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        stdout_excerpt_chars: int,
        stderr_excerpt_chars: int,
    ) -> ExternalCommandResult:
        ...


class SubprocessCommandRunner:
    """Run external commands without shell interpolation."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: int,
        stdout_excerpt_chars: int,
        stderr_excerpt_chars: int,
    ) -> ExternalCommandResult:
        started = time.monotonic()
        command_list = [str(part) for part in command]
        try:
            completed = subprocess.run(
                command_list,
                cwd=cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return ExternalCommandResult(
                ok=False,
                command=command_list,
                cwd=str(cwd),
                exit_code=None,
                timed_out=True,
                stdout_excerpt=_excerpt(exc.stdout, stdout_excerpt_chars),
                stderr_excerpt=_excerpt(exc.stderr, stderr_excerpt_chars),
                elapsed_ms=elapsed_ms,
                issue_code="command_timeout",
                summary=f"Command timed out after {timeout_seconds}s",
            )
        except OSError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return ExternalCommandResult(
                ok=False,
                command=command_list,
                cwd=str(cwd),
                exit_code=None,
                stdout_excerpt=None,
                stderr_excerpt=str(exc),
                elapsed_ms=elapsed_ms,
                issue_code="command_start_failed",
                summary=f"Command failed to start: {exc}",
            )

        elapsed_ms = int((time.monotonic() - started) * 1000)
        ok = completed.returncode == 0
        return ExternalCommandResult(
            ok=ok,
            command=command_list,
            cwd=str(cwd),
            exit_code=completed.returncode,
            stdout_excerpt=_excerpt(completed.stdout, stdout_excerpt_chars),
            stderr_excerpt=_excerpt(completed.stderr, stderr_excerpt_chars),
            elapsed_ms=elapsed_ms,
            issue_code=None if ok else "command_failed",
            summary=("Command completed successfully" if ok else f"Command failed with exit code {completed.returncode}"),
        )


def _excerpt(value: str | bytes | None, limit: int) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]"
