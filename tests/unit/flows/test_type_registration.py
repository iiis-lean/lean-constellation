from __future__ import annotations

from agent_runtime_kit.flow.registry import FlowTypeRegistry, StepTypeRegistry
from agent_runtime_kit.flow.standard_steps import DispatchStep

from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES
from lean_constellation.flows.registry import BUSINESS_FLOW_TYPES, BUSINESS_LOGIC_STEP_TYPES, register_lean_flow_step_types


def test_lean_flow_step_registry_registers_all_layer4_types() -> None:
    flow_registry = FlowTypeRegistry()
    step_registry = StepTypeRegistry()

    registered = register_lean_flow_step_types(flow_registry=flow_registry, step_registry=step_registry)

    expected_flow_types = {flow_cls.flow_type for flow_cls in BUSINESS_FLOW_TYPES}
    expected_step_types = {
        DispatchStep.step_type,
        *(step_cls.step_type for step_cls in BUSINESS_LOGIC_STEP_TYPES),
        *(step_cls.step_type for step_cls in BUSINESS_AGENT_STEP_TYPES),
    }
    assert set(registered) == expected_flow_types | expected_step_types
    assert set(flow_registry.list()) == expected_flow_types
    assert set(step_registry.list()) == expected_step_types


def test_lean_flow_step_registry_is_idempotent() -> None:
    flow_registry = FlowTypeRegistry()
    step_registry = StepTypeRegistry()

    first = register_lean_flow_step_types(flow_registry=flow_registry, step_registry=step_registry)
    second = register_lean_flow_step_types(flow_registry=flow_registry, step_registry=step_registry)

    assert first
    assert second == []
