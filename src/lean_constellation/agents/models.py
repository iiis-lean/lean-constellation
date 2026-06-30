"""Agent type configuration models for Lean Constellation."""

from __future__ import annotations

from typing import Literal

from agent_runtime_kit.agent.homes import HomeCreateSpec, McpServerSpec
from agent_runtime_kit.agent.skills import SkillSpec
from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.tool_facade.context_resolver import ActorRole


AgentHomeType = Literal["codex"]
AgentLifecycleGroup = Literal[
    "repo_lifecycle",
    "coordinator",
    "content_node_task",
    "decl_stage",
    "resource_request",
]
AgentContextScope = Literal["repo", "content_node", "decl_stage", "resource_request"]


class AgentTypeSpec(StrictModel):
    """Application-owned AgentType definition used before ARK home creation."""

    agent_type: str
    role: ActorRole
    home_type: AgentHomeType = "codex"
    lifecycle_group: AgentLifecycleGroup
    context_scope: AgentContextScope
    agent_step_type: str
    instruction_fragment_keys: list[str] = Field(default_factory=list)
    specific_instruction_key: str
    skill_keys: list[str] = Field(default_factory=list)
    application_tool_view_key: str
    submit_tool_view_key: str
    tool_view_agent_aliases: list[str] = Field(default_factory=list)
    stage: str | None = None

    @field_validator(
        "agent_type",
        "lifecycle_group",
        "context_scope",
        "agent_step_type",
        "specific_instruction_key",
        "application_tool_view_key",
        "submit_tool_view_key",
    )
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must be non-empty")
        return value

    @field_validator("instruction_fragment_keys", "skill_keys", "tool_view_agent_aliases")
    @classmethod
    def _dedupe_list(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in values:
            value = str(item).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result

    def agent_type_aliases(self) -> list[str]:
        """Return this AgentType plus configured aliases for ToolView checks."""

        aliases = [self.agent_type, *self.tool_view_agent_aliases]
        result: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            value = alias.strip()
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result


class AgentResourceIssue(StrictModel):
    code: str
    message: str
    agent_type: str | None = None
    resource_type: str | None = None
    resource_key: str | None = None
    details: dict[str, str] = Field(default_factory=dict)


class AgentResourceValidationReport(StrictModel):
    ok: bool
    issues: list[AgentResourceIssue] = Field(default_factory=list)


class AgentToolViewConfig(StrictModel):
    application_view_key: str
    submit_view_key: str
    endpoint_view_keys: list[str]
    stage: str | None = None


class AgentHomeBootstrapSpec(StrictModel):
    """Lean-side home bootstrap data plus the ARK create spec."""

    agent_type: str
    home_type: AgentHomeType
    home_id: str
    developer_instructions: str
    skill_specs: dict[str, SkillSpec] = Field(default_factory=dict)
    tool_view_config: AgentToolViewConfig
    fixed_env: dict[str, str] = Field(default_factory=dict)
    required_env: set[str] = Field(default_factory=set)
    mcp_servers: list[McpServerSpec] = Field(default_factory=list)
    ark_home_create_spec: HomeCreateSpec


__all__ = [
    "AgentContextScope",
    "AgentHomeBootstrapSpec",
    "AgentHomeType",
    "AgentLifecycleGroup",
    "AgentResourceIssue",
    "AgentResourceValidationReport",
    "AgentToolViewConfig",
    "AgentTypeSpec",
]
