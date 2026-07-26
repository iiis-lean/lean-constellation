from __future__ import annotations

from pathlib import Path

import pytest

from tests.real.lean_test_config import (
    DEFAULT_TEST_LEAN_VERSION,
    TEST_LEAN_VERSION_ENV,
    configured_test_lean_toolchain,
    configured_test_lean_version,
    configured_test_native_lake_project,
    write_test_lean_toolchain,
)


def test_real_test_lean_version_defaults_to_fixed_432() -> None:
    assert configured_test_lean_version({}) == DEFAULT_TEST_LEAN_VERSION == "4.32.0"
    assert configured_test_lean_toolchain({}) == "leanprover/lean4:v4.32.0"


def test_real_test_lean_version_allows_explicit_fixed_override() -> None:
    environ = {TEST_LEAN_VERSION_ENV: "4.31.0"}

    assert configured_test_lean_version(environ) == "4.31.0"
    assert configured_test_lean_toolchain(environ) == "leanprover/lean4:v4.31.0"
    config = configured_test_native_lake_project(environ=environ)
    assert config.lean_version == "4.31.0"
    assert config.lean_toolchain == "leanprover/lean4:v4.31.0"
    assert config.mathlib_rev == "v4.31.0"

    lightweight = configured_test_native_lake_project(
        environ=environ,
        mathlib_enabled=False,
    )
    assert lightweight.mathlib_enabled is False
    assert lightweight.mathlib_rev is None


@pytest.mark.parametrize("value", ["stable", "latest", "v4.32.0", ""])
def test_real_test_lean_version_rejects_non_fixed_aliases(value: str) -> None:
    with pytest.raises(ValueError, match="must be a fixed Lean version"):
        configured_test_lean_version({TEST_LEAN_VERSION_ENV: value})


def test_write_test_lean_toolchain_uses_configured_version(tmp_path: Path) -> None:
    path = write_test_lean_toolchain(
        tmp_path,
        environ={TEST_LEAN_VERSION_ENV: "4.30.0"},
    )

    assert path == tmp_path / "lean-toolchain"
    assert path.read_text(encoding="utf-8") == "leanprover/lean4:v4.30.0\n"
