"""Native Lean/Lake project configuration models."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator, model_validator

from lean_constellation.domain.common import StrictModel


class LocalLakePackageCacheConfig(StrictModel):
    """Local Lake package cache used to reuse heavy Mathlib dependencies."""

    cache_project_root: Path | None = None
    packages_root: Path | None = None
    manifest_path: Path | None = None
    link_mode: Literal["symlink"] = "symlink"
    package_names: list[str] | None = None
    require_all_packages: bool = True

    @field_validator("cache_project_root", "packages_root", "manifest_path", mode="before")
    @classmethod
    def _coerce_path(cls, value: Any) -> Path | None:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value)).expanduser()

    @field_validator("package_names", mode="before")
    @classmethod
    def _coerce_package_names(cls, value: Any) -> list[str] | None:
        if value is None or isinstance(value, list):
            return value
        if isinstance(value, str):
            names = [item.strip() for item in value.split(",")]
            return [item for item in names if item]
        return value

    @model_validator(mode="after")
    def _derive_paths(self) -> "LocalLakePackageCacheConfig":
        if self.cache_project_root is not None:
            if self.packages_root is None:
                self.packages_root = self.cache_project_root / ".lake" / "packages"
            if self.manifest_path is None:
                self.manifest_path = self.cache_project_root / "lake-manifest.json"
        return self


class NativeLakeProjectConfig(StrictModel):
    """Configuration for generated native Lean repositories."""

    lean_version: str = "4.28.0"
    lean_toolchain: str | None = None
    mathlib_enabled: bool = True
    mathlib_scope: str = "leanprover-community"
    mathlib_rev: str | None = None
    local_package_cache: LocalLakePackageCacheConfig | None = None

    @field_validator("lean_version", "lean_toolchain", "mathlib_scope", "mathlib_rev")
    @classmethod
    def _strip_optional_str(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("native Lake project string fields must be non-empty when provided")
        return stripped

    @model_validator(mode="after")
    def _derive_toolchain_and_mathlib_rev(self) -> "NativeLakeProjectConfig":
        if self.lean_toolchain is None:
            self.lean_toolchain = f"leanprover/lean4:v{self.lean_version}"
        if self.mathlib_enabled and self.mathlib_rev is None:
            self.mathlib_rev = f"v{self.lean_version}"
        return self


__all__ = [
    "LocalLakePackageCacheConfig",
    "NativeLakeProjectConfig",
]
