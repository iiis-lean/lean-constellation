from __future__ import annotations

import json
from urllib import error

from lean_constellation.app.cli import build_parser, main


def test_cli_help_mentions_admin_commands() -> None:
    help_text = build_parser().format_help()

    assert "Lean Constellation admin CLI" in help_text
    assert "config-view" in help_text
    assert "status" in help_text
    assert "pause" in help_text
    assert "resume" in help_text
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

    async def fake_run_production_app_server(config, **kwargs):  # noqa: ANN001
        calls.append((config, kwargs))

    monkeypatch.setattr("lean_constellation.app.cli.run_production_app_server", fake_run_production_app_server)

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
    assert calls[0][0].admin_http_host == "127.0.0.2"
    assert calls[0][0].admin_http_port == 9999
    assert calls[0][1] == {"view_keys": ["resource_curator"], "log_level": "info"}


def test_cli_start_flow_uses_admin_http(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\nadmin_http_base_url = "http://admin.test"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(method, url, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        return {"ok": True, "value": {"flow_id": "flow-1"}}

    monkeypatch.setattr("lean_constellation.app.cli._request_json", fake_request_json)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "start-flow",
            "native_repo_preparation",
            "repo-1",
            "--param",
            "repo_id=repo-1",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["value"]["flow_id"] == "flow-1"
    assert calls == [
        (
            "POST",
            "http://admin.test/admin/flows/start",
            {
                "flow_type": "native_repo_preparation",
                "scope_id": "repo-1",
                "params": {"repo_id": "repo-1"},
                "enqueue": True,
            },
        )
    ]


def test_cli_agent_turn_uses_admin_http_query(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\nadmin_http_base_url = "http://admin.test/root"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(method, url, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        return {"ok": True, "value": {"turn_id": "turn-2"}}

    monkeypatch.setattr("lean_constellation.app.cli._request_json", fake_request_json)

    exit_code = main(["--config", str(config_path), "agent-turn", "agent-1", "--index", "2"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["value"]["turn_id"] == "turn-2"
    assert calls == [
        (
            "GET",
            "http://admin.test/root/admin/agents/agent-1/turn?index=2",
            None,
        )
    ]


def test_cli_admin_http_connection_failure_prints_structured_error(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\nadmin_http_base_url = "http://127.0.0.1:65534"\n',
        encoding="utf-8",
    )

    def fake_urlopen(*args, **kwargs):  # noqa: ANN001
        raise error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr("lean_constellation.app.cli.request.urlopen", fake_urlopen)

    exit_code = main(["--config", str(config_path), "status"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["issues"][0]["kind"] == "admin_http_request_failed"
    assert "http://127.0.0.1:65534/admin/runtime/status" in payload["issues"][0]["message"]
