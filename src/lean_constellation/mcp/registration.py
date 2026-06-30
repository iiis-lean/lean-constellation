"""ToolSpec-to-MCP registration helpers."""

from __future__ import annotations

from lean_constellation.mcp.schemas import McpToolRegistration, mcp_tool_registration_from_view
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices


def build_mcp_tool_registrations(
    runtime: LeanRuntimeServices,
    *,
    view_key: str,
) -> ServiceResult[list[McpToolRegistration]]:
    """Build MCP registrations for exactly one ToolView endpoint."""

    app = runtime.tool_facade.build_mcp_view_server(view_key)
    if not app.ok or app.value is None:
        return runtime.foundation.fail(app.issues)
    return runtime.foundation.ok([mcp_tool_registration_from_view(tool) for tool in app.value.tools])
