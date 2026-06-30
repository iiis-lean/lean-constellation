"""Home bootstrap builders for Lean Constellation AgentTypes."""

from __future__ import annotations

from collections.abc import Iterable

from agent_runtime_kit.agent.homes import HomeCreateSpec, McpServerSpec

from lean_constellation.agents.instructions import assert_instruction_is_runtime_english, render_agent_instruction
from lean_constellation.agents.models import AgentHomeBootstrapSpec, AgentToolViewConfig
from lean_constellation.agents.registry import build_agent_type_specs, get_agent_type_spec, validate_agent_resources
from lean_constellation.agents.skills import build_skill_specs
from lean_constellation.mcp.context import RUNTIME_ENV_KEYS


def build_agent_home_bootstrap_spec(
    agent_type: str,
    *,
    home_id: str | None = None,
    mcp_server_url: str | None = None,
    mcp_server_command: str | None = None,
    mcp_server_args: Iterable[str] | None = None,
    mcp_server_env: dict[str, str] | None = None,
    mcp_server_name: str = "lean-constellation-tools",
    fixed_env: dict[str, str] | None = None,
    required_env: Iterable[str] | None = None,
    validate_resources: bool = True,
) -> AgentHomeBootstrapSpec:
    """Build Lean-side and ARK home creation data for one AgentType."""

    spec = get_agent_type_spec(agent_type)
    if validate_resources:
        report = validate_agent_resources([spec])
        if not report.ok:
            issue_summary = "; ".join(f"{issue.code}:{issue.resource_key}" for issue in report.issues)
            raise ValueError(f"invalid AgentType resources for {agent_type}: {issue_summary}")

    developer_instructions = render_agent_instruction(spec)
    assert_instruction_is_runtime_english(developer_instructions)

    skill_specs = build_skill_specs(spec.skill_keys)
    resolved_home_id = home_id or spec.agent_type
    env = {
        "LEAN_CONSTELLATION_AGENT_TYPE": spec.agent_type,
        "LEAN_CONSTELLATION_AGENT_ROLE": spec.role,
        "LEAN_CONSTELLATION_CONTEXT_SCOPE": spec.context_scope,
        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": spec.application_tool_view_key,
        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": spec.submit_tool_view_key,
    }
    if spec.stage:
        env["LEAN_CONSTELLATION_STAGE"] = spec.stage
    env.update(fixed_env or {})

    if mcp_server_url is not None and mcp_server_command is not None:
        raise ValueError("mcp_server_url and mcp_server_command are mutually exclusive")

    mcp_servers: list[McpServerSpec] = []
    if mcp_server_url is not None:
        mcp_servers.append(
            McpServerSpec(
                name=mcp_server_name,
                transport="http",
                url=mcp_server_url,
                required=True,
                env={
                    "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": spec.application_tool_view_key,
                    "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": spec.submit_tool_view_key,
                },
            )
        )
    elif mcp_server_command is not None:
        for purpose, view_key in (
            ("application", spec.application_tool_view_key),
            ("submit", spec.submit_tool_view_key),
        ):
            mcp_servers.append(
                McpServerSpec(
                    name=f"{mcp_server_name}-{purpose}",
                    transport="stdio",
                    command=mcp_server_command,
                    args=_stdio_args_for_view(mcp_server_args or (), view_key),
                    required=True,
                    env={
                        **(mcp_server_env or {}),
                        "LEAN_CONSTELLATION_MCP_VIEW_KEY": view_key,
                        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": spec.application_tool_view_key,
                        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": spec.submit_tool_view_key,
                    },
                    env_vars=sorted(
                        {
                            *RUNTIME_ENV_KEYS.values(),
                            "ARK_FLOW_ID",
                            "ARK_STEP_ID",
                            "ARK_AGENT_ID",
                            "LEAN_CONSTELLATION_AGENT_TYPE",
                            "LEAN_CONSTELLATION_AGENT_ROLE",
                        }
                    ),
                )
            )

    ark_spec = HomeCreateSpec(
        cli_type=spec.home_type,
        home_id=resolved_home_id,
        skill_specs=skill_specs,
        mcp_servers=mcp_servers,
        fixed_env=env,
        required_env=set(required_env or ()),
    )
    return AgentHomeBootstrapSpec(
        agent_type=spec.agent_type,
        home_type=spec.home_type,
        home_id=resolved_home_id,
        developer_instructions=developer_instructions,
        skill_specs=skill_specs,
        tool_view_config=AgentToolViewConfig(
            application_view_key=spec.application_tool_view_key,
            submit_view_key=spec.submit_tool_view_key,
            endpoint_view_keys=[spec.application_tool_view_key, spec.submit_tool_view_key],
            stage=spec.stage,
        ),
        fixed_env=env,
        required_env=set(required_env or ()),
        mcp_servers=mcp_servers,
        ark_home_create_spec=ark_spec,
    )


def build_all_agent_home_bootstrap_specs(
    *,
    mcp_server_url: str | None = None,
    validate_resources: bool = True,
) -> dict[str, AgentHomeBootstrapSpec]:
    return {
        spec.agent_type: build_agent_home_bootstrap_spec(
            spec.agent_type,
            mcp_server_url=mcp_server_url,
            validate_resources=validate_resources,
        )
        for spec in build_agent_type_specs()
    }


__all__ = [
    "build_agent_home_bootstrap_spec",
    "build_all_agent_home_bootstrap_specs",
]


def _stdio_args_for_view(base_args: Iterable[str], view_key: str) -> list[str]:
    args = [str(arg) for arg in base_args]
    if any("{view_key}" in arg for arg in args):
        return [arg.replace("{view_key}", view_key) for arg in args]
    return [*args, "--view-key", view_key]
