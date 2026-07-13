"""MCP-facing tool wrapper and invocation pipeline."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients import ToolkitCallResult
from lean_constellation.services.foundation import FoundationContext, ServiceResult, ToolResultView
from lean_constellation.services.tool_facade.context_resolver import RawToolCallContext, ToolExecutionContext, ContextResolverComponent
from lean_constellation.services.tool_facade.permission_guard import PermissionGuardComponent
from lean_constellation.services.tool_facade.submit_submission import SubmitSubmissionComponent
from lean_constellation.services.tool_facade.tool_view import SubmitBehavior, ToolCapability, ToolSpec, ToolSpecView, ToolViewComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class FastMcpViewApp(StrictModel):
    view_key: str
    tool_names: list[str]
    tools: list[ToolSpecView]
    summary: str


class SafeToolkitPathView(StrictModel):
    mode: str
    arg_name: str
    original_value: str
    resolved_value: str
    exists: bool | None = None
    summary: str


class ToolkitProxyToolSpec(StrictModel):
    proxy_tool_name: str
    toolkit_tool_name: str
    path_arg_modes: dict[str, str] = Field(default_factory=dict)


class MCPWrapperComponent:
    """Wrap Core Service APIs into Agent-facing tool calls."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        context_resolver: ContextResolverComponent,
        tool_view: ToolViewComponent,
        permission_guard: PermissionGuardComponent,
        submit_submission: SubmitSubmissionComponent,
        backing_services: Mapping[str, Any] | None = None,
    ) -> None:
        self.runtime = runtime
        self.context_resolver = context_resolver
        self.tool_view = tool_view
        self.permission_guard = permission_guard
        self.submit_submission = submit_submission
        self.backing_services = dict(backing_services or {})

    def register_tool(self, tool_spec: ToolSpec) -> ServiceResult[Any]:
        return self.tool_view.register_tool(tool_spec)

    def get_registered_tool(self, tool_name: str) -> ServiceResult[ToolSpecView]:
        spec = self.tool_view.get_tool(tool_name)
        if not spec.ok or spec.value is None:
            return self.runtime.foundation.fail(spec.issues)
        return self.runtime.foundation.ok(self.tool_view._tool_view(spec.value))

    def list_registered_tools(
        self,
        *,
        group_key: str | None = None,
        capability: str | None = None,
    ) -> ServiceResult[list[ToolSpecView]]:
        views: list[ToolSpecView] = []
        for name in sorted(self.tool_view._tools):
            spec = self.tool_view._tools[name]
            if group_key is not None and group_key not in spec.tool_groups:
                continue
            if capability is not None and spec.capability != ToolCapability(capability):
                continue
            views.append(self.tool_view._tool_view(spec))
        return self.runtime.foundation.ok(views)

    def build_view_fastmcp_app(self, view_key: str) -> ServiceResult[FastMcpViewApp]:
        tools = self.tool_view.list_tools_for_view(view_key)
        if not tools.ok or tools.value is None:
            return self.runtime.foundation.fail(tools.issues)
        return self.runtime.foundation.ok(
            FastMcpViewApp(
                view_key=view_key,
                tool_names=[tool.name for tool in tools.value],
                tools=tools.value,
                summary=f"Built MCP view app for {view_key} with {len(tools.value)} tools.",
            )
        )

    def invoke_tool(
        self,
        raw_context: RawToolCallContext,
        *,
        tool_name: str,
        flat_args: dict[str, Any],
    ) -> ServiceResult[ToolResultView]:
        endpoint_view_key = raw_context.endpoint_view_key
        if not endpoint_view_key:
            ctx_result = self.context_resolver.resolve_tool_context(raw_context)
        else:
            ctx_result = self.context_resolver.resolve_tool_context(raw_context)
        if not ctx_result.ok or ctx_result.value is None:
            return self.runtime.foundation.ok(self.format_tool_error(ctx_result))
        return self._invoke_with_context(ctx_result.value, tool_name=tool_name, flat_args=flat_args)

    def invoke_view_tool(
        self,
        endpoint_view_key: str,
        raw_context: RawToolCallContext,
        *,
        tool_name: str,
        flat_args: dict[str, Any],
    ) -> ServiceResult[ToolResultView]:
        raw = raw_context.model_copy(update={"endpoint_view_key": endpoint_view_key})
        ctx_result = self.context_resolver.resolve_tool_context(raw)
        if not ctx_result.ok or ctx_result.value is None:
            return self.runtime.foundation.ok(self.format_tool_error(ctx_result))
        return self._invoke_with_context(ctx_result.value, tool_name=tool_name, flat_args=flat_args)

    def invoke_toolkit_proxy_tool(
        self,
        ctx: ToolExecutionContext,
        *,
        proxy_tool_name: str,
        flat_args: dict[str, Any],
    ) -> ServiceResult[ToolResultView]:
        safety = self._check_toolkit_proxy_args(ctx, flat_args)
        if not safety.ok:
            return self.runtime.foundation.ok(self.format_tool_error(safety))
        result = self.runtime.external.lean_mcp_toolkit.call_tool(proxy_tool_name, dict(flat_args))
        return self.normalize_toolkit_result(ctx, proxy_tool_name=proxy_tool_name, result=result)

    def resolve_toolkit_path_arg(
        self,
        ctx: ToolExecutionContext,
        *,
        arg_name: str,
        arg_value: str,
        mode: str,
    ) -> ServiceResult[SafeToolkitPathView]:
        mode = mode.strip()
        if mode in {"repo_relative_file", "relative_file"}:
            try:
                rel = self.runtime.foundation.layout.ensure_relative_path(arg_value)
                path = Path(ctx.repo_root) / rel
                self.runtime.foundation.layout.assert_within(ctx.repo_root, path)
            except Exception as exc:  # noqa: BLE001
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("toolkit_path_rejected", f"Unsafe repo-relative path: {exc}", field=arg_name)
                )
            return self.runtime.foundation.ok(
                SafeToolkitPathView(
                    mode="repo_relative_file",
                    arg_name=arg_name,
                    original_value=arg_value,
                    resolved_value=str(path),
                    exists=path.exists(),
                    summary="Resolved safe repo-relative file path.",
                )
            )
        if mode == "decl_owned_file":
            if ctx.node is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("node_context_missing", "Decl-owned file resolution requires a current node.")
                )
            decl_kind = ctx.runtime.decl_kind
            if not decl_kind:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("decl_kind_missing", "Decl-owned file resolution requires runtime decl_kind.")
                )
            try:
                from lean_constellation.services.foundation import DeclFileKey

                path = self.runtime.foundation.layout.decl_file_path(
                    FoundationContext(repo_root=ctx.repo_root),
                    DeclFileKey(node_path=ctx.node.node_path, decl_kind=decl_kind, decl_name=arg_value),
                )
                self.runtime.foundation.layout.assert_within(ctx.repo_root, path)
            except Exception as exc:  # noqa: BLE001
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("toolkit_path_rejected", f"Unsafe Decl-owned file target: {exc}", field=arg_name)
                )
            return self.runtime.foundation.ok(
                SafeToolkitPathView(
                    mode=mode,
                    arg_name=arg_name,
                    original_value=arg_value,
                    resolved_value=str(path),
                    exists=path.exists(),
                    summary="Resolved current Decl-owned file path.",
                )
            )
        if mode in {"adapter_upstream_module", "mathlib_module"}:
            if Path(arg_value).is_absolute() or ".." in Path(arg_value).parts:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("toolkit_module_rejected", "Module arguments cannot be absolute paths or contain '..'.", field=arg_name)
                )
            module = arg_value.strip()
            if not module or any(part.strip() == "" for part in module.split(".")):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("toolkit_module_rejected", "Module name is empty or malformed.", field=arg_name)
                )
            return self.runtime.foundation.ok(
                SafeToolkitPathView(
                    mode=mode,
                    arg_name=arg_name,
                    original_value=arg_value,
                    resolved_value=module,
                    exists=None,
                    summary=f"Accepted {mode} argument.",
                )
            )
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue("toolkit_path_mode_unknown", "Unknown toolkit path guard mode.", field=arg_name, current=mode)
        )

    def normalize_toolkit_result(
        self,
        ctx: ToolExecutionContext,
        *,
        proxy_tool_name: str,
        result: Any,
    ) -> ServiceResult[ToolResultView]:
        del ctx
        if isinstance(result, ToolkitCallResult):
            if not result.ok:
                issue = self.runtime.foundation.issue(result.issue_code or "toolkit_call_failed", result.summary or "Toolkit call failed.")
                return self.runtime.foundation.ok(ToolResultView(ok=False, summary=result.summary or "Toolkit call failed.", issues=[issue]))
            value = result.value
        else:
            value = result
        clean_value = self._sanitize_toolkit_value(value)
        return self.runtime.foundation.ok(
            ToolResultView(
                ok=True,
                summary=f"Toolkit proxy {proxy_tool_name} returned normalized results.",
                value={"tool": proxy_tool_name, "result": clean_value},
            )
        )

    def format_tool_result(self, result: ServiceResult[Any], *, view_kind: str) -> ToolResultView:
        if not result.ok:
            return self.format_tool_error(result)
        value = result.value
        if isinstance(value, ToolResultView):
            return value
        dumped = self._dump_value(value)
        summary = self._summary_from_value(value) or f"{view_kind} succeeded."
        return ToolResultView(ok=True, summary=summary, issues=result.issues, value=dumped)

    def format_tool_error(self, result: ServiceResult[Any]) -> ToolResultView:
        summary = "Tool call failed."
        if result.issues:
            summary = result.issues[0].message
        return ToolResultView(ok=False, summary=summary, issues=result.issues)

    def _invoke_with_context(
        self,
        ctx: ToolExecutionContext,
        *,
        tool_name: str,
        flat_args: dict[str, Any],
    ) -> ServiceResult[ToolResultView]:
        tool_result = self.tool_view.get_tool(tool_name)
        if not tool_result.ok or tool_result.value is None:
            return self.runtime.foundation.ok(self.format_tool_error(tool_result))
        spec = tool_result.value
        allowed = self.permission_guard.assert_tool_allowed(ctx, tool_name=tool_name)
        if not allowed.ok:
            return self.runtime.foundation.ok(self.format_tool_error(allowed))
        args_result = self._validate_args(spec, flat_args)
        if not args_result.ok or args_result.value is None:
            return self.runtime.foundation.ok(self.format_tool_error(args_result))
        if spec.toolkit_proxy_name:
            return self.invoke_toolkit_proxy_tool(ctx, proxy_tool_name=spec.toolkit_proxy_name, flat_args=args_result.value.model_dump())
        core_result = self._call_backing_api(ctx, spec, args_result.value)
        if spec.submit_behavior != SubmitBehavior.NONE:
            if not core_result.ok:
                return self.runtime.foundation.ok(self.format_tool_error(core_result))
            prepared = self.submit_submission.prepare_submission(
                ctx,
                prepared=core_result.value,
                tool_name=spec.name,
            )
            if not prepared.ok or prepared.value is None:
                return self.runtime.foundation.ok(self.format_tool_error(prepared))
            ack = self.submit_submission.record_successful_submission(ctx, submission=prepared.value.submission)
            if not ack.ok or ack.value is None:
                return self.runtime.foundation.ok(self.format_tool_error(ack))
            return self.runtime.foundation.ok(
                ToolResultView(
                    ok=True,
                    summary=ack.value.message,
                    value={
                        **ack.value.model_dump(mode="json"),
                        "agent_view": prepared.value.agent_view,
                    },
                )
            )
        tool_view = self.format_tool_result(core_result, view_kind=spec.result_view)
        return self.runtime.foundation.ok(tool_view)

    def _validate_args(self, spec: ToolSpec, flat_args: dict[str, Any]) -> ServiceResult[BaseModel]:
        try:
            args = spec.args_model.model_validate(flat_args)
        except ValidationError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "tool_arguments_invalid",
                    f"Tool arguments do not match schema: {exc.errors()}",
                    object_ref=spec.name,
                )
            )
        return self.runtime.foundation.ok(args)

    def _call_backing_api(self, ctx: ToolExecutionContext, spec: ToolSpec, args: BaseModel) -> ServiceResult[Any]:
        try:
            if spec.backing_handler is not None:
                value = self._call_handler(spec, ctx, args)
            else:
                service = self.backing_services.get(spec.backing_service)
                if service is None:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "backing_service_missing",
                            "Tool backing service is not configured.",
                            object_ref=spec.backing_service,
                        )
                    )
                target = getattr(service, spec.backing_component) if spec.backing_component else service
                method = getattr(target, spec.backing_method)
                payload = args.model_dump()
                if "repo" in spec.required_context or "repo_root" in spec.required_context:
                    value = method(ctx.repo_root, **payload)
                else:
                    value = method(**payload)
        except Exception as exc:  # noqa: BLE001 - Service boundary.
            return self.runtime.foundation.fail(self.runtime.foundation.issue("backing_tool_call_failed", f"Backing tool call failed: {exc}", object_ref=spec.name))
        if isinstance(value, ServiceResult):
            return value
        return self.runtime.foundation.ok(value)

    def _call_handler(self, spec: ToolSpec, ctx: ToolExecutionContext, args: BaseModel) -> Any:
        assert spec.backing_handler is not None
        return spec.backing_handler(self.runtime, ctx, args)

    def _check_toolkit_proxy_args(self, ctx: ToolExecutionContext, flat_args: dict[str, Any]) -> ServiceResult[None]:
        for key, value in flat_args.items():
            if not isinstance(value, str):
                continue
            lower = key.lower()
            if "path" in lower or "file" in lower:
                safe = self.resolve_toolkit_path_arg(ctx, arg_name=key, arg_value=value, mode="repo_relative_file")
                if not safe.ok:
                    return self.runtime.foundation.fail(safe.issues)
        return self.runtime.foundation.ok(None)

    def _sanitize_toolkit_value(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            value = value.model_dump()
        if isinstance(value, Mapping):
            clean: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                lowered = key_text.lower()
                if any(token in lowered for token in ("traceback", "route", "server_path", "repo_root", "project_root", "local_path")):
                    continue
                clean[key_text] = self._sanitize_toolkit_value(item)
            return clean
        if isinstance(value, list):
            return [self._sanitize_toolkit_value(item) for item in value[:50]]
        if isinstance(value, str):
            return value if len(value) <= 12000 else value[:12000] + "\n...[truncated]"
        return value

    def _dump_value(self, value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if isinstance(value, BaseModel):
            return value.model_dump()
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, list):
            return {"items": value}
        return {"value": value}

    def _summary_from_value(self, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, Mapping):
            summary = value.get("summary")
            return str(summary) if summary else None
        summary = getattr(value, "summary", None)
        return str(summary) if summary else None
