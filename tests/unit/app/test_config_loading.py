from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lean_constellation.app import LeanAppConfig, load_app_config
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode


def test_checkpoint_and_trace_report_config_defaults(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace")

    assert config.automatic_checkpoints.repo_flow_boundaries_enabled is True
    assert config.automatic_checkpoints.content_task_progress_enabled is False
    assert config.agent_trace_reports.persistence == "latest_only"
    assert config.agent_trace_reports.include_in_snapshots is False


def test_checkpoint_and_trace_report_config_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        LeanAppConfig.model_validate({"automatic_checkpoints": {"unknown": True}})
    with pytest.raises(ValidationError):
        LeanAppConfig.model_validate({"agent_trace_reports": {"persistence": "unknown"}})


def test_agent_home_overrides_load_by_known_agent_type(tmp_path) -> None:
    path = tmp_path / "lean_constellation.toml"
    path.write_text(
        "\n".join(
            [
                f'workspace_root = "{tmp_path / "workspace"}"',
                "[agent_home_overrides.ContentPlanAgent]",
                'model = "gpt-5.6-sol"',
                'model_reasoning_effort = "high"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_app_config(path)

    override = config.agent_home_overrides["ContentPlanAgent"]
    assert override.model == "gpt-5.6-sol"
    assert override.model_reasoning_effort == "high"
    assert config.redacted_view().agent_home_overrides == config.agent_home_overrides
    assert config.redacted_view().default_agent_provider_type == "codex"


def test_agent_home_overrides_load_provider_neutral_home_configuration(tmp_path) -> None:
    path = tmp_path / "lean_constellation.toml"
    path.write_text(
        "\n".join(
            [
                f'workspace_root = "{tmp_path / "workspace"}"',
                "[agent_home_overrides.ContentPlanAgent]",
                'provider_type = "openai_agents"',
                'api_provider = "deepseek"',
                'api_mode = "chat_completions"',
                'model = "deepseek-chat"',
                "context_window_tokens = 65536",
                'required_env = ["DEEPSEEK_API_KEY"]',
                "[agent_home_overrides.ContentPlanAgent.provider_options]",
                'api_key_env = "DEEPSEEK_API_KEY"',
                'base_url = "https://api.deepseek.com/v1"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_app_config(path)
    override = config.agent_home_overrides["ContentPlanAgent"]

    assert override.provider_type == "openai_agents"
    assert override.api_mode == "chat_completions"
    assert override.context_window_tokens == 65536
    assert override.required_env == ["DEEPSEEK_API_KEY"]
    assert override.provider_options["api_key_env"] == "DEEPSEEK_API_KEY"


def test_agent_home_overrides_reject_inline_provider_secrets(tmp_path) -> None:
    with pytest.raises(ValidationError, match="must use an environment or file reference"):
        LeanAppConfig(
            workspace_root=tmp_path / "workspace",
            agent_home_overrides={
                "ContentPlanAgent": {
                    "provider_type": "openai_agents",
                    "provider_options": {"api_key": "inline-secret"},
                }
            },
        )


def test_agent_home_overrides_reject_inline_config_secrets(tmp_path) -> None:
    with pytest.raises(ValidationError, match="must use an environment or file reference"):
        LeanAppConfig(
            workspace_root=tmp_path / "workspace",
            agent_home_overrides={
                "ContentPlanAgent": {
                    "provider_type": "opencode",
                    "config_overrides": {"apiKey": "inline-secret"},
                }
            },
        )


def test_agent_home_overrides_reject_unknown_agent_type(tmp_path) -> None:
    with pytest.raises(ValidationError, match="unknown agent_home_overrides AgentType"):
        LeanAppConfig(
            workspace_root=tmp_path / "workspace",
            agent_home_overrides={"UnknownAgent": {"model": "example"}},
        )


def test_load_app_config_reads_toml_and_derives_codex_paths_without_reading_secrets(tmp_path) -> None:
    config_home = tmp_path / "codex"
    config_home.mkdir()
    (config_home / "auth.json").write_text('{"token": "secret-token"}\n', encoding="utf-8")
    path = tmp_path / "lean_constellation.toml"
    path.write_text(
        "\n".join(
            [
                f'workspace_root = "{tmp_path / "workspace"}"',
                f'codex_config_home = "{config_home}"',
                "max_concurrent_flow_advances = 2",
                "max_concurrent_steps = 3",
                'mcp_http_host = "0.0.0.0"',
                "mcp_http_port = 9876",
                "[toolkit]",
                'mode = "managed"',
                "port = 8288",
                'required_tools = ["diagnostics.file"]',
                "[automatic_checkpoints]",
                "repo_flow_boundaries_enabled = false",
                "content_task_progress_enabled = true",
                "[agent_trace_reports]",
                'persistence = "latest_and_turns"',
                "include_in_snapshots = true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_app_config(path)
    view = config.redacted_view()
    dumped = view.model_dump_json()

    assert config.runtime_root is None
    assert config.codex_base_config_path == config_home / "config.toml"
    assert config.codex_auth_json_path == config_home / "auth.json"
    assert view.max_concurrent_flow_advances == 2
    assert view.max_concurrent_steps == 3
    assert view.mcp_http_host == "0.0.0.0"
    assert view.mcp_http_port == 9876
    assert view.mcp_http_base_url == "http://0.0.0.0:9876"
    assert view.production_mcp_http_base_url == "http://127.0.0.1:8766"
    assert view.admin_http_base_url == "http://127.0.0.1:8766"
    assert view.server_start_paused is True
    assert view.materialize_agent_homes is True
    assert view.scheduler_enabled is True
    assert view.operator_data_api_enabled is False
    assert view.toolkit.mode == "managed"
    assert view.toolkit.effective_base_url() == "http://127.0.0.1:8288"
    assert view.toolkit.required_tools == ["diagnostics.file"]
    assert view.automatic_checkpoints.repo_flow_boundaries_enabled is False
    assert view.automatic_checkpoints.content_task_progress_enabled is True
    assert view.agent_trace_reports.persistence == "latest_and_turns"
    assert view.agent_trace_reports.include_in_snapshots is True
    assert view.native_lake_project.lean_toolchain == "leanprover/lean4:v4.28.0"
    assert view.native_lake_project.mathlib_rev == "v4.28.0"
    assert "secret-token" not in dumped


def test_load_app_config_env_overrides_json(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace_root": str(tmp_path / "from_file"), "max_concurrent_steps": 1}), encoding="utf-8")

    config = load_app_config(
        path,
        env={
            "LEAN_CONSTELLATION_WORKSPACE_ROOT": str(tmp_path / "from_env"),
            "LEAN_CONSTELLATION_MAX_CONCURRENT_STEPS": "4",
            "LEAN_CONSTELLATION_MCP_HTTP_BASE_URL": "http://127.0.0.1:9999/custom",
            "LEAN_CONSTELLATION_ADMIN_HTTP_PORT": "9998",
            "LEAN_CONSTELLATION_SERVER_START_PAUSED": "false",
            "LEAN_CONSTELLATION_OPERATOR_DATA_API_ENABLED": "true",
            "LEAN_CONSTELLATION_MATERIALIZE_AGENT_HOMES": "false",
            "LEAN_CONSTELLATION_TOOLKIT_BASE_URL": "http://toolkit.test",
            "LEAN_CONSTELLATION_TOOLKIT_ENABLED_GROUPS": "lean,mathlib",
            "LEAN_CONSTELLATION_TOOLKIT_REQUIRED_TOOLS": "diagnostics.file,lean_explore.semantic_search",
            "LEAN_CONSTELLATION_LEAN_VERSION": "4.29.0",
            "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PROJECT_ROOT": str(tmp_path / "template"),
            "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PACKAGE_NAMES": "mathlib,aesop",
            "LEAN_CONSTELLATION_CHECKPOINT_REPO_FLOW_BOUNDARIES_ENABLED": "false",
            "LEAN_CONSTELLATION_CHECKPOINT_CONTENT_TASK_PROGRESS_ENABLED": "true",
            "LEAN_CONSTELLATION_AGENT_TRACE_REPORT_PERSISTENCE": "disabled",
            "LEAN_CONSTELLATION_AGENT_TRACE_REPORT_INCLUDE_IN_SNAPSHOTS": "true",
            "LEAN_CONSTELLATION_SHARED_ELAN_HOME": str(tmp_path / "shared_elan"),
        },
    )

    assert config.workspace_root == tmp_path / "from_env"
    assert config.max_concurrent_steps == 4
    assert config.mcp_http_effective_base_url() == "http://127.0.0.1:9999/custom"
    assert config.production_mcp_http_effective_base_url() == "http://127.0.0.1:9999/custom"
    assert config.admin_http_effective_base_url() == "http://127.0.0.1:9998"
    assert config.server_start_paused is False
    assert config.materialize_agent_homes is False
    assert config.operator_data_api_enabled is True
    assert config.toolkit.base_url == "http://toolkit.test"
    assert config.toolkit.enabled_groups == ["lean", "mathlib"]
    assert config.toolkit.required_tools == ["diagnostics.file", "lean_explore.semantic_search"]
    assert config.native_lake_project.lean_version == "4.29.0"
    assert config.native_lake_project.lean_toolchain == "leanprover/lean4:v4.29.0"
    assert config.native_lake_project.mathlib_rev == "v4.29.0"
    assert config.native_lake_project.local_package_cache is not None
    assert config.native_lake_project.local_package_cache.packages_root == tmp_path / "template" / ".lake" / "packages"
    assert config.native_lake_project.local_package_cache.manifest_path == tmp_path / "template" / "lake-manifest.json"
    assert config.native_lake_project.local_package_cache.package_names == ["mathlib", "aesop"]
    assert config.automatic_checkpoints.repo_flow_boundaries_enabled is False
    assert config.automatic_checkpoints.content_task_progress_enabled is True
    assert config.agent_trace_reports.persistence == "disabled"
    assert config.agent_trace_reports.include_in_snapshots is True
    assert config.shared_elan_home == tmp_path / "shared_elan"


def test_load_app_config_reads_workspace_repo_defaults_from_env(tmp_path) -> None:
    config = load_app_config(
        None,
        env={
            "LEAN_CONSTELLATION_WORKSPACE_ROOT": str(tmp_path / "workspace"),
            "LEAN_CONSTELLATION_DEFAULT_DIRECT_REPO_COMPLETION_MODE": "graph_declared",
            "LEAN_CONSTELLATION_DEFAULT_REQUIREMENT_PROOF_AVAILABILITY": "proved",
            "LEAN_CONSTELLATION_REQUIREMENT_DECLARED_PROVIDER_COMPLETION_MODE": "graph_declared",
            "LEAN_CONSTELLATION_REQUIREMENT_PROVED_PROVIDER_COMPLETION_MODE": "graph_proved",
        },
    )

    assert config.workspace_config.default_direct_repo_completion_mode == RepoCompletionMode.GRAPH_DECLARED
    assert config.workspace_config.default_requirement_proof_availability == ProofAvailability.PROVED
    assert config.workspace_config.requirement_provider_completion_mode_by_proof_availability[ProofAvailability.DECLARED] == (
        RepoCompletionMode.GRAPH_DECLARED
    )
    assert config.workspace_config.requirement_provider_completion_mode_by_proof_availability[ProofAvailability.PROVED] == (
        RepoCompletionMode.GRAPH_PROVED
    )


def test_operator_data_api_requires_loopback_admin_bind(tmp_path) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        LeanAppConfig(
            workspace_root=tmp_path / "workspace",
            admin_http_host="0.0.0.0",
            operator_data_api_enabled=True,
        )

    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        admin_http_host="localhost",
        operator_data_api_enabled=True,
    )
    assert config.operator_data_api_enabled is True
