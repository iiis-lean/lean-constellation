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
                'mcp_http_host = "0.0.0.0"',
                "mcp_http_port = 9876",
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
    assert view.mcp_http_host == "0.0.0.0"
    assert view.mcp_http_port == 9876
    assert view.mcp_http_base_url == "http://0.0.0.0:9876"
    assert view.admin_http_base_url == "http://127.0.0.1:8766"
    assert view.server_start_paused is True
    assert view.scheduler_enabled is True
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
            "LEAN_CONSTELLATION_LEAN_VERSION": "4.29.0",
            "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PROJECT_ROOT": str(tmp_path / "template"),
            "LEAN_CONSTELLATION_LOCAL_LAKE_CACHE_PACKAGE_NAMES": "mathlib,aesop",
        },
    )

    assert config.workspace_root == tmp_path / "from_env"
    assert config.max_concurrent_steps == 4
    assert config.mcp_http_effective_base_url() == "http://127.0.0.1:9999/custom"
    assert config.admin_http_effective_base_url() == "http://127.0.0.1:9998"
    assert config.server_start_paused is False
    assert config.native_lake_project.lean_version == "4.29.0"
    assert config.native_lake_project.lean_toolchain == "leanprover/lean4:v4.29.0"
    assert config.native_lake_project.mathlib_rev == "v4.29.0"
    assert config.native_lake_project.local_package_cache is not None
    assert config.native_lake_project.local_package_cache.packages_root == tmp_path / "template" / ".lake" / "packages"
    assert config.native_lake_project.local_package_cache.manifest_path == tmp_path / "template" / "lake-manifest.json"
    assert config.native_lake_project.local_package_cache.package_names == ["mathlib", "aesop"]
