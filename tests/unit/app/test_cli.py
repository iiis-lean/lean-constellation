from __future__ import annotations

import json

from lean_constellation.app.cli import build_parser, main


def test_cli_help_mentions_admin_commands() -> None:
    help_text = build_parser().format_help()

    assert "Lean Constellation admin CLI" in help_text
    assert "config-view" in help_text
    assert "serve" in help_text
    assert "start-flow" in help_text
    assert "snapshot" in help_text
    assert "external-list" in help_text
    assert "external-complete" in help_text
    assert "external-tools" in help_text
    assert "external-call" in help_text
    assert "agent-rollout-info" in help_text
    assert "agent-turns" in help_text
    assert "agent-event" in help_text
    assert "agent-trace-report" in help_text


def test_cli_config_view_prints_redacted_config(tmp_path, capsys) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'workspace_root = "{tmp_path / "workspace"}"\n', encoding="utf-8")

    exit_code = main(["--config", str(config_path), "config-view"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "workspace" in output
    assert "secret-token" not in output


def test_cli_serve_prints_redacted_server_payload(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'workspace_root = "{tmp_path / "workspace"}"\n', encoding="utf-8")
    calls = []

    async def fake_run_mcp_http_server(runtime, **kwargs):  # noqa: ANN001
        calls.append((runtime, kwargs))

    monkeypatch.setattr("lean_constellation.app.cli.run_mcp_http_server", fake_run_mcp_http_server)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "serve",
            "--host",
            "127.0.0.2",
            "--port",
            "9999",
            "--mcp-base-url",
            "http://example.test:9999/root",
            "--view-key",
            "resource_curator",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["command"] == "serve"
    assert payload["bind_host"] == "127.0.0.2"
    assert payload["bind_port"] == 9999
    assert payload["mcp_base_url"] == "http://example.test:9999/root"
    assert payload["view_keys"] == ["resource_curator"]
    assert payload["config"]["workspace_root"].endswith("workspace")
    assert calls[0][1] == {
        "host": "127.0.0.2",
        "port": 9999,
        "view_keys": ["resource_curator"],
        "log_level": "info",
    }
