"""Lake and Lean command wrappers."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Mapping

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients.process import (
    CommandRunner,
    ExternalCommandResult,
    SubprocessCommandRunner,
)


class LakeCommandClientConfig(StrictModel):
    lake_bin: str = "lake"
    lean_bin: str = "lean"
    elan_bin: str = "elan"
    prefer_elan: bool = True
    use_lake_env_for_lean: bool = True
    timeout_seconds: int = 300
    stdout_excerpt_chars: int = 8000
    stderr_excerpt_chars: int = 8000


class LakeCommandSummaryView(StrictModel):
    ok: bool
    command: list[str]
    summary: str
    exit_code: int | None = None
    timed_out: bool = False
    stderr_excerpt: str | None = None


class LeanCheckSummaryView(StrictModel):
    ok: bool
    module: str | None = None
    command: list[str] = Field(default_factory=list)
    summary: str
    diagnostics_excerpt: str | None = None
    issue_code: str | None = None


class LakeCommandClient:
    def __init__(
        self,
        config: LakeCommandClientConfig | None = None,
        *,
        runner: CommandRunner | None = None,
    ) -> None:
        self.config = config or LakeCommandClientConfig()
        self.runner = runner or SubprocessCommandRunner()

    def run_lake_update(
        self,
        repo_root: Path,
        packages: list[str] | None = None,
        timeout: int | None = None,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExternalCommandResult:
        command = [self.config.lake_bin, "update", *(packages or [])]
        return self.run_command(
            repo_root,
            command,
            timeout=timeout_seconds or timeout,
            env=env,
        )

    def run_lake_build(
        self,
        repo_root: Path,
        target: str | None = None,
        targets: list[str] | None = None,
        timeout: int | None = None,
        timeout_seconds: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExternalCommandResult:
        command = [self.config.lake_bin, "build"]
        if targets:
            command.extend(targets)
        elif target:
            command.append(target)
        return self.run_command(
            repo_root,
            command,
            timeout=timeout_seconds or timeout,
            env=env,
        )

    def run_minimal_import_check(
        self,
        repo_root: Path,
        module: str,
        timeout: int | None = None,
        timeout_seconds: int | None = None,
    ) -> LeanCheckSummaryView:
        invalid_module = self._invalid_import_module_view(module=module)
        if invalid_module is not None:
            return invalid_module
        repo_root = Path(repo_root)
        with tempfile.TemporaryDirectory(prefix="lean-constellation-import-check-") as temp_dir:
            temp_file = Path(temp_dir) / "ImportCheck.lean"
            temp_file.write_text(f"import {module}\n", encoding="utf-8")
            command = [self.config.lean_bin, "--json", str(temp_file)]
            if self.config.use_lake_env_for_lean:
                command = [self.config.lake_bin, "env", *command]
            result = self.run_command(repo_root, command, timeout=timeout_seconds or timeout)
        return LeanCheckSummaryView(
            ok=result.ok,
            module=module,
            command=result.command,
            summary=result.summary or ("Import check passed" if result.ok else "Import check failed"),
            diagnostics_excerpt=result.stderr_excerpt or result.stdout_excerpt,
            issue_code=result.issue_code,
        )

    def run_lake_env_lean(
        self,
        *,
        repo_root: Path,
        rel_file: str,
        json: bool = True,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        command = [self.config.lake_bin, "env", self.config.lean_bin]
        if json:
            command.append("--json")
        command.append(rel_file)
        return self.run_command(repo_root, command, timeout=timeout_seconds)

    def run_snippet_check(
        self,
        *,
        repo_root: Path,
        imports: list[str],
        code: str,
        timeout_seconds: int | None = None,
    ) -> LeanCheckSummaryView:
        for module in imports:
            invalid_module = self._invalid_import_module_view(module=module)
            if invalid_module is not None:
                return invalid_module
        with tempfile.TemporaryDirectory(prefix="lean-constellation-snippet-") as temp_dir:
            temp_file = Path(temp_dir) / "Snippet.lean"
            import_lines = "".join(f"import {module}\n" for module in imports)
            temp_file.write_text(f"{import_lines}\n{code}\n", encoding="utf-8")
            command = [self.config.lean_bin, "--json", str(temp_file)]
            if self.config.use_lake_env_for_lean:
                command = [self.config.lake_bin, "env", *command]
            result = self.run_command(repo_root, command, timeout=timeout_seconds)
        return LeanCheckSummaryView(
            ok=result.ok,
            command=result.command,
            summary=result.summary or ("Snippet check passed" if result.ok else "Snippet check failed"),
            diagnostics_excerpt=result.stderr_excerpt or result.stdout_excerpt,
            issue_code=result.issue_code,
        )

    def run_command(
        self,
        repo_root: Path,
        argv: list[str],
        timeout: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExternalCommandResult:
        repo_root = Path(repo_root)
        if not argv:
            return ExternalCommandResult(
                ok=False,
                command=[],
                cwd=str(repo_root),
                exit_code=None,
                issue_code="empty_command",
                summary="Command argv must not be empty",
            )
        if not repo_root.exists() or not repo_root.is_dir():
            return ExternalCommandResult(
                ok=False,
                command=argv,
                cwd=str(repo_root),
                exit_code=None,
                issue_code="missing_repo_root",
                summary=f"Repo root does not exist: {repo_root}",
            )
        kwargs = {
            "cwd": repo_root,
            "timeout_seconds": timeout or self.config.timeout_seconds,
            "stdout_excerpt_chars": self.config.stdout_excerpt_chars,
            "stderr_excerpt_chars": self.config.stderr_excerpt_chars,
        }
        if env is not None:
            kwargs["env"] = env
        return self.runner.run(argv, **kwargs)

    def summarize_command_result(self, result: ExternalCommandResult) -> LakeCommandSummaryView:
        return LakeCommandSummaryView(
            ok=result.ok,
            command=result.command,
            summary=result.summary or ("Command succeeded" if result.ok else "Command failed"),
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stderr_excerpt=result.stderr_excerpt,
        )

    def _invalid_import_module_view(self, *, module: str) -> LeanCheckSummaryView | None:
        if self._is_valid_import_module(module):
            return None
        return LeanCheckSummaryView(
            ok=False,
            module=module,
            command=[],
            summary=f"Invalid Lean import module: {module!r}",
            issue_code="invalid_import_module",
        )

    def _is_valid_import_module(self, module: str) -> bool:
        return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", module))
