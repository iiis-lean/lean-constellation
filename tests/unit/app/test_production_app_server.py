from __future__ import annotations

import json
import time

from starlette.testclient import TestClient

from lean_constellation.app import LeanAppConfig, create_app_runtime_services, create_production_app_server


def test_production_app_server_exposes_admin_and_mcp_on_same_runtime(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False)
    app_result = create_production_app_server(config, view_keys=["resource_curator"])

    assert app_result.ok and app_result.value is not None
    app = app_result.value
    runtime = app.state.lean_constellation_runtime
    with TestClient(app) as client:
        health = client.get("/health")
        status = client.get("/admin/runtime/status")
        resumed = client.post("/admin/runtime/resume", json={})
        repo_shell = client.post(
            "/admin/main-repo/shell",
            json={
                "workspace_root": str(tmp_path / "workspace"),
                "repo_name": "MainRepo",
                "project_name": "MainProject",
            },
        )
        mcp_index = client.get("/mcp/views")

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert status.status_code == 200
    assert status.json()["value"]["paused"] is True
    assert resumed.status_code == 200
    assert resumed.json()["value"]["paused"] is False
    assert repo_shell.status_code == 200
    assert repo_shell.json()["value"]["repo_root"].endswith("workspace/MainRepo")
    assert mcp_index.status_code == 200
    assert mcp_index.json()["views"] == ["resource_curator"]
    assert app.state.lean_constellation_runtime is runtime


def test_production_app_server_scheduler_lifespan_runs_loop(tmp_path) -> None:
    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        scheduler_enabled=True,
        scheduler_tick_interval_s=0.01,
        scheduler_idle_interval_s=0.01,
        scheduler_error_interval_s=0.01,
    )
    app_result = create_production_app_server(config, view_keys=["resource_curator"])

    assert app_result.ok and app_result.value is not None
    app = app_result.value
    with TestClient(app) as client:
        time.sleep(0.05)
        health = client.get("/health")
        scheduler = health.json()["scheduler"]

    assert scheduler["running"] is True
    assert scheduler["tick_count"] >= 1


def test_production_app_server_exposes_test_control_routes_with_guard(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        response = client.post("/admin/test-control/flows/run-until-step", json={"flow_id": "flow-1"})
        wait_response = client.post("/admin/test-control/steps/wait", json={"step_id": "step-1"})

    assert response.status_code == 400
    assert response.json()["issues"][0]["kind"] == "test_control_disabled"
    assert wait_response.status_code == 400
    assert wait_response.json()["issues"][0]["kind"] == "test_control_disabled"


def test_production_app_server_exports_agent_trace_report(tmp_path) -> None:
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
        scope_id="repo:MainRepo",
        agent_type="CoordinatorAgent",
        cli_type="codex",
        home_id="CoordinatorAgent",
        thread_id="thread-1",
        rollout_relpath="sessions/trace.jsonl",
    )
    report_path = tmp_path / "trace_report.json"
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False)
    app_result = create_production_app_server(config, runtime=runtime)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        response = client.get(
            f"/admin/agents/{agent.agent_id}/trace-report",
            params={"output_path": str(report_path), "format": "json"},
        )

    assert response.status_code == 200
    assert response.json()["value"]["report_path"] == str(report_path)
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved["latest_turn"]["final_response"] == "complete"
    assert saved["tool_calls"][0]["tool_name"] == "inspect_workspace_for_coordinator"


def test_production_app_server_rejects_invalid_agent_trace_query(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        response = client.get("/admin/agents/agent-1/turn", params={"index": "not-an-int"})

    assert response.status_code == 422
    assert response.json()["issues"][0]["kind"] == "request_validation_failed"
    assert "index" in response.json()["issues"][0]["message"]
