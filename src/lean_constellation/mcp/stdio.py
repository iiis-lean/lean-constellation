"""stdio MCP bridge for Lean Constellation ToolView endpoints."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from lean_constellation.mcp.server import LeanMcpViewEndpoint, create_mcp_server
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices


def create_mcp_protocol_server(runtime: LeanRuntimeServices, *, view_key: str) -> ServiceResult[Server]:
    """Create a low-level MCP server for one ToolView endpoint."""

    server_result = create_mcp_server(runtime, view_keys=[view_key])
    if not server_result.ok or server_result.value is None:
        return runtime.foundation.fail(server_result.issues)
    endpoint_result = server_result.value.endpoint(view_key)
    if not endpoint_result.ok or endpoint_result.value is None:
        return runtime.foundation.fail(endpoint_result.issues)

    endpoint = endpoint_result.value
    protocol_server = Server(f"lean-constellation-{view_key}")

    @protocol_server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return mcp_protocol_tools(endpoint)

    @protocol_server.call_tool(validate_input=True)
    async def call_tool(tool_name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        return mcp_protocol_call_tool(
            endpoint,
            tool_name,
            arguments,
            headers=_current_request_headers(protocol_server),
            env=dict(os.environ),
        )

    return runtime.foundation.ok(protocol_server)


def mcp_protocol_tools(endpoint: LeanMcpViewEndpoint) -> list[types.Tool]:
    """Convert a Lean ToolView endpoint into MCP Tool definitions."""

    listed = endpoint.list_tools()
    if not listed.ok or listed.value is None:
        message = "; ".join(issue.message for issue in listed.issues) or "MCP endpoint tool listing failed."
        raise RuntimeError(message)
    return [
        types.Tool(
            name=tool.name,
            description=tool.description,
            inputSchema=tool.input_schema or {"type": "object", "properties": {}},
        )
        for tool in listed.value
    ]


def mcp_protocol_call_tool(
    endpoint: LeanMcpViewEndpoint,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    env: dict[str, str] | None = None,
) -> types.CallToolResult:
    """Call a Lean MCP endpoint and convert the result to MCP protocol output."""

    result = endpoint.call_tool(tool_name, arguments, headers=headers, env=env or {})
    if not result.ok or result.value is None:
        summary = _issues_summary(result)
        return _call_tool_result(
            {"ok": False, "summary": summary, "issues": _dump_issues(result)},
            is_error=True,
        )
    tool_result = result.value
    structured = tool_result.model_dump(mode="json")
    return _call_tool_result(structured, is_error=not tool_result.ok)


async def run_mcp_stdio_server(runtime: LeanRuntimeServices, *, view_key: str) -> None:
    """Run one ToolView endpoint as an MCP stdio server."""

    server = create_mcp_protocol_server(runtime, view_key=view_key)
    if not server.ok or server.value is None:
        raise RuntimeError(_issues_summary(server))
    async with stdio_server() as (read_stream, write_stream):
        await server.value.run(
            read_stream,
            write_stream,
            server.value.create_initialization_options(),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a Lean Constellation ToolView as an MCP stdio server.")
    parser.add_argument("--config", type=Path, default=None, help="Path to a Lean Constellation JSON/TOML app config.")
    parser.add_argument(
        "--view-key",
        default=os.environ.get("LEAN_CONSTELLATION_MCP_VIEW_KEY"),
        help="ToolView key to expose on this MCP stdio server.",
    )
    args = parser.parse_args(argv)
    if not args.view_key:
        parser.error("--view-key or LEAN_CONSTELLATION_MCP_VIEW_KEY is required")
    from lean_constellation.app.config import load_app_config
    from lean_constellation.app.runtime import create_app_runtime_from_config

    config = load_app_config(args.config)
    runtime = create_app_runtime_from_config(
        config,
        test_control_enabled=os.environ.get("LEAN_CONSTELLATION_TEST_CONTROL_ENABLED") == "1",
    )
    anyio.run(lambda: run_mcp_stdio_server(runtime, view_key=args.view_key))
    return 0


def _call_tool_result(structured: dict[str, Any], *, is_error: bool) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(structured, ensure_ascii=False, sort_keys=True))],
        structuredContent=structured,
        isError=is_error,
    )


def _issues_summary(result: ServiceResult[Any]) -> str:
    if not result.issues:
        return "MCP tool call failed."
    return "; ".join(issue.message for issue in result.issues)


def _dump_issues(result: ServiceResult[Any]) -> list[dict[str, Any]]:
    return [issue.model_dump(mode="json") for issue in result.issues]


def _current_request_headers(protocol_server: Server) -> dict[str, str]:
    try:
        request = protocol_server.request_context.request
    except LookupError:
        return {}
    if request is None:
        return {}
    headers = getattr(request, "headers", None)
    if headers is None:
        return {}
    items = headers.items() if hasattr(headers, "items") else []
    return {str(key): str(value) for key, value in items}


__all__ = [
    "create_mcp_protocol_server",
    "main",
    "mcp_protocol_call_tool",
    "mcp_protocol_tools",
    "run_mcp_stdio_server",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
