"""ToolFacadeService composition and public wrappers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lean_constellation.services.foundation import MutationSummaryView, ServiceResult, ToolResultView
from lean_constellation.services.tool_facade.context_resolver import (
    ContextResolverComponent,
    RawToolCallContext,
    RuntimeMcpToolGateway,
)
from lean_constellation.services.tool_facade.mcp_wrapper import FastMcpViewApp, MCPWrapperComponent
from lean_constellation.services.tool_facade.permission_guard import PermissionGuardComponent
from lean_constellation.services.tool_facade.submit_submission import ArkRuntimeSubmissionGatewayAdapter, RuntimeSubmissionGateway, SubmitSubmissionComponent
from lean_constellation.services.tool_facade.tool_view import ToolGroupSpec, ToolSpec, ToolSpecView, ToolViewComponent, ToolViewSpec

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class ToolFacadeService:
    """Composition root for Agent-facing tool registry, views, permissions, and MCP calls."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        context_resolver: ContextResolverComponent | None = None,
        tool_view: ToolViewComponent | None = None,
        permission_guard: PermissionGuardComponent | None = None,
        submit_submission: SubmitSubmissionComponent | None = None,
        mcp_wrapper: MCPWrapperComponent | None = None,
        runtime_gateway: RuntimeMcpToolGateway | None = None,
        submission_gateway: RuntimeSubmissionGateway | None = None,
        backing_services: dict[str, Any] | None = None,
    ) -> None:
        self.runtime = runtime
        self.tool_view = tool_view or ToolViewComponent(runtime)
        repo_workspace = self.runtime.repo_workspace
        node = self.runtime.node
        self.context_resolver = context_resolver or ContextResolverComponent(
            runtime,
            repo_workspace=repo_workspace,
            node=node,
            runtime_gateway=runtime_gateway,
        )
        self.permission_guard = permission_guard or PermissionGuardComponent(
            runtime,
            tool_view=self.tool_view,
        )
        self.submit_submission = submit_submission or SubmitSubmissionComponent(
            runtime,
            tool_view=self.tool_view,
            submission_gateway=submission_gateway or self._submission_gateway_from_runtime_gateway(runtime_gateway),
        )
        default_backing = {
            "foundation": self.runtime.foundation,
            "external": self.runtime.external,
            "repo_workspace": repo_workspace,
            "material": self.runtime.material,
            "node": node,
            "mathlib": self.runtime.mathlib,
            "lean_projection": self.runtime.lean_projection,
            "adapter": self.runtime.adapter,
            "decl_graph": self.runtime.decl_graph,
            "validation_snapshot": self.runtime.validation_snapshot,
        }
        if backing_services:
            default_backing.update(backing_services)
        self.mcp_wrapper = mcp_wrapper or MCPWrapperComponent(
            runtime,
            context_resolver=self.context_resolver,
            tool_view=self.tool_view,
            permission_guard=self.permission_guard,
            submit_submission=self.submit_submission,
            backing_services=default_backing,
        )

    def build_tool_view(self, agent_type: str, context: dict[str, Any] | None = None) -> ServiceResult[ToolViewSpec]:
        context = context or {}
        return self.tool_view.get_tool_view(
            agent_type,
            flow_kind=context.get("flow_kind"),
            stage=context.get("stage"),
        )

    def build_mcp_view_server(self, view_key: str) -> ServiceResult[FastMcpViewApp]:
        return self.mcp_wrapper.build_view_fastmcp_app(view_key)

    def invoke_agent_tool(
        self,
        raw_context: RawToolCallContext,
        *,
        tool_name: str,
        flat_args: dict[str, Any],
    ) -> ServiceResult[ToolResultView]:
        return self.mcp_wrapper.invoke_tool(raw_context, tool_name=tool_name, flat_args=flat_args)

    def get_registered_tool(self, tool_name: str) -> ServiceResult[ToolSpecView]:
        return self.mcp_wrapper.get_registered_tool(tool_name)

    def list_registered_tools(
        self,
        *,
        group_key: str | None = None,
        capability: str | None = None,
    ) -> ServiceResult[list[ToolSpecView]]:
        return self.mcp_wrapper.list_registered_tools(group_key=group_key, capability=capability)

    def register_application_tools(self, tool_specs: list[ToolSpec]) -> ServiceResult[MutationSummaryView]:
        changed: list[str] = []
        for tool_spec in tool_specs:
            result = self.mcp_wrapper.register_tool(tool_spec)
            if not result.ok or result.value is None:
                return self.runtime.foundation.fail(result.issues)
            changed.append(tool_spec.name)
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref="application_tools",
                changed=bool(changed),
                summary=f"Registered {len(changed)} application tools.",
                changed_items=changed,
            )
        )

    def register_tool_groups(self, group_specs: list[ToolGroupSpec]) -> ServiceResult[MutationSummaryView]:
        return self.tool_view.register_tool_groups(group_specs)

    def register_tool_views(self, view_specs: list[ToolViewSpec]) -> ServiceResult[MutationSummaryView]:
        return self.tool_view.register_tool_views(view_specs)

    @staticmethod
    def _submission_gateway_from_runtime_gateway(runtime_gateway: RuntimeMcpToolGateway | None) -> RuntimeSubmissionGateway | None:
        if runtime_gateway is None or not hasattr(runtime_gateway, "accept_step_submission"):
            return None
        return ArkRuntimeSubmissionGatewayAdapter(runtime_gateway)
