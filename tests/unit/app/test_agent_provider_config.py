from __future__ import annotations

from pathlib import Path

from lean_constellation.agents import build_agent_type_specs
from lean_constellation.app.agent_provider_config import (
    apply_agent_home_overrides,
    build_builtin_provider_registry,
)
from lean_constellation.app.bootstrap import materialize_agent_home
from lean_constellation.app.config import AgentHomeOverrideAppConfig, LeanAppConfig
from lean_constellation.app.runtime import create_app_runtime_from_config
from lean_constellation.app.runtime import create_app_runtime_services
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry


AGENT_TYPE = "RepoFormatDiscoveryAgent"


def test_provider_override_drives_agent_type_and_registry(tmp_path: Path) -> None:
    override = AgentHomeOverrideAppConfig(
        provider_type="opencode",
        api_provider="deepseek",
        api_mode="chat_completions",
        model="deepseek-chat",
    )
    config = LeanAppConfig(
        workspace_root=tmp_path,
        materialize_agent_homes=False,
        agent_home_overrides={AGENT_TYPE: override},
    )

    runtime = create_app_runtime_from_config(config)

    agent_type = runtime.ark.agent_service.agent_types.get(AGENT_TYPE)
    assert agent_type.provider_type == "opencode"
    assert agent_type.default_home_id == AGENT_TYPE
    assert {bundle.provider_type for bundle in runtime.ark.agent_service.provider_registry.list()} == {
        "codex",
        "opencode",
    }


def test_opencode_home_materialization_uses_provider_neutral_resources(tmp_path: Path) -> None:
    specs = apply_agent_home_overrides(
        build_agent_type_specs(),
        {AGENT_TYPE: AgentHomeOverrideAppConfig(provider_type="opencode")},
    )
    registry = build_builtin_provider_registry(
        tmp_path / ".agent_runtime",
        specs,
        {AGENT_TYPE: AgentHomeOverrideAppConfig(provider_type="opencode")},
    )
    runtime = create_app_runtime_from_config(
        LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False),
        agent_type_specs=specs,
        provider_registry=registry,
    )

    result = materialize_agent_home(
        runtime,
        AGENT_TYPE,
        provider_type="opencode",
        agent_type_specs=specs,
        mcp_http_base_url="http://127.0.0.1:8765",
    )

    assert result.ok and result.value is not None
    assert result.value.provider_type == "opencode"
    home_root = Path(result.value.home_root)
    assert (home_root / "opencode.json").exists()
    assert (home_root / "AGENTS.md").exists()
    assert result.value.mcp_server_names == ["lc_app", "lc_submit"]
    assert result.value.codex_config_path is None


def test_repo_runtime_registry_assembles_configured_provider(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "Repo" / ".lean_constellation").mkdir(parents=True)
    config = LeanAppConfig(
        workspace_root=workspace,
        materialize_agent_homes=False,
        agent_home_overrides={
            AGENT_TYPE: AgentHomeOverrideAppConfig(provider_type="opencode")
        },
    )

    result = RepoRuntimeRegistry(config).get_or_load("Repo")

    assert result.ok and result.value is not None
    agent_service = result.value.ark.agent_service
    assert agent_service.agent_types.get(AGENT_TYPE).provider_type == "opencode"
    assert "opencode" in agent_service.provider_registry


def test_all_builtin_provider_bundles_compose_with_codex(tmp_path: Path) -> None:
    base_specs = build_agent_type_specs()
    provider_types = ("claude_code", "pi", "openai_agents", "opencode")
    overrides = {
        spec.agent_type: AgentHomeOverrideAppConfig(provider_type=provider_type)
        for spec, provider_type in zip(base_specs[:4], provider_types, strict=True)
    }
    specs = apply_agent_home_overrides(base_specs, overrides)
    registry = build_builtin_provider_registry(tmp_path / ".agent_runtime", specs, overrides)

    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        agent_type_specs=specs,
        provider_registry=registry,
    )

    assert {bundle.provider_type for bundle in runtime.ark.agent_service.provider_registry.list()} == {
        "codex",
        "claude_code",
        "pi",
        "openai_agents",
        "opencode",
    }
