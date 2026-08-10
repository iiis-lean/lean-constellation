from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.real.lean_test_config import write_test_lean_toolchain

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.services.external_clients import (
    LakeCommandClient,
    LakeCommandClientConfig,
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
)
from lean_constellation.services.foundation import FoundationContext
from tests.unit_services_helpers import make_runtime


pytestmark = [pytest.mark.real, pytest.mark.real_toolkit, pytest.mark.slow]

REQUIRED_CANONICAL_TOOLS = [
    "diagnostics.file",
    "declarations.extract",
    "repo_nav.tree",
    "repo_nav.file_outline",
    "repo_nav.grep",
    "repo_nav.read",
    "mathlib_nav.file_outline",
    "lean_explore.find",
]


def test_live_toolkit_catalog_repo_nav_diagnostics_extract_and_mathlib(tmp_path: Path) -> None:
    client = _toolkit_client()
    repo_root, target, decl_name = _toolkit_visible_repo(tmp_path)
    toolchain = _runtime_with_toolkit(client).external.lean_toolchain

    catalog = client.probe_tool_catalog(REQUIRED_CANONICAL_TOOLS)
    assert catalog.ok, catalog.summary
    assert not catalog.missing_tools

    diagnostics = toolchain.run_file_diagnostics(repo_root, repo_root / target)
    assert diagnostics.ok, diagnostics.summary
    assert diagnostics.provider in {"lean_mcp_toolkit", "lake_command"}
    extracted = toolchain.extract_declaration(repo_root, target, decl_name)
    assert extracted.ok, extracted.summary
    assert extracted.code and decl_name.rsplit(".", 1)[-1] in extracted.code

    tree = toolchain.list_repo_tree(repo_root, depth=4, limit=20)
    assert tree.ok, tree.summary
    outline = toolchain.outline_repo_file(repo_root, target)
    assert outline.ok, outline.summary
    grep = toolchain.grep_repo(repo_root, decl_name.rsplit(".", 1)[-1], limit=10)
    assert grep.ok, grep.summary
    read = toolchain.read_repo_source_window(repo_root, target, start_line=1, end_line=5)
    assert read.ok, read.summary

    module = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_MATHLIB_MODULE", "Mathlib")
    mathlib_module = toolchain.inspect_mathlib_module(repo_root, module)
    assert mathlib_module.ok, mathlib_module.summary
    search = toolchain.search_mathlib_declarations("Nat.add", limit=3)
    if not search.ok and search.issue_code == "toolkit_call_failed" and "status=500" in (search.summary or ""):
        pytest.skip(f"Toolkit lean_explore backend is unavailable in this environment: {search.summary}")
    assert search.ok, search.summary
    assert search.items


def test_live_toolkit_adapter_upstream_capture_and_projection(tmp_path: Path) -> None:
    client = _toolkit_client()
    upstream_root = _adapter_upstream_repo(tmp_path)
    repo_root = tmp_path / "Adapter"
    repo_root.mkdir()
    runtime = _runtime_with_toolkit(client)
    foundation = runtime.foundation
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format=RepoFormat.ADAPTER,
        reason="runtime matrix real toolkit adapter upstream test",
    ).ok
    prep = RepoPreparationInput(
        goal="Expose a real upstream declaration through an adapter repo.",
        source_corpus_mode=SourceCorpusMode.NONE,
        interface_inputs=[
            DeclInterface(name="upstreamSmoke", kind=DeclKind.THEOREM, summary="Expose upstream smoke theorem.")
        ],
    )
    prep_path = foundation.layout.preparation_input_path(FoundationContext(repo_root=repo_root))
    assert foundation.store.write_json_atomic(prep_path, prep).ok

    service = runtime.adapter
    assert service.write_adapter_upstream_metadata(
        repo_root,
        source_kind="local_path",
        local_path=str(upstream_root),
        package_name="Upstream",
        dependency_name="Upstream",
        evidence_summary="Runtime Matrix local upstream repo.",
        visible_modules=["Upstream"],
    ).ok
    assert service.mark_upstream_build_trusted(repo_root, summary="Runtime Matrix upstream fixture is trusted.").ok
    assert service.ensure_flat_main_catalog(repo_root).ok

    outline = service.list_upstream_module_declarations(repo_root, module="Upstream")
    assert outline.ok, outline.issues
    assert outline.value is not None
    assert any(item.lean_decl_name in {"upstreamSmoke", "Upstream.upstreamSmoke"} for item in outline.value.declarations)
    detail = service.inspect_upstream_declaration(repo_root, module="Upstream", lean_decl_name="upstreamSmoke")
    assert detail.ok, detail.issues
    assert detail.value is not None
    assert detail.value.code_excerpt
    statement = service.capture_upstream_declaration_code(
        repo_root,
        module="Upstream",
        lean_decl_name="upstreamSmoke",
        capture_mode="statement_only",
    )
    assert statement.ok, statement.issues
    full = service.capture_upstream_declaration_code(
        repo_root,
        module="Upstream",
        lean_decl_name="upstreamSmoke",
        capture_mode="full_declaration",
    )
    assert full.ok, full.issues

    assert service.create_adapter_decl(
        repo_root,
        name="upstreamSmoke",
        kind=DeclKind.THEOREM,
        module="Upstream",
        lean_decl_name="upstreamSmoke",
        summary="Expose upstreamSmoke.",
    ).ok
    assert service.set_adapter_statement_formal(
        repo_root,
        name="upstreamSmoke",
        code=statement.value.code,
    ).ok
    assert service.set_adapter_statement_nl(repo_root, name="upstreamSmoke", text="Upstream smoke statement.").ok
    assert service.set_adapter_proof_formal(
        repo_root,
        name="upstreamSmoke",
        code=full.value.code,
    ).ok
    assert service.set_adapter_proof_nl(repo_root, name="upstreamSmoke", text="Upstream smoke proof.").ok
    assert service.finalize_adapter_decl(repo_root, name="upstreamSmoke").ok
    assert service.bind_adapter_interface(
        repo_root,
        interface_name="upstreamSmoke",
        decl_name="upstreamSmoke",
        binding_summary="The upstream declaration satisfies the adapter interface.",
    ).ok
    assert service.refresh_adapter_projection(repo_root).ok
    ready = service.check_adapter_ready(repo_root)
    assert ready.ok, ready.issues
    assert ready.value is not None
    assert ready.value.passed is True


def _toolkit_client() -> LeanMcpToolkitClient:
    base_url = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")
    if not base_url:
        pytest.skip("Set LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL to run Runtime Matrix live Toolkit tests.")
    return LeanMcpToolkitClient.from_config(
        LeanMcpToolkitClientConfig(
            base_url=base_url,
            api_prefix=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_API_PREFIX", "/api/v1"),
            auth_token=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_AUTH_TOKEN"),
            timeout_seconds=int(os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_TIMEOUT", "120")),
        )
    )


def _runtime_with_toolkit(client: LeanMcpToolkitClient):
    return make_runtime(
        external_overrides={
            "lake": LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
            "lean_mcp_toolkit": client,
        }
    )


def _toolkit_visible_repo(tmp_path: Path) -> tuple[Path, str, str]:
    raw = os.environ.get("LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT")
    target = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_TARGET")
    decl = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_NAME")
    if raw or target or decl:
        if not (raw and target and decl):
            pytest.skip(
                "Set LEAN_CONSTELLATION_REAL_LEAN_TEMPLATE_ROOT, "
                "LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_TARGET, and "
                "LEAN_CONSTELLATION_REAL_TOOLKIT_DECL_NAME together."
            )
        repo = Path(raw).expanduser().resolve()
        if not repo.is_dir():
            pytest.skip(f"Toolkit visible repo does not exist: {repo}")
        return repo, target, decl
    repo = tmp_path / "ToolkitVisible"
    _write_toolkit_visible_repo(repo)
    return repo, "Main.lean", "target_decl"


def _adapter_upstream_repo(tmp_path: Path) -> Path:
    raw = os.environ.get("LEAN_CONSTELLATION_REAL_ADAPTER_UPSTREAM_REPO")
    if raw:
        repo = Path(raw).expanduser().resolve()
        if not repo.is_dir():
            pytest.skip(f"Adapter upstream repo does not exist: {repo}")
        return repo
    repo = tmp_path / "Upstream"
    _write_upstream_repo(repo)
    return repo


def _write_toolkit_visible_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    write_test_lean_toolchain(repo_root)
    (repo_root / "lakefile.toml").write_text(
        'name = "ToolkitVisible"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Main"]\n\n'
        "[[lean_lib]]\n"
        'name = "Main"\n',
        encoding="utf-8",
    )
    (repo_root / "Main.lean").write_text(
        "def target_decl : Nat := 1\n"
        "theorem target_true : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    if shutil.which("lake"):
        subprocess.run(["lake", "build"], cwd=repo_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _write_upstream_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    write_test_lean_toolchain(repo_root)
    (repo_root / "lakefile.toml").write_text(
        'name = "Upstream"\n'
        'version = "0.1.0"\n'
        'defaultTargets = ["Upstream"]\n\n'
        "[[lean_lib]]\n"
        'name = "Upstream"\n',
        encoding="utf-8",
    )
    (repo_root / "Upstream.lean").write_text(
        "theorem upstreamSmoke : True := by\n"
        "  trivial\n",
        encoding="utf-8",
    )
    if shutil.which("lake"):
        subprocess.run(["lake", "build"], cwd=repo_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
