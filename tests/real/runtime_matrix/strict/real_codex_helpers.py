"""Helpers for strict Runtime Matrix tests that run the real Codex SDK."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
from typing import Iterable

import pytest

from lean_constellation.agents import build_agent_type_specs, build_controlled_test_agent_type_specs
from lean_constellation.app import materialize_agent_home
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.transport import ensure_runtime_mcp_http_server, requested_mcp_transport_mode


def require_real_codex() -> Path:
    if os.environ.get("LEAN_CONSTELLATION_RUN_REAL_CODEX") != "1":
        pytest.skip("Set LEAN_CONSTELLATION_RUN_REAL_CODEX=1 to run strict real Codex Runtime Matrix tests.")
    if importlib.util.find_spec("openai_codex") is None:
        pytest.skip("openai_codex Python SDK is required for strict real Codex Runtime Matrix tests.")
    if shutil.which("codex") is None:
        pytest.skip("codex CLI is required for strict real Codex Runtime Matrix tests.")
    config_home = os.environ.get("LEAN_CONSTELLATION_CODEX_CONFIG_HOME")
    if not config_home:
        pytest.skip("Set LEAN_CONSTELLATION_CODEX_CONFIG_HOME to a Codex config directory.")
    home = Path(config_home).expanduser()
    if not (home / "config.toml").exists() or not (home / "auth.json").exists():
        pytest.skip("LEAN_CONSTELLATION_CODEX_CONFIG_HOME must contain config.toml and auth.json.")
    return home


def write_noninteractive_codex_base_config(config_home: Path, tmp_path: Path) -> Path:
    source = config_home / "config.toml"
    target = tmp_path / "codex_noninteractive_config.toml"
    blocked_prefixes = ("approval_policy", "approvals_reviewer", "notify")
    lines: list[str] = []
    inserted_approval_policy = False
    for line in source.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in blocked_prefixes):
            continue
        if stripped.startswith("request_rule"):
            lines.append("request_rule = false")
            continue
        if stripped.startswith("[") and not inserted_approval_policy:
            lines.append('approval_policy = "never"')
            lines.append("")
            inserted_approval_policy = True
        if stripped.startswith("model_reasoning_effort"):
            lines.append('model_reasoning_effort = "low"')
            continue
        lines.append(line)
    if not inserted_approval_policy:
        lines.append("")
        lines.append('approval_policy = "never"')
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def strict_controlled_agent_specs(*base_agent_types: str):
    base_specs = build_agent_type_specs()
    controlled_specs = build_controlled_test_agent_type_specs(
        specs=base_specs,
        base_agent_types=base_agent_types or None,
    )
    return [*base_specs, *controlled_specs]


def materialize_strict_codex_home(
    ws: RuntimeMatrixWorkspace,
    *,
    agent_type: str,
    config_home: Path,
    base_config_path: Path,
    agent_type_specs: Iterable[object],
    transport: str | None = None,
) -> Path:
    transport = transport or requested_mcp_transport_mode()
    if transport not in {"http", "stdio"}:
        raise ValueError("materialize_strict_codex_home only supports a concrete 'http' or 'stdio' transport")
    http_server = ensure_runtime_mcp_http_server(ws) if transport == "http" else None
    app_config = ws.tmp_path / f"lean_constellation_{agent_type}.toml"
    config_lines = [
        f'workspace_root = "{ws.workspace_root}"',
        f'runtime_root = "{ws.runtime_root}"',
    ]
    if http_server is not None:
        config_lines.append(f'mcp_http_base_url = "{http_server.base_url}"')
    config_lines.extend(
        [
            "max_concurrent_flow_advances = 1",
            "max_concurrent_steps = 1",
        ]
    )
    app_config.write_text("\n".join(config_lines) + "\n", encoding="utf-8")
    mcp_kwargs = (
        {"mcp_http_base_url": http_server.base_url}
        if http_server is not None
        else {
            "mcp_server_command": sys.executable,
            "mcp_server_args": ["-m", "lean_constellation.mcp.stdio", "--config", str(app_config)],
            "mcp_server_env": _mcp_server_env(),
        }
    )
    materialized = materialize_agent_home(
        ws.runtime,
        agent_type,
        **mcp_kwargs,
        base_config_path=base_config_path,
        auth_json_path=config_home / "auth.json",
        agent_type_specs=list(agent_type_specs),
    )
    assert materialized.ok and materialized.value is not None, materialized.issues
    return Path(materialized.value.home_root)


def _mcp_pythonpath() -> str:
    repo_root = Path(__file__).resolve().parents[4]
    entries = [str(repo_root / "src")]
    if ark_src := os.environ.get("LEAN_CONSTELLATION_ARK_SRC"):
        entries.append(str(Path(ark_src).expanduser()))
    existing = os.environ.get("PYTHONPATH")
    if existing:
        entries.append(existing)
    return os.pathsep.join(entries)


def _mcp_server_env() -> dict[str, str]:
    env = {
        "PYTHONPATH": _mcp_pythonpath(),
        "LEAN_CONSTELLATION_TEST_CONTROL_ENABLED": "1",
    }
    if path := os.environ.get("PATH"):
        env["PATH"] = path
    elan_home = os.environ.get("ELAN_HOME")
    if elan_home:
        env["ELAN_HOME"] = elan_home
    for key in ("LEAN_SYSROOT", "LEAN_PATH", "LAKE_HOME", "MATHLIB_CACHE_DIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env
