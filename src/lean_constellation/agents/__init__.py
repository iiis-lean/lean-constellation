"""Lean Constellation AgentType configuration."""

from lean_constellation.agents.homes import build_agent_home_bootstrap_spec, build_all_agent_home_bootstrap_specs
from lean_constellation.agents.instructions import build_instruction_service, render_agent_instruction
from lean_constellation.agents.keys import AgentCapabilityKey, ProductionAgentTypeKey, SkillKey
from lean_constellation.agents.models import (
    AgentHomeBootstrapSpec,
    AgentResourceIssue,
    AgentResourceValidationReport,
    AgentToolViewConfig,
    AgentTypeSpec,
)
from lean_constellation.agents.registry import (
    AGENT_TYPE_SPECS,
    agent_skill_keys,
    agent_type_capabilities,
    agent_type_permission_names,
    build_agent_type_specs,
    derive_agent_type_spec,
    get_agent_type_spec,
    validate_agent_resources,
)
from lean_constellation.agents.skills import build_skill_specs, known_skill_keys, materialize_skill_specs
from lean_constellation.agents.surface import AgentSurfaceReport, AgentSurfaceSkill, AgentSurfaceTool, build_agent_surface_reports
from lean_constellation.agents.testing import build_controlled_test_agent_type_specs, controlled_test_agent_type_name

__all__ = [
    "AGENT_TYPE_SPECS",
    "AgentHomeBootstrapSpec",
    "AgentCapabilityKey",
    "AgentResourceIssue",
    "AgentResourceValidationReport",
    "AgentSurfaceReport",
    "AgentSurfaceSkill",
    "AgentSurfaceTool",
    "AgentToolViewConfig",
    "AgentTypeSpec",
    "ProductionAgentTypeKey",
    "SkillKey",
    "agent_skill_keys",
    "agent_type_capabilities",
    "agent_type_permission_names",
    "build_agent_home_bootstrap_spec",
    "build_all_agent_home_bootstrap_specs",
    "build_agent_type_specs",
    "build_controlled_test_agent_type_specs",
    "build_instruction_service",
    "build_skill_specs",
    "build_agent_surface_reports",
    "controlled_test_agent_type_name",
    "derive_agent_type_spec",
    "get_agent_type_spec",
    "known_skill_keys",
    "materialize_skill_specs",
    "render_agent_instruction",
    "validate_agent_resources",
]
