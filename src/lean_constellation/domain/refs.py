"""Stable reference models."""

from __future__ import annotations

from pydantic import Field, model_validator

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
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_line_range(self) -> SourceRef:
        if self.start_line > self.end_line:
            raise ValueError("source ref start_line must be <= end_line")
        return self


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
