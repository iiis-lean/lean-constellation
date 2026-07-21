"""Home bootstrap builders for Lean Constellation AgentTypes."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from urllib.parse import quote

from agent_runtime_kit.agent.homes import McpServerSpec
from agent_runtime_kit.agent.provider_contracts import (
    BaseConfigSource,
    ModelBackendIdentity,
    ProviderHomeSpec,
)
from agent_runtime_kit.agent.providers.codex_home import CodexHomeOptions

from lean_constellation.agents.instructions import assert_instruction_is_runtime_english, render_agent_instruction
from lean_constellation.agents.models import (
    AgentHomeBootstrapSpec,
    AgentHomeType,
    AgentToolViewConfig,
    AgentTypeSpec,
)
from lean_constellation.agents.registry import build_agent_type_specs, get_agent_type_spec, validate_agent_resources
from lean_constellation.agents.skills import build_skill_specs
from lean_constellation.mcp.context import RUNTIME_ENV_KEYS


@dataclass(frozen=True)
class ViewMcpEndpointSpec:
    purpose: str
    view_key: str
    server_name: str


_RUNTIME_HTTP_HEADER_ENV = {
    "x-ark-flow-id": RUNTIME_ENV_KEYS["flow_id"],
    "x-ark-step-id": RUNTIME_ENV_KEYS["step_id"],
    "x-ark-agent-id": RUNTIME_ENV_KEYS["agent_id"],
    "x-ark-scope-id": RUNTIME_ENV_KEYS["scope_id"],
    "x-ark-agent-type": RUNTIME_ENV_KEYS["agent_type"],
    "x-ark-agent-role": RUNTIME_ENV_KEYS["agent_role"],
    "x-ark-expected-tool-view": RUNTIME_ENV_KEYS["expected_view_key"],
    "x-ark-workspace-root": RUNTIME_ENV_KEYS["workspace_root"],
    "x-ark-repo-root": RUNTIME_ENV_KEYS["repo_root"],
    "x-ark-node-path": RUNTIME_ENV_KEYS["node_path"],
    "x-ark-node-kind": RUNTIME_ENV_KEYS["node_kind"],
    "x-ark-contract-version": RUNTIME_ENV_KEYS["contract_version"],
    "x-ark-decl-stage": RUNTIME_ENV_KEYS["stage"],
    "x-ark-round-id": RUNTIME_ENV_KEYS["round_id"],
    "x-ark-batch-decls": RUNTIME_ENV_KEYS["batch_decls"],
    "x-ark-current-decl": RUNTIME_ENV_KEYS["current_decl"],
    "x-ark-decl-kind": RUNTIME_ENV_KEYS["decl_kind"],
    "x-ark-retry-attempt": RUNTIME_ENV_KEYS["retry_attempt"],
    "x-ark-successful-submission-count": RUNTIME_ENV_KEYS["successful_submission_count"],
    "x-ark-successful-submission-kind": RUNTIME_ENV_KEYS["successful_submission_kind"],
}


def build_agent_home_bootstrap_spec(
    agent_type: str,
    *,
    home_id: str | None = None,
    mcp_http_base_url: str | None = None,
    mcp_server_command: str | None = None,
    mcp_server_args: Iterable[str] | None = None,
    mcp_server_env: dict[str, str] | None = None,
    mcp_server_name: str = "lc",
    fixed_env: dict[str, str] | None = None,
    required_env: Iterable[str] | None = None,
    provider_type: AgentHomeType | None = None,
    base_config: BaseConfigSource | None = None,
    config_overrides: dict[str, object] | None = None,
    model_config: ModelBackendIdentity | None = None,
    auth_refs: Iterable[str] | None = None,
    provider_options: object | None = None,
    specs: Sequence[AgentTypeSpec] | None = None,
    validate_resources: bool = True,
) -> AgentHomeBootstrapSpec:
    """Build Lean-side and ARK home creation data for one AgentType."""

    resolved_specs = list(specs) if specs is not None else build_agent_type_specs()
    spec = get_agent_type_spec(agent_type, specs=resolved_specs)
    if validate_resources:
        report = validate_agent_resources(resolved_specs)
        if not report.ok:
            issue_summary = "; ".join(f"{issue.code}:{issue.resource_key}" for issue in report.issues)
            raise ValueError(f"invalid AgentType resources for {agent_type}: {issue_summary}")

    developer_instructions = render_agent_instruction(spec)
    assert_instruction_is_runtime_english(developer_instructions)

    skill_specs = build_skill_specs(spec.skill_keys)
    resolved_home_id = home_id or spec.agent_type
    resolved_provider_type = provider_type or spec.home_type
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

    if mcp_http_base_url is not None and mcp_server_command is not None:
        raise ValueError("HTTP MCP settings and mcp_server_command are mutually exclusive")

    mcp_servers: list[McpServerSpec] = []
    if mcp_http_base_url is not None:
        for endpoint_spec in _view_endpoint_specs(
            spec.application_tool_view_key,
            spec.submit_tool_view_key,
            mcp_server_name=mcp_server_name,
        ):
            mcp_servers.append(
                McpServerSpec(
                    name=endpoint_spec.server_name,
                    transport="http",
                    url=_mcp_view_url(mcp_http_base_url, endpoint_spec.view_key),
                    required=True,
                    env_http_headers=_RUNTIME_HTTP_HEADER_ENV,
                )
            )
    elif mcp_server_command is not None:
        for purpose, view_key in (
            ("app", spec.application_tool_view_key),
            ("submit", spec.submit_tool_view_key),
        ):
            mcp_servers.append(
                McpServerSpec(
                    name=f"{mcp_server_name}_{purpose}",
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

    if resolved_provider_type == "codex":
        if provider_options is not None and not isinstance(provider_options, CodexHomeOptions):
            raise TypeError("codex provider_options must be CodexHomeOptions")
        codex_options = provider_options or CodexHomeOptions()
        provider_options = replace(
            codex_options,
            skill_specs={**codex_options.skill_specs, **skill_specs},
            mcp_servers=codex_options.mcp_servers or tuple(mcp_servers),
        )
    ark_spec = ProviderHomeSpec(
        provider_type=resolved_provider_type,
        home_id=resolved_home_id,
        base_config=base_config,
        config_overrides=dict(config_overrides or {}),
        model_config=model_config,
        instructions=(developer_instructions,),
        skills=tuple(skill_specs.values()),
        mcp_servers=tuple(mcp_servers),
        auth_refs=tuple(auth_refs or ()),
        fixed_env=env,
        required_env=tuple(sorted(set(required_env or ()))),
        provider_options=provider_options,
    )
    return AgentHomeBootstrapSpec(
        agent_type=spec.agent_type,
        home_type=resolved_provider_type,
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
        provider_home_spec=ark_spec,
    )


def build_all_agent_home_bootstrap_specs(
    *,
    mcp_http_base_url: str | None = None,
    specs: Sequence[AgentTypeSpec] | None = None,
    validate_resources: bool = True,
) -> dict[str, AgentHomeBootstrapSpec]:
    resolved_specs = list(specs) if specs is not None else build_agent_type_specs()
    return {
        spec.agent_type: build_agent_home_bootstrap_spec(
            spec.agent_type,
            mcp_http_base_url=mcp_http_base_url,
            specs=resolved_specs,
            validate_resources=validate_resources,
        )
        for spec in resolved_specs
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


def _view_endpoint_specs(application_view_key: str, submit_view_key: str, *, mcp_server_name: str) -> list[ViewMcpEndpointSpec]:
    return [
        ViewMcpEndpointSpec(
            purpose="application",
            view_key=application_view_key,
            server_name=f"{mcp_server_name}_app",
        ),
        ViewMcpEndpointSpec(
            purpose="submit",
            view_key=submit_view_key,
            server_name=f"{mcp_server_name}_submit",
        ),
    ]


def _mcp_view_url(base_url: str, view_key: str) -> str:
    return f"{base_url.rstrip('/')}/mcp/views/{quote(view_key, safe='')}/"
