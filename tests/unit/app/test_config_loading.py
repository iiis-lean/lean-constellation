from __future__ import annotations

import json

from lean_constellation.app import load_app_config


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
                'mcp_server_url = "http://127.0.0.1:8765/mcp"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    config = load_app_config(path)
    view = config.redacted_view()
    dumped = view.model_dump_json()

    assert config.runtime_root == tmp_path / "workspace" / ".agent_runtime"
    assert config.codex_base_config_path == config_home / "config.toml"
    assert config.codex_auth_json_path == config_home / "auth.json"
    assert view.max_concurrent_flow_advances == 2
    assert view.max_concurrent_steps == 3
    assert "secret-token" not in dumped


def test_load_app_config_env_overrides_json(tmp_path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"workspace_root": str(tmp_path / "from_file"), "max_concurrent_steps": 1}), encoding="utf-8")

    config = load_app_config(
        path,
        env={
            "LEAN_CONSTELLATION_WORKSPACE_ROOT": str(tmp_path / "from_env"),
            "LEAN_CONSTELLATION_MAX_CONCURRENT_STEPS": "4",
        },
    )

    assert config.workspace_root == tmp_path / "from_env"
    assert config.max_concurrent_steps == 4
