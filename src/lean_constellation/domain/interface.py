"""Interface-related domain models."""

from __future__ import annotations

from enum import StrEnum
from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef, MaterialRef


class DeclKind(StrEnum):
    TYPE = "type"
    DEFINITION = "definition"
    THEOREM = "theorem"
    LEMMA = "lemma"
    INSTANCE = "instance"
    STRUCTURE = "structure"
    CLASS = "class"
    OTHER = "other"


_DECL_KIND_ALIASES: dict[str, DeclKind] = {
    "abbrev": DeclKind.DEFINITION,
    "inductive": DeclKind.TYPE,
}


def normalize_decl_kind(kind: DeclKind | str) -> DeclKind | None:
    """Normalize current declaration-kind evidence for interface matching."""

    if isinstance(kind, DeclKind):
        return kind
    normalized = kind.strip().lower()
    alias = _DECL_KIND_ALIASES.get(normalized)
    if alias is not None:
        return alias
    try:
        return DeclKind(normalized)
    except ValueError:
        return None


def decl_kind_compatible(
    required: DeclKind | str,
    actual: DeclKind | str,
) -> bool:
    """Apply the current-schema interface/declaration kind policy."""

    required_kind = normalize_decl_kind(required)
    actual_kind = normalize_decl_kind(actual)
    if required_kind is None or actual_kind is None:
        return False
    if required_kind is DeclKind.TYPE:
        return actual_kind in {DeclKind.TYPE, DeclKind.STRUCTURE, DeclKind.CLASS}
    if required_kind == actual_kind:
        return True
    return {required_kind, actual_kind} == {
        DeclKind.THEOREM,
        DeclKind.LEMMA,
    }


def exact_interface_lean_decl_name(interface_name: str) -> str | None:
    """Return the exact Lean identity required by a qualified interface name."""

    normalized = interface_name.strip()
    if normalized.startswith("_root_."):
        normalized = normalized[len("_root_.") :]
    return normalized if "." in normalized else None


INTERFACE_KIND_DESCRIPTION = (
    "Required interface category: type accepts structure, class or inductive declarations "
    "(including inductively defined predicates); structure and class require their exact kind. "
    "definition accepts def/abbrev; theorem and lemma are mutually compatible. "
    "A def returning Type is still a definition. other is not a wildcard. "
    "Kind compatibility does not establish equality of definitions or constructors."
)


class DeclInterface(StrictModel):
    name: str
    kind: DeclKind = Field(description=INTERFACE_KIND_DESCRIPTION)
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
