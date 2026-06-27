"""Interface-related domain models."""

from __future__ import annotations

from enum import StrEnum
from pydantic import Field

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


class DeclInterface(StrictModel):
    name: str
    kind: DeclKind
    summary: str
    source_refs: list[MaterialRef] = Field(default_factory=list)
    bound_decl: DeclRef | None = None
    note: str | None = None
