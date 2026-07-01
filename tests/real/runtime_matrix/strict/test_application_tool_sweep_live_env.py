from __future__ import annotations

import os
from pathlib import Path

import pytest

from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.external_clients import LeanMcpToolkitClient, LeanMcpToolkitClientConfig
from lean_constellation.services.external_clients.lean_toolchain import LeanToolchainClient
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.strict_helpers import call_tool_with_evidence


pytestmark = [pytest.mark.real_toolkit, pytest.mark.slow]


def test_strict_live_env_github_mathlib_and_arxiv_tools_execute_through_mcp(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    tmp_path: Path,
) -> None:
    _require_live_tool_sweep_enabled()
    client = _live_toolkit_client()
    ws = runtime_matrix_workspace
    ws.prepare_provider_native_repo()
    _install_live_toolkit(ws, client)
    recorder = EvidenceRecorder()
    recorder.add_note("Runtime Matrix strict live-env ToolSweep used real GitHub CLI and live Lean MCP Toolkit HTTP server.")

    server = unwrap(create_mcp_server(ws.runtime, view_keys=["repo_format_discovery", "mathlib_recon"]))
    repo_ctx = _ctx(
        ws.provider_repo,
        view="repo_format_discovery",
        agent_type="RepoFormatDiscoveryAgent",
        role="coordinator",
    )
    mathlib_ctx = _ctx(
        ws.provider_repo,
        view="mathlib_recon",
        agent_type="MathlibReconAgent",
        node_path="Main.Topic.Core",
    )

    github_search = call_tool_with_evidence(
        server,
        "repo_format_discovery",
        "search_github_lean_repositories",
        {"query": os.environ.get("LEAN_CONSTELLATION_REAL_GITHUB_QUERY", "leanprover-community/lean4-samples"), "limit": 1},
        runtime_context=repo_ctx,
        recorder=recorder,
        assertion_summary="Live GitHub repository search returned a real Lean repository candidate.",
    )
    assert github_search.value["candidates"]
    assert any("lean4-samples" in item["full_name"] for item in github_search.value["candidates"])

    github_inspect = call_tool_with_evidence(
        server,
        "repo_format_discovery",
        "inspect_github_lean_repository",
        {"url_or_slug": os.environ.get("LEAN_CONSTELLATION_REAL_GITHUB_REPO", "leanprover-community/lean4-samples")},
        runtime_context=repo_ctx,
        recorder=recorder,
        assertion_summary="Live GitHub repository inspect returned metadata from gh.",
    )
    assert github_inspect.value["full_name"] == "leanprover-community/lean4-samples"
    assert github_inspect.value["html_url"].startswith("https://github.com/")

    external_search = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "search_external_mathlib",
        {"query": "Nat.add_assoc", "search_kinds": ["lean_explore"], "limit": 3},
        runtime_context=mathlib_ctx,
        recorder=recorder,
        assertion_summary="Live Lean Explore search returned Mathlib candidates and populated the repo cache.",
    )
    assert external_search.value["candidates"]
    candidate_id = external_search.value["candidates"][0]["candidate_id"]

    semantic_search = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "search_mathlib_declarations",
        {"query": "Nat.add_assoc", "limit": 3},
        runtime_context=mathlib_ctx,
        recorder=recorder,
        assertion_summary="Live semantic Mathlib declaration search returned candidates.",
    )
    assert semantic_search.value["candidates"]

    candidate = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "inspect_mathlib_search_candidate",
        {"candidate_id": candidate_id},
        runtime_context=mathlib_ctx,
        recorder=recorder,
        assertion_summary="Cached live Mathlib search candidate was enriched through declaration navigation.",
    )
    assert candidate.value["name"]
    assert candidate.value["module"]
    assert candidate.value["signature"] or candidate.value["snippet"]

    declaration = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "inspect_mathlib_declaration",
        {"decl_name": "PNat.add_coe"},
        runtime_context=mathlib_ctx,
        recorder=recorder,
        assertion_summary="Live Mathlib declaration inspect returned source text and module metadata.",
    )
    assert declaration.value["module"] == "Mathlib.Data.PNat.Basic"
    assert "add_coe" in (declaration.value["code_excerpt"] or "")

    module = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "inspect_mathlib_module",
        {"module": "Mathlib.Data.Nat.Basic", "pattern": "Nat.instLinearOrder"},
        runtime_context=mathlib_ctx,
        recorder=recorder,
        assertion_summary="Live Mathlib module inspect read declarations from the template Mathlib checkout.",
    )
    assert module.value["module"] == "Mathlib.Data.Nat.Basic"
    assert "Nat.instLinearOrder" in module.value["important_decl_hints"]

    arxiv = call_tool_with_evidence(
        server,
        "mathlib_recon",
        "search_arxiv_theorems",
        {"query": os.environ.get("LEAN_CONSTELLATION_REAL_ARXIV_THEOREM_QUERY", "math/0001001"), "limit": 1},
        runtime_context=mathlib_ctx,
        recorder=recorder,
        assertion_summary="Live arXiv theorem search returned a real arXiv e-print theorem candidate through MCP.",
    )
    assert arxiv.value["items"]
    assert arxiv.value["items"][0]["arxiv_id"]
    assert any(warning["code"] == "arxiv_eprint_fallback" for warning in arxiv.value["warnings"])

    artifact_dir = tmp_path / "runtime_matrix_evidence"
    recorder.export_json(artifact_dir / "strict_live_env_tool_sweep.json")
    recorder.export_markdown_summary(artifact_dir / "strict_live_env_tool_sweep.md")
    assert {
        "search_github_lean_repositories",
        "inspect_github_lean_repository",
        "search_external_mathlib",
        "search_mathlib_declarations",
        "inspect_mathlib_search_candidate",
        "inspect_mathlib_declaration",
        "inspect_mathlib_module",
        "search_arxiv_theorems",
    } <= recorder.evidence.application_tool_names
    assert any(item.tool_name == "search_arxiv_theorems" and item.ok for item in recorder.evidence.application_tool_calls)


def _require_live_tool_sweep_enabled() -> None:
    if os.environ.get("LEAN_CONSTELLATION_STRICT_LIVE_TOOL_SWEEP") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_STRICT_LIVE_TOOL_SWEEP=1 to run strict live-env ToolSweep.")


def _live_toolkit_client() -> LeanMcpToolkitClient:
    base_url = os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL")
    if not base_url:
        pytest.skip("Set LEAN_CONSTELLATION_REAL_TOOLKIT_BASE_URL to a live Lean MCP Toolkit HTTP server.")
    return LeanMcpToolkitClient.from_config(
        LeanMcpToolkitClientConfig(
            base_url=base_url,
            api_prefix=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_API_PREFIX", "/api/v1"),
            auth_token=os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_AUTH_TOKEN"),
            timeout_seconds=int(os.environ.get("LEAN_CONSTELLATION_REAL_TOOLKIT_TIMEOUT", "120")),
        )
    )


def _install_live_toolkit(ws: RuntimeMatrixWorkspace, client: LeanMcpToolkitClient) -> None:
    ws.runtime.external.lean_mcp_toolkit = client
    ws.runtime.external.lean_toolkit = client
    ws.runtime.external.lean_toolchain = LeanToolchainClient(lake=ws.runtime.external.lake, toolkit=client)


def _ctx(
    repo_root: Path,
    *,
    view: str,
    agent_type: str,
    role: str = "worker",
    node_path: str | None = None,
) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id=f"strict_live_env_{view}",
        step_id=f"strict_live_env_{view}_step",
        agent_id=f"strict_live_env_{view}_agent",
        agent_type=agent_type,
        agent_role=role,  # type: ignore[arg-type]
        expected_view_key=view,
        repo_root=repo_root,
        node_path=node_path,
        node_kind="content" if node_path else None,
        contract_version=1 if node_path else None,
    )
