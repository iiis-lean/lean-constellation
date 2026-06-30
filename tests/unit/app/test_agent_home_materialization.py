from __future__ import annotations

import json
from pathlib import Path

from lean_constellation.app import create_app_runtime_services, materialize_agent_home


def test_agent_home_materialization_writes_instruction_skills_and_mcp_config(tmp_path: Path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")

    view = materialize_agent_home(
        runtime,
        "SourceCorpusPrepareAgent",
        mcp_server_url="http://127.0.0.1:8765/mcp",
    )

    assert view.ok and view.value is not None
    home_root = Path(view.value.home_root)
    assert Path(view.value.instruction_path).exists()
    assert "lean-constellation-tools" in view.value.mcp_server_names
    assert (home_root / ".agents" / "skills" / "material-acquisition" / "SKILL.md").exists()
    assert view.value.codex_config_path is not None
    config_text = Path(view.value.codex_config_path).read_text(encoding="utf-8")
    assert "http://127.0.0.1:8765/mcp" in config_text
    manifest = json.loads((home_root / ".agents" / "lean_constellation_home.json").read_text(encoding="utf-8"))
    assert manifest["tool_view_config"]["application_view_key"] == "source_corpus_prepare"
    assert manifest["tool_view_config"]["submit_view_key"] == "source_corpus_prepare_submit"


def test_agent_home_materialization_supports_base_config_and_auth_reference(tmp_path: Path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    base_config = tmp_path / "base_config.toml"
    auth = tmp_path / "auth.json"
    base_config.write_text('model = "test-model"\n', encoding="utf-8")
    auth.write_text('{"token": "secret-token"}\n', encoding="utf-8")

    view = materialize_agent_home(
        runtime,
        "RepoFormatDiscoveryAgent",
        mcp_server_url="http://127.0.0.1:8765/mcp",
        base_config_path=base_config,
        auth_json_path=auth,
    )

    assert view.ok and view.value is not None
    home_root = Path(view.value.home_root)
    assert (home_root / ".codex" / "auth.json").read_text(encoding="utf-8") == '{"token": "secret-token"}\n'
    assert 'model = "test-model"' in (home_root / ".codex" / "config.toml").read_text(encoding="utf-8")


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
        "lean-constellation-tools-application",
        "lean-constellation-tools-submit",
    ]
    config_text = Path(view.value.codex_config_path or "").read_text(encoding="utf-8")
    assert "lean_constellation.mcp.stdio" in config_text
    assert "--view-key" in config_text
    assert "repo_format_discovery_submit" in config_text
    assert "env_vars" in config_text
    assert "ARK_STEP_ID" in config_text
