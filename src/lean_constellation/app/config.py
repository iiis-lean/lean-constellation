"""Application configuration loading for Lean Constellation runtime bootstrap."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tomllib
from typing import Any, Mapping

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel


DEFAULT_MCP_HTTP_HOST = "127.0.0.1"
DEFAULT_MCP_HTTP_PORT = 8765


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

    @field_validator("mcp_http_host")
    @classmethod
    def _non_empty_host(cls, value: str) -> str:
        stripped = str(value).strip()
        if not stripped:
            raise ValueError("mcp_http_host must be non-empty")
        return stripped

    @field_validator("mcp_http_port")
    @classmethod
    def _valid_port(cls, value: int) -> int:
        if value < 0 or value > 65535:
            raise ValueError("mcp_http_port must be between 0 and 65535")
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
        "max_concurrent_flow_advances": "LEAN_CONSTELLATION_MAX_CONCURRENT_FLOW_ADVANCES",
        "max_concurrent_steps": "LEAN_CONSTELLATION_MAX_CONCURRENT_STEPS",
    }
    for field, env_key in aliases.items():
        value = env.get(env_key)
        if value is not None and str(value).strip():
            data[field] = value
