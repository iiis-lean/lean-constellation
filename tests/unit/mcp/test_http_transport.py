from __future__ import annotations

import json
from pathlib import Path

import anyio
import httpx
import pytest
from agent_runtime_kit.agent.homes import MCP_RESULT_PROFILE_HTTP_HEADER
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lean_constellation.app import LeanAppConfig, RepoRuntimeRegistry
from lean_constellation.mcp.http import create_mcp_http_app
from lean_constellation.mcp.http import create_repo_mcp_http_routes
from tests.unit.mcp._helpers import FakeSubmissionGateway, make_mcp_runtime, runtime_env


@pytest.mark.mcp_http
def test_http_transport_exposes_and_invokes_mcp_tool(tmp_path: Path) -> None:
    runtime = make_mcp_runtime()
    app_result = create_mcp_http_app(runtime, view_keys=["resource_curator"])
    assert app_result.ok and app_result.value is not None

    anyio.run(_exercise_resource_curator_http_mcp, app_result.value, tmp_path)


@pytest.mark.mcp_http
def test_http_transport_accepts_submit_tool(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = make_mcp_runtime(gateway)
    app_result = create_mcp_http_app(runtime, view_keys=["repo_format_discovery_submit"])
    assert app_result.ok and app_result.value is not None

    anyio.run(_exercise_submit_http_mcp, app_result.value, tmp_path)

    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].tool_name == "submit_native_repo_choice"
    assert gateway.accepted[0].submitted_by_agent_id == "agent_http_submit"


@pytest.mark.mcp_http
def test_http_tools_list_exposes_self_contained_repo_discovery_schemas(tmp_path: Path) -> None:
    runtime = make_mcp_runtime()
    app_result = create_mcp_http_app(
        runtime,
        view_keys=[
            "repo_resource_discovery_submit",
            "native_repo_coordinator_submit",
            "repo_mathlib_recon",
        ],
    )
    assert app_result.ok and app_result.value is not None

    anyio.run(_exercise_discovery_schema_http_mcp, app_result.value, tmp_path)


def test_repo_mcp_router_keeps_same_view_managers_repo_local(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    registry = RepoRuntimeRegistry(LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False))
    router, _ = create_repo_mcp_http_routes(registry, view_keys=["resource_curator"])

    async def exercise() -> None:
        repo_a = await router._get_manager("RepoA", "resource_curator")
        repo_b = await router._get_manager("RepoB", "resource_curator")
        assert repo_a.ok and repo_a.value is not None
        assert repo_b.ok and repo_b.value is not None
        assert repo_a.value is not repo_b.value
        assert ("RepoA", "resource_curator") in router._managers
        assert ("RepoB", "resource_curator") in router._managers
        await router.shutdown()

    anyio.run(exercise)


def test_repo_mcp_router_cleanup_repo_closes_only_that_repo_managers(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    registry = RepoRuntimeRegistry(LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False))
    router, _ = create_repo_mcp_http_routes(registry, view_keys=["resource_curator"])

    async def exercise() -> None:
        assert (await router._get_manager("RepoA", "resource_curator")).ok
        assert (await router._get_manager("RepoB", "resource_curator")).ok
        await router.cleanup_repo("RepoA")
        assert ("RepoA", "resource_curator") not in router._managers
        assert ("RepoB", "resource_curator") in router._managers
        await router.shutdown()

    anyio.run(exercise)


async def _exercise_resource_curator_http_mcp(app, tmp_path: Path) -> None:  # noqa: ANN001 - ASGI app boundary.
    env = runtime_env(
        tmp_path,
        view="resource_curator",
        agent_type="ResourceCuratorAgent",
        role="worker",
        agent_id="agent_http",
    )
    headers = _runtime_headers(env)
    headers[MCP_RESULT_PROFILE_HTTP_HEADER] = "content_only"
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=headers,
        ) as client:
            async with streamable_http_client(
                "http://testserver/mcp/views/resource_curator/",
                http_client=client,
                terminate_on_close=False,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    tools = {tool.name: tool for tool in listed.tools}
                    assert "normalize_resource_target" in tools
                    assert tools["normalize_resource_target"].inputSchema["type"] == "object"

                    result = await session.call_tool(
                        "normalize_resource_target",
                        {"target": "https://example.com/paper"},
                    )
                    wrong_view = await session.call_tool(
                        "submit_native_repo_choice",
                        {"summary": "Wrong view."},
                    )

    assert result.isError is False
    assert result.structuredContent is None
    assert json.loads(result.content[0].text)["value"]["kind"] == "web_url"
    assert wrong_view.isError is True
    assert wrong_view.structuredContent is None
    assert json.loads(wrong_view.content[0].text)["ok"] is False


async def _exercise_submit_http_mcp(app, tmp_path: Path) -> None:  # noqa: ANN001 - ASGI app boundary.
    env = runtime_env(
        tmp_path,
        view="repo_format_discovery_submit",
        agent_type="RepoFormatDiscoveryAgent",
        role="coordinator",
        agent_id="agent_http_submit",
    )
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers=_runtime_headers(env),
        ) as client:
            async with streamable_http_client(
                "http://testserver/mcp/views/repo_format_discovery_submit/",
                http_client=client,
                terminate_on_close=False,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "submit_native_repo_choice",
                        {"summary": "Use native.", "searched_targets": ["provider theorem Lean"]},
                    )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is True


async def _exercise_discovery_schema_http_mcp(app, tmp_path: Path) -> None:  # noqa: ANN001 - ASGI app boundary.
    cases = (
        (
            "repo_resource_discovery_submit",
            "RepoResourceDiscoveryAgent",
            "worker",
            "submit_repo_resource_discovery_result",
        ),
        (
            "native_repo_coordinator_submit",
            "CoordinatorAgent",
            "coordinator",
            "submit_repo_exploration",
        ),
        (
            "repo_mathlib_recon",
            "RepoMathlibReconAgent",
            "worker",
            "record_mathlib_batch",
        ),
    )
    schemas: dict[str, dict] = {}

    async with app.router.lifespan_context(app):
        for view, agent_type, role, tool_name in cases:
            env = runtime_env(
                tmp_path,
                view=view,
                agent_type=agent_type,
                role=role,
                agent_id=f"agent_http_{view}",
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                headers=_runtime_headers(env),
            ) as client:
                async with streamable_http_client(
                    f"http://testserver/mcp/views/{view}/",
                    http_client=client,
                    terminate_on_close=False,
                ) as (read_stream, write_stream, _):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        listed = await session.list_tools()
                        tools = {tool.name: tool for tool in listed.tools}
                        schemas[tool_name] = tools[tool_name].inputSchema

    for schema in schemas.values():
        _assert_no_schema_refs(schema)

    candidate = schemas["submit_repo_resource_discovery_result"]["properties"]["candidates"]["items"]
    assert candidate["type"] == "object"
    assert candidate["properties"]["target"]["description"]
    assert "canonical_locator" not in candidate["properties"]

    coordinator = schemas["submit_repo_exploration"]["properties"]
    assert "explorations" not in coordinator
    assert coordinator["resource_objective"]["description"]
    assert coordinator["lean_provider_objective"]["description"]
    assert coordinator["mathlib_objective"]["description"]

    declaration = schemas["record_mathlib_batch"]["properties"]["declarations"]["items"]
    assert declaration["type"] == "object"
    assert set(declaration["properties"]) == {"decl_name", "summary", "source"}


def _runtime_headers(env: dict[str, str]) -> dict[str, str]:
    return {
        "X-Ark-Flow-Id": env["ARK_FLOW_ID"],
        "X-Ark-Step-Id": env["ARK_STEP_ID"],
        "X-Ark-Agent-Id": env["ARK_AGENT_ID"],
        "X-Ark-Scope-Id": env["ARK_SCOPE_ID"],
        "X-Lean-Constellation-Agent-Type": env["LEAN_CONSTELLATION_AGENT_TYPE"],
        "X-Lean-Constellation-Agent-Role": env["LEAN_CONSTELLATION_AGENT_ROLE"],
        "X-Lean-Constellation-Expected-Tool-View": env["LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW"],
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


def _make_repo(workspace: Path, name: str) -> Path:
    repo_root = workspace / name
    (repo_root / ".lean_constellation").mkdir(parents=True)
    return repo_root


def _assert_no_schema_refs(value) -> None:  # noqa: ANN001 - recursive JSON helper.
    if isinstance(value, dict):
        assert "$ref" not in value
        assert "$dynamicRef" not in value
        for child in value.values():
            _assert_no_schema_refs(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_schema_refs(child)
