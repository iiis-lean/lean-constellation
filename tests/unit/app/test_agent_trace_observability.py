from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from lean_constellation.app import LeanAdminApi, create_app_runtime_services
from lean_constellation.app import cli as cli_module


def test_admin_api_reads_agent_trace_views(tmp_path: Path) -> None:
    runtime, agent_id = _runtime_with_rollout(tmp_path)
    admin = LeanAdminApi(runtime)

    rollout = admin.get_agent_rollout_info(agent_id)
    turns = admin.list_agent_turns(agent_id)
    latest_response = admin.get_latest_agent_response_text(agent_id)
    tool_call = admin.get_agent_tool_call(agent_id, last=True)
    report = admin.export_agent_trace_report(agent_id)

    assert rollout.ok and rollout.value["event_count"] == 4
    assert turns.ok and turns.value[0]["turn_id"] == "turn-1"
    assert latest_response.ok and latest_response.value == "complete"
    assert tool_call.ok and tool_call.value["tool_name"] == "inspect_workspace_for_coordinator"
    assert report.ok and report.value["latest_turn"]["final_response"] == "complete"


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

    exit_code = cli_module.main(["--config", str(config_path), "--admin-base-url", "http://admin.test", "agent-response-text", agent_id])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert json.loads(output)["value"] == "complete"

    exit_code = cli_module.main(
        ["--config", str(config_path), "--admin-base-url", "http://admin.test", "agent-trace-report", agent_id, "--out", str(report_path)]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert json.loads(output)["value"]["report_path"] == str(report_path)
    assert calls[0] == ("GET", "http://admin.test/admin/agents/agent-1/latest-response", None)
    trace_url = urlsplit(calls[1][1])
    assert calls[1][0] == "GET"
    assert f"{trace_url.scheme}://{trace_url.netloc}{trace_url.path}" == "http://admin.test/admin/agents/agent-1/trace-report"
    assert parse_qs(trace_url.query) == {"output_path": [str(report_path)], "format": ["json"]}


def _runtime_with_rollout(tmp_path: Path):
    runtime_root = tmp_path / ".agent_runtime"
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    rollout = runtime_root / "homes" / "codex" / "CoordinatorAgent" / ".codex" / "sessions" / "trace.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text(
        "\n".join(
            json.dumps(event)
            for event in [
                {"type": "turn_context", "payload": {"turn_id": "turn-1"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "inspect_workspace_for_coordinator",
                        "call_id": "call-1",
                        "arguments": "{}",
                    },
                },
                {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "{}"}},
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": "turn-1", "last_agent_message": "complete"},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    agent = runtime.ark.agent_service.store.create_agent_record(
        scope_id="repo:Provider",
        agent_type="CoordinatorAgent",
        cli_type="codex",
        home_id="CoordinatorAgent",
        thread_id="thread-1",
        rollout_relpath="sessions/trace.jsonl",
    )
    return runtime, agent.agent_id
