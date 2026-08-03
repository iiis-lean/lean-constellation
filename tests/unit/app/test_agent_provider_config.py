from __future__ import annotations

import json
from pathlib import Path
import tomllib

import pytest

from lean_constellation.agents import build_agent_type_specs
from lean_constellation.app.agent_provider_config import (
    apply_agent_home_overrides,
    build_builtin_provider_registry,
    model_identity_from_override,
    provider_options_from_override,
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


def test_opencode_provider_override_resolves_auth_json_path(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    override = AgentHomeOverrideAppConfig(
        provider_type="opencode",
        provider_options={"auth_json_path": str(auth_path)},
    )

    options = provider_options_from_override("opencode", override)

    assert options is not None
    assert options.auth_json_path == auth_path


def test_default_agent_provider_type_selects_one_provider_for_all_agents(tmp_path: Path) -> None:
    runtime = create_app_runtime_from_config(
        LeanAppConfig(
            workspace_root=tmp_path,
            materialize_agent_homes=False,
            default_agent_provider_type="opencode",
        )
    )

    assert {
        item.provider_type for item in runtime.ark.agent_service.agent_types.list()
    } == {"opencode"}
    assert {
        bundle.provider_type for bundle in runtime.ark.agent_service.provider_registry.list()
    } == {"opencode"}


def test_fresh_agent_record_uses_only_standard_schema_fields(tmp_path: Path) -> None:
    runtime = create_app_runtime_from_config(
        LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False)
    )
    agent = runtime.ark.agent_service.store.create_agent_record(
        scope_id="repo:Fresh",
        agent_type=AGENT_TYPE,
        provider_type="codex",
        home_id=AGENT_TYPE,
    )

    payload = json.loads(
        runtime.ark.agent_service.store.resolve_agent_path(agent.agent_id).read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == 3
    assert payload["provider_type"] == "codex"
    assert payload["session_locator"] is None
    assert payload["artifact_locator"] is None
    assert set(payload) == {
        "object_type",
        "agent_id",
        "scope_id",
        "agent_type",
        "provider_type",
        "home_id",
        "schema_version",
        "session_locator",
        "latest_turn_locator",
        "artifact_locator",
        "fork_info",
        "status",
        "last_completion",
        "created_at",
        "updated_at",
    }


def test_opencode_home_materialization_uses_provider_neutral_resources(tmp_path: Path) -> None:
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"opencode-go":{"type":"api","key":"test-key"}}\n')
    override = AgentHomeOverrideAppConfig(
        provider_type="opencode",
        model="deepseek-v4-flash",
        api_provider="opencode-go",
        api_mode="chat_completions",
        model_reasoning_effort="max",
    )
    specs = apply_agent_home_overrides(
        build_agent_type_specs(),
        {AGENT_TYPE: override},
    )
    registry = build_builtin_provider_registry(
        tmp_path / ".agent_runtime",
        specs,
        {AGENT_TYPE: override},
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
        model_config=model_identity_from_override(override),
        agent_type_specs=specs,
        mcp_http_base_url="http://127.0.0.1:8765",
        auth_json_path=auth_path,
    )

    assert result.ok and result.value is not None
    assert result.value.provider_type == "opencode"
    assert result.value.effective_model == "deepseek-v4-flash"
    assert result.value.effective_reasoning_effort == "max"
    home_root = Path(result.value.home_root)
    assert (home_root / "opencode.json").exists()
    opencode_config = json.loads((home_root / "opencode.json").read_text(encoding="utf-8"))
    assert opencode_config["permission"]["external_directory"] == "deny"
    assert opencode_config["tools"] == {
        "apply_patch": False,
        "bash": False,
        "edit": False,
        "glob": False,
        "grep": False,
        "read": False,
        "webfetch": True,
        "websearch": True,
        "write": False,
    }
    assert (home_root / "AGENTS.md").exists()
    materialized_auth = home_root / ".opencode" / "auth.json"
    assert materialized_auth.read_text() == auth_path.read_text()
    assert materialized_auth.stat().st_mode & 0o777 == 0o600
    assert result.value.mcp_server_names == ["lc_app", "lc_submit"]
    manifest = json.loads(
        (home_root / ".agents" / "lean_constellation_home.json").read_text(encoding="utf-8")
    )
    assert manifest["effective_reasoning_effort"] == "max"


def test_opencode_home_forces_external_directory_denial(tmp_path: Path) -> None:
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
        config_overrides={
            "permission": {
                "external_directory": "allow",
                "read": "allow",
            },
            "tools": {"bash": True, "read": True},
        },
    )

    assert result.ok and result.value is not None
    config = json.loads(
        (Path(result.value.home_root) / "opencode.json").read_text(encoding="utf-8")
    )
    assert config["permission"] == {
        "external_directory": "deny",
        "read": "allow",
    }
    assert config["tools"] == {
        "apply_patch": False,
        "bash": False,
        "edit": False,
        "glob": False,
        "grep": False,
        "read": False,
        "webfetch": True,
        "websearch": True,
        "write": False,
    }


def test_opencode_formal_worker_keeps_repo_file_tools_but_not_bash(tmp_path: Path) -> None:
    specs = apply_agent_home_overrides(
        build_agent_type_specs(),
        {
            "StatementFormalWorkerAgent": AgentHomeOverrideAppConfig(
                provider_type="opencode"
            )
        },
    )
    registry = build_builtin_provider_registry(
        tmp_path / ".agent_runtime",
        specs,
        {
            "StatementFormalWorkerAgent": AgentHomeOverrideAppConfig(
                provider_type="opencode"
            )
        },
    )
    runtime = create_app_runtime_from_config(
        LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False),
        agent_type_specs=specs,
        provider_registry=registry,
    )

    result = materialize_agent_home(
        runtime,
        "StatementFormalWorkerAgent",
        provider_type="opencode",
        agent_type_specs=specs,
        config_overrides={"tools": {"read": True, "edit": True}},
    )

    assert result.ok and result.value is not None
    config = json.loads(
        (Path(result.value.home_root) / "opencode.json").read_text(encoding="utf-8")
    )
    assert config["permission"]["external_directory"] == "deny"
    assert config["tools"] == {
        "apply_patch": True,
        "bash": False,
        "edit": True,
        "glob": True,
        "grep": True,
        "read": True,
        "webfetch": False,
        "websearch": False,
        "write": True,
    }


@pytest.mark.parametrize("agent_type", ["SourceCorpusPrepareAgent", "ResourceCuratorAgent"])
def test_opencode_material_agents_receive_scoped_file_and_web_capabilities(
    tmp_path: Path,
    agent_type: str,
) -> None:
    specs = apply_agent_home_overrides(
        build_agent_type_specs(),
        {agent_type: AgentHomeOverrideAppConfig(provider_type="opencode")},
    )
    registry = build_builtin_provider_registry(
        tmp_path / ".agent_runtime",
        specs,
        {agent_type: AgentHomeOverrideAppConfig(provider_type="opencode")},
    )
    runtime = create_app_runtime_from_config(
        LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False),
        agent_type_specs=specs,
        provider_registry=registry,
    )

    result = materialize_agent_home(
        runtime,
        agent_type,
        provider_type="opencode",
        agent_type_specs=specs,
    )

    assert result.ok and result.value is not None
    config = json.loads(
        (Path(result.value.home_root) / "opencode.json").read_text(encoding="utf-8")
    )
    assert config["permission"]["external_directory"] == "deny"
    assert config["tools"] == {
        "apply_patch": True,
        "bash": False,
        "edit": True,
        "glob": True,
        "grep": True,
        "read": True,
        "webfetch": True,
        "websearch": True,
        "write": True,
    }


@pytest.mark.parametrize(
    ("agent_type", "sandbox_mode", "web_search"),
    [
        ("CoordinatorAgent", "read-only", "live"),
        ("ContentPlanAgent", "read-only", "disabled"),
        ("ResourceCuratorAgent", "workspace-write", "live"),
        ("ProofFormalWorkerAgent", "workspace-write", "disabled"),
    ],
)
def test_codex_home_projects_agent_capability_policy(
    tmp_path: Path,
    agent_type: str,
    sandbox_mode: str,
    web_search: str,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")

    result = materialize_agent_home(runtime, agent_type, provider_type="codex")

    assert result.ok and result.value is not None
    config = tomllib.loads(
        (Path(result.value.home_root) / ".codex" / "config.toml").read_text(
            encoding="utf-8"
        )
    )
    assert config["sandbox_mode"] == sandbox_mode
    assert config["web_search"] == web_search


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
