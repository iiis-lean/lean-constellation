"""Bootstrap helpers for registering Lean Constellation Flow / Step types."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime_kit.flow.models import BaseStep
from agent_runtime_kit.flow.standard_steps import DispatchStep

from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES
from lean_constellation.flows.content_node_task import CONTENT_NODE_TASK_FLOW_TYPES, CONTENT_NODE_TASK_STEP_TYPES
from lean_constellation.flows.content_node_task.decl_round import DECL_ROUND_FLOW_TYPES, DECL_ROUND_STEP_TYPES
from lean_constellation.flows.content_node_task.preparation import PREPARATION_RECON_FLOW_TYPES
from lean_constellation.flows.coordinator import COORDINATOR_FLOW_TYPES, COORDINATOR_STEP_TYPES
from lean_constellation.flows.repo_lifecycle import (
    REPO_LIFECYCLE_FLOW_TYPES,
    REPO_LIFECYCLE_STEP_TYPES,
    ROOT_INTERFACE_FLOW_TYPES,
    ROOT_INTERFACE_STEP_TYPES,
    SOURCE_INDEX_BUILD_FLOW_TYPES,
    SOURCE_INDEX_BUILD_STEP_TYPES,
    CONTINUATION_FLOW_TYPES,
    CONTINUATION_STEP_TYPES,
    RUN_STEP_TYPES,
)
from lean_constellation.flows.resource_request import RESOURCE_REQUEST_FLOW_TYPES, RESOURCE_REQUEST_STEP_TYPES


BUSINESS_FLOW_TYPES = (
    *REPO_LIFECYCLE_FLOW_TYPES,
    *SOURCE_INDEX_BUILD_FLOW_TYPES,
    *ROOT_INTERFACE_FLOW_TYPES,
    *CONTINUATION_FLOW_TYPES,
    *RESOURCE_REQUEST_FLOW_TYPES,
    *COORDINATOR_FLOW_TYPES,
    *CONTENT_NODE_TASK_FLOW_TYPES,
    *PREPARATION_RECON_FLOW_TYPES,
    *DECL_ROUND_FLOW_TYPES,
)

STANDARD_STEP_TYPES = (DispatchStep,)
BUSINESS_LOGIC_STEP_TYPES = (
    *REPO_LIFECYCLE_STEP_TYPES,
    *SOURCE_INDEX_BUILD_STEP_TYPES,
    *ROOT_INTERFACE_STEP_TYPES,
    *CONTINUATION_STEP_TYPES,
    *RUN_STEP_TYPES,
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
    step_type_overrides: Mapping[str, type[BaseStep]] | None = None,
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
        step_classes = _apply_step_type_overrides(
            (*STANDARD_STEP_TYPES, *BUSINESS_LOGIC_STEP_TYPES, *BUSINESS_AGENT_STEP_TYPES),
            step_type_overrides or {},
        )
        for step_cls in step_classes:
            step_type = step_cls.step_type
            if step_type in getattr(step_registry, "types", {}):
                continue
            step_registry.register(step_cls)
            registered.append(step_type)

    return registered


def _apply_step_type_overrides(
    step_classes: tuple[type[BaseStep], ...],
    overrides: Mapping[str, type[BaseStep]],
) -> tuple[type[BaseStep], ...]:
    if not overrides:
        return step_classes

    known_step_types = {step_cls.step_type for step_cls in step_classes}
    unknown = sorted(set(overrides) - known_step_types)
    if unknown:
        raise ValueError(f"unknown step_type override: {','.join(unknown)}")

    resolved: list[type[BaseStep]] = []
    for step_cls in step_classes:
        override_cls = overrides.get(step_cls.step_type)
        if override_cls is None:
            resolved.append(step_cls)
            continue
        if not issubclass(override_cls, BaseStep):
            raise TypeError(f"step_type override must inherit BaseStep: {step_cls.step_type}")
        if override_cls.step_type != step_cls.step_type:
            raise ValueError(
                f"step_type override for {step_cls.step_type} declares {override_cls.step_type}"
            )
        resolved.append(override_cls)
    return tuple(resolved)


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
