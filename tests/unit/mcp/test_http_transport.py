from __future__ import annotations

from pathlib import Path

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from lean_constellation.mcp.http import create_mcp_http_app
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


async def _exercise_resource_curator_http_mcp(app, tmp_path: Path) -> None:  # noqa: ANN001 - ASGI app boundary.
    env = runtime_env(
        tmp_path,
        view="resource_curator",
        agent_type="ResourceCuratorAgent",
        role="worker",
        agent_id="agent_http",
    )
    headers = _runtime_headers(env)
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
                        {"summary": "Wrong view.", "source_corpus_mode": "prepare"},
                    )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is True
    assert result.structuredContent["value"]["kind"] == "web_url"
    assert wrong_view.isError is True
    assert wrong_view.structuredContent is not None
    assert wrong_view.structuredContent["ok"] is False


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
                        {"summary": "Use native.", "source_corpus_mode": "prepare"},
                    )

    assert result.isError is False
    assert result.structuredContent is not None
    assert result.structuredContent["ok"] is True


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
