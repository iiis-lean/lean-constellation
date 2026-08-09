"""ToolSpec-to-MCP registration helpers."""

from __future__ import annotations

from agent_runtime_kit.agent.provider_contracts.tool_schema import ToolSchemaMaterializationError

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
    registrations: list[McpToolRegistration] = []
    for tool in app.value.tools:
        try:
            registrations.append(mcp_tool_registration_from_view(tool))
        except ToolSchemaMaterializationError as exc:
            details = {"code": exc.code}
            if exc.path:
                details["path"] = _format_schema_path(exc.path)
            if exc.ref is not None:
                details["ref"] = exc.ref
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "tool_schema_materialization_failed",
                    f"Tool input schema cannot be exposed safely: {exc}",
                    object_ref=tool.name,
                    field="input_schema",
                    details=details,
                )
            )
    return runtime.foundation.ok(registrations)


def _format_schema_path(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)
