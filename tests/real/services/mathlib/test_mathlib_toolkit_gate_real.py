from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.external_clients import (
    LakeCommandClient,
    LakeCommandClientConfig,
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
)
from lean_constellation.services.mathlib import MathlibService


def _require_lake_and_lean() -> None:
    if shutil.which("lake") is None:
        pytest.skip("`lake` is required for Mathlib real gate tests.")
    if shutil.which("lean") is None:
        pytest.skip("`lean` is required for Mathlib real gate tests.")


def _write_minimal_lake_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    (repo_root / "lakefile.toml").write_text(
        'name = "MathlibGateReal"\n'
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


def _create_content_node(repo_root: Path, service: MathlibService) -> None:
    tree = service.runtime.node.node_tree
    assert tree.ensure_root_scope_node(repo_root).ok
    assert tree.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    created = tree.create_content_node(
        repo_root,
        path="Main.Topic.Core",
        goal="Core goal.",
        boundary="Core node used by Mathlib real gate tests.",
        objective="Persist checked Mathlib-style module and declaration hints.",
        success_criteria="Checked entries and node hints survive service reload.",
    )
    assert created.ok, created.issues


def _lake_backed_toolkit(repo_root: Path, timeout_seconds: int = 120) -> LeanMcpToolkitClient:
    lake = LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout_seconds))

    def dispatch(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in {"lsp.run_snippet", "run_snippet", "check_mathlib_name"}:
            raise KeyError(tool_name)
        code = str(payload.get("code") or "")
        result = lake.run_snippet_check(repo_root=Path(payload.get("repo_root") or repo_root), imports=[], code=code, timeout_seconds=60)
        if tool_name == "check_mathlib_name":
            return {
                "passed": result.ok,
                "diagnostics": [] if result.ok else [{"severity": "error", "message": result.diagnostics_excerpt or result.summary}],
            }
        return {
            "diagnostics": [] if result.ok else [{"severity": "error", "message": result.diagnostics_excerpt or result.summary}],
        }

    return LeanMcpToolkitClient(dispatcher=dispatch)


@pytest.mark.real
def test_mathlib_checked_writes_and_node_hints_with_real_lake_gate(tmp_path: Path) -> None:
    _require_lake_and_lean()
    repo_root = tmp_path / "MathlibGateReal"
    _write_minimal_lake_repo(repo_root)
    service = make_runtime(external_overrides={"lean_mcp_toolkit": _lake_backed_toolkit(repo_root)}).mathlib
    _create_content_node(repo_root, service)

    build = service.runtime.external.lake.run_lake_build(repo_root, timeout_seconds=120)
    assert build.ok, build.summary

    module = service.record_mathlib_module_checked(
        repo_root,
        module_name="Main",
        summary="Minimal local module used as a lightweight Mathlib access gate fixture.",
        source="real lake env lean check",
    )
    assert module.ok, module.issues
    assert module.value is not None
    assert module.value.module == "Main"

    decl = service.record_mathlib_decl_checked(
        repo_root,
        decl_name="smokeTrue",
        module_name="Main",
        kind="theorem",
        signature="smokeTrue : True",
        snippet="theorem smokeTrue : True := by\n  trivial",
        summary="Minimal theorem checked through real lake env lean.",
        source="real lake env lean check",
    )
    assert decl.ok, decl.issues
    assert decl.value is not None
    assert decl.value.module == "Main"

    module_hint = service.add_node_mathlib_module_hint(
        repo_root,
        node_path="Main.Topic.Core",
        module="Main",
        reason="Real gate import fixture.",
        actor="coordinator",
    )
    assert module_hint.ok, module_hint.issues
    assert module_hint.value is not None
    assert module_hint.value.changed is True

    decl_hint = service.add_node_mathlib_decl_hint(
        repo_root,
        node_path="Main.Topic.Core",
        decl_name="smokeTrue",
        reason="Real gate theorem fixture.",
        actor="worker",
    )
    assert decl_hint.ok, decl_hint.issues
    assert decl_hint.value is not None
    assert decl_hint.value.changed is True
    inspected_hints = service.get_node_mathlib_hint_view(
        repo_root,
        node_path="Main.Topic.Core",
    )
    assert inspected_hints.ok and inspected_hints.value is not None
    assert inspected_hints.value.validation_gate.passed is True

    reloaded = make_runtime(external_overrides={"lean_mcp_toolkit": _lake_backed_toolkit(repo_root)}).mathlib
    hints = reloaded.get_node_mathlib_hint_view(repo_root, node_path="Main.Topic.Core")
    assert hints.ok, hints.issues
    assert hints.value is not None
    assert [item.module for item in hints.value.modules] == ["Main"]
    assert [item.name for item in hints.value.declarations] == ["smokeTrue"]
    assert hints.value.validation_gate.passed is True


@pytest.mark.real
@pytest.mark.real_toolkit
def test_mathlib_live_toolkit_semantic_search_gate(tmp_path: Path) -> None:
    base_url = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")
    if not base_url:
        pytest.skip("Set LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL to run live Mathlib toolkit tests.")
    query = os.environ.get("LEAN_CONSTELLATION_REAL_MATHLIB_QUERY", "Nat.add")
    service = make_runtime(
        external_overrides={
            "lean_mcp_toolkit": LeanMcpToolkitClient.from_config(
                LeanMcpToolkitClientConfig(
                    base_url=base_url,
                    api_prefix=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_API_PREFIX", "/api/v1"),
                    auth_token=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_AUTH_TOKEN"),
                    timeout_seconds=int(os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_TIMEOUT", "120")),
                )
            )
        }
    ).mathlib

    search = service.search_mathlib_declarations(tmp_path, query=query, limit=3)
    if not search.ok and search.issues and search.issues[0].kind == "toolkit_call_failed":
        message = search.issues[0].message
        if "status=500" in message:
            pytest.skip(f"Live toolkit lean_explore backend is unavailable: {message}")
    assert search.ok, search.issues
    assert search.value is not None
    if not search.value.candidates:
        pytest.skip(f"Live toolkit returned no Mathlib candidates for query {query!r}.")
    assert any(candidate.name or candidate.module for candidate in search.value.candidates)
