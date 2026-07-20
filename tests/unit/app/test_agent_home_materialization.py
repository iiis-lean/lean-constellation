from __future__ import annotations

import json
from pathlib import Path
import tomllib

from lean_constellation.agents import build_agent_type_specs, derive_agent_type_spec
from lean_constellation.app import create_app_runtime_services, materialize_agent_home


def test_agent_home_materialization_writes_instruction_skills_and_mcp_config(tmp_path: Path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")

    view = materialize_agent_home(
        runtime,
        "SourceCorpusPrepareAgent",
        mcp_http_base_url="http://127.0.0.1:8765",
    )

    assert view.ok and view.value is not None
    home_root = Path(view.value.home_root)
    assert Path(view.value.instruction_path).exists()
    assert view.value.mcp_server_names == [
        "lc_app",
        "lc_submit",
    ]
    assert (home_root / ".agents" / "skills" / "source-material-acquisition" / "SKILL.md").exists()
    assert view.value.codex_config_path is not None
    config_text = Path(view.value.codex_config_path).read_text(encoding="utf-8")
    assert "http://127.0.0.1:8765/mcp/views/source_corpus_prepare" in config_text
    assert "http://127.0.0.1:8765/mcp/views/source_corpus_prepare_submit" in config_text
    assert "env_http_headers" in config_text
    assert ".env]" not in config_text
    assert "x-ark-flow-id" in config_text
    assert "x-ark-expected-tool-view" in config_text
    assert "LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW" in config_text
    assert "LEAN_CONSTELLATION_EXPECTED_VIEW_KEY" not in config_text
    assert "LEAN_CONSTELLATION_MCP_VIEW_KEY" not in config_text
    config = tomllib.loads(config_text)
    assert config["features"] == {"apps": False, "plugins": False, "tool_suggest": False}
    manifest = json.loads((home_root / ".agents" / "lean_constellation_home.json").read_text(encoding="utf-8"))
    assert manifest["tool_view_config"]["application_view_key"] == "source_corpus_prepare"
    assert manifest["tool_view_config"]["submit_view_key"] == "source_corpus_prepare_submit"
    assert manifest["mcp_transport"] == "http"
    assert len(manifest["mcp_server_specs"]) == 2


def test_agent_home_materialization_supports_base_config_and_auth_reference(tmp_path: Path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    base_config = tmp_path / "base_config.toml"
    auth = tmp_path / "auth.json"
    base_config.write_text(
        'model = "test-model"\n\n[features]\napps = true\nplugins = true\n',
        encoding="utf-8",
    )
    auth.write_text('{"token": "secret-token"}\n', encoding="utf-8")

    view = materialize_agent_home(
        runtime,
        "RepoFormatDiscoveryAgent",
        mcp_http_base_url="http://127.0.0.1:8765",
        base_config_path=base_config,
        auth_json_path=auth,
    )

    assert view.ok and view.value is not None
    home_root = Path(view.value.home_root)
    assert (home_root / ".codex" / "auth.json").read_text(encoding="utf-8") == '{"token": "secret-token"}\n'
    assert 'model = "test-model"' in (home_root / ".codex" / "config.toml").read_text(encoding="utf-8")
    config = tomllib.loads((home_root / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert config["features"] == {"apps": False, "plugins": False, "tool_suggest": False}


def test_agent_home_materialization_writes_stdio_mcp_view_servers(tmp_path: Path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    config_path = tmp_path / "app.toml"
    config_path.write_text(f'workspace_root = "{tmp_path}"\n', encoding="utf-8")

    view = materialize_agent_home(
        runtime,
        "RepoFormatDiscoveryAgent",
        mcp_server_command="python",
        mcp_server_args=["-m", "lean_constellation.mcp.stdio", "--config", str(config_path)],
    )

    assert view.ok and view.value is not None
    assert view.value.mcp_server_names == [
        "lc_app",
        "lc_submit",
    ]
    config_text = Path(view.value.codex_config_path or "").read_text(encoding="utf-8")
    assert "lean_constellation.mcp.stdio" in config_text
    assert "--view-key" in config_text
    assert "repo_format_discovery_submit" in config_text
    assert "env_vars" in config_text
    assert "ARK_STEP_ID" in config_text


def test_agent_home_materialization_supports_derived_agent_type_specs(tmp_path: Path) -> None:
    controlled = derive_agent_type_spec(
        base_agent_type="RepoFormatDiscoveryAgent",
        agent_type="RepoFormatDiscoveryControlledTestAgent",
    )
    specs = build_agent_type_specs(extra_specs=[controlled])
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime", agent_type_specs=specs)

    view = materialize_agent_home(
        runtime,
        "RepoFormatDiscoveryControlledTestAgent",
        mcp_http_base_url="http://127.0.0.1:8765",
        agent_type_specs=specs,
    )

    assert view.ok and view.value is not None
    home_root = Path(view.value.home_root)
    manifest = json.loads((home_root / ".agents" / "lean_constellation_home.json").read_text(encoding="utf-8"))
    assert manifest["agent_type"] == "RepoFormatDiscoveryControlledTestAgent"
    assert manifest["fixed_env"]["LEAN_CONSTELLATION_AGENT_TYPE"] == "RepoFormatDiscoveryControlledTestAgent"
    assert manifest["tool_view_config"]["submit_view_key"] == "repo_format_discovery_submit"
