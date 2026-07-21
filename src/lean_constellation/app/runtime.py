"""Runtime assembly for the Lean Constellation application."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from threading import current_thread

from agent_runtime_kit.agent.report_policy import AgentTraceReportPolicy, TraceReportPersistence
from agent_runtime_kit.agent.provider_contracts import ProviderRegistry
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

from lean_constellation.app.config import AgentTraceReportAppConfig, AutomaticCheckpointAppConfig, LeanAppConfig
from lean_constellation.app.agent_provider_config import (
    apply_agent_home_overrides,
    build_builtin_provider_registry,
)
from lean_constellation.agents.ark import build_ark_agent_type_registry
from lean_constellation.agents.models import AgentTypeSpec
from lean_constellation.agents.registry import build_agent_type_specs
from lean_constellation.agents.testing import build_controlled_test_agent_type_specs
from lean_constellation.app.external_takeover import build_external_takeover_agent_providers
from lean_constellation.flows.registry import register_lean_flow_step_types
from lean_constellation.flows.testing import CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES
from lean_constellation.services.foundation import GateReport, MutationSummaryView, ServiceResult
from lean_constellation.services import LeanProviderOverrides, LeanRuntimeServices, create_lean_runtime_services
from lean_constellation.services.external_clients import ExternalClientConfig, LeanMcpToolkitClientConfig
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
from lean_constellation.services.validation_snapshot.snapshot_restore import (
    RepoCheckpointKind,
    RepoCheckpointSnapshotView,
    SnapshotRestoreView,
)
from lean_constellation.services.validation_snapshot.source_index_checkpoint import SourceIndexCheckpointAdapter
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
    provider_registry: ProviderRegistry | None = None,
    native_lake_project_config: object | None = None,
    workspace_config: object | None = None,
    max_concurrent_flow_advances: int = 1,
    max_concurrent_steps: int = 1,
    start_paused: bool = False,
    test_control_enabled: bool = False,
    automatic_checkpoints: AutomaticCheckpointAppConfig | None = None,
    agent_trace_reports: AgentTraceReportAppConfig | None = None,
) -> LeanRuntimeServices:
    """Create a runtime with ARK Flow/Step/Agent services and Lean app services."""

    root = Path(runtime_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    if agent_type_specs is not None:
        effective_agent_type_specs = list(agent_type_specs)
    elif test_control_enabled and extra_agent_type_specs is None:
        base_specs = build_agent_type_specs()
        effective_agent_type_specs = [
            *base_specs,
            *build_controlled_test_agent_type_specs(specs=base_specs),
        ]
    else:
        effective_agent_type_specs = build_agent_type_specs(extra_specs=extra_agent_type_specs)
    effective_step_type_overrides = dict(step_type_overrides or {})
    if test_control_enabled:
        effective_step_type_overrides = {
            **CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES,
            **effective_step_type_overrides,
        }

    flow_registry = FlowTypeRegistry()
    step_registry = StepTypeRegistry()
    register_lean_flow_step_types(
        flow_registry=flow_registry,
        step_registry=step_registry,
        step_type_overrides=effective_step_type_overrides,
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
        workspace_config=workspace_config,  # type: ignore[arg-type]
        register_application_tools=register_application_tools,
        test_control_enabled=test_control_enabled,
    )
    runtime_gateway.delegate.app = runtime.app
    runtime.app.automatic_checkpoints = automatic_checkpoints or AutomaticCheckpointAppConfig()
    trace_config = agent_trace_reports or AgentTraceReportAppConfig()
    trace_report_policy = AgentTraceReportPolicy(
        persistence=TraceReportPersistence(trace_config.persistence),
        include_in_snapshots=trace_config.include_in_snapshots,
    )

    ark.agent_service = AgentService(
        root,
        agent_types=build_ark_agent_type_registry(specs=effective_agent_type_specs),
        providers=agent_providers,
        provider_registry=provider_registry,
        ark_services=ark,
        app_services=runtime.app,
        start_paused=start_paused,
        trace_report_policy=trace_report_policy,
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
        trace_report_policy=trace_report_policy,
    )
    ark_snapshot_provider = ArkRuntimeSnapshotProviderAdapter(runtime, ark.snapshot_service)
    runtime.app.snapshot_runtime = ApplicationSnapshotRuntime(runtime, ark_snapshot_provider)
    runtime.app.source_index_checkpoint = SourceIndexCheckpointAdapter(runtime)

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
    provider_registry: ProviderRegistry | None = None,
    start_paused: bool = False,
    test_control_enabled: bool = False,
) -> LeanRuntimeServices:
    runtime_root = config.runtime_root or (config.workspace_root / ".agent_runtime")
    resolved_external_config = external_config or external_client_config_from_app_config(config)
    if agent_type_specs is not None:
        configured_specs = list(agent_type_specs)
    else:
        configured_specs = build_agent_type_specs(extra_specs=extra_agent_type_specs)
    configured_specs = apply_agent_home_overrides(configured_specs, config.agent_home_overrides)
    effective_provider_registry = provider_registry or build_builtin_provider_registry(
        runtime_root,
        configured_specs,
        config.agent_home_overrides,
    )
    if test_control_enabled and agent_type_specs is None and extra_agent_type_specs is None:
        return create_test_control_runtime_services(
            runtime_root=runtime_root,
            external_config=resolved_external_config,
            external_overrides=external_overrides,
            providers=providers,
            agent_providers=agent_providers,
            provider_registry=effective_provider_registry,
            agent_type_specs=configured_specs,
            max_concurrent_flow_advances=config.max_concurrent_flow_advances,
            max_concurrent_steps=config.max_concurrent_steps,
            start_paused=start_paused,
            native_lake_project_config=config.native_lake_project,
            workspace_config=config.workspace_config,
            automatic_checkpoints=config.automatic_checkpoints,
            agent_trace_reports=config.agent_trace_reports,
        )
    return create_app_runtime_services(
        runtime_root=runtime_root,
        external_config=resolved_external_config,
        external_overrides=external_overrides,
        providers=providers,
        agent_type_specs=configured_specs,
        step_type_overrides=step_type_overrides,
        agent_providers=agent_providers,
        provider_registry=effective_provider_registry,
        native_lake_project_config=config.native_lake_project,
        workspace_config=config.workspace_config,
        max_concurrent_flow_advances=config.max_concurrent_flow_advances,
        max_concurrent_steps=config.max_concurrent_steps,
        start_paused=start_paused,
        test_control_enabled=test_control_enabled,
        automatic_checkpoints=config.automatic_checkpoints,
        agent_trace_reports=config.agent_trace_reports,
    )


def external_client_config_from_app_config(config: LeanAppConfig) -> ExternalClientConfig:
    toolkit = config.toolkit
    return ExternalClientConfig(
        lean_toolkit=LeanMcpToolkitClientConfig(
            base_url=toolkit.effective_base_url(),
            api_prefix=toolkit.api_prefix,
            auth_token=toolkit.auth_token,
            timeout_seconds=toolkit.timeout_seconds,
            enabled_groups=toolkit.enabled_groups,
            response_excerpt_chars=toolkit.response_excerpt_chars,
        )
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
    provider_registry: ProviderRegistry | None = None,
    native_lake_project_config: object | None = None,
    workspace_config: object | None = None,
    external_takeover_cli_type: str = "external_takeover",
    register_external_takeover_provider: bool = True,
    max_concurrent_flow_advances: int = 1,
    max_concurrent_steps: int = 1,
    start_paused: bool = True,
    automatic_checkpoints: AutomaticCheckpointAppConfig | None = None,
    agent_trace_reports: AgentTraceReportAppConfig | None = None,
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
        provider_registry=provider_registry,
        native_lake_project_config=native_lake_project_config,
        workspace_config=workspace_config,
        max_concurrent_flow_advances=max_concurrent_flow_advances,
        max_concurrent_steps=max_concurrent_steps,
        start_paused=start_paused,
        test_control_enabled=True,
        automatic_checkpoints=automatic_checkpoints,
        agent_trace_reports=agent_trace_reports,
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


@dataclass(frozen=True)
class ArkRuntimeSnapshotRef:
    snapshot_id: str
    scope_ids: list[str]


class ApplicationSnapshotRuntime:
    """Compose ARK runtime snapshots with pure Lean Constellation checkpoint archives."""

    _NODE_SCOPE_KINDS = {
        RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH,
        RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL,
        RepoCheckpointKind.AFTER_CONTENT_PREPARATION_TERMINAL,
        RepoCheckpointKind.AFTER_CONTENT_DECL_ROUND_TERMINAL,
    }

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        ark_snapshot: "ArkRuntimeSnapshotProviderAdapter",
        runtime_stability: object | None = None,
    ) -> None:
        self.runtime = runtime
        self.ark_snapshot = ark_snapshot
        self.runtime_stability = runtime_stability or ark_snapshot

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        node_paths: list[str] | None = None,
        node_ids: list[str] | None = None,
    ) -> ServiceResult[GateReport]:
        kind = RepoCheckpointKind(checkpoint_kind)
        resolved = self._resolve_node_scopes(Path(repo_root), kind, node_paths or [], node_ids or [])
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        runtime_gate = self.runtime_stability.check_repo_stable_point(
            Path(repo_root), checkpoint_kind=kind, node_paths=resolved.value[0]
        )
        if not runtime_gate.ok or runtime_gate.value is None:
            return self.runtime.foundation.fail(runtime_gate.issues)
        business_gate = self.runtime.validation_snapshot.check_repo_checkpoint_business_gate(
            Path(repo_root), checkpoint_kind=kind
        )
        if not business_gate.ok or business_gate.value is None:
            return self.runtime.foundation.fail(business_gate.issues)
        return self.runtime.foundation.ok(
            self.runtime.foundation.merge_gate_reports(
                f"{kind.value}_stable_point",
                [runtime_gate.value, business_gate.value],
            )
        )

    def create_repo_stable_point_snapshot(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind | str,
        label: str | None = None,
        node_paths: list[str] | None = None,
        node_ids: list[str] | None = None,
        scope_ids: list[str] | None = None,
        snapshot_id: str | None = None,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        repo_root = Path(repo_root)
        kind = RepoCheckpointKind(checkpoint_kind)
        if snapshot_id is not None:
            existing = self.runtime.validation_snapshot.validate_repo_checkpoint_snapshot(
                repo_root, snapshot_id=snapshot_id
            )
            if existing.ok and existing.value is not None:
                if existing.value.checkpoint_kind != kind:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "repo_checkpoint_snapshot_id_conflict",
                            "The requested checkpoint id belongs to a different checkpoint kind.",
                            object_ref=snapshot_id,
                            current=existing.value.checkpoint_kind.value,
                            expected=kind.value,
                        )
                    )
                return existing
        resolved = self._resolve_node_scopes(repo_root, kind, node_paths or [], node_ids or [])
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        gate = self.check_repo_stable_point(
            repo_root,
            checkpoint_kind=kind,
            node_paths=resolved.value[0],
        )
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        normalized_scopes = self._normalize_scope_ids(scope_ids)
        if not normalized_scopes.ok:
            return self.runtime.foundation.fail(normalized_scopes.issues)
        effective_scopes = normalized_scopes.value or [f"repo:{repo_root.name}", *resolved.value[1]]
        ark = self.ark_snapshot.create_runtime_snapshot(repo_root, scope_ids=effective_scopes, label=label)
        if not ark.ok or ark.value is None:
            return self.runtime.foundation.fail(ark.issues)
        ark_snapshot_id = (
            ark.value.snapshot_id
            if isinstance(ark.value, ArkRuntimeSnapshotRef)
            else str(ark.value)
        )
        return self.runtime.validation_snapshot.create_repo_checkpoint_archive(
            repo_root,
            checkpoint_kind=kind,
            label=label,
            snapshot_id=snapshot_id,
            ark_runtime_snapshot_id=ark_snapshot_id,
        )

    def create_repo_stable_point_snapshot_with_id(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        checkpoint_kind: RepoCheckpointKind | str,
        label: str | None = None,
        scope_ids: list[str] | None = None,
    ) -> ServiceResult[RepoCheckpointSnapshotView]:
        return self.create_repo_stable_point_snapshot(
            repo_root,
            checkpoint_kind=checkpoint_kind,
            label=label,
            scope_ids=scope_ids,
            snapshot_id=snapshot_id,
        )

    def restore_repo_checkpoint_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        dry_run: bool = False,
        leave_runtime_paused: bool = True,
        prune_extra_files: bool = False,
    ) -> ServiceResult[SnapshotRestoreView]:
        repo_root = Path(repo_root)
        validated = self.runtime.validation_snapshot.validate_repo_checkpoint_snapshot(
            repo_root, snapshot_id=snapshot_id
        )
        if not validated.ok or validated.value is None:
            return self.runtime.foundation.fail(validated.issues)
        if dry_run:
            return self.runtime.validation_snapshot.restore_repo_checkpoint_snapshot(
                repo_root,
                snapshot_id=snapshot_id,
                dry_run=True,
                prune_extra_files=prune_extra_files,
            )
        if validated.value.ark_runtime_snapshot_id is not None:
            ark = self.ark_snapshot.restore_runtime_snapshot(
                repo_root,
                snapshot_id=validated.value.ark_runtime_snapshot_id,
                leave_runtime_paused=leave_runtime_paused,
            )
            if not ark.ok:
                return self.runtime.foundation.fail(ark.issues)
        return self.runtime.validation_snapshot.restore_repo_checkpoint_snapshot(
            repo_root,
            snapshot_id=snapshot_id,
            prune_extra_files=prune_extra_files,
        )

    def _normalize_scope_ids(self, scope_ids: list[str] | None) -> ServiceResult[list[str] | None]:
        if scope_ids is None:
            return self.runtime.foundation.ok(None)
        normalized: list[str] = []
        for index, raw_scope_id in enumerate(scope_ids):
            scope_id = str(raw_scope_id).strip()
            if not scope_id:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "checkpoint_scope_id_required",
                        "Checkpoint scope_ids cannot contain an empty scope id.",
                        field=f"scope_ids[{index}]",
                    )
                )
            if scope_id not in normalized:
                normalized.append(scope_id)
        if not normalized:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "checkpoint_scope_ids_required",
                    "Checkpoint scope_ids must contain at least one scope id when provided.",
                )
            )
        return self.runtime.foundation.ok(normalized)

    def _resolve_node_scopes(
        self,
        repo_root: Path,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str],
        node_ids: list[str],
    ) -> ServiceResult[tuple[list[str], list[str]]]:
        if checkpoint_kind not in self._NODE_SCOPE_KINDS:
            return self.runtime.foundation.ok(([], []))
        paths: list[str] = []
        scopes: list[str] = []
        seen_node_ids: set[str] = set()
        node_id_by_path: dict[str, str] = {}

        def add_node(node, *, field: str) -> ServiceResult[None]:  # noqa: ANN001
            existing_node_id = node_id_by_path.get(node.path)
            if existing_node_id is not None and existing_node_id != node.node_id:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "checkpoint_node_ref_conflict",
                        "Checkpoint node references contain conflicting node ids for the same path.",
                        object_ref=node.path,
                        field=field,
                        details={
                            "existing_node_id": existing_node_id,
                            "incoming_node_id": node.node_id,
                        },
                    )
                )
            if node.node_id not in seen_node_ids:
                paths.append(node.path)
                scopes.append(f"repo:{repo_root.name}:node:{node.node_id}")
                seen_node_ids.add(node.node_id)
                node_id_by_path[node.path] = node.node_id
            return self.runtime.foundation.ok(None)

        for index, raw_path in enumerate(node_paths):
            path = str(raw_path).strip()
            if not path:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "checkpoint_node_path_required",
                        "Content task checkpoint node_paths cannot contain an empty node path.",
                        field=f"node_paths[{index}]",
                    )
                )
            node = self.runtime.node.node_tree.node_store.resolve_active_node(repo_root, path=path)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(node.issues)
            added = add_node(node.value, field="node_paths")
            if not added.ok:
                return self.runtime.foundation.fail(added.issues)
        for index, raw_node_id in enumerate(node_ids):
            node_id = str(raw_node_id).strip()
            if not node_id:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "checkpoint_node_id_required",
                        "Content task checkpoint node_ids cannot contain an empty node id.",
                        field=f"node_ids[{index}]",
                    )
                )
            node = self.runtime.node.node_tree.node_store.load_node_by_id(repo_root, node_id=node_id)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(node.issues)
            added = add_node(node.value, field="node_ids")
            if not added.ok:
                return self.runtime.foundation.fail(added.issues)
        return self.runtime.foundation.ok((paths, scopes))


class ArkRuntimeSnapshotProviderAdapter:
    """Application bridge from Lean checkpoint orchestration to ARK snapshots."""

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
    ) -> ServiceResult[ArkRuntimeSnapshotRef]:
        del repo_root, label
        refresh_scope_ids: list[str] = []
        for scope_id in scope_ids:
            normalized = str(scope_id)
            if normalized not in refresh_scope_ids:
                refresh_scope_ids.append(normalized)
        selected_scope_ids: list[str] = []
        for scope_id in [*refresh_scope_ids, *self.snapshot_service.store.list_scope_ids()]:
            normalized = str(scope_id)
            if normalized not in selected_scope_ids:
                selected_scope_ids.append(normalized)
        for scope_id in selected_scope_ids:
            if scope_id not in refresh_scope_ids and self.snapshot_service.get_latest_scope_snapshot(scope_id) is None:
                refresh_scope_ids.append(scope_id)
        with self._filter_current_terminal_step_from_running_list():
            result = self.snapshot_service.create_runtime_snapshot_for_scopes(
                refresh_scope_ids=refresh_scope_ids,
                scope_ids=selected_scope_ids,
                reuse_latest_for_other_scopes=True,
                wait=False,
            )
        if result.status != "created" or result.snapshot_id is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "ark_runtime_snapshot_failed",
                    f"ARK runtime snapshot failed with status {result.status}.",
                )
            )
        return self.runtime.foundation.ok(
            ArkRuntimeSnapshotRef(
                snapshot_id=result.snapshot_id,
                scope_ids=list(result.scope_snapshot_ids),
            )
        )

    def restore_runtime_snapshot(
        self,
        repo_root: Path,
        *,
        snapshot_id: str,
        leave_runtime_paused: bool = True,
    ) -> ServiceResult[MutationSummaryView]:
        del repo_root
        result = self.snapshot_service.restore_runtime_snapshot(
            snapshot_id,
            leave_paused=leave_runtime_paused,
            prune_extra_scopes=True,
        )
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
