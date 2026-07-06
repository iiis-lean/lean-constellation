"""Domain models for Lean diagnostics and policy checks."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel


class LeanDiagnosticItem(StrictModel):
    severity: str
    message: str
    line: int | None = None
    column: int | None = None


class LeanDiagnostics(StrictModel):
    repo_root: str
    file_path: str | None = None
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

    @model_validator(mode="before")
    @classmethod
    def _from_compact_legacy_summary(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        if "diagnostics" in value and "scan" in value:
            return value
        status = str(value.get("status") or value.get("passed") or "").strip().lower()
        passed = status in {"passed", "true", "1", "yes"}
        contains_sorry = _truthy(value.get("contains_sorry"))
        contains_axiom = _truthy(value.get("contains_axiom"))
        contains_admit = _truthy(value.get("contains_admit"))
        contains_opaque = _truthy(value.get("contains_opaque"))
        contains_unsafe = _truthy(value.get("contains_unsafe"))
        policy = str(value.get("policy") or "legacy_compact_check")
        message = str(value.get("message") or ("Lean check passed." if passed else "Lean check failed."))
        return {
            "status": "passed" if passed else "failed",
            "policy": policy,
            "allow_sorry": _truthy(value.get("allow_sorry")),
            "contains_sorry": contains_sorry,
            "contains_axiom": contains_axiom,
            "message": message,
            "diagnostics": {
                "repo_root": str(value.get("repo_root") or ""),
                "file_path": value.get("file_path"),
                "passed": passed,
                "diagnostics": [],
                "summary": str(value.get("diagnostics_summary") or message),
            },
            "scan": {
                "contains_sorry": contains_sorry,
                "contains_admit": contains_admit,
                "contains_axiom": contains_axiom,
                "contains_opaque": contains_opaque,
                "contains_unsafe": contains_unsafe,
                "sorry_count": 1 if contains_sorry else 0,
                "admit_count": 1 if contains_admit else 0,
                "axiom_count": 1 if contains_axiom else 0,
                "opaque_count": 1 if contains_opaque else 0,
                "unsafe_count": 1 if contains_unsafe else 0,
                "occurrences": [],
                "summary": str(value.get("scan_summary") or message),
                "limitation": "Imported from compact legacy check summary.",
            },
        }


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


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "passed"}
