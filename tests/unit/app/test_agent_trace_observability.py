from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from agent_runtime_kit.agent.provider_contracts import ProviderHomeSpec

from lean_constellation.app import LeanAdminApi, create_app_runtime_services
from lean_constellation.app import cli as cli_module
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider


def test_admin_api_reads_agent_trace_views(tmp_path: Path) -> None:
    runtime, agent_id = _runtime_with_scripted_session(tmp_path)
    admin = LeanAdminApi(runtime)

    turns = admin.list_agent_turns(agent_id)
    latest_response = admin.get_latest_agent_response_text(agent_id)
    tool_call = admin.get_agent_tool_call(agent_id, last=True)
    report = admin.export_agent_trace_report(agent_id)

    assert turns.ok and turns.value[0]["locator"]["turn_id"].startswith("scripted-turn-")
    assert latest_response.ok and latest_response.value == "CoordinatorAgent called replace_fixture"
    assert tool_call.ok and tool_call.value["tool_name"] == "replace_fixture"
    assert report.ok and report.value["turns"][0]["result"]["final_text"] == latest_response.value


def test_cli_agent_trace_commands_use_admin_http(tmp_path: Path, monkeypatch, capsys) -> None:
    agent_id = "agent-1"
    report_path = tmp_path / "trace_report.json"
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'workspace_root = "{tmp_path / "workspace"}"\n', encoding="utf-8")
    calls = []

    def fake_request_json(method, url, payload=None):  # noqa: ANN001
        calls.append((method, url, payload))
        if url.endswith("/latest-response"):
            return {"ok": True, "value": "complete"}
        return {"ok": True, "value": {"report_path": str(report_path), "tool_calls": [{"call_id": "call-1"}]}}

    monkeypatch.setattr(cli_module, "_request_json", fake_request_json)

    exit_code = cli_module.main(
        ["--config", str(config_path), "--admin-base-url", "http://admin.test", "agent-response-text", "--repo-key", "Repo", agent_id]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert json.loads(output)["value"] == "complete"

    exit_code = cli_module.main(
        [
            "--config",
            str(config_path),
            "--admin-base-url",
            "http://admin.test",
            "agent-trace-report",
            "--repo-key",
            "Repo",
            agent_id,
            "--out",
            str(report_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert json.loads(output)["value"]["report_path"] == str(report_path)
    assert calls[0] == ("GET", "http://admin.test/admin/repos/Repo/agents/agent-1/latest-response", None)
    trace_url = urlsplit(calls[1][1])
    assert calls[1][0] == "GET"
    assert f"{trace_url.scheme}://{trace_url.netloc}{trace_url.path}" == "http://admin.test/admin/repos/Repo/agents/agent-1/trace-report"
    assert parse_qs(trace_url.query) == {"output_path": [str(report_path)], "format": ["json"]}


def _runtime_with_scripted_session(tmp_path: Path):
    runtime_root = tmp_path / ".agent_runtime"
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    provider = ScriptedMcpProvider(runtime)
    install_scripted_provider(runtime, provider)
    runtime.ark.agent_service.home_service.create_home(
        ProviderHomeSpec(provider_type="scripted", home_id="CoordinatorAgent")
    )
    fixture = tmp_path / "fixture.txt"
    fixture.write_text("before", encoding="utf-8")
    provider.enqueue(
        "CoordinatorAgent",
        (
            "file_replace",
            "replace_fixture",
            {"repo_root": str(tmp_path), "path": str(fixture), "old": "before", "new": "after"},
        ),
    )
    agent = runtime.ark.agent_service.create_agent(
        scope_id="repo:Provider",
        agent_type="CoordinatorAgent",
        provider_type="scripted",
        home_id="CoordinatorAgent",
    )
    runtime.ark.agent_service.start_agent(agent.agent_id, prompt="Run the scripted fixture.")
    runtime.ark.agent_service.wait_agent(agent.agent_id, timeout_s=5)
    return runtime, agent.agent_id
