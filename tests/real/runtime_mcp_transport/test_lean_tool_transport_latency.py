from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from lean_constellation.app import create_app_runtime_services
from lean_constellation.mcp import create_mcp_server, runtime_context_to_env
from lean_constellation.mcp.http import create_mcp_http_app
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig, LeanMcpToolkitClient
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.lean_tool_latency.bench import LatencyRecorder, artifact_dirs, latency_timeout, service_ok
from tests.real.lean_tool_latency.test_lean_tool_latency_matrix import (
    DECL_NAME,
    NODE_PATH,
    _require_lake_and_lean,
    _setup_decl_round,
    _write_tiny_lake_repo,
)
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.strict.test_real_codex_agent_resource_matrix import (
    test_strict_real_codex_coordinator_resources_tools_and_submit as _run_coordinator_case,
)
from tests.real.runtime_matrix.transport import stdio_compare_enabled


pytestmark = [pytest.mark.real, pytest.mark.slow, pytest.mark.lean_latency, pytest.mark.transport_compare]


@dataclass
class RuntimeTransportFixture:
    name: str
    runtime: object
    repo_root: Path
    runtime_root: Path
    app_config_path: Path
    round_id: str
    lake: LakeCommandClient


def test_lean_tool_transport_latency_matrix(tmp_path: Path) -> None:
    timeout = _require_lake_and_lean()
    artifact_dir, mirror_dir = artifact_dirs(tmp_path, "runtime_mcp_transport")
    recorder = LatencyRecorder(test_name="runtime_mcp_transport", artifact_dir=artifact_dir, mirror_dir=mirror_dir)

    shell_fixture = _make_fixture(tmp_path, "direct_shell", timeout)
    recorder.measure(
        case_id="direct_shell_lake_build",
        fixture="tiny_lake",
        operation="lake build",
        backend="direct_shell",
        iteration=1,
        func=lambda: shell_fixture.lake.run_lake_build(shell_fixture.repo_root, timeout_seconds=timeout),
        validate=service_ok,
    )
    recorder.measure(
        case_id="direct_shell_lake_env_lean",
        fixture="tiny_lake",
        operation="lake env lean --json Main.lean",
        backend="direct_shell",
        iteration=1,
        func=lambda: shell_fixture.lake.run_lake_env_lean(
            repo_root=shell_fixture.repo_root,
            rel_file="Main.lean",
            json=True,
            timeout_seconds=timeout,
        ),
        validate=service_ok,
    )

    _measure_direct_service_sequence(recorder, _make_fixture(tmp_path, "direct_service", timeout))
    _measure_mcp_sequence(
        recorder,
        _make_fixture(tmp_path, "in_memory_mcp", timeout),
        backend="in_memory_mcp",
        call_tool=_in_memory_tool_call,
    )
    _measure_mcp_sequence(
        recorder,
        _make_fixture(tmp_path, "http_mcp", timeout),
        backend="http_mcp",
        call_tool=_http_tool_call,
    )
    if stdio_compare_enabled():
        _measure_mcp_sequence(
            recorder,
            _make_fixture(tmp_path, "stdio_mcp", timeout),
            backend="stdio_mcp",
            call_tool=_stdio_tool_call,
        )

    recorder.export()
    recorder.assert_required_validated()


@pytest.mark.real_codex
@pytest.mark.mcp_http
def test_lean_tool_transport_real_codex_http_smoke_env_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAN_CONSTELLATION_REAL_CODEX_MCP_TRANSPORT", "http")
    recorder = EvidenceRecorder()
    _run_coordinator_case(tmp_path, recorder)
    assert recorder.evidence.codex_artifacts
    assert recorder.evidence.codex_artifacts[0].mcp_transport == "http"


@pytest.mark.real_codex
@pytest.mark.mcp_stdio
def test_lean_tool_transport_real_codex_stdio_smoke_env_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if not stdio_compare_enabled():
        pytest.skip("Set LEAN_CONSTELLATION_RUN_MCP_STDIO_COMPARE=1 to run stdio real Codex transport smoke.")
    monkeypatch.setenv("LEAN_CONSTELLATION_REAL_CODEX_MCP_TRANSPORT", "stdio")
    recorder = EvidenceRecorder()
    _run_coordinator_case(tmp_path, recorder)
    assert recorder.evidence.codex_artifacts
    assert recorder.evidence.codex_artifacts[0].mcp_transport == "stdio"


def _make_fixture(tmp_path: Path, name: str, timeout: int) -> RuntimeTransportFixture:
    repo_root = tmp_path / name / "TinyLake"
    runtime_root = tmp_path / name / ".agent_runtime"
    _write_tiny_lake_repo(repo_root)
    lake = LakeCommandClient(LakeCommandClientConfig(timeout_seconds=timeout))
    built = lake.run_lake_build(repo_root, timeout_seconds=timeout)
    assert built.ok, built.summary
    runtime = create_app_runtime_services(
        runtime_root=runtime_root,
        external_overrides={
            "lake": lake,
            "lean_mcp_toolkit": LeanMcpToolkitClient(),
        },
    )
    round_id = _setup_decl_round(runtime, repo_root)
    refreshed = runtime.lean_projection.refresh_node_projection(repo_root, node_path=NODE_PATH)
    assert refreshed.ok, refreshed.issues
    app_config_path = tmp_path / name / "lean_constellation.toml"
    app_config_path.write_text(
        f'workspace_root = "{repo_root.parent}"\n'
        f'runtime_root = "{runtime_root}"\n'
        "max_concurrent_flow_advances = 1\n"
        "max_concurrent_steps = 1\n",
        encoding="utf-8",
    )
    return RuntimeTransportFixture(
        name=name,
        runtime=runtime,
        repo_root=repo_root,
        runtime_root=runtime_root,
        app_config_path=app_config_path,
        round_id=round_id,
        lake=lake,
    )


def _measure_direct_service_sequence(recorder: LatencyRecorder, fixture: RuntimeTransportFixture) -> None:
    runtime = fixture.runtime
    statement = recorder.measure(
        case_id="direct_service_prepare_statement_formal",
        fixture=fixture.name,
        operation="prepare_statement_formal_stage_file",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.lean_projection.prepare_statement_formal_stage_file(
            fixture.repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
        ),
        validate=service_ok,
    )
    assert statement is not None
    statement_path = Path(statement.value.path)
    _complete_trivial_file(statement_path)
    recorder.measure(
        case_id="direct_service_check_statement_formal_policy",
        fixture=fixture.name,
        operation="build_statement_lean_check",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.lean_projection.lean_check.build_statement_lean_check(
            fixture.repo_root,
            file_path=statement_path,
            decl_kind="theorem",
        ),
        validate=service_ok,
    )
    recorder.measure(
        case_id="direct_service_capture_statement_formal",
        fixture=fixture.name,
        operation="capture_statement_formal",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.lean_projection.capture_statement_formal(
            fixture.repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
        ),
        validate=service_ok,
    )
    _write_proof_nl(runtime, fixture)
    proof = recorder.measure(
        case_id="direct_service_prepare_proof_formal",
        fixture=fixture.name,
        operation="prepare_proof_formal_stage_file",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.lean_projection.prepare_proof_formal_stage_file(
            fixture.repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
        ),
        validate=service_ok,
    )
    proof_path = Path(proof.value.path)
    _complete_trivial_file(proof_path)
    recorder.measure(
        case_id="direct_service_check_proof_formal_policy",
        fixture=fixture.name,
        operation="build_proof_lean_check",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.lean_projection.lean_check.build_proof_lean_check(
            fixture.repo_root,
            file_path=proof_path,
        ),
        validate=service_ok,
    )
    recorder.measure(
        case_id="direct_service_capture_proof_formal",
        fixture=fixture.name,
        operation="capture_proof_formal",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.lean_projection.capture_proof_formal(
            fixture.repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
        ),
        validate=service_ok,
    )
    recorder.measure(
        case_id="direct_service_check_formal_stage_consistency",
        fixture=fixture.name,
        operation="check_formal_stage_consistency proof",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.decl_graph.check_formal_stage_consistency(
            fixture.repo_root,
            node_path=NODE_PATH,
            decl_name=DECL_NAME,
            stage="proof",
        ),
        validate=service_ok,
    )
    recorder.measure(
        case_id="direct_service_check_mathlib_module",
        fixture=fixture.name,
        operation="check_mathlib_module Init",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.external.lean_toolchain.check_mathlib_module(fixture.repo_root, module="Init"),
        validate=service_ok,
    )
    recorder.measure(
        case_id="direct_service_check_mathlib_name",
        fixture=fixture.name,
        operation="check_mathlib_name True.intro",
        backend="direct_service",
        iteration=1,
        func=lambda: runtime.external.lean_toolchain.check_mathlib_name(
            fixture.repo_root,
            module="Init",
            decl_name="True.intro",
        ),
        validate=service_ok,
    )


def _measure_mcp_sequence(
    recorder: LatencyRecorder,
    fixture: RuntimeTransportFixture,
    *,
    backend: str,
    call_tool: Callable[[RuntimeTransportFixture, str, str, dict[str, Any], str], Any],
) -> None:
    statement = recorder.measure(
        case_id=f"{backend}_prepare_statement_formal",
        fixture=fixture.name,
        operation="prepare_statement_formal_file",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(fixture, "statement_formal_worker", "prepare_statement_formal_file", {"decl_name": DECL_NAME}, "statement_formal"),
        validate=_mcp_call_ok,
    )
    assert statement is not None
    statement_path = _path_from_mcp_result(statement)
    _complete_trivial_file(statement_path)
    recorder.measure(
        case_id=f"{backend}_check_statement_formal_policy",
        fixture=fixture.name,
        operation="check_statement_formal_policy",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(
            fixture,
            "statement_formal_worker",
            "check_statement_formal_policy",
            {"file_path": _repo_rel(fixture, statement_path), "decl_kind": "theorem"},
            "statement_formal",
        ),
        validate=_mcp_call_ok,
    )
    recorder.measure(
        case_id=f"{backend}_capture_statement_formal",
        fixture=fixture.name,
        operation="capture_statement_formal_file",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(fixture, "statement_formal_worker", "capture_statement_formal_file", {"decl_name": DECL_NAME}, "statement_formal"),
        validate=_mcp_call_ok,
    )
    _write_proof_nl(fixture.runtime, fixture)
    proof = recorder.measure(
        case_id=f"{backend}_prepare_proof_formal",
        fixture=fixture.name,
        operation="prepare_proof_formal_file",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(fixture, "proof_formal_worker", "prepare_proof_formal_file", {"decl_name": DECL_NAME}, "proof_formal"),
        validate=_mcp_call_ok,
    )
    proof_path = _path_from_mcp_result(proof)
    _complete_trivial_file(proof_path)
    recorder.measure(
        case_id=f"{backend}_check_proof_formal_policy",
        fixture=fixture.name,
        operation="check_proof_formal_policy",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(
            fixture,
            "proof_formal_worker",
            "check_proof_formal_policy",
            {"file_path": _repo_rel(fixture, proof_path)},
            "proof_formal",
        ),
        validate=_mcp_call_ok,
    )
    recorder.measure(
        case_id=f"{backend}_capture_proof_formal",
        fixture=fixture.name,
        operation="capture_proof_formal_file",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(fixture, "proof_formal_worker", "capture_proof_formal_file", {"decl_name": DECL_NAME}, "proof_formal"),
        validate=_mcp_call_ok,
    )
    recorder.measure(
        case_id=f"{backend}_check_formal_stage_consistency",
        fixture=fixture.name,
        operation="check_formal_stage_consistency proof",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(
            fixture,
            "proof_formal_worker",
            "check_formal_stage_consistency",
            {"decl_name": DECL_NAME, "stage": "proof"},
            "proof_formal",
        ),
        validate=_mcp_call_ok,
    )
    recorder.measure(
        case_id=f"{backend}_record_mathlib_module",
        fixture=fixture.name,
        operation="record_mathlib_module Init",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(
            fixture,
            "mathlib_recon",
            "record_mathlib_module",
            {"module_name": "Init", "summary": "Runtime MCP transport latency module.", "source": "runtime_mcp_transport"},
            "mathlib_recon",
        ),
        validate=_mcp_call_ok,
    )
    recorder.measure(
        case_id=f"{backend}_check_mathlib_name",
        fixture=fixture.name,
        operation="check_mathlib_name True.intro",
        backend=backend,
        iteration=1,
        func=lambda: call_tool(
            fixture,
            "mathlib_recon",
            "check_mathlib_name",
            {"module": "Init", "decl_name": "True.intro"},
            "mathlib_recon",
        ),
        validate=_mcp_call_ok,
    )


def _in_memory_tool_call(fixture: RuntimeTransportFixture, view_key: str, tool_name: str, args: dict[str, Any], stage: str) -> Any:
    server = create_mcp_server(fixture.runtime, view_keys=[view_key])
    assert server.ok and server.value is not None, server.issues
    return server.value.call_tool(view_key, tool_name, args, env=_runtime_env(fixture, view_key=view_key, stage=stage))


def _http_tool_call(fixture: RuntimeTransportFixture, view_key: str, tool_name: str, args: dict[str, Any], stage: str) -> Any:
    return anyio.run(_http_tool_call_async, fixture, view_key, tool_name, args, stage)


async def _http_tool_call_async(fixture: RuntimeTransportFixture, view_key: str, tool_name: str, args: dict[str, Any], stage: str) -> Any:
    app_result = create_mcp_http_app(fixture.runtime, view_keys=[view_key])
    assert app_result.ok and app_result.value is not None, app_result.issues
    headers = _runtime_headers(_runtime_env(fixture, view_key=view_key, stage=stage))
    transport = httpx.ASGITransport(app=app_result.value)
    async with app_result.value.router.lifespan_context(app_result.value):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", headers=headers) as client:
            async with streamable_http_client(
                f"http://testserver/mcp/views/{view_key}/",
                http_client=client,
                terminate_on_close=False,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await session.call_tool(tool_name, args)


def _stdio_tool_call(fixture: RuntimeTransportFixture, view_key: str, tool_name: str, args: dict[str, Any], stage: str) -> Any:
    return anyio.run(_stdio_tool_call_async, fixture, view_key, tool_name, args, stage)


async def _stdio_tool_call_async(fixture: RuntimeTransportFixture, view_key: str, tool_name: str, args: dict[str, Any], stage: str) -> Any:
    env = {
        **os.environ,
        **_runtime_env(fixture, view_key=view_key, stage=stage),
        "PYTHONPATH": _mcp_pythonpath(),
    }
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "lean_constellation.mcp.stdio", "--config", str(fixture.app_config_path), "--view-key", view_key],
        env=env,
    )
    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await session.call_tool(tool_name, args)


def _runtime_env(fixture: RuntimeTransportFixture, *, view_key: str, stage: str) -> dict[str, str]:
    return runtime_context_to_env(
        RuntimeToolContext(
            flow_id=f"flow_{fixture.name}_{stage}",
            step_id=f"step_{fixture.name}_{stage}",
            agent_id=f"agent_{fixture.name}_{stage}",
            scope_id=f"scope_{fixture.name}",
            agent_type=_agent_type_for_view(view_key),
            agent_role="worker",
            expected_view_key=view_key,
            repo_root=fixture.repo_root,
            workspace_root=fixture.repo_root.parent,
            node_path=NODE_PATH,
            node_kind="content",
            contract_version=1,
            stage=stage,
            round_id=fixture.round_id,
            batch_decls=[DECL_NAME],
            current_decl=DECL_NAME,
            decl_kind="theorem",
        )
    )


def _runtime_headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "X-Ark-Flow-Id": env["ARK_FLOW_ID"],
        "X-Ark-Step-Id": env["ARK_STEP_ID"],
        "X-Ark-Agent-Id": env["ARK_AGENT_ID"],
        "X-Ark-Scope-Id": env["ARK_SCOPE_ID"],
        "X-Lean-Constellation-Agent-Type": env["LEAN_CONSTELLATION_AGENT_TYPE"],
        "X-Lean-Constellation-Agent-Role": env["LEAN_CONSTELLATION_AGENT_ROLE"],
        "X-Lean-Constellation-Expected-Tool-View": env["LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW"],
        "X-Lean-Constellation-Workspace-Root": env["LEAN_CONSTELLATION_WORKSPACE_ROOT"],
        "X-Lean-Constellation-Repo-Root": env["LEAN_CONSTELLATION_REPO_ROOT"],
        "X-Ark-Node-Path": env["LEAN_CONSTELLATION_NODE_PATH"],
        "X-Ark-Node-Kind": env["LEAN_CONSTELLATION_NODE_KIND"],
        "X-Ark-Contract-Version": env["LEAN_CONSTELLATION_CONTRACT_VERSION"],
        "X-Ark-Decl-Stage": env["LEAN_CONSTELLATION_STAGE"],
        "X-Ark-Round-Id": env["LEAN_CONSTELLATION_ROUND_ID"],
        "X-Ark-Batch-Decls": env["LEAN_CONSTELLATION_BATCH_DECLS"],
        "X-Ark-Current-Decl": env["LEAN_CONSTELLATION_CURRENT_DECL"],
        "X-Ark-Decl-Kind": env["LEAN_CONSTELLATION_DECL_KIND"],
    }


def _agent_type_for_view(view_key: str) -> str:
    return {
        "statement_formal_worker": "StatementFormalWorkerAgent",
        "proof_formal_worker": "ProofFormalWorkerAgent",
        "mathlib_recon": "MathlibReconAgent",
    }[view_key]


def _write_proof_nl(runtime: object, fixture: RuntimeTransportFixture) -> None:
    proof_nl = runtime.decl_graph.write_proof_nl(
        fixture.repo_root,
        node_path=NODE_PATH,
        round_id=fixture.round_id,
        decl_name=DECL_NAME,
        nl="Use triviality.",
        origin=[{"kind": "runtime_mcp_transport"}],
        deps=[],
    )
    assert proof_nl.ok, proof_nl.issues


def _complete_trivial_file(path: Path) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")


def _repo_rel(fixture: RuntimeTransportFixture, path: Path) -> str:
    return str(Path(path).relative_to(fixture.repo_root))


def _mcp_call_ok(value: Any) -> bool:
    structured = _mcp_structured(value)
    return bool(structured and structured.get("ok") is True)


def _path_from_mcp_result(value: Any) -> Path:
    structured = _mcp_structured(value)
    assert structured is not None, value
    payload = structured.get("value")
    assert isinstance(payload, dict) and payload.get("path"), structured
    return Path(str(payload["path"]))


def _mcp_structured(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "structuredContent"):
        structured = getattr(value, "structuredContent")
        return dict(structured) if isinstance(structured, dict) else None
    if hasattr(value, "ok") and hasattr(value, "value"):
        if not value.ok or value.value is None:
            return None
        tool_result = value.value
        if hasattr(tool_result, "model_dump"):
            return tool_result.model_dump(mode="json")
    if isinstance(value, dict):
        return value
    return None


def _mcp_pythonpath() -> str:
    entries = [str(Path(__file__).resolve().parents[3] / "src")]
    if ark_src := os.environ.get("LEAN_CONSTELLATION_ARK_SRC"):
        entries.append(str(Path(ark_src).expanduser()))
    existing = os.environ.get("PYTHONPATH")
    if existing:
        entries.append(existing)
    return os.pathsep.join(entries)
