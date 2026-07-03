"""Application configuration loading for Lean Constellation runtime bootstrap."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.lake_project import NativeLakeProjectConfig


DEFAULT_MCP_HTTP_HOST = "127.0.0.1"
DEFAULT_MCP_HTTP_PORT = 8765
DEFAULT_ADMIN_HTTP_HOST = "127.0.0.1"
DEFAULT_ADMIN_HTTP_PORT = 8766


class LeanAppConfigView(StrictModel):
    workspace_root: str
    runtime_root: str
    codex_config_home: str | None = None
    codex_base_config_configured: bool = False
    codex_auth_configured: bool = False
    max_concurrent_flow_advances: int
    max_concurrent_steps: int
    mcp_server_url: str | None = None
    mcp_http_host: str
    mcp_http_port: int
    mcp_http_base_url: str
    admin_http_host: str
    admin_http_port: int
    admin_http_base_url: str
    server_start_paused: bool
    scheduler_enabled: bool
    scheduler_tick_interval_s: float
    scheduler_idle_interval_s: float
    scheduler_error_interval_s: float
    native_lake_project: NativeLakeProjectConfig
    summary: str


class LeanAppConfig(StrictModel):
    workspace_root: Path
    runtime_root: Path | None = None
    codex_config_home: Path | None = None
    codex_base_config_path: Path | None = None
    codex_auth_json_path: Path | None = None
    max_concurrent_flow_advances: int = 1
    max_concurrent_steps: int = 1
    mcp_server_url: str | None = None
    mcp_http_host: str = DEFAULT_MCP_HTTP_HOST
    mcp_http_port: int = DEFAULT_MCP_HTTP_PORT
    mcp_http_base_url: str | None = None
    admin_http_host: str = DEFAULT_ADMIN_HTTP_HOST
    admin_http_port: int = DEFAULT_ADMIN_HTTP_PORT
    admin_http_base_url: str | None = None
    server_start_paused: bool = True
    scheduler_enabled: bool = True
    scheduler_tick_interval_s: float = 0.25
    scheduler_idle_interval_s: float = 0.5
    scheduler_error_interval_s: float = 2.0
    native_lake_project: NativeLakeProjectConfig = Field(default_factory=NativeLakeProjectConfig)

    @field_validator(
        "workspace_root",
        "runtime_root",
        "codex_config_home",
        "codex_base_config_path",
        "codex_auth_json_path",
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
        if self.runtime_root is None:
            self.runtime_root = self.workspace_root / ".agent_runtime"
        if self.codex_config_home is not None:
            if self.codex_base_config_path is None:
                self.codex_base_config_path = self.codex_config_home / "config.toml"
            if self.codex_auth_json_path is None:
                self.codex_auth_json_path = self.codex_config_home / "auth.json"
        return self

    def mcp_http_effective_base_url(self) -> str:
        if self.mcp_http_base_url is not None and self.mcp_http_base_url.strip():
            return self.mcp_http_base_url.rstrip("/")
        return f"http://{self.mcp_http_host}:{self.mcp_http_port}".rstrip("/")

    def admin_http_effective_base_url(self) -> str:
        if self.admin_http_base_url is not None and self.admin_http_base_url.strip():
            return self.admin_http_base_url.rstrip("/")
        return f"http://{self.admin_http_host}:{self.admin_http_port}".rstrip("/")

    def redacted_view(self) -> LeanAppConfigView:
        runtime_root = self.runtime_root or (self.workspace_root / ".agent_runtime")
        return LeanAppConfigView(
            workspace_root=str(self.workspace_root),
            runtime_root=str(runtime_root),
            codex_config_home=str(self.codex_config_home) if self.codex_config_home else None,
            codex_base_config_configured=self.codex_base_config_path is not None,
            codex_auth_configured=self.codex_auth_json_path is not None,
            max_concurrent_flow_advances=self.max_concurrent_flow_advances,
            max_concurrent_steps=self.max_concurrent_steps,
            mcp_server_url=self.mcp_server_url,
            mcp_http_host=self.mcp_http_host,
            mcp_http_port=self.mcp_http_port,
            mcp_http_base_url=self.mcp_http_effective_base_url(),
            admin_http_host=self.admin_http_host,
            admin_http_port=self.admin_http_port,
            admin_http_base_url=self.admin_http_effective_base_url(),
            server_start_paused=self.server_start_paused,
            scheduler_enabled=self.scheduler_enabled,
            scheduler_tick_interval_s=self.scheduler_tick_interval_s,
            scheduler_idle_interval_s=self.scheduler_idle_interval_s,
            scheduler_error_interval_s=self.scheduler_error_interval_s,
            native_lake_project=self.native_lake_project,
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
        "codex_base_config_path": "LEAN_CONSTELLATION_CODEX_BASE_CONFIG_PATH",
        "codex_auth_json_path": "LEAN_CONSTELLATION_CODEX_AUTH_JSON_PATH",
        "mcp_server_url": "LEAN_CONSTELLATION_MCP_SERVER_URL",
        "mcp_http_host": "LEAN_CONSTELLATION_MCP_HTTP_HOST",
        "mcp_http_port": "LEAN_CONSTELLATION_MCP_HTTP_PORT",
        "mcp_http_base_url": "LEAN_CONSTELLATION_MCP_HTTP_BASE_URL",
        "admin_http_host": "LEAN_CONSTELLATION_ADMIN_HTTP_HOST",
        "admin_http_port": "LEAN_CONSTELLATION_ADMIN_HTTP_PORT",
        "admin_http_base_url": "LEAN_CONSTELLATION_ADMIN_HTTP_BASE_URL",
        "server_start_paused": "LEAN_CONSTELLATION_SERVER_START_PAUSED",
        "scheduler_enabled": "LEAN_CONSTELLATION_SCHEDULER_ENABLED",
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
    _apply_native_lake_env(data, env)


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
