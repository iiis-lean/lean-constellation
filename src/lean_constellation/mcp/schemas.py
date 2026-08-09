"""MCP-facing schema views derived from ToolSpec registry entries."""

from __future__ import annotations

from typing import Any

from agent_runtime_kit.agent.provider_contracts.tool_schema import materialize_tool_input_schema
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.tool_facade import ToolSpecView


class McpToolRegistration(StrictModel):
    """Stable MCP registration view for one Lean Constellation tool."""

    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    capability: str
    submit_behavior: str
    result_view: str
    required_context: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)


def mcp_tool_registration_from_view(tool: ToolSpecView) -> McpToolRegistration:
    """Convert a ToolSpecView into the schema shape used by MCP endpoints."""

    return McpToolRegistration(
        name=tool.name,
        description=tool.description,
        input_schema=materialize_tool_input_schema(tool.args_schema),
        capability=str(tool.capability.value),
        submit_behavior=str(tool.submit_behavior.value),
        result_view=tool.result_view,
        required_context=list(tool.required_context),
        allowed_roles=list(tool.allowed_roles),
    )
