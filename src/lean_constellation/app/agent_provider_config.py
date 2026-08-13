"""Provider-neutral AgentType, Home, and runtime assembly from app config."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_runtime_kit.agent.provider_contracts import ModelBackendIdentity, ProviderRegistry
from agent_runtime_kit.agent.providers import (
    ClaudeCodeProvider,
    OpenAIAgentsHomeOptions,
    OpenAIAgentsProvider,
    OpenCodeHomeOptions,
    PiHomeOptions,
    build_opencode_provider_bundle,
    build_pi_provider_bundle,
    CodexProvider,
)
from agent_runtime_kit.agent.providers.claude_code_home import ClaudeCodeHomeOptions
from agent_runtime_kit.agent.providers.claude_code_bundle import build_claude_code_provider_bundle
from agent_runtime_kit.agent.providers.codex_home import CodexHomeOptions

from lean_constellation.agents.models import AgentTypeSpec
from lean_constellation.app.config import AgentHomeOverrideAppConfig


DEFAULT_OPENAI_AGENTS_FACTORY_REF = "lean_constellation/default"


CODEX_NATIVE_WEB_AGENT_TYPES = frozenset(
    {
        "RepoFormatDiscoveryAgent",
        "SourceCorpusBuilderAgent",
        "ResourceCuratorAgent",
        "RepoResourceDiscoveryAgent",
        "RepoLeanProviderDiscoveryAgent",
        "CoordinatorAgent",
    }
)
CODEX_NATIVE_FILE_AGENT_TYPES = frozenset(
    {
        "SourceCorpusBuilderAgent",
        "ResourceCuratorAgent",
        "StatementFormalWorkerAgent",
        "ProofFormalWorkerAgent",
    }
)
OPENCODE_NATIVE_WEB_AGENT_TYPES = frozenset(
    {
        "RepoFormatDiscoveryAgent",
        "SourceCorpusBuilderAgent",
        "ResourceCuratorAgent",
        "RepoResourceDiscoveryAgent",
        "RepoLeanProviderDiscoveryAgent",
        "CoordinatorAgent",
    }
)
OPENCODE_NATIVE_FILE_AGENT_TYPES = frozenset(
    {
        "SourceCorpusBuilderAgent",
        "ResourceCuratorAgent",
        "StatementFormalWorkerAgent",
        "ProofFormalWorkerAgent",
    }
)


def codex_native_config_defaults(permission_names: set[str]) -> dict[str, object]:
    """Return Codex-native defaults for one AgentType permission lineage."""

    return {
        "sandbox_mode": (
            "workspace-write"
            if permission_names & CODEX_NATIVE_FILE_AGENT_TYPES
            else "read-only"
        ),
        "web_search": (
            "live" if permission_names & CODEX_NATIVE_WEB_AGENT_TYPES else "disabled"
        ),
    }


def opencode_native_tool_defaults(permission_names: set[str]) -> dict[str, bool]:
    """Return OpenCode-native tool defaults for one AgentType permission lineage."""

    direct_file_access = bool(permission_names & OPENCODE_NATIVE_FILE_AGENT_TYPES)
    general_web = bool(permission_names & OPENCODE_NATIVE_WEB_AGENT_TYPES)
    return {
        "bash": False,
        "glob": direct_file_access,
        "grep": direct_file_access,
        "read": direct_file_access,
        "edit": direct_file_access,
        "write": direct_file_access,
        "apply_patch": direct_file_access,
        "webfetch": general_web,
        "websearch": general_web,
    }


def apply_agent_home_overrides(
    specs: Sequence[AgentTypeSpec],
    overrides: Mapping[str, AgentHomeOverrideAppConfig] | None,
    *,
    default_provider_type: str | None = None,
) -> list[AgentTypeSpec]:
    """Apply only the Provider binding; resources remain owned by AgentTypeSpec."""

    configured = overrides or {}
    return [
        spec.model_copy(
            update={"home_type": configured[spec.agent_type].provider_type}
        )
        if spec.agent_type in configured and configured[spec.agent_type].provider_type is not None
        else spec.model_copy(update={"home_type": default_provider_type})
        if default_provider_type is not None
        else spec
        for spec in specs
    ]


def build_builtin_provider_registry(
    runtime_root: Path | str,
    specs: Sequence[AgentTypeSpec],
    overrides: Mapping[str, AgentHomeOverrideAppConfig] | None,
) -> ProviderRegistry:
    """Build every configured built-in Provider bundle through the common registry."""

    provider_types = {spec.home_type for spec in specs}
    root = Path(runtime_root).expanduser()
    registry = ProviderRegistry()
    if "codex" in provider_types:
        provider = CodexProvider(runtime_root=root)
        registry.register(provider.build_provider_bundle(runtime_root=root))
    if "claude_code" in provider_types:
        provider = ClaudeCodeProvider()
        registry.register(build_claude_code_provider_bundle(provider, runtime_root=root))
    if "pi" in provider_types:
        registry.register(build_pi_provider_bundle(runtime_root=root))
    if "openai_agents" in provider_types:
        provider = OpenAIAgentsProvider()
        provider.registry.register_agent_factory(
            DEFAULT_OPENAI_AGENTS_FACTORY_REF,
            _build_default_openai_agent,
        )
        for spec in specs:
            if spec.home_type != "openai_agents":
                continue
            configured = (overrides or {}).get(spec.agent_type)
            factory_ref = str((configured.provider_options if configured else {}).get(
                "agent_factory_ref",
                DEFAULT_OPENAI_AGENTS_FACTORY_REF,
            ))
            if factory_ref != DEFAULT_OPENAI_AGENTS_FACTORY_REF:
                raise ValueError(
                    "custom OpenAI Agents factory refs require an application-provided ProviderRegistry"
                )
        registry.register(provider.build_bundle(runtime_root=root))
    if "opencode" in provider_types:
        registry.register(build_opencode_provider_bundle(runtime_root=root))
    unknown = sorted(provider_types - {bundle.provider_type for bundle in registry.list()})
    if unknown:
        raise ValueError(f"no built-in Provider bundle assembly for: {', '.join(unknown)}")
    return registry


def model_identity_from_override(
    override: AgentHomeOverrideAppConfig | None,
) -> ModelBackendIdentity | None:
    if override is None:
        return None
    configured = any(
        value is not None
        for value in (
            override.api_provider,
            override.api_mode,
            override.model,
            override.model_version,
            override.model_reasoning_effort,
        )
    )
    if not configured:
        return None
    if override.api_provider is None or override.api_mode is None:
        # A model-only value can still be a provider-native Home override.  It
        # is not a complete cross-provider backend identity until both API
        # dimensions are known, so leave the normalized identity absent.
        return None
    return ModelBackendIdentity(
        api_provider=override.api_provider,
        api_mode=override.api_mode,
        requested_model=override.model,
        model_version=override.model_version,
        reasoning_effort=override.model_reasoning_effort,
    )


def provider_options_from_override(
    provider_type: str,
    override: AgentHomeOverrideAppConfig | None,
) -> object | None:
    options = dict(override.provider_options if override is not None else {})
    if provider_type == "codex":
        for field_name in ("auth_json_path",):
            if field_name in options and options[field_name] is not None:
                options[field_name] = Path(options[field_name]).expanduser()
        return CodexHomeOptions(**options)
    if provider_type == "claude_code":
        if override is not None:
            options.setdefault("model", override.model)
            options.setdefault("effort", override.model_reasoning_effort)
        options = {key: value for key, value in options.items() if value is not None}
        if "add_dirs" in options:
            options["add_dirs"] = tuple(Path(item).expanduser() for item in options["add_dirs"])
        return ClaudeCodeHomeOptions(**options)
    if provider_type == "pi":
        for field_name in (
            "auth_json_path",
            "models_json_path",
            "pi_cli_path",
            "mcp_runtime_root",
        ):
            if field_name in options and options[field_name] is not None:
                options[field_name] = Path(options[field_name]).expanduser()
        return PiHomeOptions(**options)
    if provider_type == "openai_agents":
        options.setdefault("agent_factory_ref", DEFAULT_OPENAI_AGENTS_FACTORY_REF)
        if override is not None:
            options.setdefault("context_window_tokens", override.context_window_tokens)
            options.setdefault("max_output_tokens", override.max_output_tokens)
        options = {key: value for key, value in options.items() if value is not None}
        return OpenAIAgentsHomeOptions(**options)
    if provider_type == "opencode":
        if "auth_json_path" in options and options["auth_json_path"] is not None:
            options["auth_json_path"] = Path(options["auth_json_path"]).expanduser()
        return OpenCodeHomeOptions(**options)
    raise ValueError(f"unsupported Agent Home Provider: {provider_type}")


def _build_default_openai_agent(context: Any) -> object:
    try:
        from agents import Agent
    except ImportError as exc:  # pragma: no cover - exercised by optional-dependency environments.
        raise RuntimeError(
            "OpenAI Agents Provider requires agent-runtime-kit[openai-agents]"
        ) from exc
    return Agent(
        name=f"lean-constellation-{context.home_id}",
        instructions=context.instructions,
        model=context.model,
    )


__all__ = [
    "CODEX_NATIVE_FILE_AGENT_TYPES",
    "CODEX_NATIVE_WEB_AGENT_TYPES",
    "DEFAULT_OPENAI_AGENTS_FACTORY_REF",
    "OPENCODE_NATIVE_FILE_AGENT_TYPES",
    "OPENCODE_NATIVE_WEB_AGENT_TYPES",
    "apply_agent_home_overrides",
    "build_builtin_provider_registry",
    "codex_native_config_defaults",
    "model_identity_from_override",
    "opencode_native_tool_defaults",
    "provider_options_from_override",
]
