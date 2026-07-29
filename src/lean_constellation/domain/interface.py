"""Interface-related domain models."""

from __future__ import annotations

from enum import StrEnum
from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef, MaterialRef


class DeclKind(StrEnum):
    DEFINITION = "definition"
    THEOREM = "theorem"
    LEMMA = "lemma"
    INSTANCE = "instance"
    STRUCTURE = "structure"
    CLASS = "class"
    OTHER = "other"


def exact_interface_lean_decl_name(interface_name: str) -> str | None:
    """Return the exact Lean identity required by a qualified interface name."""

    normalized = interface_name.strip()
    if normalized.startswith("_root_."):
        normalized = normalized[len("_root_.") :]
    return normalized if "." in normalized else None


class DeclInterface(StrictModel):
    name: str
    kind: DeclKind
    summary: str
    source_refs: list[MaterialRef] = Field(default_factory=list)
    expected_statement_lean_code: str | None = None
    bound_decl: DeclRef | None = None
    note: str | None = None

    @field_validator("expected_statement_lean_code")
    @classmethod
    def _normalize_expected_statement_lean_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("expected_statement_lean_code must be non-empty when provided")
        return normalized
