from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.real.lean_test_config import write_test_lean_toolchain
from lean_constellation.services.external_clients import LeanMcpToolkitClient, LeanMcpToolkitClientConfig


REQUIRED_CANONICAL_TOOLS = [
    "diagnostics.file",
    "declarations.extract",
    "repo_nav.tree",
    "repo_nav.file_outline",
    "repo_nav.grep",
    "mathlib_nav.file_outline",
    "lean_explore.find",
]


def _toolkit_client() -> LeanMcpToolkitClient:
    base_url = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")
    if not base_url:
        pytest.skip("Set LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL to run live Lean MCP Toolkit tests.")
    return LeanMcpToolkitClient.from_config(
        LeanMcpToolkitClientConfig(
            base_url=base_url,
            api_prefix=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_API_PREFIX", "/api/v1"),
            auth_token=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_AUTH_TOKEN"),
            timeout_seconds=int(os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_TIMEOUT", "120")),
        )
    )


def _write_minimal_toolkit_project(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    write_test_lean_toolchain(repo_root)
    (repo_root / "lakefile.toml").write_text(
        'name = "ToolkitReal"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Main"]\n\n'
        '[[lean_lib]]\n'
        'name = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text(
        "def target_decl : Nat := 1\n",
        encoding="utf-8",
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
                "LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_NAME for external project wrapper checks."
            )
        repo_root = Path(repo_root_raw).expanduser().resolve()
        if not repo_root.is_dir():
            pytest.skip(f"LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT is not a directory: {repo_root}")
        return repo_root, target, decl_name

    repo_root = tmp_path / "ToolkitReal"
    _write_minimal_toolkit_project(repo_root)
    return repo_root, "Main.lean", "target_decl"


@pytest.mark.real
@pytest.mark.real_toolkit
def test_toolkit_live_catalog_contains_canonical_service_tools() -> None:
    client = _toolkit_client()

    catalog = client.probe_tool_catalog(REQUIRED_CANONICAL_TOOLS)

    assert catalog.ok, catalog.summary
    assert not catalog.missing_tools
    names = {tool.name for tool in catalog.tools}
    for tool_name in REQUIRED_CANONICAL_TOOLS:
        assert tool_name in names
    assert catalog.raw_excerpt


@pytest.mark.real
@pytest.mark.real_toolkit
def test_toolkit_live_mathlib_wrappers_use_canonical_tools() -> None:
    client = _toolkit_client()
    catalog = client.probe_tool_catalog(["lean_explore.find", "mathlib_nav.file_outline"])
    assert catalog.ok, catalog.summary

    search = client.search_mathlib("Nat.add", limit=3)
    if not search.ok and search.issue_code == "toolkit_call_failed" and "status=500" in (search.summary or ""):
        pytest.skip(f"Toolkit lean_explore backend is unavailable in this environment: {search.summary}")
    assert search.ok, search.summary
    assert search.items
    assert search.raw_excerpt
    assert all(warning.code for warning in search.warnings)
    assert all(item.get("source_tool") == "lean_explore.find" for item in search.items)

    module_name = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_MATHLIB_MODULE", "Mathlib")
    module = client.inspect_mathlib_module(module_name)
    assert module.ok, module.summary
    assert module.module == module_name
    assert module.raw_excerpt


@pytest.mark.real
@pytest.mark.real_toolkit
def test_toolkit_live_project_diagnostics_and_declaration_extract(tmp_path: Path) -> None:
    client = _toolkit_client()
    repo_root, target, decl_name = _toolkit_project_target(tmp_path)

    diagnostics_file = repo_root / target
    if not diagnostics_file.exists():
        pytest.skip(f"Toolkit diagnostics target does not exist: {diagnostics_file}")

    diagnostics = client.run_file_diagnostics(repo_root, diagnostics_file)
    assert diagnostics.ok, diagnostics.summary
    assert diagnostics.raw_excerpt is not None
    assert all(warning.code for warning in diagnostics.warnings)

    extracted = client.extract_declaration(repo_root, target, decl_name)
    assert extracted.ok, extracted.summary
    assert extracted.code and decl_name in extracted.code
    assert extracted.raw_excerpt
    assert all(warning.code for warning in extracted.warnings)
