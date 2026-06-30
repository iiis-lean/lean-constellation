"""FastMCP-compatible server factory for Lean Constellation ToolViews."""

from __future__ import annotations

from lean_constellation.mcp.context import build_raw_tool_call_context
from lean_constellation.mcp.registration import build_mcp_tool_registrations
from lean_constellation.mcp.schemas import McpToolRegistration
from lean_constellation.services.foundation import ServiceResult, ToolResultView
from lean_constellation.services.runtime import LeanRuntimeServices


class LeanMcpViewEndpoint:
    """In-memory endpoint adapter used by tests and future transport bridges."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        view_key: str,
        tools: list[McpToolRegistration],
    ) -> None:
        self.runtime = runtime
        self.view_key = view_key
        self._tools = {tool.name: tool for tool in tools}

    @property
    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def list_tools(self) -> ServiceResult[list[McpToolRegistration]]:
        return self.runtime.foundation.ok([self._tools[name] for name in self.tool_names])

    def get_tool(self, tool_name: str) -> ServiceResult[McpToolRegistration]:
        tool = self._tools.get(tool_name)
        if tool is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mcp_tool_not_registered", "Tool is not registered on this MCP endpoint.", object_ref=tool_name)
            )
        return self.runtime.foundation.ok(tool)

    def call_tool(
        self,
        tool_name: str,
        arguments: dict,
        *,
        headers: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        runtime_context: object | None = None,
    ) -> ServiceResult[ToolResultView]:
        raw_context = build_raw_tool_call_context(
            endpoint_view_key=self.view_key,
            headers=headers,
            env=env,
            runtime_context=runtime_context,
        )
        return self.runtime.tool_facade.invoke_agent_tool(
            raw_context,
            tool_name=tool_name,
            flat_args=dict(arguments),
        )


class LeanMcpServer:
    """Multi-view MCP server model with one isolated endpoint per ToolView."""

    transport: str = "in_memory_fastmcp_compatible"

    def __init__(self, runtime: LeanRuntimeServices, *, endpoints: list[LeanMcpViewEndpoint]) -> None:
        self.runtime = runtime
        self._endpoints = {endpoint.view_key: endpoint for endpoint in endpoints}

    def list_endpoints(self) -> list[str]:
        return sorted(self._endpoints)

    def endpoint(self, view_key: str) -> ServiceResult[LeanMcpViewEndpoint]:
        endpoint = self._endpoints.get(view_key)
        if endpoint is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("mcp_endpoint_not_registered", "MCP endpoint is not registered.", object_ref=view_key)
            )
        return self.runtime.foundation.ok(endpoint)

    def list_tools(self, view_key: str) -> ServiceResult[list[McpToolRegistration]]:
        endpoint = self.endpoint(view_key)
        if not endpoint.ok or endpoint.value is None:
            return self.runtime.foundation.fail(endpoint.issues)
        return endpoint.value.list_tools()

    def call_tool(
        self,
        view_key: str,
        tool_name: str,
        arguments: dict,
        *,
        headers: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
        runtime_context: object | None = None,
    ) -> ServiceResult[ToolResultView]:
        endpoint = self.endpoint(view_key)
        if not endpoint.ok or endpoint.value is None:
            return self.runtime.foundation.fail(endpoint.issues)
        return endpoint.value.call_tool(
            tool_name,
            arguments,
            headers=headers,
            env=env,
            runtime_context=runtime_context,
        )


def create_fastmcp_server(
    runtime: LeanRuntimeServices,
    *,
    view_keys: list[str] | None = None,
) -> ServiceResult[LeanMcpServer]:
    """Create a FastMCP-compatible server from registered ToolView endpoints."""

    keys = view_keys if view_keys is not None else sorted(runtime.tool_facade.tool_view._views)
    endpoints: list[LeanMcpViewEndpoint] = []
    for view_key in keys:
        registrations = build_mcp_tool_registrations(runtime, view_key=view_key)
        if not registrations.ok or registrations.value is None:
            return runtime.foundation.fail(registrations.issues)
        endpoints.append(LeanMcpViewEndpoint(runtime, view_key=view_key, tools=registrations.value))
    return runtime.foundation.ok(LeanMcpServer(runtime, endpoints=endpoints))


def create_mcp_server(
    runtime: LeanRuntimeServices,
    *,
    view_keys: list[str] | None = None,
) -> ServiceResult[LeanMcpServer]:
    """Alias used by application bootstrap code."""

    return create_fastmcp_server(runtime, view_keys=view_keys)
