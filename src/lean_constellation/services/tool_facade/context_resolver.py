"""Runtime context resolution for Agent-facing tools."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import FoundationService, ServiceResult
from lean_constellation.services.node import NodeService
from lean_constellation.services.node.node_tree import NodeKind
from lean_constellation.services.repo_workspace import RepoWorkspaceService


ActorRole = Literal["coordinator", "plan", "worker", "reviewer", "admin", "system"]
AddedBy = Literal["coordinator", "worker"]


class RawToolCallContext(StrictModel):
    """Raw context passed by an MCP endpoint before Lean-specific resolution."""

    endpoint_view_key: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    runtime_context: Any | None = None


class RuntimeToolContext(StrictModel):
    """Lean-normalized subset of ARK's runtime tool context."""

    flow_id: str | None = None
    step_id: str | None = None
    agent_id: str | None = None
    scope_id: str | None = None
    agent_type: str | None = None
    agent_role: ActorRole | None = None
    expected_view_key: str | None = None
    workspace_root: Path | None = None
    repo_root: Path | None = None
    node_path: str | None = None
    node_kind: str | None = None
    contract_version: int | None = None
    stage: str | None = None
    round_id: str | None = None
    batch_decls: list[str] = Field(default_factory=list)
    current_decl: str | None = None
    decl_kind: str | None = None
    retry_attempt: int | None = None
    successful_submission_count: int = 0
    successful_submission_kind: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("repo_root", "workspace_root", mode="before")
    @classmethod
    def _coerce_path(cls, value: Any) -> Path | None:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value)).expanduser()


class RepoContextView(StrictModel):
    repo_key: str
    workspace_key: str | None = None
    main_node: str | None = None
    repo_format: str | None = None
    preparation_input_exists: bool = False
    summary: str


class NodeContextView(StrictModel):
    node_path: str
    node_kind: str | None = None
    contract_version: int | None = None
    summary: str


class DeclStageContextView(StrictModel):
    stage: str
    round_id: str | None = None
    batch_decls: list[str] = Field(default_factory=list)
    current_decl: str | None = None
    retry_attempt: int | None = None
    summary: str


class ActorContext(StrictModel):
    agent_type: str | None = None
    role: ActorRole
    added_by: AddedBy | None = None
    is_admin: bool = False
    summary: str


class ProtectedInterfaceView(StrictModel):
    protected_names: list[str] = Field(default_factory=list)
    protected_kinds: dict[str, str] = Field(default_factory=dict)
    summary: str


class ToolExecutionContext(StrictModel):
    """Resolved Lean context used by ToolFacade internals.

    The absolute paths here are internal service inputs. Agent-facing methods
    should return the smaller view objects instead.
    """

    runtime: RuntimeToolContext
    endpoint_view_key: str
    expected_view_key: str
    repo_root: Path
    workspace_root: Path | None = None
    repo: RepoContextView
    node: NodeContextView | None = None
    decl_stage: DeclStageContextView | None = None
    actor: ActorContext

    @property
    def has_successful_submission(self) -> bool:
        return self.runtime.successful_submission_count > 0


@runtime_checkable
class RuntimeMcpToolGateway(Protocol):
    """Protocol for the ARK gateway used by Lean ToolFacade."""

    def resolve_tool_context(self, raw_context: RawToolCallContext) -> Any:
        ...


class ContextResolverComponent:
    """Resolve ARK runtime identity into Lean repo/node/stage context."""

    def __init__(
        self,
        foundation: FoundationService | None = None,
        *,
        repo_workspace: RepoWorkspaceService | None = None,
        node: NodeService | None = None,
        runtime_gateway: RuntimeMcpToolGateway | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.repo_workspace = repo_workspace
        self.node = node
        self.runtime_gateway = runtime_gateway

    def resolve_tool_context(self, raw_context: RawToolCallContext) -> ServiceResult[ToolExecutionContext]:
        endpoint_view_key = raw_context.endpoint_view_key
        if raw_context.runtime_context is not None:
            runtime_result = self._normalize_runtime_context(raw_context.runtime_context)
        else:
            if self.runtime_gateway is None:
                return self.foundation.fail(
                    self.foundation.issue(
                        "runtime_gateway_missing",
                        "Cannot resolve tool context because no ARK runtime gateway is configured.",
                        suggested_action="Use a RuntimeMcpToolGateway or provide a validated runtime_context in tests.",
                    )
                )
            try:
                raw_runtime = self.runtime_gateway.resolve_tool_context(raw_context)
            except Exception as exc:  # noqa: BLE001 - external boundary.
                return self.foundation.fail(
                    self.foundation.issue("runtime_context_resolution_failed", f"ARK runtime context resolution failed: {exc}")
                )
            runtime_result = self._normalize_runtime_context(raw_runtime)
        if not runtime_result.ok or runtime_result.value is None:
            return self.foundation.fail(runtime_result.issues)
        endpoint_view_key = endpoint_view_key or runtime_result.value.expected_view_key
        if not endpoint_view_key:
            return self.foundation.fail(
                self.foundation.issue("endpoint_view_missing", "Tool endpoint view key is missing from the MCP call context.")
            )
        return self.resolve_from_runtime_context(runtime_result.value, endpoint_view_key=endpoint_view_key)

    def resolve_from_runtime_context(
        self,
        runtime_ctx: RuntimeToolContext | Mapping[str, Any] | object,
        *,
        endpoint_view_key: str,
    ) -> ServiceResult[ToolExecutionContext]:
        normalized = self._normalize_runtime_context(runtime_ctx)
        if not normalized.ok or normalized.value is None:
            return self.foundation.fail(normalized.issues)
        runtime = normalized.value
        if not runtime.expected_view_key:
            return self.foundation.fail(
                self.foundation.issue(
                    "expected_view_missing",
                    "Runtime context does not declare the expected tool view for this step.",
                    suggested_action="Record expected_view_key in the AgentStep state before exposing MCP tools.",
                )
            )
        if endpoint_view_key != runtime.expected_view_key:
            return self.foundation.fail(
                self.foundation.issue(
                    "tool_view_mismatch",
                    "MCP endpoint view does not match the current step expected tool view.",
                    field="endpoint_view_key",
                    current=endpoint_view_key,
                    expected=runtime.expected_view_key,
                )
            )
        if runtime.repo_root is None:
            return self.foundation.fail(
                self.foundation.issue("repo_context_missing", "Runtime context does not include a current repo root.")
            )
        repo_root = Path(runtime.repo_root).expanduser()
        actor = self._actor_from_runtime(runtime)
        repo = self._repo_view_from_runtime(runtime, repo_root)
        node = self._node_view_from_runtime(runtime, repo_root)
        stage = self._stage_view_from_runtime(runtime)
        ctx = ToolExecutionContext(
            runtime=runtime,
            endpoint_view_key=endpoint_view_key,
            expected_view_key=runtime.expected_view_key,
            repo_root=repo_root,
            workspace_root=runtime.workspace_root,
            repo=repo,
            node=node,
            decl_stage=stage,
            actor=actor,
        )
        return self.foundation.ok(ctx)

    def resolve_current_repo(self, ctx: ToolExecutionContext) -> ServiceResult[RepoContextView]:
        if self.repo_workspace is None:
            return self.foundation.ok(ctx.repo)
        state = self.repo_workspace.metadata.get_repo_state_view(ctx.repo_root)
        if not state.ok or state.value is None:
            return self.foundation.fail(state.issues)
        prep_exists = state.value.preparation_input_exists
        return self.foundation.ok(
            RepoContextView(
                repo_key=Path(ctx.repo_root).name,
                workspace_key=Path(ctx.workspace_root).name if ctx.workspace_root else None,
                main_node=state.value.main_node,
                repo_format=state.value.repo_format.value,
                preparation_input_exists=prep_exists,
                summary=state.value.summary or "Resolved current repo context.",
            )
        )

    def resolve_current_node(self, ctx: ToolExecutionContext) -> ServiceResult[NodeContextView]:
        if ctx.node is None:
            return self.foundation.fail(
                self.foundation.issue("node_context_missing", "Current tool context is not bound to a node.")
            )
        if self.node is None:
            return self.foundation.ok(ctx.node)
        view = self.node.node_tree.get_node(ctx.repo_root, path=ctx.node.node_path)
        if not view.ok or view.value is None:
            return self.foundation.fail(view.issues)
        return self.foundation.ok(
            NodeContextView(
                node_path=view.value.path,
                node_kind=view.value.kind.value,
                contract_version=view.value.current_contract_version,
                summary=view.value.summary,
            )
        )

    def resolve_current_decl_stage(self, ctx: ToolExecutionContext) -> ServiceResult[DeclStageContextView]:
        if ctx.decl_stage is None:
            return self.foundation.fail(
                self.foundation.issue("decl_stage_context_missing", "Current tool context is not bound to a decl stage.")
            )
        return self.foundation.ok(ctx.decl_stage)

    def resolve_actor(self, ctx: ToolExecutionContext) -> ServiceResult[ActorContext]:
        return self.foundation.ok(ctx.actor)

    def resolve_root_interface_protection(self, ctx: ToolExecutionContext) -> ServiceResult[ProtectedInterfaceView]:
        if self.repo_workspace is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "repo_workspace_service_missing",
                    "RepoWorkspaceService is required to resolve protected root interfaces.",
                )
            )
        prep = self.repo_workspace.preparation.get_preparation_input(ctx.repo_root)
        if not prep.ok or prep.value is None:
            return self.foundation.fail(prep.issues)
        names = sorted({interface.name for interface in prep.value.input.interface_inputs})
        kinds = {interface.name: interface.kind.value for interface in prep.value.input.interface_inputs}
        return self.foundation.ok(
            ProtectedInterfaceView(
                protected_names=names,
                protected_kinds=kinds,
                summary=f"Resolved {len(names)} protected root interfaces from preparation input.",
            )
        )

    def _normalize_runtime_context(self, raw: Any) -> ServiceResult[RuntimeToolContext]:
        if isinstance(raw, ServiceResult):
            if not raw.ok or raw.value is None:
                return self.foundation.fail(raw.issues)
            raw = raw.value
        if isinstance(raw, RuntimeToolContext):
            return self.foundation.ok(raw)
        if isinstance(raw, Mapping):
            merged = self._extract_nested_runtime_mapping(raw)
            try:
                return self.foundation.ok(RuntimeToolContext.model_validate(merged))
            except Exception as exc:  # noqa: BLE001 - normalize validation.
                return self.foundation.fail(self.foundation.issue("runtime_context_invalid", f"Invalid runtime context: {exc}"))
        data: dict[str, Any] = {}
        for name in RuntimeToolContext.model_fields:
            if hasattr(raw, name):
                data[name] = getattr(raw, name)
        if not data:
            return self.foundation.fail(
                self.foundation.issue("runtime_context_invalid", "Runtime context is not a supported mapping or object.")
            )
        try:
            return self.foundation.ok(RuntimeToolContext.model_validate(data))
        except Exception as exc:  # noqa: BLE001
            return self.foundation.fail(self.foundation.issue("runtime_context_invalid", f"Invalid runtime context: {exc}"))

    def _extract_nested_runtime_mapping(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(raw)
        for nested_key in ("flow_input", "step_state", "scope_metadata", "tool_context"):
            nested = raw.get(nested_key)
            if isinstance(nested, Mapping):
                for key, value in nested.items():
                    data.setdefault(str(key), value)
        return data

    def _actor_from_runtime(self, runtime: RuntimeToolContext) -> ActorContext:
        role = runtime.agent_role or self._infer_role(runtime.agent_type)
        added_by: AddedBy | None
        if role in {"coordinator", "admin", "system"}:
            added_by = "coordinator"
        elif role in {"plan", "worker"}:
            added_by = "worker"
        else:
            added_by = None
        return ActorContext(
            agent_type=runtime.agent_type,
            role=role,
            added_by=added_by,
            is_admin=role == "admin",
            summary=f"Resolved actor role {role}.",
        )

    def _infer_role(self, agent_type: str | None) -> ActorRole:
        key = (agent_type or "").lower()
        if "admin" in key or "debug" in key:
            return "admin"
        if "reviewer" in key or "review" in key:
            return "reviewer"
        if "coordinator" in key:
            return "coordinator"
        if "plan" in key:
            return "plan"
        if "system" in key:
            return "system"
        return "worker"

    def _repo_view_from_runtime(self, runtime: RuntimeToolContext, repo_root: Path) -> RepoContextView:
        return RepoContextView(
            repo_key=repo_root.name,
            workspace_key=Path(runtime.workspace_root).name if runtime.workspace_root else None,
            summary="Resolved current repo from runtime context.",
        )

    def _node_view_from_runtime(self, runtime: RuntimeToolContext, repo_root: Path) -> NodeContextView | None:
        del repo_root
        if not runtime.node_path:
            return None
        node_kind: str | None = None
        if runtime.node_kind:
            try:
                node_kind = NodeKind(runtime.node_kind).value
            except ValueError:
                node_kind = str(runtime.node_kind)
        return NodeContextView(
            node_path=runtime.node_path,
            node_kind=node_kind,
            contract_version=runtime.contract_version,
            summary=f"Resolved node {runtime.node_path} from runtime context.",
        )

    def _stage_view_from_runtime(self, runtime: RuntimeToolContext) -> DeclStageContextView | None:
        if not runtime.stage:
            return None
        return DeclStageContextView(
            stage=runtime.stage,
            round_id=runtime.round_id,
            batch_decls=list(runtime.batch_decls),
            current_decl=runtime.current_decl,
            retry_attempt=runtime.retry_attempt,
            summary=f"Resolved decl stage {runtime.stage}.",
        )
