"""Application configuration loading for Lean Constellation runtime bootstrap."""

from __future__ import annotations

import json
from ipaddress import ip_address
import os
from pathlib import Path
import tomllib
from typing import Any, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.repo import WorkspaceConfig
from lean_constellation.agents.models import AgentHomeType


DEFAULT_MCP_HTTP_HOST = "127.0.0.1"
DEFAULT_MCP_HTTP_PORT = 8765
DEFAULT_ADMIN_HTTP_HOST = "127.0.0.1"
DEFAULT_ADMIN_HTTP_PORT = 8766


class LeanAppConfigView(StrictModel):
    workspace_root: str
    runtime_root: str
    default_agent_provider_type: AgentHomeType
    codex_config_home: str | None = None
    codex_base_config_configured: bool = False
    codex_auth_configured: bool = False
    shared_elan_home: str | None = None
    max_concurrent_flow_advances: int
    max_concurrent_steps: int
    mcp_http_host: str
    mcp_http_port: int
    mcp_http_base_url: str
    production_mcp_http_base_url: str
    admin_http_host: str
    admin_http_port: int
    admin_http_base_url: str
    server_start_paused: bool
    materialize_agent_homes: bool
    scheduler_enabled: bool
    test_control_enabled: bool
    operator_data_api_enabled: bool
    scheduler_tick_interval_s: float
    scheduler_idle_interval_s: float
    scheduler_error_interval_s: float
    toolkit: "LeanToolkitAppConfig"
    automatic_checkpoints: "AutomaticCheckpointAppConfig"
    agent_trace_reports: "AgentTraceReportAppConfig"
    agent_home_overrides: dict[str, "AgentHomeOverrideAppConfig"] = Field(default_factory=dict)
    native_lake_project: NativeLakeProjectConfig
    workspace_config: WorkspaceConfig
    summary: str


class LeanToolkitAppConfig(StrictModel):
    mode: Literal["disabled", "external", "managed"] = "external"
    base_url: str | None = None
    api_prefix: str = "/api/v1"
    auth_token: str | None = None
    timeout_seconds: int = 120
    enabled_groups: list[str] = Field(default_factory=list)
    response_excerpt_chars: int = 12000
    host: str = "127.0.0.1"
    port: int = 8279
    config_path: Path | None = None
    project_root: Path | None = None
    python_executable: Path | None = None
    module: str = "lean_mcp_toolkit.app.cli"
    startup_timeout_s: float = 60.0
    shutdown_timeout_s: float = 10.0
    health_interval_s: float = 0.5
    required_tools: list[str] = Field(default_factory=list)
    warmup_tools: list[str] = Field(default_factory=list)
    strict_startup: bool = True

    @field_validator("config_path", "project_root", "python_executable", mode="before")
    @classmethod
    def _coerce_path(cls, value: Any) -> Path | None:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value)).expanduser()

    @field_validator("enabled_groups", "required_tools", "warmup_tools", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return list(value)

    @field_validator("host", "api_prefix", "module")
    @classmethod
    def _non_empty_string(cls, value: str) -> str:
        stripped = str(value).strip()
        if not stripped:
            raise ValueError("toolkit string settings must be non-empty")
        return stripped

    @field_validator("port")
    @classmethod
    def _valid_port(cls, value: int) -> int:
        if value < 0 or value > 65535:
            raise ValueError("toolkit port must be between 0 and 65535")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("toolkit timeout_seconds must be > 0")
        return value

    @field_validator("startup_timeout_s", "shutdown_timeout_s", "health_interval_s")
    @classmethod
    def _positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("toolkit timing settings must be > 0")
        return value

    def effective_base_url(self) -> str | None:
        if self.base_url is not None and self.base_url.strip():
            return self.base_url.rstrip("/")
        if self.mode == "managed":
            return f"http://{self.host}:{self.port}".rstrip("/")
        return None


class AutomaticCheckpointAppConfig(StrictModel):
    repo_flow_boundaries_enabled: bool = True
    content_task_progress_enabled: bool = False


class AgentTraceReportAppConfig(StrictModel):
    persistence: Literal["disabled", "latest_only", "latest_and_turns"] = "latest_only"
    include_in_snapshots: bool = False


class AgentHomeOverrideAppConfig(StrictModel):
    provider_type: AgentHomeType | None = None
    base_config_path: Path | None = None
    config_overrides: dict[str, Any] = Field(default_factory=dict)
    api_provider: str | None = None
    api_mode: str | None = None
    model: str | None = None
    model_version: str | None = None
    model_reasoning_effort: str | None = None
    context_window_tokens: int | None = None
    effective_context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    required_env: list[str] = Field(default_factory=list)
    auth_refs: list[str] = Field(default_factory=list)
    provider_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "api_provider",
        "api_mode",
        "model",
        "model_version",
        "model_reasoning_effort",
    )
    @classmethod
    def _optional_non_empty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Agent home model overrides must be non-empty")
        return stripped

    @field_validator("base_config_path", mode="before")
    @classmethod
    def _coerce_base_config_path(cls, value: Any) -> Path | None:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value)).expanduser()

    @field_validator("context_window_tokens", "effective_context_window_tokens", "max_output_tokens")
    @classmethod
    def _positive_optional_int(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("Agent home context limits must be positive")
        return value

    @field_validator("required_env", "auth_refs", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = [value] if isinstance(value, str) else list(value)
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    @field_validator("provider_options")
    @classmethod
    def _reject_inline_provider_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_provider_options_secrets(value)
        return value

    @field_validator("config_overrides")
    @classmethod
    def _reject_inline_config_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        _validate_provider_options_secrets(value, path="config_overrides")
        return value


class LeanAppConfig(StrictModel):
    workspace_root: Path
    default_agent_provider_type: AgentHomeType = "codex"
    runtime_root: Path | None = Field(
        default=None,
        description="Debug/local single-runtime root. Production serve uses repo-local <repo>/.agent_runtime roots.",
    )
    codex_config_home: Path | None = None
    codex_base_config_path: Path | None = None
    codex_auth_json_path: Path | None = None
    shared_elan_home: Path | None = None
    max_concurrent_flow_advances: int = 1
    max_concurrent_steps: int = 1
    mcp_http_host: str = DEFAULT_MCP_HTTP_HOST
    mcp_http_port: int = DEFAULT_MCP_HTTP_PORT
    mcp_http_base_url: str | None = None
    admin_http_host: str = DEFAULT_ADMIN_HTTP_HOST
    admin_http_port: int = DEFAULT_ADMIN_HTTP_PORT
    admin_http_base_url: str | None = None
    server_start_paused: bool = True
    materialize_agent_homes: bool = True
    scheduler_enabled: bool = True
    test_control_enabled: bool = False
    operator_data_api_enabled: bool = False
    scheduler_tick_interval_s: float = 0.25
    scheduler_idle_interval_s: float = 0.5
    scheduler_error_interval_s: float = 2.0
    toolkit: LeanToolkitAppConfig = Field(default_factory=LeanToolkitAppConfig)
    automatic_checkpoints: AutomaticCheckpointAppConfig = Field(default_factory=AutomaticCheckpointAppConfig)
    agent_trace_reports: AgentTraceReportAppConfig = Field(default_factory=AgentTraceReportAppConfig)
    agent_home_overrides: dict[str, AgentHomeOverrideAppConfig] = Field(default_factory=dict)
    native_lake_project: NativeLakeProjectConfig = Field(default_factory=NativeLakeProjectConfig)
    workspace_config: WorkspaceConfig = Field(default_factory=WorkspaceConfig)

    @field_validator(
        "workspace_root",
        "runtime_root",
        "codex_config_home",
        "codex_base_config_path",
        "codex_auth_json_path",
        "shared_elan_home",
        mode="before",
    )
    @classmethod
    def _coerce_path(cls, value: Any) -> Path | None:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value)).expanduser()

    @field_validator("max_concurrent_flow_advances", "max_concurrent_steps")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value < 1:
            raise ValueError("concurrency values must be >= 1")
        return value

    @field_validator("mcp_http_host", "admin_http_host")
    @classmethod
    def _non_empty_host(cls, value: str) -> str:
        stripped = str(value).strip()
        if not stripped:
            raise ValueError("HTTP host values must be non-empty")
        return stripped

    @field_validator("mcp_http_port", "admin_http_port")
    @classmethod
    def _valid_port(cls, value: int) -> int:
        if value < 0 or value > 65535:
            raise ValueError("HTTP port values must be between 0 and 65535")
        return value

    @field_validator("scheduler_tick_interval_s", "scheduler_idle_interval_s", "scheduler_error_interval_s")
    @classmethod
    def _positive_float(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("scheduler intervals must be > 0")
        return value

    @model_validator(mode="after")
    def _derive_runtime_and_codex_paths(self) -> "LeanAppConfig":
        if self.codex_config_home is not None:
            if self.codex_base_config_path is None:
                self.codex_base_config_path = self.codex_config_home / "config.toml"
            if self.codex_auth_json_path is None:
                self.codex_auth_json_path = self.codex_config_home / "auth.json"
        if self.operator_data_api_enabled and not _is_loopback_host(self.admin_http_host):
            raise ValueError("operator_data_api_enabled requires a loopback admin_http_host")
        if self.agent_home_overrides:
            from lean_constellation.agents import build_agent_type_specs

            known_agent_types = {spec.agent_type for spec in build_agent_type_specs()}
            unknown = sorted(set(self.agent_home_overrides) - known_agent_types)
            if unknown:
                raise ValueError(f"unknown agent_home_overrides AgentType(s): {', '.join(unknown)}")
        return self

    def mcp_http_effective_base_url(self) -> str:
        if self.mcp_http_base_url is not None and self.mcp_http_base_url.strip():
            return self.mcp_http_base_url.rstrip("/")
        return f"http://{self.mcp_http_host}:{self.mcp_http_port}".rstrip("/")

    def admin_http_effective_base_url(self) -> str:
        if self.admin_http_base_url is not None and self.admin_http_base_url.strip():
            return self.admin_http_base_url.rstrip("/")
        return f"http://{self.admin_http_host}:{self.admin_http_port}".rstrip("/")

    def production_mcp_http_effective_base_url(self) -> str:
        if self.mcp_http_base_url is not None and self.mcp_http_base_url.strip():
            return self.mcp_http_base_url.rstrip("/")
        return self.admin_http_effective_base_url()

    def redacted_view(self) -> LeanAppConfigView:
        runtime_root = self.runtime_root or (self.workspace_root / ".agent_runtime")
        return LeanAppConfigView(
            workspace_root=str(self.workspace_root),
            runtime_root=str(runtime_root),
            default_agent_provider_type=self.default_agent_provider_type,
            codex_config_home=str(self.codex_config_home) if self.codex_config_home else None,
            codex_base_config_configured=self.codex_base_config_path is not None,
            codex_auth_configured=self.codex_auth_json_path is not None,
            shared_elan_home=str(self.shared_elan_home) if self.shared_elan_home else None,
            max_concurrent_flow_advances=self.max_concurrent_flow_advances,
            max_concurrent_steps=self.max_concurrent_steps,
            mcp_http_host=self.mcp_http_host,
            mcp_http_port=self.mcp_http_port,
            mcp_http_base_url=self.mcp_http_effective_base_url(),
            production_mcp_http_base_url=self.production_mcp_http_effective_base_url(),
            admin_http_host=self.admin_http_host,
            admin_http_port=self.admin_http_port,
            admin_http_base_url=self.admin_http_effective_base_url(),
            server_start_paused=self.server_start_paused,
            materialize_agent_homes=self.materialize_agent_homes,
            scheduler_enabled=self.scheduler_enabled,
            test_control_enabled=self.test_control_enabled,
            operator_data_api_enabled=self.operator_data_api_enabled,
            scheduler_tick_interval_s=self.scheduler_tick_interval_s,
            scheduler_idle_interval_s=self.scheduler_idle_interval_s,
            scheduler_error_interval_s=self.scheduler_error_interval_s,
            toolkit=self.toolkit,
            automatic_checkpoints=self.automatic_checkpoints,
            agent_trace_reports=self.agent_trace_reports,
            agent_home_overrides=self.agent_home_overrides,
            native_lake_project=self.native_lake_project,
            workspace_config=self.workspace_config,
            summary="Loaded Lean Constellation app config with secret-bearing file contents redacted.",
        )


def load_app_config(
    path: Path | str | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> LeanAppConfig:
    """Load app config from JSON/TOML plus environment overrides."""

    data: dict[str, Any] = {}
    if path is not None:
        config_path = Path(path).expanduser()
        if config_path.suffix.lower() == ".json":
            data.update(json.loads(config_path.read_text(encoding="utf-8")))
        else:
            data.update(tomllib.loads(config_path.read_text(encoding="utf-8")))
    source_env = os.environ if env is None else env
    _apply_env(data, source_env)
    return LeanAppConfig.model_validate(data)


def _apply_env(data: dict[str, Any], env: Mapping[str, str]) -> None:
    aliases = {
        "workspace_root": "LEAN_CONSTELLATION_WORKSPACE_ROOT",
        "runtime_root": "LEAN_CONSTELLATION_RUNTIME_ROOT",
        "codex_config_home": "LEAN_CONSTELLATION_CODEX_CONFIG_HOME",
        "default_agent_provider_type": "LEAN_CONSTELLATION_DEFAULT_AGENT_PROVIDER_TYPE",
        "codex_base_config_path": "LEAN_CONSTELLATION_CODEX_BASE_CONFIG_PATH",
        "codex_auth_json_path": "LEAN_CONSTELLATION_CODEX_AUTH_JSON_PATH",
        "shared_elan_home": "LEAN_CONSTELLATION_SHARED_ELAN_HOME",
        "mcp_http_host": "LEAN_CONSTELLATION_MCP_HTTP_HOST",
        "mcp_http_port": "LEAN_CONSTELLATION_MCP_HTTP_PORT",
        "mcp_http_base_url": "LEAN_CONSTELLATION_MCP_HTTP_BASE_URL",
        "admin_http_host": "LEAN_CONSTELLATION_ADMIN_HTTP_HOST",
        "admin_http_port": "LEAN_CONSTELLATION_ADMIN_HTTP_PORT",
        "admin_http_base_url": "LEAN_CONSTELLATION_ADMIN_HTTP_BASE_URL",
        "server_start_paused": "LEAN_CONSTELLATION_SERVER_START_PAUSED",
        "materialize_agent_homes": "LEAN_CONSTELLATION_MATERIALIZE_AGENT_HOMES",
        "scheduler_enabled": "LEAN_CONSTELLATION_SCHEDULER_ENABLED",
        "test_control_enabled": "LEAN_CONSTELLATION_TEST_CONTROL_ENABLED",
        "operator_data_api_enabled": "LEAN_CONSTELLATION_OPERATOR_DATA_API_ENABLED",
        "scheduler_tick_interval_s": "LEAN_CONSTELLATION_SCHEDULER_TICK_INTERVAL_S",
        "scheduler_idle_interval_s": "LEAN_CONSTELLATION_SCHEDULER_IDLE_INTERVAL_S",
        "scheduler_error_interval_s": "LEAN_CONSTELLATION_SCHEDULER_ERROR_INTERVAL_S",
        "max_concurrent_flow_advances": "LEAN_CONSTELLATION_MAX_CONCURRENT_FLOW_ADVANCES",
        "max_concurrent_steps": "LEAN_CONSTELLATION_MAX_CONCURRENT_STEPS",
    }
    for field, env_key in aliases.items():
        value = env.get(env_key)
        if value is not None and str(value).strip():
            data[field] = value
    _apply_toolkit_env(data, env)
    _apply_checkpoint_env(data, env)
    _apply_agent_trace_report_env(data, env)
    _apply_native_lake_env(data, env)
    _apply_workspace_config_env(data, env)


def _is_loopback_host(host: str) -> bool:
    value = str(host).strip().lower()
    if value == "localhost":
        return True
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def _validate_provider_options_secrets(value: Any, *, path: str = "provider_options") -> None:
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            sensitive = any(
                marker in normalized
                for marker in ("api_key", "apikey", "token", "secret", "authorization", "password")
            )
            is_reference = (
                normalized.endswith(("_env", "_env_var", "_path"))
                or (
                    isinstance(item, str)
                    and (item.startswith("$") or item.startswith("{env:") or item.startswith("env:"))
                )
            )
            if sensitive and not is_reference:
                raise ValueError(f"{path}.{key} must use an environment or file reference")
            _validate_provider_options_secrets(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_provider_options_secrets(item, path=f"{path}[{index}]")


def _apply_toolkit_env(data: dict[str, Any], env: Mapping[str, str]) -> None:
    aliases = {
        "mode": "LEAN_CONSTELLATION_TOOLKIT_MODE",
        "base_url": "LEAN_CONSTELLATION_TOOLKIT_BASE_URL",
        "api_prefix": "LEAN_CONSTELLATION_TOOLKIT_API_PREFIX",
        "auth_token": "LEAN_CONSTELLATION_TOOLKIT_AUTH_TOKEN",
        "timeout_seconds": "LEAN_CONSTELLATION_TOOLKIT_TIMEOUT",
        "enabled_groups": "LEAN_CONSTELLATION_TOOLKIT_ENABLED_GROUPS",
        "response_excerpt_chars": "LEAN_CONSTELLATION_TOOLKIT_RESPONSE_EXCERPT_CHARS",
        "host": "LEAN_CONSTELLATION_TOOLKIT_HOST",
        "port": "LEAN_CONSTELLATION_TOOLKIT_PORT",
        "config_path": "LEAN_CONSTELLATION_TOOLKIT_CONFIG_PATH",
        "project_root": "LEAN_CONSTELLATION_TOOLKIT_PROJECT_ROOT",
        "python_executable": "LEAN_CONSTELLATION_TOOLKIT_PYTHON",
        "module": "LEAN_CONSTELLATION_TOOLKIT_MODULE",
        "startup_timeout_s": "LEAN_CONSTELLATION_TOOLKIT_STARTUP_TIMEOUT",
        "shutdown_timeout_s": "LEAN_CONSTELLATION_TOOLKIT_SHUTDOWN_TIMEOUT",
        "health_interval_s": "LEAN_CONSTELLATION_TOOLKIT_HEALTH_INTERVAL",
        "required_tools": "LEAN_CONSTELLATION_TOOLKIT_REQUIRED_TOOLS",
        "warmup_tools": "LEAN_CONSTELLATION_TOOLKIT_WARMUP_TOOLS",
        "strict_startup": "LEAN_CONSTELLATION_TOOLKIT_STRICT_STARTUP",
    }
    toolkit: dict[str, Any] = dict(data.get("toolkit") or {})
    for field, env_key in aliases.items():
        value = env.get(env_key)
        if value is not None and str(value).strip():
            toolkit[field] = value
    if toolkit:
        data["toolkit"] = toolkit


def _apply_checkpoint_env(data: dict[str, Any], env: Mapping[str, str]) -> None:
    aliases = {
        "repo_flow_boundaries_enabled": "LEAN_CONSTELLATION_CHECKPOINT_REPO_FLOW_BOUNDARIES_ENABLED",
        "content_task_progress_enabled": "LEAN_CONSTELLATION_CHECKPOINT_CONTENT_TASK_PROGRESS_ENABLED",
    }
    section: dict[str, Any] = dict(data.get("automatic_checkpoints") or {})
    for field, env_key in aliases.items():
        value = env.get(env_key)
        if value is not None and str(value).strip():
            section[field] = value
    if section:
        data["automatic_checkpoints"] = section


def _apply_agent_trace_report_env(data: dict[str, Any], env: Mapping[str, str]) -> None:
    aliases = {
        "persistence": "LEAN_CONSTELLATION_AGENT_TRACE_REPORT_PERSISTENCE",
        "include_in_snapshots": "LEAN_CONSTELLATION_AGENT_TRACE_REPORT_INCLUDE_IN_SNAPSHOTS",
    }
    section: dict[str, Any] = dict(data.get("agent_trace_reports") or {})
    for field, env_key in aliases.items():
        value = env.get(env_key)
        if value is not None and str(value).strip():
            section[field] = value
    if section:
        data["agent_trace_reports"] = section


def _apply_native_lake_env(data: dict[str, Any], env: Mapping[str, str]) -> None:
    native_aliases = {
        "lean_version": "LEAN_CONSTELLATION_LEAN_VERSION",
        "lean_toolchain": "LEAN_CONSTELLATION_LEAN_TOOLCHAIN",
        "mathlib_enabled": "LEAN_CONSTELLATION_MATHLIB_ENABLED",
        "mathlib_scope": "LEAN_CONSTELLATION_MATHLIB_SCOPE",
        "mathlib_rev": "LEAN_CONSTELLATION_MATHLIB_REV",
    }
    cache_aliases = {
        "cache_project_root": "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PROJECT_ROOT",
        "packages_root": "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PACKAGES_ROOT",
        "manifest_path": "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_MANIFEST_PATH",
        "package_names": "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PACKAGE_NAMES",
        "require_all_packages": "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_REQUIRE_ALL_PACKAGES",
    }
    native: dict[str, Any] = dict(data.get("native_lake_project") or {})
    for field, env_key in native_aliases.items():
        value = env.get(env_key)
        if value is not None and str(value).strip():
            native[field] = value
    cache: dict[str, Any] = dict(native.get("local_package_cache") or {})
    for field, env_key in cache_aliases.items():
        value = env.get(env_key)
        if value is not None and str(value).strip():
            cache[field] = value
    if cache:
        native["local_package_cache"] = cache
    if native:
        data["native_lake_project"] = native


def _apply_workspace_config_env(data: dict[str, Any], env: Mapping[str, str]) -> None:
    aliases = {
        "default_direct_repo_completion_mode": "LEAN_CONSTELLATION_DEFAULT_DIRECT_REPO_COMPLETION_MODE",
        "default_requirement_proof_availability": "LEAN_CONSTELLATION_DEFAULT_REQUIREMENT_PROOF_AVAILABILITY",
    }
    workspace_config: dict[str, Any] = dict(data.get("workspace_config") or {})
    for field, env_key in aliases.items():
        value = env.get(env_key)
        if value is not None and str(value).strip():
            workspace_config[field] = value
    declared_provider_mode = env.get(
        "LEAN_CONSTELLATION_REQUIREMENT_DECLARED_PROVIDER_COMPLETION_MODE"
    )
    proved_provider_mode = env.get(
        "LEAN_CONSTELLATION_REQUIREMENT_PROVED_PROVIDER_COMPLETION_MODE"
    )
    if declared_provider_mode is not None or proved_provider_mode is not None:
        mapping = dict(
            workspace_config.get(
                "requirement_provider_completion_mode_by_proof_availability"
            )
            or {}
        )
        if declared_provider_mode is not None and declared_provider_mode.strip():
            mapping["declared"] = declared_provider_mode
        if proved_provider_mode is not None and proved_provider_mode.strip():
            mapping["proved"] = proved_provider_mode
        workspace_config[
            "requirement_provider_completion_mode_by_proof_availability"
        ] = mapping
    if workspace_config:
        data["workspace_config"] = workspace_config
