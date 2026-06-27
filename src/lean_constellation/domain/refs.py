"""Stable reference models."""

from __future__ import annotations

from lean_constellation.domain.common import StrictModel


class DeclRef(StrictModel):
    repo: str | None = None
    node: str = "Main"
    name: str
    revision: int = 1


class NodeRef(StrictModel):
    repo: str | None = None
    node: str = "Main"


class SourceRef(StrictModel):
    path: str
    start_line: int | None = None
    end_line: int | None = None


class ResourceRef(StrictModel):
    resource_key: str
    locator: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class MathlibRef(StrictModel):
    name: str
    module: str | None = None


class MaterialRef(StrictModel):
    kind: str
    ref: SourceRef | ResourceRef
