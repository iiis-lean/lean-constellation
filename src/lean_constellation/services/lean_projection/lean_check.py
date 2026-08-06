"""Lean diagnostics and whole-file policy checks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lean_constellation.domain.lean_check import (
    DeclarationSoundnessEvidence,
    DeclarationSoundnessWarning,
    LeanCheckFinding,
    LeanCheckFingerprint,
    LeanCheckImportOccurrence,
    LeanCheckSubject,
    LeanCheckView,
    LeanDiagnosticItemView,
    LeanDiagnosticsView,
    ManagedImportCheck,
    SorryAxiomOccurrenceView,
    SorryAxiomScanView,
)
from lean_constellation.services.external_clients.lean_toolchain import (
    ToolchainDeclarationSoundnessItem,
)
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.lean_projection.annotation import target_marker_line_numbers
from lean_constellation.services.lean_projection.managed_file import (
    DECLARATION_SOURCE_BEGIN,
    MANAGED_IMPORTS_BEGIN,
    MANAGED_IMPORTS_END,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class LeanCheckComponent:
    """Build compact LeanCheck summaries from external diagnostics and policy scans."""

    _THEOREM_LIKE = {"theorem", "lemma", "proposition", "corollary"}
    _ERROR_SEVERITIES = {"error", "fatal"}
    _LONG_LINE_DIAGNOSTIC_MARKERS = ("linter.style.longline", "line exceeds the 100 character limit")
    _LONG_LINE_DISABLE_RE = re.compile(r"(?m)^\s*set_option\s+linter\.style\.longLine\s+false\b")
    _UNUSED_DECIDABLE_DISABLE_RE = re.compile(
        r"(?m)^\s*set_option\s+linter\.unusedDecidableInType\s+false\b"
    )
    _UNUSED_DECIDABLE_NOLINT_RE = re.compile(
        r"(?m)^\s*@\[\s*nolint\s+unusedDecidableInType\s*\]"
    )
    _FORBIDDEN_WORD_RE = re.compile(r"(?<![A-Za-z0-9_'])(sorry|admit|axiom|opaque|unsafe)(?![A-Za-z0-9_'])")
    _ALLOWED_RECURSIVE_AXIOMS = {"Classical.choice", "propext", "Quot.sound"}
    _SOURCE_ESCAPE_ERROR_CODES = {
        "sorry_ax": "decl_sorry_ax_forbidden",
        "native_decide": "decl_native_decide_forbidden",
        "bv_decide": "decl_bv_decide_forbidden",
        "reduce_bool": "decl_reduce_bool_forbidden",
        "unsafe_cast": "decl_unsafe_cast_forbidden",
        "partial_def": "decl_partial_def_forbidden",
        "native_decide_linter_disabled": "decl_native_decide_linter_disable_forbidden",
        "axiom_declaration_injection": "decl_axiom_injection_forbidden",
    }
    _SOURCE_ESCAPE_WARNING_CODES = {
        "run_cmd": "decl_run_cmd_review_required",
        "command_elaborator": "decl_command_elaborator_review_required",
        "command_macro": "decl_command_macro_review_required",
        "environment_mutation": "decl_environment_mutation_review_required",
    }
    _UNMANAGED_IMPORT_RE = re.compile(
        r"(?m)^[ \t]*(?P<command>(?:public[ \t]+)?import)\b(?P<modules>[^\n]*)"
    )

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
                subject=self._native_subject(diagnostics.value, stage="statement"),
                fingerprint=self._fingerprint(repo_root, text.value),
                diagnostics=diagnostics.value,
                scan=scan.value,
                allow_sorry=theorem_like,
                source_text=text.value,
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
                subject=self._native_subject(diagnostics.value, stage="proof"),
                fingerprint=self._fingerprint(repo_root, text.value),
                diagnostics=diagnostics.value,
                scan=scan.value,
                allow_sorry=False,
                source_text=text.value,
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
            repo_file_path=None,
            passed=True,
            diagnostics=[],
            summary=f"Policy scan for compiler-confirmed upstream module {module}; source diagnostics are represented by the registered declaration identity check.",
        )
        return self.runtime.foundation.ok(
            self._build_check_view(
                policy="adapter_trusted_upstream",
                subject=LeanCheckSubject(
                    repo_kind="adapter",
                    stage="adapter_registration",
                    module=module.strip(),
                ),
                fingerprint=self._fingerprint(repo_root, code),
                diagnostics=diagnostics,
                scan=scan.value,
                allow_sorry=False,
                source_text=code,
            )
        )

    def build_adapter_declaration_check(
        self,
        repo_root: Path,
        *,
        module: str,
        declaration_name: str,
        code: str,
        theorem_like: bool,
        soundness: ToolchainDeclarationSoundnessItem,
        raw_excerpt: str | None = None,
        upstream_revision: str | None = None,
    ) -> ServiceResult[LeanCheckView]:
        if soundness.module != module or soundness.declaration_name != declaration_name:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_soundness_identity_mismatch",
                    "Declaration soundness evidence does not match the Adapter declaration identity.",
                    object_ref=declaration_name,
                    current=f"{soundness.module}::{soundness.declaration_name}",
                    expected=f"{module}::{declaration_name}",
                )
            )
        scan = self.detect_sorry_axiom(code)
        if not scan.ok or scan.value is None:
            return self.runtime.foundation.fail(scan.issues)
        recursive_sorry = any(self.is_sorry_axiom(axiom) for axiom in soundness.axioms)
        forbidden_axioms = sorted(
            axiom
            for axiom in soundness.axioms
            if not self.is_sorry_axiom(axiom)
            and axiom not in self._ALLOWED_RECURSIVE_AXIOMS
        )
        diagnostics = LeanDiagnosticsView(
            repo_file_path=None,
            passed=soundness.success,
            diagnostics=[],
            summary=(
                f"Recursive declaration soundness report for {declaration_name}."
                if soundness.success
                else soundness.error_message or "Recursive declaration soundness report was unresolved."
            ),
            raw_excerpt=raw_excerpt,
        )
        check = self._build_check_view(
            policy="adapter_declaration_soundness",
            subject=LeanCheckSubject(
                repo_kind="adapter",
                stage="adapter_registration",
                module=module,
                declaration_name=declaration_name,
            ),
            fingerprint=self._fingerprint(
                repo_root,
                code,
                upstream_revision=upstream_revision,
            ),
            diagnostics=diagnostics,
            scan=scan.value,
            allow_sorry=theorem_like and recursive_sorry,
            source_text=code,
        )
        findings = list(check.findings)
        if recursive_sorry:
            findings.append(
                LeanCheckFinding(
                    code="recursive_sorry_axiom",
                    severity="warning" if theorem_like else "error",
                    message="The declaration recursively depends on sorryAx.",
                )
            )
        allowed_axioms = sorted(
            axiom for axiom in soundness.axioms if axiom in self._ALLOWED_RECURSIVE_AXIOMS
        )
        if allowed_axioms:
            findings.append(
                LeanCheckFinding(
                    code="allowed_foundational_axioms",
                    severity="info",
                    message="Allowed foundational axioms: " + ", ".join(allowed_axioms),
                )
            )
        if forbidden_axioms:
            findings.append(
                LeanCheckFinding(
                    code="forbidden_recursive_axioms",
                    severity="error",
                    message="Forbidden recursive axioms: " + ", ".join(forbidden_axioms),
                )
            )
        policy_failed = (
            check.status == "failed"
            or not soundness.success
            or bool(forbidden_axioms)
            or (recursive_sorry and not theorem_like)
        )
        evidence = DeclarationSoundnessEvidence(
            toolkit_tool="lsp.declaration_soundness_batch",
            module=module,
            declaration_name=declaration_name,
            report_resolved=soundness.success,
            axioms=list(soundness.axioms),
            warnings=[
                DeclarationSoundnessWarning(
                    line=warning.line,
                    pattern=warning.pattern,
                )
                for warning in soundness.warnings
            ],
            error_message=soundness.error_message,
            raw_excerpt=raw_excerpt,
        )
        return self.runtime.foundation.ok(
            check.model_copy(
                update={
                    "status": "failed" if policy_failed else "passed",
                    "message": (
                        "Adapter declaration soundness check failed."
                        if policy_failed
                        else "Adapter declaration soundness check passed."
                    ),
                    "declaration_soundness": evidence,
                    "findings": findings,
                }
            )
        )

    def is_sorry_axiom(self, axiom: str) -> bool:
        return axiom == "sorryAx" or axiom.endswith(".sorryAx")

    def adapter_declaration_check_is_current(
        self,
        repo_root: Path,
        *,
        check: LeanCheckView | None,
        module: str,
        declaration_name: str,
        code: str,
        upstream_revision: str | None,
    ) -> bool:
        if check is None or check.status != "passed":
            return False
        if (
            check.subject.repo_kind != "adapter"
            or check.subject.stage != "adapter_registration"
            or check.subject.module != module
            or check.subject.declaration_name != declaration_name
        ):
            return False
        evidence = check.declaration_soundness
        if (
            evidence is None
            or not evidence.report_resolved
            or evidence.module != module
            or evidence.declaration_name != declaration_name
        ):
            return False
        return check.fingerprint == self._fingerprint(
            repo_root,
            code,
            upstream_revision=upstream_revision,
        )

    def _build_check_view(
        self,
        *,
        policy: str,
        subject: LeanCheckSubject,
        fingerprint: LeanCheckFingerprint,
        diagnostics: LeanDiagnosticsView,
        scan: SorryAxiomScanView,
        allow_sorry: bool,
        source_text: str,
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
        managed_import_lines = self._managed_import_lines(source_text)
        target_marker_lines = target_marker_line_numbers(source_text)
        if any(
            any(marker in item.message.lower() for marker in self._LONG_LINE_DIAGNOSTIC_MARKERS)
            and item.line not in managed_import_lines
            and item.line not in target_marker_lines
            for item in diagnostics.diagnostics
        ):
            policy_issues.append("linter_style_long_line")
        if self._LONG_LINE_DISABLE_RE.search(source_text):
            policy_issues.append("linter_style_long_line_disabled")
        if self._UNUSED_DECIDABLE_DISABLE_RE.search(source_text):
            policy_issues.append("linter_unused_decidable_in_type_disabled")
        if self._UNUSED_DECIDABLE_NOLINT_RE.search(source_text):
            policy_issues.append("linter_unused_decidable_in_type_suppressed")
        managed_import_check = self._managed_import_check(source_text) if subject.repo_kind == "native" else None
        if managed_import_check is not None and managed_import_check.checked and not managed_import_check.passed:
            policy_issues.append("decl_unmanaged_import_forbidden")
        source_findings: list[LeanCheckFinding] = []
        if subject.repo_kind == "native":
            for occurrence in scan.occurrences:
                error_code = self._SOURCE_ESCAPE_ERROR_CODES.get(occurrence.kind)
                warning_code = self._SOURCE_ESCAPE_WARNING_CODES.get(occurrence.kind)
                if error_code is not None:
                    policy_issues.append(error_code)
                    source_findings.append(
                        LeanCheckFinding(
                            code=error_code,
                            severity="error",
                            message=f"Native source policy rejected {occurrence.kind}.",
                            line=occurrence.line,
                            column=occurrence.column,
                        )
                    )
                elif warning_code is not None:
                    source_findings.append(
                        LeanCheckFinding(
                            code=warning_code,
                            severity="warning",
                            message=f"Native source uses {occurrence.kind}; Reviewer inspection is required.",
                            line=occurrence.line,
                            column=occurrence.column,
                        )
                    )
        policy_issues = list(dict.fromkeys(policy_issues))
        passed = not policy_issues
        message = "Lean check passed." if passed else "Lean check failed: " + ", ".join(policy_issues)
        return LeanCheckView(
            schema_version=1,
            status="passed" if passed else "failed",
            policy=policy,
            allow_sorry=allow_sorry,
            contains_sorry=scan.contains_sorry,
            contains_axiom=scan.contains_axiom,
            message=message,
            subject=subject,
            fingerprint=fingerprint,
            diagnostics=diagnostics,
            scan=scan,
            managed_import_check=managed_import_check,
            findings=[
                LeanCheckFinding(
                    code=issue,
                    severity="error",
                    message=f"Lean policy rejected evidence: {issue}.",
                )
                for issue in policy_issues
                if issue not in self._SOURCE_ESCAPE_ERROR_CODES.values()
            ]
            + source_findings
            + self._managed_import_findings(managed_import_check),
        )

    def _managed_import_check(self, source_text: str) -> ManagedImportCheck:
        marker_counts = {
            MANAGED_IMPORTS_BEGIN: source_text.count(MANAGED_IMPORTS_BEGIN),
            MANAGED_IMPORTS_END: source_text.count(MANAGED_IMPORTS_END),
            DECLARATION_SOURCE_BEGIN: source_text.count(DECLARATION_SOURCE_BEGIN),
        }
        if any(count != 1 for count in marker_counts.values()):
            return ManagedImportCheck(
                checked=False,
                summary="Managed import policy is not applicable to a non-managed Lean file.",
            )
        source_offset = source_text.index(DECLARATION_SOURCE_BEGIN) + len(DECLARATION_SOURCE_BEGIN)
        sanitized = self._strip_comments_and_strings(source_text)
        occurrences: list[LeanCheckImportOccurrence] = []
        lines = source_text.splitlines() or [""]
        for match in self._UNMANAGED_IMPORT_RE.finditer(sanitized, source_offset):
            command = " ".join(match.group("command").split())
            line, column = self._line_col(sanitized, match.start("command"))
            modules = match.group("modules").strip()
            if not modules and line - 1 < len(lines):
                modules = lines[line - 1].strip()
            occurrences.append(
                LeanCheckImportOccurrence(
                    command=command,  # type: ignore[arg-type]
                    module=modules,
                    line=line,
                    column=column,
                )
            )
        return ManagedImportCheck(
            checked=True,
            passed=not occurrences,
            unmanaged_imports=occurrences,
            summary=(
                "No imports occur after the declaration source marker."
                if not occurrences
                else f"Found {len(occurrences)} import command(s) after the declaration source marker."
            ),
        )

    def _managed_import_findings(
        self,
        check: ManagedImportCheck | None,
    ) -> list[LeanCheckFinding]:
        if check is None or not check.checked:
            return []
        return [
            LeanCheckFinding(
                code="decl_unmanaged_import_forbidden",
                severity="error",
                message=f"Import command after the declaration source marker: {item.module}.",
                line=item.line,
                column=item.column,
            )
            for item in check.unmanaged_imports
        ]

    def _native_subject(
        self,
        diagnostics: LeanDiagnosticsView,
        *,
        stage: Literal["statement", "proof"],
    ) -> LeanCheckSubject:
        rel_file = diagnostics.repo_file_path
        module = None
        if rel_file and rel_file.endswith(".lean"):
            module = rel_file.removesuffix(".lean").replace("/", ".")
        return LeanCheckSubject(
            repo_kind="native",
            stage=stage,
            repo_file_path=rel_file,
            module=module,
        )

    def _fingerprint(
        self,
        repo_root: Path,
        source_text: str,
        *,
        upstream_revision: str | None = None,
    ) -> LeanCheckFingerprint:
        repo = Path(repo_root)
        environment = hashlib.sha256()
        has_environment = False
        for name in ("lean-toolchain", "lakefile.toml", "lake-manifest.json"):
            path = repo / name
            if not path.is_file():
                continue
            has_environment = True
            environment.update(name.encode("utf-8"))
            environment.update(b"\0")
            environment.update(path.read_bytes())
            environment.update(b"\0")
        return LeanCheckFingerprint(
            source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            environment_sha256=environment.hexdigest() if has_environment else None,
            upstream_revision=upstream_revision,
        )

    def _managed_import_lines(self, source_text: str) -> set[int]:
        """Return 1-based system-owned import lines that agents cannot reformat."""
        lines = source_text.splitlines()
        try:
            begin = lines.index(MANAGED_IMPORTS_BEGIN)
            end = lines.index(MANAGED_IMPORTS_END, begin + 1)
        except ValueError:
            return set()
        return {
            line_number
            for line_number, line in enumerate(lines[begin + 1 : end], start=begin + 2)
            if line.strip().startswith("import ")
        }

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
            repo_file_path=(
                file_path.resolve().relative_to(repo_root.resolve()).as_posix()
                if file_path
                else None
            ),
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
