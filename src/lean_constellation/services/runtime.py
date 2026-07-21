"""Shared Lean Constellation service runtime containers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_runtime_kit.runtime import ARKServices, AppServices

if TYPE_CHECKING:
    from lean_constellation.app.config import AutomaticCheckpointAppConfig
    from lean_constellation.app.runtime import ApplicationSnapshotRuntime
    from lean_constellation.services.adapter import AdapterService
    from lean_constellation.services.decl_graph import DeclGraphService
    from lean_constellation.services.external_clients import ExternalClientService
    from lean_constellation.services.foundation import FoundationService
    from lean_constellation.services.lean_projection import LeanProjectionService
    from lean_constellation.services.material import MaterialService
    from lean_constellation.services.mathlib import MathlibService
    from lean_constellation.services.node import NodeService
    from lean_constellation.services.repo_workspace import RepoWorkspaceService
    from lean_constellation.services.tool_facade import ToolFacadeService
    from lean_constellation.services.validation_snapshot import ValidationSnapshotService
    from lean_constellation.services.validation_snapshot.source_index_checkpoint import SourceIndexCheckpointAdapter


@dataclass
class LeanConstellationServices(AppServices):
    """Mutable registry for all Lean Constellation application services."""

    foundation: "FoundationService | None" = None
    external: "ExternalClientService | None" = None
    repo_workspace: "RepoWorkspaceService | None" = None
    material: "MaterialService | None" = None
    node: "NodeService | None" = None
    mathlib: "MathlibService | None" = None
    lean_projection: "LeanProjectionService | None" = None
    adapter: "AdapterService | None" = None
    decl_graph: "DeclGraphService | None" = None
    validation_snapshot: "ValidationSnapshotService | None" = None
    source_index_checkpoint: "SourceIndexCheckpointAdapter | None" = None
    snapshot_runtime: "ApplicationSnapshotRuntime | None" = None
    tool_facade: "ToolFacadeService | None" = None
    automatic_checkpoints: "AutomaticCheckpointAppConfig | None" = None

    def validate(self) -> None:
        missing = [
            name
            for name in (
                "foundation",
                "external",
                "repo_workspace",
                "material",
                "node",
                "mathlib",
                "lean_projection",
                "adapter",
                "decl_graph",
                "validation_snapshot",
                "tool_facade",
            )
            if getattr(self, name) is None
        ]
        if missing:
            raise RuntimeError(f"Lean Constellation services are not initialized: {', '.join(missing)}")


@dataclass
class LeanRuntimeServices:
    """Shared runtime reference passed to every service and component."""

    ark: ARKServices
    app: LeanConstellationServices
    test_control_enabled: bool = False

    @property
    def foundation(self) -> "FoundationService":
        return self.require_app_service("foundation")

    @property
    def external(self) -> "ExternalClientService":
        return self.require_app_service("external")

    @property
    def repo_workspace(self) -> "RepoWorkspaceService":
        return self.require_app_service("repo_workspace")

    @property
    def material(self) -> "MaterialService":
        return self.require_app_service("material")

    @property
    def node(self) -> "NodeService":
        return self.require_app_service("node")

    @property
    def mathlib(self) -> "MathlibService":
        return self.require_app_service("mathlib")

    @property
    def lean_projection(self) -> "LeanProjectionService":
        return self.require_app_service("lean_projection")

    @property
    def adapter(self) -> "AdapterService":
        return self.require_app_service("adapter")

    @property
    def decl_graph(self) -> "DeclGraphService":
        return self.require_app_service("decl_graph")

    @property
    def validation_snapshot(self) -> "ValidationSnapshotService":
        return self.require_app_service("validation_snapshot")

    @property
    def tool_facade(self) -> "ToolFacadeService":
        return self.require_app_service("tool_facade")

    def require_app_service(self, name: str) -> Any:
        if not hasattr(self.app, name):
            raise RuntimeError(f"Unknown Lean Constellation app service: {name}")
        service = getattr(self.app, name)
        if service is None:
            raise RuntimeError(f"Lean Constellation app service is not initialized: {name}")
        return service

    def require_ark_service(self, name: str) -> Any:
        if not hasattr(self.ark, name):
            raise RuntimeError(f"Unknown ARK service: {name}")
        service = getattr(self.ark, name)
        if service is None:
            raise RuntimeError(f"ARK service is not initialized: {name}")
        return service

    def get_flow(self, flow_id: str) -> Any:
        flow_service = self.require_ark_service("flow_service")
        if hasattr(flow_service, "get_flow"):
            return flow_service.get_flow(flow_id)
        if hasattr(flow_service, "load_flow"):
            return flow_service.load_flow(flow_id)
        raise RuntimeError("ARK flow_service does not expose get_flow/load_flow.")

    def get_step(self, step_id: str) -> Any:
        step_service = self.require_ark_service("step_service")
        if hasattr(step_service, "get_step"):
            return step_service.get_step(step_id)
        if hasattr(step_service, "load_step"):
            return step_service.load_step(step_id)
        store = getattr(step_service, "store", None)
        if store is not None and hasattr(store, "get_step"):
            return store.get_step(step_id)
        raise RuntimeError("ARK step_service does not expose get_step/load_step.")

    def list_flows(self, **filters: Any) -> list[Any]:
        flow_service = self.require_ark_service("flow_service")
        if hasattr(flow_service, "list_flows"):
            return list(flow_service.list_flows(**filters))
        raise RuntimeError("ARK flow_service does not expose list_flows.")

    def list_steps(self, **filters: Any) -> list[Any]:
        step_service = self.require_ark_service("step_service")
        if hasattr(step_service, "list_steps"):
            return list(step_service.list_steps(**filters))
        store = getattr(step_service, "store", None)
        if store is not None and hasattr(store, "list_steps"):
            return list(store.list_steps(**filters))
        raise RuntimeError("ARK step_service does not expose list_steps.")

    def list_child_flows(
        self,
        *,
        parent_flow_id: str | None = None,
        parent_dispatch_step_id: str | None = None,
    ) -> list[Any]:
        flow_service = self.require_ark_service("flow_service")
        if hasattr(flow_service, "list_child_flows"):
            return list(
                flow_service.list_child_flows(
                    parent_flow_id=parent_flow_id,
                    parent_dispatch_step_id=parent_dispatch_step_id,
                )
            )
        filters: dict[str, Any] = {}
        if parent_flow_id is not None:
            filters["parent_flow_id"] = parent_flow_id
        if parent_dispatch_step_id is not None:
            filters["parent_dispatch_step_id"] = parent_dispatch_step_id
        if hasattr(flow_service, "list_flows"):
            return list(flow_service.list_flows(**filters))
        raise RuntimeError("ARK flow_service does not expose child flow listing.")

    def get_agent(self, agent_id: str) -> Any:
        agent_service = self.require_ark_service("agent_service")
        if hasattr(agent_service, "get_agent"):
            return agent_service.get_agent(agent_id)
        if hasattr(agent_service, "load_agent"):
            return agent_service.load_agent(agent_id)
        raise RuntimeError("ARK agent_service does not expose get_agent/load_agent.")

    def query_latest_turn(self, agent_id: str) -> Any:
        agent_service = self.require_ark_service("agent_service")
        return agent_service.query_turn(agent_id, latest=True)
