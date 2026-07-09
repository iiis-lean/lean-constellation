from __future__ import annotations

from lean_constellation.mcp import create_mcp_server
from tests.unit.mcp._helpers import make_mcp_runtime, runtime_env


def test_tool_view_endpoint_exposes_only_its_tool_allowlist(tmp_path) -> None:
    runtime = make_mcp_runtime()
    server = create_mcp_server(runtime, view_keys=["resource_curator", "content_plan_submit"])
    assert server.ok and server.value is not None

    resource_tools = {tool.name for tool in server.value.list_tools("resource_curator").value or []}
    content_submit_tools = {tool.name for tool in server.value.list_tools("content_plan_submit").value or []}

    assert "normalize_resource_target" in resource_tools
    assert "submit_resource_request" not in resource_tools
    assert "submit_resource_request" in content_submit_tools
    assert "submit_native_repo_choice" not in content_submit_tools

    denied = server.value.call_tool(
        "resource_curator",
        "submit_native_repo_choice",
        {"summary": "Use native."},
        env=runtime_env(tmp_path, view="resource_curator", agent_type="resource_curator", role="worker"),
    )

    assert denied.ok
    assert denied.value is not None
    assert denied.value.ok is False
    assert denied.value.issues[0].kind == "tool_not_in_view"
