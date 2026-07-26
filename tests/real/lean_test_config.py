"""Explicit Lean version policy for real and Runtime Matrix tests."""

from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import re

from lean_constellation.domain.lake_project import (
    LocalLakePackageCacheConfig,
    NativeLakeProjectConfig,
)


DEFAULT_TEST_LEAN_VERSION = "4.32.0"
TEST_LEAN_VERSION_ENV = "LEAN_CONSTELLATION_TEST_LEAN_VERSION"
_FIXED_LEAN_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")


def configured_test_lean_version(environ: Mapping[str, str] | None = None) -> str:
    """Return the fixed test Lean version, allowing only an explicit version override."""

    source = os.environ if environ is None else environ
    value = source.get(TEST_LEAN_VERSION_ENV, DEFAULT_TEST_LEAN_VERSION).strip()
    if not _FIXED_LEAN_VERSION.fullmatch(value):
        raise ValueError(
            f"{TEST_LEAN_VERSION_ENV} must be a fixed Lean version such as "
            f"{DEFAULT_TEST_LEAN_VERSION}; aliases such as stable/latest are not allowed."
        )
    return value


def configured_test_lean_toolchain(environ: Mapping[str, str] | None = None) -> str:
    """Return the exact Elan toolchain identifier used by generated test repos."""

    return f"leanprover/lean4:v{configured_test_lean_version(environ)}"


def write_test_lean_toolchain(
    repo_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Pin an internally generated test repo to the configured fixed Lean version."""

    path = Path(repo_root) / "lean-toolchain"
    path.write_text(f"{configured_test_lean_toolchain(environ)}\n", encoding="utf-8")
    return path


def configured_test_native_lake_project(
    *,
    template_root: Path | None = None,
    mathlib_enabled: bool = True,
    environ: Mapping[str, str] | None = None,
) -> NativeLakeProjectConfig:
    """Build the native-project config used by real tests without changing production defaults."""

    cache = (
        LocalLakePackageCacheConfig(cache_project_root=template_root)
        if template_root is not None
        else None
    )
    return NativeLakeProjectConfig(
        lean_version=configured_test_lean_version(environ),
        mathlib_enabled=mathlib_enabled,
        local_package_cache=cache,
    )


__all__ = [
    "DEFAULT_TEST_LEAN_VERSION",
    "TEST_LEAN_VERSION_ENV",
    "configured_test_lean_toolchain",
    "configured_test_lean_version",
    "configured_test_native_lake_project",
    "write_test_lean_toolchain",
]
