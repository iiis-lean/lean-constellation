from __future__ import annotations

from lean_constellation.mcp import create_fastmcp_server
from tests.unit.mcp._helpers import make_mcp_runtime


def test_fastmcp_server_factory_builds_multiple_view_endpoints() -> None:
    runtime = make_mcp_runtime()

    server = create_fastmcp_server(
        runtime,
        view_keys=["resource_curator", "repo_format_discovery_submit", "content_plan_submit"],
    )

    assert server.ok
    assert server.value is not None
    assert server.value.transport == "in_memory_fastmcp_compatible"
    assert server.value.list_endpoints() == [
        "content_plan_submit",
        "repo_format_discovery_submit",
        "resource_curator",
    ]

    resource_tools = {tool.name for tool in server.value.list_tools("resource_curator").value or []}
    submit_tools = {tool.name for tool in server.value.list_tools("repo_format_discovery_submit").value or []}
    assert "normalize_resource_target" in resource_tools
    assert "submit_native_repo_choice" not in resource_tools
    assert {"submit_adapter_repo_choice", "submit_native_repo_choice"} <= submit_tools
