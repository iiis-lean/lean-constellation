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
    kind: Literal["sorry", "admit", "axiom", "opaque", "unsafe"]
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


class LeanCheck(StrictModel):
    status: Literal["passed", "failed"]
    policy: str
    allow_sorry: bool
    contains_sorry: bool
    contains_axiom: bool
    message: str
    diagnostics: LeanDiagnostics
    scan: SorryAxiomScan

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
        "policy": check.policy,
        "allow_sorry": str(check.allow_sorry),
        "contains_sorry": str(check.contains_sorry),
        "contains_axiom": str(check.contains_axiom),
        "contains_admit": str(check.scan.contains_admit),
        "contains_opaque": str(check.scan.contains_opaque),
        "contains_unsafe": str(check.scan.contains_unsafe),
        "message": check.message,
    }
