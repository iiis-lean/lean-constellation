"""Lean diagnostics and whole-file policy checks."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lean_constellation.domain.lean_check import (
    LeanCheck,
    LeanCheckView,
    LeanDiagnosticItem,
    LeanDiagnosticItemView,
    LeanDiagnostics,
    LeanDiagnosticsView,
    SorryAxiomOccurrence,
    SorryAxiomOccurrenceView,
    SorryAxiomScan,
    SorryAxiomScanView,
    compact_lean_check,
)
from lean_constellation.services.foundation import ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class LeanCheckComponent:
    """Build compact LeanCheck summaries from external diagnostics and policy scans."""

    _THEOREM_LIKE = {"theorem", "lemma", "proposition", "corollary"}
    _ERROR_SEVERITIES = {"error", "fatal"}
    _FORBIDDEN_WORD_RE = re.compile(r"(?<![A-Za-z0-9_'])(sorry|admit|axiom|opaque|unsafe)(?![A-Za-z0-9_'])")

    def __init__(
        self,
        runtime: LeanRuntimeServices,
    ) -> None:
        self.runtime = runtime

    def run_file_diagnostics(self, repo_root: Path, *, file_path: Path) -> ServiceResult[LeanDiagnosticsView]:
        resolved = self._resolve_file(repo_root, file_path)
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        repo, target, rel_file = resolved.value

        result = self.runtime.external.lean_toolchain.run_file_diagnostics(repo, target, rel_file=rel_file)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    result.issue_code or "lean_diagnostics_failed",
                    result.summary,
                    object_ref=str(target),
                    details={"provider": result.provider, "fallback_reason": result.fallback_reason or ""},
                )
            )
        diagnostics = result.diagnostics
        view = self._diagnostics_view(
            repo_root=repo,
            file_path=target,
            diagnostics=diagnostics,
            raw_excerpt=result.raw_excerpt,
            summary=result.summary,
        )
        return self.runtime.foundation.ok(view)

    def detect_sorry_axiom(self, file_text: str) -> ServiceResult[SorryAxiomScanView]:
        scan = self.runtime.external.lean_toolchain.scan_sorry_axiom(file_text)
        return self.runtime.foundation.ok(
            SorryAxiomScanView(
                contains_sorry=scan.contains_sorry,
                contains_admit=scan.contains_admit,
                contains_axiom=scan.contains_axiom,
                contains_opaque=scan.contains_opaque,
                contains_unsafe=scan.contains_unsafe,
                sorry_count=scan.sorry_count,
                admit_count=scan.admit_count,
                axiom_count=scan.axiom_count,
                opaque_count=scan.opaque_count,
                unsafe_count=scan.unsafe_count,
                occurrences=[SorryAxiomOccurrenceView(**occurrence.model_dump(mode="python")) for occurrence in scan.occurrences],
                summary=scan.summary,
                limitation=scan.limitation,
            )
        )

    def build_statement_lean_check(self, repo_root: Path, *, file_path: Path, decl_kind: str) -> ServiceResult[LeanCheckView]:
        diagnostics = self.run_file_diagnostics(repo_root, file_path=file_path)
        if not diagnostics.ok or diagnostics.value is None:
            return self.runtime.foundation.fail(diagnostics.issues)
        text = self._read_file(repo_root, file_path)
        if not text.ok or text.value is None:
            return self.runtime.foundation.fail(text.issues)
        scan = self.detect_sorry_axiom(text.value)
        if not scan.ok or scan.value is None:
            return self.runtime.foundation.fail(scan.issues)
        theorem_like = decl_kind.strip().lower() in self._THEOREM_LIKE
        return self.runtime.foundation.ok(
            self._build_check_view(
                policy="statement_formal",
                diagnostics=diagnostics.value,
                scan=scan.value,
                allow_sorry=theorem_like,
            )
        )

    def build_proof_lean_check(self, repo_root: Path, *, file_path: Path) -> ServiceResult[LeanCheckView]:
        diagnostics = self.run_file_diagnostics(repo_root, file_path=file_path)
        if not diagnostics.ok or diagnostics.value is None:
            return self.runtime.foundation.fail(diagnostics.issues)
        text = self._read_file(repo_root, file_path)
        if not text.ok or text.value is None:
            return self.runtime.foundation.fail(text.issues)
        scan = self.detect_sorry_axiom(text.value)
        if not scan.ok or scan.value is None:
            return self.runtime.foundation.fail(scan.issues)
        return self.runtime.foundation.ok(
            self._build_check_view(
                policy="proof_formal",
                diagnostics=diagnostics.value,
                scan=scan.value,
                allow_sorry=False,
            )
        )

    def build_trusted_adapter_check(
        self,
        repo_root: Path,
        *,
        module: str,
        code: str,
        theorem_like: bool,
    ) -> ServiceResult[LeanCheckView]:
        del theorem_like
        if not module or not module.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_module_missing", "Adapter trusted check requires module.", field="module"))
        if not code or not code.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("adapter_code_missing", "Adapter trusted check requires code.", field="code"))
        scan = self.detect_sorry_axiom(code)
        if not scan.ok or scan.value is None:
            return self.runtime.foundation.fail(scan.issues)
        diagnostics = LeanDiagnosticsView(
            repo_root=str(Path(repo_root)),
            file_path=None,
            passed=True,
            diagnostics=[],
            summary=f"Policy scan for compiler-confirmed upstream module {module}; source diagnostics are represented by the registered declaration identity check.",
        )
        return self.runtime.foundation.ok(
            self._build_check_view(
                policy="adapter_trusted_upstream",
                diagnostics=diagnostics,
                scan=scan.value,
                allow_sorry=False,
            )
        )

    def _build_check_view(
        self,
        *,
        policy: str,
        diagnostics: LeanDiagnosticsView,
        scan: SorryAxiomScanView,
        allow_sorry: bool,
    ) -> LeanCheckView:
        policy_issues: list[str] = []
        if not diagnostics.passed:
            policy_issues.append("diagnostics_failed")
        if scan.contains_axiom:
            policy_issues.append("contains_axiom")
        if scan.contains_admit:
            policy_issues.append("contains_admit")
        if scan.contains_opaque:
            policy_issues.append("contains_opaque")
        if scan.contains_unsafe:
            policy_issues.append("contains_unsafe")
        if scan.contains_sorry and not allow_sorry:
            policy_issues.append("contains_sorry")
        passed = not policy_issues
        message = "Lean check passed." if passed else "Lean check failed: " + ", ".join(policy_issues)
        return LeanCheckView(
            status="passed" if passed else "failed",
            policy=policy,
            allow_sorry=allow_sorry,
            contains_sorry=scan.contains_sorry,
            contains_axiom=scan.contains_axiom,
            message=message,
            diagnostics=diagnostics,
            scan=scan,
        )

    def _resolve_file(self, repo_root: Path, file_path: Path) -> ServiceResult[tuple[Path, Path, str]]:
        repo = Path(repo_root).expanduser().resolve(strict=False)
        if not repo.exists() or not repo.is_dir():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("repo_root_missing", f"Repo root does not exist: {repo}", field="repo_root"))
        raw_file = Path(file_path).expanduser()
        target = raw_file.resolve(strict=False) if raw_file.is_absolute() else (repo / raw_file).resolve(strict=False)
        try:
            self.runtime.foundation.layout.assert_within(repo, target)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("lean_file_outside_repo", str(exc), field="file_path"))
        if not target.exists() or not target.is_file():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("lean_file_missing", f"Lean file does not exist: {target}", field="file_path"))
        rel_file = target.relative_to(repo).as_posix()
        return self.runtime.foundation.ok((repo, target, rel_file))

    def _read_file(self, repo_root: Path, file_path: Path) -> ServiceResult[str]:
        resolved = self._resolve_file(repo_root, file_path)
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        _, target, _ = resolved.value
        try:
            return self.runtime.foundation.ok(target.read_text(encoding="utf-8"))
        except UnicodeDecodeError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("lean_file_not_utf8", str(exc), field="file_path"))
        except OSError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("lean_file_read_failed", str(exc), field="file_path"))

    def _diagnostics_view(
        self,
        *,
        repo_root: Path,
        file_path: Path | None,
        diagnostics: list[dict[str, Any]],
        raw_excerpt: str | None,
        summary: str,
    ) -> LeanDiagnosticsView:
        items = [self._diagnostic_item(item) for item in diagnostics]
        passed = not any(item.severity.lower() in self._ERROR_SEVERITIES for item in items)
        return LeanDiagnosticsView(
            repo_root=str(repo_root),
            file_path=str(file_path) if file_path else None,
            passed=passed,
            diagnostics=items,
            summary=summary,
            raw_excerpt=raw_excerpt,
        )

    def _diagnostic_item(self, item: dict[str, Any]) -> LeanDiagnosticItemView:
        severity = str(item.get("severity") or item.get("level") or item.get("kind") or "info").lower()
        message = item.get("message") or item.get("text") or item.get("data") or item.get("value") or repr(item)
        line = self._int_or_none(item.get("line") or self._get_nested(item, ("pos", "line")) or self._get_nested(item, ("range", "start", "line")))
        column = self._int_or_none(item.get("column") or item.get("col") or self._get_nested(item, ("pos", "column")) or self._get_nested(item, ("pos", "character")))
        return LeanDiagnosticItemView(severity=severity, message=str(message), line=line, column=column)

    def _diagnostics_from_command_output(self, stdout: str | None, stderr: str | None) -> list[LeanDiagnosticItemView]:
        diagnostics: list[LeanDiagnosticItemView] = []
        for text in [stdout, stderr]:
            if not text:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    diagnostics.append(self._diagnostic_item(payload))
        if diagnostics:
            return diagnostics
        fallback_text = (stderr or stdout or "").strip()
        if fallback_text:
            return [LeanDiagnosticItemView(severity="error", message=fallback_text[:1000])]
        return []

    def _strip_comments_and_strings(self, text: str) -> str:
        chars = list(text)
        i = 0
        block_depth = 0
        in_string = False
        while i < len(chars):
            current = chars[i]
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if in_string:
                if current != "\n":
                    chars[i] = " "
                if current == "\\" and i + 1 < len(chars):
                    if chars[i + 1] != "\n":
                        chars[i + 1] = " "
                    i += 2
                    continue
                if current == '"':
                    in_string = False
                i += 1
                continue
            if block_depth:
                if current == "/" and nxt == "-":
                    chars[i] = chars[i + 1] = " "
                    block_depth += 1
                    i += 2
                    continue
                if current == "-" and nxt == "/":
                    chars[i] = chars[i + 1] = " "
                    block_depth -= 1
                    i += 2
                    continue
                if current != "\n":
                    chars[i] = " "
                i += 1
                continue
            if current == "-" and nxt == "-":
                chars[i] = chars[i + 1] = " "
                i += 2
                while i < len(chars) and chars[i] != "\n":
                    chars[i] = " "
                    i += 1
                continue
            if current == "/" and nxt == "-":
                chars[i] = chars[i + 1] = " "
                block_depth = 1
                i += 2
                continue
            if current == '"':
                chars[i] = " "
                in_string = True
                i += 1
                continue
            i += 1
        return "".join(chars)

    def _line_col(self, text: str, offset: int) -> tuple[int, int]:
        line = text.count("\n", 0, offset) + 1
        last_newline = text.rfind("\n", 0, offset)
        column = offset + 1 if last_newline < 0 else offset - last_newline
        return line, column

    def _get_nested(self, value: dict[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = value
        for part in path:
            if not isinstance(current, dict):
                return None
            current = current.get(part)
        return current

    def _int_or_none(self, value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
