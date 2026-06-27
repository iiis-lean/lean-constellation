"""ToolFacadeService composition and public wrappers."""

from __future__ import annotations

from typing import Any

from lean_constellation.services.external_clients import ExternalClientService
from lean_constellation.services.foundation import FoundationService, MutationSummaryView, ServiceResult, ToolResultView
from lean_constellation.services.node import NodeService
from lean_constellation.services.repo_workspace import RepoWorkspaceService
from lean_constellation.services.tool_facade.context_resolver import (
    ContextResolverComponent,
    RawToolCallContext,
    RuntimeMcpToolGateway,
)
from lean_constellation.services.tool_facade.mcp_wrapper import FastMcpViewApp, MCPWrapperComponent
from lean_constellation.services.tool_facade.permission_guard import PermissionGuardComponent
from lean_constellation.services.tool_facade.submit_submission import RuntimeSubmissionGateway, SubmitSubmissionComponent
from lean_constellation.services.tool_facade.tool_view import ToolGroupSpec, ToolSpec, ToolSpecView, ToolViewComponent, ToolViewSpec


class ToolFacadeService:
    """Composition root for Agent-facing tool registry, views, permissions, and MCP calls."""

    def __init__(
        self,
        *,
        foundation: FoundationService | None = None,
        external: ExternalClientService | None = None,
        repo_workspace: RepoWorkspaceService | None = None,
        node: NodeService | None = None,
        context_resolver: ContextResolverComponent | None = None,
        tool_view: ToolViewComponent | None = None,
        permission_guard: PermissionGuardComponent | None = None,
        submit_submission: SubmitSubmissionComponent | None = None,
        mcp_wrapper: MCPWrapperComponent | None = None,
        runtime_gateway: RuntimeMcpToolGateway | None = None,
        submission_gateway: RuntimeSubmissionGateway | None = None,
        backing_services: dict[str, Any] | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.external = external or ExternalClientService()
        self.repo_workspace = repo_workspace or RepoWorkspaceService(foundation=self.foundation, external=self.external)
        self.node = node or NodeService(foundation=self.foundation, repo_workspace=self.repo_workspace)
        self.tool_view = tool_view or ToolViewComponent(self.foundation)
        self.context_resolver = context_resolver or ContextResolverComponent(
            self.foundation,
            repo_workspace=self.repo_workspace,
            node=self.node,
            runtime_gateway=runtime_gateway,
        )
        self.permission_guard = permission_guard or PermissionGuardComponent(
            self.foundation,
            tool_view=self.tool_view,
        )
        self.submit_submission = submit_submission or SubmitSubmissionComponent(
            self.foundation,
            tool_view=self.tool_view,
            submission_gateway=submission_gateway,
        )
        default_backing = {
            "foundation": self.foundation,
            "external": self.external,
            "repo_workspace": self.repo_workspace,
            "node": self.node,
        }
        if backing_services:
            default_backing.update(backing_services)
        self.mcp_wrapper = mcp_wrapper or MCPWrapperComponent(
            self.foundation,
            context_resolver=self.context_resolver,
            tool_view=self.tool_view,
            permission_guard=self.permission_guard,
            submit_submission=self.submit_submission,
            external=self.external,
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
                return self.foundation.fail(result.issues)
            changed.append(tool_spec.name)
        return self.foundation.ok(
            self.foundation.mutation_view(
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
