from __future__ import annotations

from lean_constellation.mcp import create_mcp_server
from lean_constellation.mcp.stdio import create_mcp_protocol_server, mcp_protocol_call_tool, mcp_protocol_tools
from tests.unit.mcp._helpers import FakeSubmissionGateway, make_mcp_runtime, runtime_env


def test_stdio_bridge_exposes_tools_with_tool_spec_schema(tmp_path) -> None:
    runtime = make_mcp_runtime()
    protocol = create_mcp_protocol_server(runtime, view_key="repo_format_discovery_submit")
    assert protocol.ok and protocol.value is not None
    server = create_mcp_server(runtime, view_keys=["repo_format_discovery_submit"])
    endpoint = server.value.endpoint("repo_format_discovery_submit").value

    tools = {tool.name: tool for tool in mcp_protocol_tools(endpoint)}

    assert "submit_native_repo_choice" in tools
    native = tools["submit_native_repo_choice"]
    assert native.inputSchema["required"] == ["summary"]
    assert "source_corpus_mode" not in native.inputSchema["properties"]
    assert "native_repo_name" not in native.inputSchema["properties"]


def test_stdio_bridge_converts_tool_result_for_success_and_gate_failure(tmp_path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = make_mcp_runtime(gateway)
    server = create_mcp_server(runtime, view_keys=["repo_format_discovery_submit"])
    endpoint = server.value.endpoint("repo_format_discovery_submit").value
    env = runtime_env(
        tmp_path,
        view="repo_format_discovery_submit",
        agent_type="RepoFormatDiscoveryAgent",
        role="coordinator",
        agent_id="agent_stdio",
    )

    rejected = mcp_protocol_call_tool(
        endpoint,
        "submit_adapter_repo_choice",
        {"git_url": "https://example.com/project", "evidence_summary": "Remote probe found a lakefile."},
        env=env,
    )
    assert rejected.isError is True
    assert rejected.structuredContent["ok"] is False
    assert rejected.structuredContent["issues"][0]["kind"] == "git_url_invalid"

    submitted = mcp_protocol_call_tool(
        endpoint,
        "submit_native_repo_choice",
        {"summary": "Use native.", "searched_targets": ["No repo found"], "rejected_candidates": []},
        env=env,
    )
    assert submitted.isError is False
    assert submitted.structuredContent["ok"] is True
    assert len(gateway.accepted) == 1
