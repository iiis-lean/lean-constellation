"""Factory helpers for assembling Lean Constellation runtime services."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lean_constellation.services.runtime import ARKServices, LeanConstellationServices, LeanRuntimeServices

if TYPE_CHECKING:
    from lean_constellation.agents.models import AgentTypeSpec
    from lean_constellation.domain.lake_project import NativeLakeProjectConfig
    from lean_constellation.domain.repo import WorkspaceConfig


@dataclass
class LeanProviderOverrides:
    """Optional provider overrides for protocol-backed service components."""

    content_public_decl_provider: object | None = None
    decl_file_revision_provider: object | None = None
    repair_decl_provider: object | None = None
    content_readiness_provider: object | None = None
    formal_stage_provider: object | None = None
    decl_graph_audit_provider: object | None = None
    runtime_gateway: object | None = None
    submission_gateway: object | None = None


def create_lean_runtime_services(
    *,
    ark_services: ARKServices | None = None,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    providers: LeanProviderOverrides | None = None,
    agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    native_lake_project_config: "NativeLakeProjectConfig | None" = None,
    workspace_config: "WorkspaceConfig | None" = None,
    register_application_tools: bool = False,
    test_control_enabled: bool = False,
) -> LeanRuntimeServices:
    """Create a fully wired Lean Constellation service graph."""

    from lean_constellation.services.adapter import AdapterService
    from lean_constellation.services.decl_graph import DeclGraphService
    from lean_constellation.services.external_clients import ExternalClientConfig, ExternalClientService
    from lean_constellation.services.foundation import FoundationService
    from lean_constellation.services.lean_projection import LeanProjectionService
    from lean_constellation.services.material import MaterialService
    from lean_constellation.services.mathlib import MathlibService
    from lean_constellation.services.node import NodeService
    from lean_constellation.services.repo_workspace import RepoWorkspaceService
    from lean_constellation.services.tool_facade import ToolFacadeService
    from lean_constellation.services.validation_snapshot import ValidationSnapshotService
    from lean_constellation.agents.registry import (
        agent_skill_keys,
        agent_type_permission_names,
        build_agent_type_specs,
    )

    providers = providers or LeanProviderOverrides()
    overrides = external_overrides or {}
    resolved_agent_type_specs = list(agent_type_specs) if agent_type_specs is not None else build_agent_type_specs()
    ark = ark_services or ARKServices()
    app = LeanConstellationServices()
    runtime = LeanRuntimeServices(ark=ark, app=app, test_control_enabled=test_control_enabled)

    config = external_config
    if config is not None and not isinstance(config, ExternalClientConfig):
        raise TypeError("external_config must be an ExternalClientConfig instance.")

    app.foundation = FoundationService(runtime)
    app.external = ExternalClientService(
        runtime,
        config,
        github_repo=overrides.get("github_repo"),
        lake=overrides.get("lake"),
        lean_mcp_toolkit=overrides.get("lean_mcp_toolkit"),
        lean_toolchain=overrides.get("lean_toolchain"),
        material_acquisition=overrides.get("material_acquisition"),
    )
    app.repo_workspace = RepoWorkspaceService(
        runtime,
        native_lake_project_config=native_lake_project_config,
        workspace_config=workspace_config,
    )
    app.material = MaterialService(runtime)
    app.decl_graph = DeclGraphService(runtime)
    app.node = NodeService(runtime, public_decl_provider=providers.content_public_decl_provider or app.decl_graph)
    app.mathlib = MathlibService(runtime)
    app.lean_projection = LeanProjectionService(
        runtime,
        decl_revision_provider=providers.decl_file_revision_provider or app.decl_graph,
        repair_decl_provider=providers.repair_decl_provider or app.decl_graph,
    )
    app.lean_projection.node_projection.contract = app.node.contract
    app.lean_projection.node_projection.export = app.node.export
    app.adapter = AdapterService(runtime)
    if app.lean_projection.adapter_facade is not None:
        app.lean_projection.adapter_facade.provider = app.adapter
    app.validation_snapshot = ValidationSnapshotService(
        runtime,
        content_readiness_provider=providers.content_readiness_provider or app.decl_graph,
        formal_stage_provider=providers.formal_stage_provider or app.decl_graph,
        decl_graph_audit_provider=providers.decl_graph_audit_provider or app.decl_graph,
    )
    app.tool_facade = ToolFacadeService(
        runtime,
        runtime_gateway=providers.runtime_gateway,
        submission_gateway=providers.submission_gateway,
        agent_skill_keys=agent_skill_keys(specs=resolved_agent_type_specs),
        agent_type_permission_names=lambda agent_type: agent_type_permission_names(
            agent_type,
            specs=resolved_agent_type_specs,
        ),
    )
    app.validate()
    if register_application_tools:
        from lean_constellation.tools import register_application_tooling

        registered = register_application_tooling(runtime)
        if not registered.ok:
            messages = "; ".join(issue.message for issue in registered.issues)
            raise RuntimeError(f"Failed to register application tooling: {messages}")
    return runtime


def create_test_runtime_services(
    *,
    ark_services: ARKServices | None = None,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    providers: LeanProviderOverrides | None = None,
    agent_type_specs: Sequence[AgentTypeSpec] | None = None,
    native_lake_project_config: "NativeLakeProjectConfig | None" = None,
    workspace_config: "WorkspaceConfig | None" = None,
    register_application_tools: bool = False,
    test_control_enabled: bool = False,
) -> LeanRuntimeServices:
    """Alias with test-oriented naming for unit tests."""

    return create_lean_runtime_services(
        ark_services=ark_services,
        external_config=external_config,
        external_overrides=external_overrides,
        providers=providers,
        agent_type_specs=agent_type_specs,
        native_lake_project_config=native_lake_project_config,
        workspace_config=workspace_config,
        register_application_tools=register_application_tools,
        test_control_enabled=test_control_enabled,
    )
