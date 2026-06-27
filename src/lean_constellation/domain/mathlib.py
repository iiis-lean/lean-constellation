"""Repo-local Mathlib index truth models."""

from __future__ import annotations

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel


class MathlibModuleEntry(StrictModel):
    module: str
    summary: str | None = None
    important_decl_names: list[str] = Field(default_factory=list)
    note: str | None = None

    @field_validator("module")
    @classmethod
    def _non_empty_module(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("module must be non-empty")
        return value

    @field_validator("summary", "note")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("important_decl_names")
    @classmethod
    def _dedupe_important_decl_names(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        names: list[str] = []
        for item in value:
            name = item.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)
        return names


class MathlibDeclEntry(StrictModel):
    name: str
    module: str | None = None
    kind: str | None = None
    signature: str | None = None
    snippet: str | None = None
    summary: str | None = None
    note: str | None = None

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must be non-empty")
        return value

    @field_validator("module", "kind", "signature", "snippet", "summary", "note")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


class MathlibIndex(StrictModel):
    modules: dict[str, MathlibModuleEntry] = Field(default_factory=dict)
    declarations: dict[str, MathlibDeclEntry] = Field(default_factory=dict)
