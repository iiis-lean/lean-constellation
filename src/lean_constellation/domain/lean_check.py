"""Domain models for Lean diagnostics and policy checks."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel


class LeanDiagnosticItem(StrictModel):
    severity: str
    message: str
    line: int | None = None
    column: int | None = None


class LeanDiagnostics(StrictModel):
    schema_version: int = 2
    repo_file_path: str | None = None
    passed: bool
    diagnostics: list[LeanDiagnosticItem] = Field(default_factory=list)
    summary: str
    raw_excerpt: str | None = None

class SorryAxiomOccurrence(StrictModel):
    kind: Literal[
        "sorry",
        "admit",
        "axiom",
        "opaque",
        "unsafe",
        "sorry_ax",
        "native_decide",
        "bv_decide",
        "reduce_bool",
        "unsafe_cast",
        "partial_def",
        "native_decide_linter_disabled",
        "axiom_declaration_injection",
        "run_cmd",
        "command_elaborator",
        "command_macro",
        "environment_mutation",
    ]
    line: int
    column: int
    excerpt: str


class SorryAxiomScan(StrictModel):
    contains_sorry: bool
    contains_admit: bool
    contains_axiom: bool
    contains_opaque: bool
    contains_unsafe: bool
    sorry_count: int
    admit_count: int
    axiom_count: int
    opaque_count: int
    unsafe_count: int
    occurrences: list[SorryAxiomOccurrence] = Field(default_factory=list)
    summary: str
    limitation: str


class LeanCheckSubject(StrictModel):
    repo_kind: Literal["native", "adapter"]
    stage: Literal["statement", "proof", "adapter_registration"]
    repo_file_path: str | None = None
    module: str | None = None
    declaration_name: str | None = None


class LeanCheckFingerprint(StrictModel):
    source_sha256: str
    environment_sha256: str | None = None
    upstream_revision: str | None = None


class LeanCheckImportOccurrence(StrictModel):
    command: Literal["import", "public import"]
    module: str
    line: int
    column: int


class ManagedImportCheck(StrictModel):
    checked: bool
    passed: bool | None = None
    unmanaged_imports: list[LeanCheckImportOccurrence] = Field(default_factory=list)
    summary: str


class DeclarationSoundnessWarning(StrictModel):
    line: int
    pattern: str


class DeclarationSoundnessEvidence(StrictModel):
    toolkit_tool: Literal["lsp.declaration_soundness_batch"]
    module: str
    declaration_name: str
    report_resolved: bool
    axioms: list[str] = Field(default_factory=list)
    warnings: list[DeclarationSoundnessWarning] = Field(default_factory=list)
    error_message: str | None = None
    raw_excerpt: str | None = None


class LeanCheckFinding(StrictModel):
    code: str
    severity: Literal["error", "warning", "info"]
    message: str
    line: int | None = None
    column: int | None = None


class LeanCheck(StrictModel):
    schema_version: Literal[1]
    status: Literal["passed", "failed"]
    policy: str
    allow_sorry: bool
    contains_sorry: bool
    contains_axiom: bool
    message: str
    subject: LeanCheckSubject
    fingerprint: LeanCheckFingerprint
    diagnostics: LeanDiagnostics
    scan: SorryAxiomScan
    managed_import_check: ManagedImportCheck | None = None
    declaration_soundness: DeclarationSoundnessEvidence | None = None
    findings: list[LeanCheckFinding] = Field(default_factory=list)

LeanDiagnosticItemView = LeanDiagnosticItem
LeanDiagnosticsView = LeanDiagnostics
SorryAxiomOccurrenceView = SorryAxiomOccurrence
SorryAxiomScanView = SorryAxiomScan
LeanCheckView = LeanCheck


def compact_lean_check(check: LeanCheck | None) -> dict[str, str] | None:
    if check is None:
        return None
    return {
        "status": check.status,
        "schema_version": str(check.schema_version),
        "policy": check.policy,
        "repo_kind": check.subject.repo_kind,
        "stage": check.subject.stage,
        "allow_sorry": str(check.allow_sorry),
        "contains_sorry": str(check.contains_sorry),
        "contains_axiom": str(check.contains_axiom),
        "contains_admit": str(check.scan.contains_admit),
        "contains_opaque": str(check.scan.contains_opaque),
        "contains_unsafe": str(check.scan.contains_unsafe),
        "declaration_soundness": str(check.declaration_soundness is not None),
        "finding_count": str(len(check.findings)),
        "message": check.message,
    }
