"""Bootstrap helpers for registering Lean Constellation Flow / Step types."""

from __future__ import annotations

from typing import Any

from agent_runtime_kit.flow.standard_steps import DispatchStep

from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES
from lean_constellation.flows.content_node_task import CONTENT_NODE_TASK_FLOW_TYPES, CONTENT_NODE_TASK_STEP_TYPES
from lean_constellation.flows.content_node_task.decl_round import DECL_ROUND_FLOW_TYPES, DECL_ROUND_STEP_TYPES
from lean_constellation.flows.content_node_task.preparation import PREPARATION_RECON_FLOW_TYPES
from lean_constellation.flows.coordinator import COORDINATOR_FLOW_TYPES, COORDINATOR_STEP_TYPES
from lean_constellation.flows.repo_lifecycle import REPO_LIFECYCLE_FLOW_TYPES, REPO_LIFECYCLE_STEP_TYPES
from lean_constellation.flows.resource_request import RESOURCE_REQUEST_FLOW_TYPES, RESOURCE_REQUEST_STEP_TYPES


BUSINESS_FLOW_TYPES = (
    *REPO_LIFECYCLE_FLOW_TYPES,
    *RESOURCE_REQUEST_FLOW_TYPES,
    *COORDINATOR_FLOW_TYPES,
    *CONTENT_NODE_TASK_FLOW_TYPES,
    *PREPARATION_RECON_FLOW_TYPES,
    *DECL_ROUND_FLOW_TYPES,
)

STANDARD_STEP_TYPES = (DispatchStep,)
BUSINESS_LOGIC_STEP_TYPES = (
    *REPO_LIFECYCLE_STEP_TYPES,
    *RESOURCE_REQUEST_STEP_TYPES,
    *COORDINATOR_STEP_TYPES,
    *CONTENT_NODE_TASK_STEP_TYPES,
    *DECL_ROUND_STEP_TYPES,
)


def register_lean_flow_step_types(
    *,
    flow_registry: Any | None = None,
    step_registry: Any | None = None,
    runtime: Any | None = None,
) -> list[str]:
    """Register Lean business Flow / Step types with ARK registries.

    The helper accepts explicit registries, a FlowService-like object, or the
    Lean runtime wrapper. Existing layer-3 callers that pass only a
    ``step_registry`` continue to receive only newly registered step type keys.
    """

    flow_registry = flow_registry or _resolve_registry(runtime, "flow_registry")
    step_registry = step_registry or _resolve_registry(runtime, "step_registry")
    if flow_registry is None and step_registry is None:
        raise ValueError("flow_registry or step_registry is required to register Lean Flow / Step types.")

    registered: list[str] = []

    if flow_registry is not None:
        for flow_cls in BUSINESS_FLOW_TYPES:
            flow_type = flow_cls.flow_type
            if flow_type in getattr(flow_registry, "types", {}):
                continue
            flow_registry.register(flow_cls)
            registered.append(flow_type)

    if step_registry is not None:
        for step_cls in (*STANDARD_STEP_TYPES, *BUSINESS_LOGIC_STEP_TYPES, *BUSINESS_AGENT_STEP_TYPES):
            step_type = step_cls.step_type
            if step_type in getattr(step_registry, "types", {}):
                continue
            step_registry.register(step_cls)
            registered.append(step_type)

    return registered


def _resolve_registry(runtime: Any | None, attr_name: str) -> Any | None:
    if runtime is None:
        return None
    direct = getattr(runtime, attr_name, None)
    if direct is not None:
        return direct
    flow_service = getattr(runtime, "flow_service", None)
    if flow_service is not None:
        value = getattr(flow_service, attr_name, None)
        if value is not None:
            return value
    ark = getattr(runtime, "ark", None)
    if ark is not None:
        value = getattr(ark, attr_name, None)
        if value is not None:
            return value
        flow_service = getattr(ark, "flow_service", None)
        if flow_service is not None:
            value = getattr(flow_service, attr_name, None)
            if value is not None:
                return value
    app = getattr(runtime, "app", None)
    if app is not None:
        value = getattr(app, attr_name, None)
        if value is not None:
            return value
        flow_service = getattr(app, "flow_service", None)
        if flow_service is not None:
            value = getattr(flow_service, attr_name, None)
            if value is not None:
                return value
    return None
