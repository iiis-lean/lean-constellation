"""Domain models for Lean diagnostics and policy checks."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel


class LeanDiagnosticItem(StrictModel):
    severity: str
    message: str
    line: int | None = None
    column: int | None = None


class LeanDiagnostics(StrictModel):
    schema_version: Literal[2] = 2
    repo_file_path: str | None = None
    passed: bool
    diagnostics: list[LeanDiagnosticItem] = Field(default_factory=list)
    summary: str
    raw_excerpt: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_paths(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        repo_root = migrated.pop("repo_root", None)
        file_path = migrated.pop("file_path", None)
        if "repo_file_path" not in migrated and file_path is not None:
            candidate = Path(str(file_path))
            if candidate.is_absolute():
                if repo_root is None:
                    raise ValueError(
                        "absolute legacy diagnostic file_path requires repo_root"
                    )
                root = Path(str(repo_root)).resolve()
                resolved = candidate.resolve()
                if not resolved.is_relative_to(root):
                    raise ValueError(
                        "legacy diagnostic file_path is outside repo_root"
                    )
                migrated["repo_file_path"] = resolved.relative_to(root).as_posix()
            else:
                if any(part in {"", ".", ".."} for part in candidate.parts):
                    raise ValueError("diagnostic repo_file_path is unsafe")
                migrated["repo_file_path"] = candidate.as_posix()
        migrated["schema_version"] = 2
        return migrated


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
