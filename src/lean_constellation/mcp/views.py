"""MCP endpoint views over ToolView registry entries."""

from __future__ import annotations

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices


class McpEndpointView(StrictModel):
    view_key: str
    tool_names: list[str] = Field(default_factory=list)
    summary: str


def build_mcp_endpoint_views(
    runtime: LeanRuntimeServices,
    *,
    view_keys: list[str] | None = None,
) -> ServiceResult[list[McpEndpointView]]:
    """Build endpoint metadata for one or more registered ToolViews."""

    keys = view_keys if view_keys is not None else sorted(runtime.tool_facade.tool_view._views)
    endpoints: list[McpEndpointView] = []
    for view_key in keys:
        tool_names = runtime.tool_facade.tool_view.tool_names_for_view(view_key)
        if not tool_names.ok or tool_names.value is None:
            return runtime.foundation.fail(tool_names.issues)
        endpoints.append(
            McpEndpointView(
                view_key=view_key,
                tool_names=tool_names.value,
                summary=f"MCP endpoint {view_key} exposes {len(tool_names.value)} tools.",
            )
        )
    return runtime.foundation.ok(endpoints)
