"""Registry surface helpers for Runtime Matrix strict tests."""

from __future__ import annotations

from dataclasses import dataclass

from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES
from lean_constellation.flows.registry import BUSINESS_FLOW_TYPES, BUSINESS_LOGIC_STEP_TYPES
from lean_constellation.tools import build_application_tool_specs
from lean_constellation.tools.submit_registry import build_submit_tool_specs
from tests.real.runtime_matrix.evidence import EvidenceRecorder


@dataclass(frozen=True)
class RuntimeSurface:
    flows: frozenset[str]
    logic_steps: frozenset[str]
    agent_steps: frozenset[str]
    application_tools: frozenset[str]
    submit_tools: frozenset[str]


def current_runtime_surface() -> RuntimeSurface:
    return RuntimeSurface(
        flows=frozenset(cls.flow_type for cls in BUSINESS_FLOW_TYPES),
        logic_steps=frozenset(cls.step_type for cls in BUSINESS_LOGIC_STEP_TYPES),
        agent_steps=frozenset(cls.step_type for cls in BUSINESS_AGENT_STEP_TYPES),
        application_tools=frozenset(spec.name for spec in build_application_tool_specs()),
        submit_tools=frozenset(spec.name for spec in build_submit_tool_specs()),
    )


def strict_missing_report(recorder: EvidenceRecorder, surface: RuntimeSurface | None = None) -> dict[str, list[str]]:
    surface = surface or current_runtime_surface()
    return {
        "missing_flows": sorted(recorder.missing_flows(set(surface.flows))),
        "missing_logic_steps": sorted(recorder.missing_logic_steps(set(surface.logic_steps))),
        "missing_agent_steps": sorted(recorder.missing_agent_steps(set(surface.agent_steps))),
        "missing_application_tools": sorted(recorder.missing_application_tools(set(surface.application_tools))),
        "missing_submit_tools": sorted(recorder.missing_submit_tools(set(surface.submit_tools))),
        "env_gated_blocked": [],
    }
