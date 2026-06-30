"""ARK AgentType registration for Lean Constellation Agent specs."""

from __future__ import annotations

from agent_runtime_kit.agent.service import AgentType, AgentTypeRegistry

from lean_constellation.agents.instructions import render_agent_instruction
from lean_constellation.agents.registry import build_agent_type_specs


def build_ark_agent_type_registry() -> AgentTypeRegistry:
    """Build the ARK AgentTypeRegistry from Lean AgentTypeSpec records."""

    registry = AgentTypeRegistry()
    for spec in build_agent_type_specs():
        registry.register(build_ark_agent_type(spec.agent_type))
    return registry


def build_ark_agent_type(agent_type: str) -> AgentType:
    """Create a minimal ARK AgentType carrying Lean developer instructions."""

    from lean_constellation.agents.registry import get_agent_type_spec

    spec = get_agent_type_spec(agent_type)
    instructions = render_agent_instruction(spec)

    class LeanArkAgentType(AgentType):
        pass

    LeanArkAgentType.agent_type = spec.agent_type
    LeanArkAgentType.developer_instructions_template = instructions
    return LeanArkAgentType()


__all__ = ["build_ark_agent_type", "build_ark_agent_type_registry"]
