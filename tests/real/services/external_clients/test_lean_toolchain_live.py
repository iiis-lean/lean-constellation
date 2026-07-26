from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.real.lean_test_config import write_test_lean_toolchain
from lean_constellation.services.external_clients import (
    LakeCommandClient,
    LakeCommandClientConfig,
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
    LeanToolchainClient,
)


REQUIRED_TOOLCHAIN_TOOLKIT_TOOLS = [
    "diagnostics.file",
    "declarations.extract",
    "repo_nav.file_outline",
    "mathlib_nav.file_outline",
    "lean_explore.find",
]


def _require_lake_and_lean() -> None:
    if shutil.which("lake") is None:
        pytest.skip("`lake` is required for live Lean toolchain tests.")
    if shutil.which("lean") is None:
        pytest.skip("`lean` is required for live Lean toolchain tests.")


def _write_minimal_lake_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    write_test_lean_toolchain(repo_root)
    (repo_root / "lakefile.toml").write_text(
        'name = "ToolchainReal"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Main"]\n\n'
        '[[lean_lib]]\n'
        'name = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text(
        "def smokeNat : Nat := 1\n"
        "theorem smokeTrue : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )


def _toolchain_client(*, toolkit: LeanMcpToolkitClient | None = None, timeout_seconds: int = 120) -> LeanToolchainClient:
    return LeanToolchainClient(
        lake=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout_seconds)),
        toolkit=toolkit or LeanMcpToolkitClient(),
    )


def _toolkit_client() -> LeanMcpToolkitClient:
    base_url = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")
    if not base_url:
        pytest.skip("Set LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL to run live Lean toolchain toolkit tests.")
    return LeanMcpToolkitClient.from_config(
        LeanMcpToolkitClientConfig(
            base_url=base_url,
            api_prefix=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_API_PREFIX", "/api/v1"),
            auth_token=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_AUTH_TOKEN"),
            timeout_seconds=int(os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_TIMEOUT", "120")),
        )
    )


def _toolkit_project_target(tmp_path: Path) -> tuple[Path, str, str]:
    repo_root_raw = os.environ.get("LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT")
    target = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_TARGET")
    decl_name = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_NAME")
    if repo_root_raw or target or decl_name:
        if not repo_root_raw or not target or not decl_name:
            pytest.skip(
                "Set all of LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT, "
                "LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_TARGET, and "
                "LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_NAME for live toolchain project checks."
            )
        repo_root = Path(repo_root_raw).expanduser().resolve()
        if not repo_root.is_dir():
            pytest.skip(f"LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT is not a directory: {repo_root}")
        return repo_root, target, decl_name

    repo_root = tmp_path / "ToolchainToolkitReal"
    _write_minimal_lake_repo(repo_root)
    return repo_root, "Main.lean", "smokeNat"


@pytest.mark.real
def test_toolchain_live_lake_build_snippet_and_diagnostics_fallback(tmp_path: Path) -> None:
    _require_lake_and_lean()
    repo_root = tmp_path / "ToolchainReal"
    _write_minimal_lake_repo(repo_root)
    client = _toolchain_client(timeout_seconds=120)

    build = client.run_lake_build(repo_root, timeout_seconds=120)
    assert build.ok, build.summary
    assert build.provider == "lake_command"

    snippet_ok = client.run_snippet_check(repo_root, imports=["Main"], code="#check smokeNat", timeout_seconds=60)
    assert snippet_ok.ok, snippet_ok.summary
    assert snippet_ok.provider == "lake_command"

    snippet_bad = client.run_snippet_check(repo_root, imports=["Main"], code="#check (true : Nat)", timeout_seconds=60)
    assert snippet_bad.ok is False
    assert snippet_bad.provider == "lake_command"
    assert snippet_bad.diagnostics_excerpt

    broken = repo_root / "Broken.lean"
    broken.write_text("def bad : Nat := true\n", encoding="utf-8")
    diagnostics = client.run_file_diagnostics(repo_root, broken, rel_file="Broken.lean", timeout_seconds=60)

    assert diagnostics.ok, diagnostics.summary
    assert diagnostics.provider == "lake_command"
    assert diagnostics.fallback_provider == "lean_mcp_toolkit"
    assert diagnostics.diagnostics
    assert any(str(item.get("severity") or "").lower() == "error" for item in diagnostics.diagnostics)


@pytest.mark.real
@pytest.mark.real_toolkit
def test_toolchain_live_toolkit_catalog_diagnostics_and_extract(tmp_path: Path) -> None:
    toolkit = _toolkit_client()
    catalog = toolkit.probe_tool_catalog(REQUIRED_TOOLCHAIN_TOOLKIT_TOOLS)
    assert catalog.ok, catalog.summary

    client = _toolchain_client(toolkit=toolkit, timeout_seconds=120)
    repo_root, target, decl_name = _toolkit_project_target(tmp_path)
    diagnostics_file = repo_root / target
    if not diagnostics_file.exists():
        pytest.skip(f"Toolkit diagnostics target does not exist: {diagnostics_file}")

    diagnostics = client.run_file_diagnostics(repo_root, diagnostics_file, timeout_seconds=60)
    assert diagnostics.ok, diagnostics.summary
    assert diagnostics.provider == "lean_mcp_toolkit"

    extracted = client.extract_declaration(repo_root, target, decl_name)
    assert extracted.ok, extracted.summary
    assert extracted.provider == "lean_mcp_toolkit"
    assert extracted.code and decl_name in extracted.code


@pytest.mark.real
@pytest.mark.real_toolkit
def test_toolchain_live_toolkit_mathlib_search() -> None:
    toolkit = _toolkit_client()
    client = _toolchain_client(toolkit=toolkit, timeout_seconds=120)

    search = client.search_mathlib_declarations("Nat.add", limit=3)

    if not search.ok and search.issue_code == "toolkit_call_failed" and "status=500" in (search.summary or ""):
        pytest.skip(f"Toolkit lean_explore backend is unavailable in this environment: {search.summary}")
    assert search.ok, search.summary
    assert search.provider == "lean_mcp_toolkit"
    assert search.items
