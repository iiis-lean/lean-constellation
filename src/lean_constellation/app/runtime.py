"""Runtime assembly for the Lean Constellation application."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import current_thread
from typing import Any

from agent_runtime_kit.agent.snapshots import AgentSnapshotService
from agent_runtime_kit.agent.service import AgentService
from agent_runtime_kit.flow.models import BaseStep
from agent_runtime_kit.flow.registry import FlowTypeRegistry, StepTypeRegistry
from agent_runtime_kit.flow.scheduler import RuntimeScheduleService
from agent_runtime_kit.flow.services import FlowService, StepService
from agent_runtime_kit.flow.store import FlowStepStore
from agent_runtime_kit.runtime import ARKServices
from agent_runtime_kit.runtime.mcp_tool_gateway import RuntimeMcpToolGateway
from agent_runtime_kit.runtime.services import RuntimePauseController

from lean_constellation.app.config import LeanAppConfig
from lean_constellation.agents.ark import build_ark_agent_type_registry
from lean_constellation.agents.models import AgentTypeSpec
from lean_constellation.agents.registry import build_agent_type_specs
from lean_constellation.agents.testing import build_controlled_test_agent_type_specs
from lean_constellation.app.external_takeover import build_external_takeover_agent_providers
from lean_constellation.flows.registry import register_lean_flow_step_types
from lean_constellation.flows.testing import CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES
from lean_constellation.services.foundation import GateReport, MutationSummaryView, ServiceResult
from lean_constellation.services import LeanProviderOverrides, LeanRuntimeServices, create_lean_runtime_services
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
from lean_constellation.services.validation_snapshot.snapshot_restore import RepoCheckpointKind
from lean_constellation.tools import register_submit_tooling


def create_app_runtime_services(
    *,
    runtime_root: Path | str,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    providers: LeanProviderOverrides | None = None,
    register_application_tools: bool = True,
    register_submit_tools: bool = True,
    agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    extra_agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    step_type_overrides: Mapping[str, type[BaseStep]] | None = None,
    agent_providers: dict[str, object] | None = None,
    native_lake_project_config: object | None = None,
    max_concurrent_flow_advances: int = 1,
    max_concurrent_steps: int = 1,
    start_paused: bool = False,
    test_control_enabled: bool = False,
) -> LeanRuntimeServices:
    """Create a runtime with ARK Flow/Step/Agent services and Lean app services."""

    root = Path(runtime_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    effective_agent_type_specs = (
        list(agent_type_specs)
        if agent_type_specs is not None
        else build_agent_type_specs(extra_specs=extra_agent_type_specs)
    )

    flow_registry = FlowTypeRegistry()
    step_registry = StepTypeRegistry()
    register_lean_flow_step_types(
        flow_registry=flow_registry,
        step_registry=step_registry,
        step_type_overrides=step_type_overrides,
    )
    store = FlowStepStore(root, flow_registry=flow_registry, step_registry=step_registry)

    ark = ARKServices()
    pause_controller = RuntimePauseController(global_paused=start_paused)
    ark.pause_controller = pause_controller
    runtime_gateway = LeanRuntimeMcpGatewayAdapter(
        RuntimeMcpToolGateway(ark_services=ark),
        agent_type_specs=effective_agent_type_specs,
    )
    effective_providers = _with_runtime_gateway(providers, runtime_gateway)

    runtime = create_lean_runtime_services(
        ark_services=ark,
        external_config=external_config,
        external_overrides=external_overrides,
        providers=effective_providers,
        agent_type_specs=effective_agent_type_specs,
        native_lake_project_config=native_lake_project_config,
        register_application_tools=register_application_tools,
        test_control_enabled=test_control_enabled,
    )
    runtime_gateway.delegate.app = runtime.app

    ark.agent_service = AgentService(
        root,
        agent_types=build_ark_agent_type_registry(specs=effective_agent_type_specs),
        providers=agent_providers,
        ark_services=ark,
        app_services=runtime.app,
        start_paused=start_paused,
    )
    ark.flow_service = FlowService(
        root,
        flow_registry=flow_registry,
        step_registry=step_registry,
        ark_services=ark,
        app_services=runtime.app,
        store=store,
    )
    ark.step_service = StepService(
        root,
        step_registry=step_registry,
        ark_services=ark,
        app_services=runtime.app,
        store=store,
    )
    ark.schedule_service = RuntimeScheduleService(
        ark_services=ark,
        app_services=runtime.app,
        max_concurrent_flow_advances=max_concurrent_flow_advances,
        max_concurrent_steps=max_concurrent_steps,
    )
    ark.snapshot_service = AgentSnapshotService(
        root,
        store=ark.agent_service.store,
        agent_service=ark.agent_service,
        ark_services=ark,
        app_services=runtime.app,
    )
    ark_snapshot_provider = ArkRuntimeSnapshotProviderAdapter(runtime, ark.snapshot_service)
    runtime.validation_snapshot.snapshot_restore.runtime_stability_provider = ark_snapshot_provider
    runtime.validation_snapshot.snapshot_restore.ark_snapshot_provider = ark_snapshot_provider

    if register_submit_tools:
        registered = register_submit_tooling(runtime)
        if not registered.ok:
            messages = "; ".join(issue.message for issue in registered.issues)
            raise RuntimeError(f"Failed to register submit tooling: {messages}")
    return runtime


def create_app_runtime_from_config(
    config: LeanAppConfig,
    *,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    providers: LeanProviderOverrides | None = None,
    agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    extra_agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    step_type_overrides: Mapping[str, type[BaseStep]] | None = None,
    agent_providers: dict[str, object] | None = None,
    start_paused: bool = False,
    test_control_enabled: bool = False,
) -> LeanRuntimeServices:
    runtime_root = config.runtime_root or (config.workspace_root / ".agent_runtime")
    if test_control_enabled and agent_type_specs is None and extra_agent_type_specs is None:
        return create_test_control_runtime_services(
            runtime_root=runtime_root,
            external_config=external_config,
            external_overrides=external_overrides,
            providers=providers,
            agent_providers=agent_providers,
            max_concurrent_flow_advances=config.max_concurrent_flow_advances,
            max_concurrent_steps=config.max_concurrent_steps,
            start_paused=start_paused,
            native_lake_project_config=config.native_lake_project,
        )
    return create_app_runtime_services(
        runtime_root=runtime_root,
        external_config=external_config,
        external_overrides=external_overrides,
        providers=providers,
        agent_type_specs=agent_type_specs,
        extra_agent_type_specs=extra_agent_type_specs,
        step_type_overrides=step_type_overrides,
        agent_providers=agent_providers,
        native_lake_project_config=config.native_lake_project,
        max_concurrent_flow_advances=config.max_concurrent_flow_advances,
        max_concurrent_steps=config.max_concurrent_steps,
        start_paused=start_paused,
        test_control_enabled=test_control_enabled,
    )


def create_test_control_runtime_services(
    *,
    runtime_root: Path | str,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    providers: LeanProviderOverrides | None = None,
    register_application_tools: bool = True,
    register_submit_tools: bool = True,
    agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    extra_agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    controlled_base_agent_types: Sequence[str] | None = None,
    step_type_overrides: Mapping[str, type[BaseStep]] | None = None,
    agent_providers: dict[str, object] | None = None,
    native_lake_project_config: object | None = None,
    external_takeover_cli_type: str = "external_takeover",
    register_external_takeover_provider: bool = True,
    max_concurrent_flow_advances: int = 1,
    max_concurrent_steps: int = 1,
    start_paused: bool = True,
) -> LeanRuntimeServices:
    """Create a runtime profile for paused, externally controlled scheduler tests."""

    base_specs = (
        list(agent_type_specs)
        if agent_type_specs is not None
        else build_agent_type_specs(extra_specs=extra_agent_type_specs)
    )
    controlled_specs = build_controlled_test_agent_type_specs(
        specs=base_specs,
        base_agent_types=controlled_base_agent_types,
    )
    effective_agent_type_specs = [*base_specs, *controlled_specs]
    effective_step_overrides = {
        **CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES,
        **dict(step_type_overrides or {}),
    }
    effective_agent_providers = dict(agent_providers or {})
    if register_external_takeover_provider and external_takeover_cli_type not in effective_agent_providers:
        effective_agent_providers.update(
            build_external_takeover_agent_providers(
                runtime_root,
                cli_type=external_takeover_cli_type,
            )
        )
    return create_app_runtime_services(
        runtime_root=runtime_root,
        external_config=external_config,
        external_overrides=external_overrides,
        providers=providers,
        register_application_tools=register_application_tools,
        register_submit_tools=register_submit_tools,
        agent_type_specs=effective_agent_type_specs,
        step_type_overrides=effective_step_overrides,
        agent_providers=effective_agent_providers,
        native_lake_project_config=native_lake_project_config,
        max_concurrent_flow_advances=max_concurrent_flow_advances,
        max_concurrent_steps=max_concurrent_steps,
        start_paused=start_paused,
        test_control_enabled=True,
    )


def _with_runtime_gateway(
    providers: LeanProviderOverrides | None,
    runtime_gateway: RuntimeMcpToolGateway,
) -> LeanProviderOverrides:
    if providers is None:
        return LeanProviderOverrides(runtime_gateway=runtime_gateway)
    if providers.runtime_gateway is not None:
        return providers
    return replace(providers, runtime_gateway=runtime_gateway)


class ArkRuntimeSnapshotProviderAdapter:
    """Bridge ValidationSnapshotService to ARK AgentSnapshotService."""

    def __init__(self, runtime: LeanRuntimeServices, snapshot_service: AgentSnapshotService) -> None:
        self.runtime = runtime
        self.snapshot_service = snapshot_service

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ) -> ServiceResult[GateReport]:
        del repo_root, checkpoint_kind, node_paths
        running_agents = []
        agent_service = self.runtime.ark.agent_service
        if agent_service is not None and hasattr(agent_service, "list_running_agents"):
            running_agents = list(agent_service.list_running_agents())
        running_steps = []
        step_service = self.runtime.ark.step_service
        if step_service is not None and hasattr(step_service, "list_running_steps"):
            running_steps = list(step_service.list_running_steps())
        ignored_step_id = self._current_terminal_active_step_id()
        if ignored_step_id is not None:
            running_steps = [step_id for step_id in running_steps if step_id != ignored_step_id]
        if running_agents or running_steps:
            issue = self.runtime.foundation.issue(
                "runtime_not_stable",
                "ARK runtime has running agents or steps and cannot create a stable checkpoint.",
                details={
                    "running_agents": ",".join(getattr(agent, "agent_id", str(agent)) for agent in running_agents),
                    "running_steps": ",".join(getattr(step, "step_id", str(step)) for step in running_steps),
                },
            )
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("ark_runtime_stability", issue, summary="ARK runtime is not stable.")
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("ark_runtime_stability", summary="ARK runtime is stable for checkpoint.")
        )

    def create_runtime_snapshot(
        self,
        repo_root: Path,
        *,
        scope_ids: list[str],
        label: str | None = None,
    ) -> ServiceResult[str]:
        del repo_root, label
        with self._filter_current_terminal_step_from_running_list():
            result = self.snapshot_service.create_runtime_snapshot_for_scopes(
                refresh_scope_ids=list(scope_ids),
                scope_ids=list(scope_ids),
                wait=False,
            )
        if result.status != "created" or result.snapshot_id is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "ark_runtime_snapshot_failed",
                    f"ARK runtime snapshot failed with status {result.status}.",
                )
            )
        return self.runtime.foundation.ok(result.snapshot_id)

    def restore_runtime_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        leave_runtime_paused: bool = True,
    ) -> ServiceResult[MutationSummaryView]:
        del repo_root
        result = self.snapshot_service.restore_runtime_snapshot(snapshot_id, leave_paused=leave_runtime_paused)
        if result.status != "created":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "ark_runtime_restore_failed",
                    f"ARK runtime restore failed with status {result.status}.",
                    object_ref=snapshot_id,
                )
            )
        if leave_runtime_paused and self.runtime.ark.pause_controller is not None:
            self.runtime.ark.pause_controller.pause(None)
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=snapshot_id,
                changed=True,
                summary="Restored ARK runtime snapshot.",
                changed_items=["ark_runtime_snapshot"],
            )
        )

    def _current_terminal_active_step_id(self) -> str | None:
        step_service = self.runtime.ark.step_service
        active_steps = getattr(step_service, "active_steps", None)
        if step_service is None or not isinstance(active_steps, dict):
            return None
        thread = current_thread()
        for step_id, active in active_steps.items():
            if getattr(active, "worker_ref", None) is not thread:
                continue
            store = getattr(step_service, "store", None)
            if store is None:
                return None
            try:
                step = store.get_step(step_id)
            except Exception:  # noqa: BLE001 - stability check must fall back to strict behavior.
                return None
            status = getattr(getattr(step, "status", None), "value", getattr(step, "status", None))
            if status in {"completed", "failed"}:
                return step_id
        return None

    @contextmanager
    def _filter_current_terminal_step_from_running_list(self):
        ignored_step_id = self._current_terminal_active_step_id()
        step_service = self.runtime.ark.step_service
        if ignored_step_id is None or step_service is None or not hasattr(step_service, "list_running_steps"):
            yield
            return

        original = step_service.list_running_steps

        def filtered_list_running_steps(scope_id: str | None = None):
            return [step_id for step_id in original(scope_id=scope_id) if step_id != ignored_step_id]

        step_service.list_running_steps = filtered_list_running_steps
        try:
            yield
        finally:
            step_service.list_running_steps = original


class LeanRuntimeMcpGatewayAdapter:
    """Adapt ARK's runtime MCP gateway to Lean ToolFacade context shape."""

    def __init__(
        self,
        delegate: RuntimeMcpToolGateway,
        *,
        agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    ) -> None:
        self.delegate = delegate
        self.agent_type_specs = list(agent_type_specs or build_agent_type_specs())

    @property
    def resolver(self):
        return self.delegate.resolver

    def resolve_tool_context(self, raw_context: RawToolCallContext) -> RuntimeToolContext:
        if raw_context.headers:
            ark_ctx = self.delegate.resolve_context_from_http_headers(raw_context.headers)
        else:
            ark_ctx = self.delegate.resolve_context_from_env(raw_context.env)
        return self._to_lean_runtime_context(ark_ctx, endpoint_view_key=raw_context.endpoint_view_key)

    def accept_step_submission(self, ctx, submission):  # noqa: ANN001
        return self.delegate.accept_step_submission(ctx, submission)

    def _to_lean_runtime_context(self, ark_ctx, *, endpoint_view_key: str | None) -> RuntimeToolContext:  # noqa: ANN001
        step = ark_ctx.step
        flow = ark_ctx.flow
        agent = ark_ctx.agent
        state = getattr(step, "state", None)
        env = dict(getattr(state, "env_overrides", {}) or {})
        variables = dict(getattr(state, "variables", {}) or {})
        flow_input = getattr(flow, "input", None)
        agent_type = (
            _first_value(env, "LEAN_CONSTELLATION_AGENT_TYPE")
            or getattr(agent, "agent_type", None)
            or getattr(state, "agent_type", None)
        )

        expected_view_key = (
            endpoint_view_key
            or _first_value(variables, "expected_view_key")
            or _first_value(env, "LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW")
            or _first_value(env, "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW")
            or _first_value(env, "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW")
        )
        repo_root = (
            _first_value(env, "LEAN_CONSTELLATION_REPO_ROOT", "ARK_REPO_ROOT")
            or _attr(flow_input, "repo_root")
            or _attr(flow_input, "repo_path")
            or getattr(state, "workdir_override", None)
        )
        workspace_root = (
            _first_value(env, "LEAN_CONSTELLATION_WORKSPACE_ROOT", "ARK_WORKSPACE_ROOT")
            or _attr(flow_input, "workspace_root")
        )
        node_path = (
            _first_value(env, "LEAN_CONSTELLATION_NODE_PATH", "ARK_NODE_PATH")
            or _first_value(variables, "node_path")
            or _attr(flow_input, "node_path")
            or _attr(_attr(flow_input, "caller_context"), "node_path")
        )
        return RuntimeToolContext(
            flow_id=ark_ctx.identity.flow_id,
            step_id=ark_ctx.identity.step_id,
            agent_id=ark_ctx.identity.agent_id,
            scope_id=ark_ctx.scope_id,
            agent_type=agent_type,
            agent_role=_actor_role(agent_type, variables, env, specs=self.agent_type_specs),
            expected_view_key=expected_view_key,
            workspace_root=workspace_root,
            repo_root=repo_root,
            node_path=node_path,
            node_kind=_first_value(env, "LEAN_CONSTELLATION_NODE_KIND", "ARK_NODE_KIND"),
            contract_version=_first_int(
                _first_value(env, "LEAN_CONSTELLATION_CONTRACT_VERSION", "ARK_CONTRACT_VERSION")
                or _first_value(variables, "contract_version")
                or _attr(flow_input, "contract_version")
            ),
            stage=(
                _first_value(env, "LEAN_CONSTELLATION_STAGE", "ARK_DECL_STAGE")
                or _first_value(variables, "stage")
            ),
            round_id=(
                _first_value(env, "LEAN_CONSTELLATION_ROUND_ID", "ARK_ROUND_ID")
                or _first_value(variables, "round_id")
                or _attr(flow_input, "round_id")
            ),
            batch_decls=list(_first_value(variables, "batch_decls") or []),
            current_decl=_first_value(variables, "current_decl"),
            decl_kind=_first_value(variables, "decl_kind"),
            retry_attempt=_first_int(
                _first_value(env, "LEAN_CONSTELLATION_RETRY_ATTEMPT", "ARK_RETRY_ATTEMPT")
                or _first_value(variables, "retry_attempt")
            ),
            extra={
                "mcp_context_source": "ark_runtime_gateway",
                "flow_type": getattr(flow, "flow_type", None) or "",
                "step_type": getattr(step, "step_type", None) or "",
            },
        )


def _attr(obj: object | None, name: str) -> object | None:
    if obj is None:
        return None
    return getattr(obj, name, None)


def _first_value(source: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = source.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _first_int(value: object | None) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _actor_role(
    agent_type: object | None,
    variables: dict[str, object],
    env: dict[str, object] | None = None,
    *,
    specs: Sequence[AgentTypeSpec] | None = None,
):
    role = _first_value(variables, "agent_role") or _first_value(env or {}, "LEAN_CONSTELLATION_AGENT_ROLE")
    if role in {"coordinator", "plan", "worker", "reviewer", "admin", "system"}:
        return role
    if agent_type:
        try:
            from lean_constellation.agents.registry import get_agent_type_spec

            return get_agent_type_spec(str(agent_type), specs=specs).role
        except Exception:  # noqa: BLE001 - fallback to ToolFacade role inference.
            return None
    return None
