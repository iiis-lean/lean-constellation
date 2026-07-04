"""Runtime context resolution for Agent-facing tools."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.node import NodeService
from lean_constellation.services.node.node_tree import NodeKind
from lean_constellation.services.repo_workspace import RepoWorkspaceService

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


ActorRole = Literal["coordinator", "plan", "worker", "reviewer", "admin", "system"]
AddedBy = Literal["coordinator", "worker"]


class RawToolCallContext(StrictModel):
    """Raw context passed by an MCP endpoint before Lean-specific resolution."""

    endpoint_view_key: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    runtime_context: Any | None = None
    expected_repo_key: str | None = None
    expected_repo_root: Path | None = None

    @field_validator("expected_repo_root", mode="before")
    @classmethod
    def _coerce_expected_repo_root(cls, value: Any) -> Path | None:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value)).expanduser()


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
        runtime: LeanRuntimeServices,
        *,
        repo_workspace: RepoWorkspaceService | None = None,
        node: NodeService | None = None,
        runtime_gateway: RuntimeMcpToolGateway | None = None,
    ) -> None:
        self.runtime = runtime
        self._repo_workspace_override = repo_workspace
        self._node_override = node
        self.runtime_gateway = runtime_gateway

    @property
    def repo_workspace(self) -> RepoWorkspaceService:
        return self._repo_workspace_override or self.runtime.repo_workspace

    @property
    def node(self) -> NodeService:
        return self._node_override or self.runtime.node

    def resolve_tool_context(self, raw_context: RawToolCallContext) -> ServiceResult[ToolExecutionContext]:
        endpoint_view_key = raw_context.endpoint_view_key
        if raw_context.runtime_context is not None:
            runtime_result = self._normalize_runtime_context(raw_context.runtime_context)
        else:
            metadata_result = self._runtime_context_from_call_metadata(raw_context, endpoint_view_key=endpoint_view_key)
            if metadata_result.ok and metadata_result.value is not None:
                runtime_result = metadata_result
            elif self.runtime_gateway is None:
                return self.runtime.foundation.fail(metadata_result.issues)
            else:
                try:
                    raw_runtime = self.runtime_gateway.resolve_tool_context(raw_context)
                except Exception as exc:  # noqa: BLE001 - external boundary.
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue("runtime_context_resolution_failed", f"ARK runtime context resolution failed: {exc}")
                    )
                runtime_result = self._normalize_runtime_context(raw_runtime)
        if not runtime_result.ok or runtime_result.value is None:
            return self.runtime.foundation.fail(runtime_result.issues)
        expected_repo = self._validate_expected_repo(raw_context, runtime_result.value)
        if not expected_repo.ok:
            return self.runtime.foundation.fail(expected_repo.issues)
        endpoint_view_key = endpoint_view_key or runtime_result.value.expected_view_key
        if not endpoint_view_key:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("endpoint_view_missing", "Tool endpoint view key is missing from the MCP call context.")
            )
        return self.resolve_from_runtime_context(runtime_result.value, endpoint_view_key=endpoint_view_key)

    def _validate_expected_repo(
        self,
        raw_context: RawToolCallContext,
        runtime: RuntimeToolContext,
    ) -> ServiceResult[None]:
        if raw_context.expected_repo_key is None and raw_context.expected_repo_root is None:
            return self.runtime.foundation.ok(None)
        if runtime.repo_root is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "runtime_repo_missing_for_route",
                    "MCP route expected a repo, but runtime context did not resolve a repo root.",
                    expected=str(raw_context.expected_repo_root or raw_context.expected_repo_key),
                )
            )
        actual_root = Path(runtime.repo_root).expanduser().resolve()
        if raw_context.expected_repo_root is not None:
            expected_root = Path(raw_context.expected_repo_root).expanduser().resolve()
            if actual_root != expected_root:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "runtime_repo_route_mismatch",
                        "MCP route repo does not match the resolved runtime repo root.",
                        field="repo_root",
                        current=str(actual_root),
                        expected=str(expected_root),
                    )
                )
        if raw_context.expected_repo_key is not None and actual_root.name != raw_context.expected_repo_key:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "runtime_repo_key_route_mismatch",
                    "MCP route repo key does not match the resolved runtime repo root.",
                    field="repo_key",
                    current=actual_root.name,
                    expected=raw_context.expected_repo_key,
                )
            )
        return self.runtime.foundation.ok(None)

    def resolve_from_runtime_context(
        self,
        runtime_ctx: RuntimeToolContext | Mapping[str, Any] | object,
        *,
        endpoint_view_key: str,
    ) -> ServiceResult[ToolExecutionContext]:
        normalized = self._normalize_runtime_context(runtime_ctx)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        runtime = normalized.value
        if not runtime.expected_view_key:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "expected_view_missing",
                    "Runtime context does not declare the expected tool view for this step.",
                    suggested_action="Record expected_view_key in the AgentStep state before exposing MCP tools.",
                )
            )
        if endpoint_view_key != runtime.expected_view_key:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "tool_view_mismatch",
                    "MCP endpoint view does not match the current step expected tool view.",
                    field="endpoint_view_key",
                    current=endpoint_view_key,
                    expected=runtime.expected_view_key,
                )
            )
        if runtime.repo_root is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("repo_context_missing", "Runtime context does not include a current repo root.")
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
        return self.runtime.foundation.ok(ctx)

    def resolve_current_repo(self, ctx: ToolExecutionContext) -> ServiceResult[RepoContextView]:
        if self.repo_workspace is None:
            return self.runtime.foundation.ok(ctx.repo)
        state = self.repo_workspace.metadata.get_repo_state_view(ctx.repo_root)
        if not state.ok or state.value is None:
            return self.runtime.foundation.fail(state.issues)
        prep_exists = state.value.preparation_input_exists
        return self.runtime.foundation.ok(
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
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_context_missing", "Current tool context is not bound to a node.")
            )
        if self.node is None:
            return self.runtime.foundation.ok(ctx.node)
        view = self.node.node_tree.get_node(ctx.repo_root, path=ctx.node.node_path)
        if not view.ok or view.value is None:
            return self.runtime.foundation.fail(view.issues)
        return self.runtime.foundation.ok(
            NodeContextView(
                node_path=view.value.path,
                node_kind=view.value.kind.value,
                contract_version=view.value.current_contract_version,
                summary=view.value.summary,
            )
        )

    def resolve_current_decl_stage(self, ctx: ToolExecutionContext) -> ServiceResult[DeclStageContextView]:
        if ctx.decl_stage is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_stage_context_missing", "Current tool context is not bound to a decl stage.")
            )
        return self.runtime.foundation.ok(ctx.decl_stage)

    def resolve_actor(self, ctx: ToolExecutionContext) -> ServiceResult[ActorContext]:
        return self.runtime.foundation.ok(ctx.actor)

    def resolve_root_interface_protection(self, ctx: ToolExecutionContext) -> ServiceResult[ProtectedInterfaceView]:
        if self.repo_workspace is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_workspace_service_missing",
                    "RepoWorkspaceService is required to resolve protected root interfaces.",
                )
            )
        prep = self.repo_workspace.preparation.get_preparation_input(ctx.repo_root)
        if not prep.ok or prep.value is None:
            return self.runtime.foundation.fail(prep.issues)
        names = sorted({interface.name for interface in prep.value.input.interface_inputs})
        kinds = {interface.name: interface.kind.value for interface in prep.value.input.interface_inputs}
        return self.runtime.foundation.ok(
            ProtectedInterfaceView(
                protected_names=names,
                protected_kinds=kinds,
                summary=f"Resolved {len(names)} protected root interfaces from preparation input.",
            )
        )

    def _normalize_runtime_context(self, raw: Any) -> ServiceResult[RuntimeToolContext]:
        if isinstance(raw, ServiceResult):
            if not raw.ok or raw.value is None:
                return self.runtime.foundation.fail(raw.issues)
            raw = raw.value
        if isinstance(raw, RuntimeToolContext):
            return self.runtime.foundation.ok(raw)
        if isinstance(raw, Mapping):
            merged = self._extract_nested_runtime_mapping(raw)
            try:
                return self.runtime.foundation.ok(RuntimeToolContext.model_validate(merged))
            except Exception as exc:  # noqa: BLE001 - normalize validation.
                return self.runtime.foundation.fail(self.runtime.foundation.issue("runtime_context_invalid", f"Invalid runtime context: {exc}"))
        data: dict[str, Any] = {}
        for name in RuntimeToolContext.model_fields:
            if hasattr(raw, name):
                data[name] = getattr(raw, name)
        if not data:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("runtime_context_invalid", "Runtime context is not a supported mapping or object.")
            )
        try:
            return self.runtime.foundation.ok(RuntimeToolContext.model_validate(data))
        except Exception as exc:  # noqa: BLE001
            return self.runtime.foundation.fail(self.runtime.foundation.issue("runtime_context_invalid", f"Invalid runtime context: {exc}"))

    def _extract_nested_runtime_mapping(self, raw: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(raw)
        for nested_key in ("flow_input", "step_state", "scope_metadata", "tool_context"):
            nested = raw.get(nested_key)
            if isinstance(nested, Mapping):
                for key, value in nested.items():
                    data.setdefault(str(key), value)
        return data

    def _runtime_context_from_call_metadata(
        self,
        raw_context: RawToolCallContext,
        *,
        endpoint_view_key: str | None,
    ) -> ServiceResult[RuntimeToolContext]:
        metadata = self._merged_call_metadata(raw_context)
        if not metadata:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "runtime_context_metadata_missing",
                    "MCP call metadata does not include ARK runtime context fields.",
                    suggested_action="Inject ARK flow/step/agent/repo context through endpoint env or headers.",
                )
            )

        data: dict[str, Any] = {}
        json_payload = self._metadata_value(
            metadata,
            "ARK_RUNTIME_CONTEXT_JSON",
            "LEAN_CONSTELLATION_RUNTIME_CONTEXT_JSON",
            "X-Ark-Runtime-Context",
            "X-Lean-Constellation-Runtime-Context",
        )
        if json_payload:
            try:
                loaded = json.loads(json_payload)
            except json.JSONDecodeError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("runtime_context_invalid", f"Invalid runtime context JSON metadata: {exc}")
                )
            if not isinstance(loaded, Mapping):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("runtime_context_invalid", "Runtime context JSON metadata must be an object.")
                )
            for key in RuntimeToolContext.model_fields:
                if key in loaded:
                    data[key] = loaded[key]

        aliases: dict[str, tuple[str, ...]] = {
            "flow_id": ("ARK_FLOW_ID", "LEAN_CONSTELLATION_FLOW_ID", "X-Ark-Flow-Id", "X-Lean-Constellation-Flow-Id"),
            "step_id": ("ARK_STEP_ID", "LEAN_CONSTELLATION_STEP_ID", "X-Ark-Step-Id", "X-Lean-Constellation-Step-Id"),
            "agent_id": ("ARK_AGENT_ID", "LEAN_CONSTELLATION_AGENT_ID", "X-Ark-Agent-Id", "X-Lean-Constellation-Agent-Id"),
            "scope_id": ("ARK_SCOPE_ID", "LEAN_CONSTELLATION_SCOPE_ID", "X-Ark-Scope-Id", "X-Lean-Constellation-Scope-Id"),
            "agent_type": ("ARK_AGENT_TYPE", "LEAN_CONSTELLATION_AGENT_TYPE", "X-Ark-Agent-Type", "X-Lean-Constellation-Agent-Type"),
            "agent_role": ("ARK_AGENT_ROLE", "LEAN_CONSTELLATION_AGENT_ROLE", "X-Ark-Agent-Role", "X-Lean-Constellation-Agent-Role"),
            "expected_view_key": (
                "ARK_EXPECTED_TOOL_VIEW",
                "ARK_TOOL_VIEW",
                "LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW",
                "LEAN_CONSTELLATION_EXPECTED_VIEW_KEY",
                "X-Ark-Expected-Tool-View",
                "X-Lean-Constellation-Expected-Tool-View",
            ),
            "workspace_root": ("ARK_WORKSPACE_ROOT", "LEAN_CONSTELLATION_WORKSPACE_ROOT", "X-Ark-Workspace-Root"),
            "repo_root": ("ARK_REPO_ROOT", "LEAN_CONSTELLATION_REPO_ROOT", "X-Ark-Repo-Root", "X-Lean-Constellation-Repo-Root"),
            "node_path": ("ARK_NODE_PATH", "LEAN_CONSTELLATION_NODE_PATH", "X-Ark-Node-Path"),
            "node_kind": ("ARK_NODE_KIND", "LEAN_CONSTELLATION_NODE_KIND", "X-Ark-Node-Kind"),
            "contract_version": ("ARK_CONTRACT_VERSION", "LEAN_CONSTELLATION_CONTRACT_VERSION", "X-Ark-Contract-Version"),
            "stage": ("ARK_DECL_STAGE", "LEAN_CONSTELLATION_STAGE", "LEAN_CONSTELLATION_DECL_STAGE", "X-Ark-Decl-Stage"),
            "round_id": ("ARK_ROUND_ID", "LEAN_CONSTELLATION_ROUND_ID", "X-Ark-Round-Id"),
            "batch_decls": ("ARK_BATCH_DECLS", "LEAN_CONSTELLATION_BATCH_DECLS", "X-Ark-Batch-Decls"),
            "current_decl": ("ARK_CURRENT_DECL", "LEAN_CONSTELLATION_CURRENT_DECL", "X-Ark-Current-Decl"),
            "decl_kind": ("ARK_DECL_KIND", "LEAN_CONSTELLATION_DECL_KIND", "X-Ark-Decl-Kind"),
            "retry_attempt": ("ARK_RETRY_ATTEMPT", "LEAN_CONSTELLATION_RETRY_ATTEMPT", "X-Ark-Retry-Attempt"),
            "successful_submission_count": (
                "ARK_SUCCESSFUL_SUBMISSION_COUNT",
                "LEAN_CONSTELLATION_SUCCESSFUL_SUBMISSION_COUNT",
                "X-Ark-Successful-Submission-Count",
            ),
            "successful_submission_kind": (
                "ARK_SUCCESSFUL_SUBMISSION_KIND",
                "LEAN_CONSTELLATION_SUCCESSFUL_SUBMISSION_KIND",
                "X-Ark-Successful-Submission-Kind",
            ),
        }
        for field, field_aliases in aliases.items():
            value = self._metadata_value(metadata, *field_aliases)
            if value is None:
                continue
            try:
                data[field] = self._coerce_metadata_field(field, value)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("runtime_context_invalid", f"Invalid MCP runtime metadata field {field}: {exc}", field=field)
                )

        if not data.get("expected_view_key"):
            data["expected_view_key"] = (
                endpoint_view_key
                or self._metadata_value(metadata, "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW", "ARK_APPLICATION_TOOL_VIEW")
                or self._metadata_value(metadata, "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW", "ARK_SUBMIT_TOOL_VIEW")
            )

        missing = [field for field in ("flow_id", "step_id", "agent_id", "repo_root") if not data.get(field)]
        if missing:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "runtime_context_metadata_incomplete",
                    "MCP call metadata is missing required ARK runtime context fields.",
                    details={"missing": ",".join(missing)},
                    suggested_action="Inject flow_id, step_id, agent_id, and repo_root through endpoint env or headers.",
                )
            )

        extra = dict(data.get("extra") or {})
        extra["mcp_context_source"] = "env_header"
        application_view = self._metadata_value(metadata, "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW", "ARK_APPLICATION_TOOL_VIEW")
        submit_view = self._metadata_value(metadata, "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW", "ARK_SUBMIT_TOOL_VIEW")
        if application_view:
            extra["application_tool_view"] = application_view
        if submit_view:
            extra["submit_tool_view"] = submit_view
        data["extra"] = extra
        return self._normalize_runtime_context(data)

    def _merged_call_metadata(self, raw_context: RawToolCallContext) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for source in (raw_context.headers, raw_context.env):
            for key, value in source.items():
                if value is None:
                    continue
                metadata[str(key)] = str(value)
        return metadata

    def _metadata_value(self, metadata: Mapping[str, str], *keys: str) -> str | None:
        normalized = {self._metadata_key(key): value for key, value in metadata.items()}
        for key in keys:
            value = normalized.get(self._metadata_key(key))
            if value is not None and value.strip() != "":
                return value
        return None

    def _metadata_key(self, key: str) -> str:
        return "".join(ch for ch in str(key).lower() if ch.isalnum())

    def _coerce_metadata_field(self, field: str, value: str) -> Any:
        if field in {"contract_version", "retry_attempt", "successful_submission_count"}:
            return int(value)
        if field == "batch_decls":
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                loaded = json.loads(text)
                if isinstance(loaded, list):
                    return [str(item) for item in loaded]
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

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
