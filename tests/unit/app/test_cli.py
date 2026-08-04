from __future__ import annotations

import json
from urllib import error

import pytest

from lean_constellation.app.cli import build_parser, main


def test_cli_help_mentions_admin_commands() -> None:
    help_text = build_parser().format_help()

    assert "Lean Constellation admin CLI" in help_text
    assert "config-view" in help_text
    assert "status" in help_text
    assert "pause" in help_text
    assert "resume" in help_text
    assert "serve" in help_text
    assert "flow-tree" in help_text
    assert "waiting-requirements" in help_text
    assert "resume-candidates" in help_text
    assert "agents" in help_text
    assert "external-health" in help_text
    assert "main-repo-status" in help_text
    assert "start-flow" in help_text
    assert "snapshot" in help_text
    assert "agent-turns" in help_text
    assert "agent-event" in help_text
    assert "agent-trace-report" in help_text
    assert "semantic-watch" in help_text
    assert "source-stats" in help_text
    assert "repo-run-initial" in help_text
    assert "repo-run-continue" in help_text
    assert "repo-release-preview" in help_text
    assert "repo-release-restore-preview" in help_text
    assert "repo-release-restore-apply" in help_text
    assert "repo-publication-prepare" in help_text
    assert "repo-publication-remote-preview" in help_text
    assert "repo-publication-remote-apply" in help_text
    assert "repo-dependency-change-preview" in help_text
    assert "repo-dependency-change-apply" in help_text
    assert "workspace-publication-preview" in help_text
    assert "workspace-publication-apply" in help_text

def test_cli_config_view_prints_redacted_config(tmp_path, capsys) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'workspace_root = "{tmp_path / "workspace"}"\n', encoding="utf-8")

    exit_code = main(["--config", str(config_path), "config-view"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "workspace" in output
    assert "secret-token" not in output


def test_cli_source_stats_is_standalone_and_read_only(tmp_path, capsys) -> None:
    (tmp_path / "Main.lean").write_text("theorem result : True := by trivial\n", encoding="utf-8")

    exit_code = main(["source-stats", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["lean_file_count"] == 1
    assert payload["graph_status"] == "unavailable"


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


def test_cli_serve_treats_keyboard_interrupt_as_clean_shutdown(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'workspace_root = "{tmp_path / "workspace"}"\n', encoding="utf-8")

    def interrupted_run(_callable):  # noqa: ANN001
        raise KeyboardInterrupt

    monkeypatch.setattr("lean_constellation.app.cli.anyio.run", interrupted_run)

    exit_code = main(["--config", str(config_path), "serve"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["command"] == "serve"


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
            "--repo-key",
            "Repo",
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
            "http://admin.test/admin/repos/Repo/flows/start",
            {
                "flow_type": "native_repo_preparation",
                "scope_id": "repo-1",
                "params": {"repo_id": "repo-1"},
                "enqueue": True,
            },
        )
    ]


def test_cli_resume_sends_exact_bounded_and_empty_payloads(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\nadmin_http_base_url = "http://admin.test"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(method, url, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        return {"ok": True, "value": {"paused": False}}

    monkeypatch.setattr("lean_constellation.app.cli._request_json", fake_request_json)

    bounded_exit = main([
        "--config",
        str(config_path),
        "resume",
        "--repo-key",
        "Repo",
        "--flow-advances",
        "1",
        "--step-starts",
        "0",
    ])
    empty_exit = main([
        "--config",
        str(config_path),
        "resume",
        "--repo-key",
        "Repo",
        "--unbounded",
    ])
    capsys.readouterr()

    assert bounded_exit == 0
    assert empty_exit == 0
    assert calls == [
        (
            "POST",
            "http://admin.test/admin/repos/Repo/runtime/resume",
            {"budget": {"flow_advances": 1, "step_starts": 0}},
        ),
        (
            "POST",
            "http://admin.test/admin/repos/Repo/runtime/resume",
            {"unbounded": True},
        ),
    ]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--flow-advances", "1"],
        ["--flow-advances", "0", "--step-starts", "0"],
        ["--flow-advances", "-1", "--step-starts", "0"],
        ["--scope-id", "repo:Repo", "--flow-advances", "1", "--step-starts", "0"],
        [],
        ["--unbounded", "--flow-advances", "1", "--step-starts", "0"],
    ],
)
def test_cli_resume_rejects_invalid_budget_combinations(tmp_path, arguments) -> None:  # noqa: ANN001
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'workspace_root = "{tmp_path / "workspace"}"\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        main([
            "--config",
            str(config_path),
            "resume",
            "--repo-key",
            "Repo",
            *arguments,
        ])

    assert exc_info.value.code == 2


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

    exit_code = main(["--config", str(config_path), "agent-turn", "--repo-key", "Repo", "agent-1", "--index", "2"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["value"]["turn_id"] == "turn-2"
    assert calls == [
        (
            "GET",
            "http://admin.test/root/admin/repos/Repo/agents/agent-1/turn?index=2",
            None,
        )
    ]


def test_cli_external_health_uses_workspace_admin_route(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\nadmin_http_base_url = "http://admin.test"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(method, url, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        return {"ok": True, "value": {"health": {"lean_toolkit_available": True}}}

    monkeypatch.setattr("lean_constellation.app.cli._request_json", fake_request_json)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "external-health",
            "--required-toolkit-tool",
            "diagnostics.file",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["value"]["health"]["lean_toolkit_available"] is True
    assert calls == [
        (
            "GET",
            "http://admin.test/admin/workspace/external/health?required_toolkit_tools=diagnostics.file",
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
    assert "http://127.0.0.1:65534/admin/workspace/status" in payload["issues"][0]["message"]


def test_cli_admin_http_timeout_prints_structured_error(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\nadmin_http_base_url = "http://admin.test"\n',
        encoding="utf-8",
    )

    def fake_urlopen(*args, **kwargs):  # noqa: ANN001
        raise TimeoutError("timed out")

    monkeypatch.setattr("lean_constellation.app.cli.request.urlopen", fake_urlopen)

    exit_code = main(["--config", str(config_path), "status"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["issues"][0]["kind"] == "admin_http_request_timeout"


def test_cli_semantic_watch_uses_long_poll_timeout_margin(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\nadmin_http_base_url = "http://admin.test"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(method, url, payload=None, *, timeout_s=30):  # noqa: ANN001
        calls.append((method, url, payload, timeout_s))
        terminal = "/runtime/leases/lease-1/wait?" in url
        return {
            "ok": True,
            "value": {
                "lease": {
                    "lease_id": "lease-1",
                    "version": 2 if terminal else 1,
                    "status": "terminal" if terminal else "active",
                    "terminal_reason": "semantic_boundary_reached" if terminal else None,
                },
                "started_steps": [],
                "current_content_task_flow_id": None,
                "current_agent_id": None,
            },
            "issues": [],
        }

    monkeypatch.setattr("lean_constellation.app.cli._request_json", fake_request_json)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "semantic-watch",
            "--repo-key",
            "Repo",
            "--lease-id",
            "lease-1",
            "--wait-s",
            "25",
            "--output",
            "summary",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["event"] == "watch_completed"
    assert calls[0] == ("GET", "http://admin.test/admin/repos/Repo/runtime/leases/lease-1", None, 30)
    assert calls[1][0] == "GET"
    assert "/runtime/leases/lease-1/wait?" in calls[1][1]
    assert "timeout_s=25.0" in calls[1][1]
    assert calls[1][2:] == (None, 35.0)


def test_cli_repo_run_initial_builds_semantic_http_payload(tmp_path, capsys, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\nadmin_http_base_url = "http://admin.test"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(method, url, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        return {"ok": True, "value": {"flow_id": "flow-initial"}}

    monkeypatch.setattr("lean_constellation.app.cli._request_json", fake_request_json)
    exit_code = main([
        "--config", str(config_path), "repo-run-initial", "--repo-key", "Provider",
        "--run-objective", "Declare chapter ten interfaces.", "--source-scope", "selected",
        "--source-selector", "book/chapter10.tex", "--completion-mode", "interface_declared",
    ])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["value"]["flow_id"] == "flow-initial"
    assert calls == [(
        "POST", "http://admin.test/admin/repos/Provider/runs/initial",
        {
            "request": {
                "run_objective": "Declare chapter ten interfaces.",
                "completion_mode": "interface_declared",
                "source_scope": {"mode": "selected", "selectors": ["book/chapter10.tex"]},
            },
            "admin_notes": None,
            "enqueue": True,
        },
    )]


def test_cli_git_release_restore_preview_and_apply_use_cas_routes(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:  # noqa: ANN001
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\n'
        'admin_http_base_url = "http://admin.test"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(method, url, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        return {"ok": True, "value": {}}

    monkeypatch.setattr("lean_constellation.app.cli._request_json", fake_request_json)
    assert main([
        "--config",
        str(config_path),
        "repo-release-restore-preview",
        "--repo-key",
        "Provider",
        "release-r2",
    ]) == 0
    assert main([
        "--config",
        str(config_path),
        "repo-release-restore-apply",
        "--repo-key",
        "Provider",
        "release-r2",
        "--expected-token",
        "a" * 64,
    ]) == 0
    capsys.readouterr()

    assert calls == [
        (
            "POST",
            "http://admin.test/admin/repos/Provider/releases/release-r2/restore/preview",
            {},
        ),
        (
            "POST",
            "http://admin.test/admin/repos/Provider/releases/release-r2/restore/apply",
            {"expected_recovery_token": "a" * 64},
        ),
    ]


def test_cli_publication_commands_preserve_explicit_policy_inputs(
    tmp_path,
    capsys,
    monkeypatch,
) -> None:  # noqa: ANN001
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'workspace_root = "{tmp_path / "workspace"}"\n'
        'admin_http_base_url = "http://admin.test"\n',
        encoding="utf-8",
    )
    calls = []

    def fake_request_json(method, url, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        return {"ok": True, "value": {}}

    monkeypatch.setattr("lean_constellation.app.cli._request_json", fake_request_json)
    assert main([
        "--config",
        str(config_path),
        "repo-publication-prepare",
        "--repo-key",
        "Provider",
        "--title",
        "Provider API",
    ]) == 0
    assert main([
        "--config",
        str(config_path),
        "repo-publication-remote-apply",
        "--repo-key",
        "Provider",
        "release-r2",
        "--expected-token",
        "b" * 64,
        "--push",
    ]) == 0
    assert main([
        "--config",
        str(config_path),
        "repo-publication-github-topics-preview",
        "--repo-key",
        "Provider",
    ]) == 0
    assert main([
        "--config",
        str(config_path),
        "repo-publication-github-topics-apply",
        "--repo-key",
        "Provider",
        "--expected-token",
        "d" * 64,
    ]) == 0
    assert main([
        "--config",
        str(config_path),
        "repo-dependency-change-preview",
        "--repo-key",
        "Consumer",
        "--provider-repo-key",
        "Provider",
        "--target-provider-release-id",
        "release-r2",
        "--target-git-url",
        "https://example.invalid/Provider.git",
    ]) == 0
    assert main([
        "--config",
        str(config_path),
        "workspace-publication-apply",
        "--repo-key",
        "Provider",
        "--output-root",
        str(tmp_path / "published"),
        "--push-children",
        "--expected-token",
        "c" * 64,
    ]) == 0
    capsys.readouterr()

    assert calls == [
        (
            "POST",
            "http://admin.test/admin/repos/Provider/publication/prepare",
            {"title": "Provider API"},
        ),
        (
            "POST",
            "http://admin.test/admin/repos/Provider/publication/remotes/release-r2/apply",
            {"expected_recovery_token": "b" * 64, "push": True},
        ),
        (
            "POST",
            "http://admin.test/admin/repos/Provider/publication/github-topics/preview",
            {"remote_name": "origin"},
        ),
        (
            "POST",
            "http://admin.test/admin/repos/Provider/publication/github-topics/apply",
            {
                "remote_name": "origin",
                "expected_recovery_token": "d" * 64,
            },
        ),
        (
            "POST",
            "http://admin.test/admin/repos/Consumer/publication/dependencies/preview",
            {
                "provider_repo_key": "Provider",
                "target_provider_release_id": "release-r2",
                "target_git_url": "https://example.invalid/Provider.git",
                "release_mode": "defer",
                "validation_profile": "dependency_minimal",
            },
        ),
        (
            "POST",
            "http://admin.test/admin/workspace/publication/apply",
            {
                "repo_keys": ["Provider"],
                "output_root": str(tmp_path / "published"),
                "push_children": True,
                "push_superproject": False,
                "expected_recovery_token": "c" * 64,
            },
        ),
    ]
