from __future__ import annotations

import json
import time
from pathlib import Path

from starlette.testclient import TestClient

from lean_constellation.app import (
    LeanAppConfig,
    create_app_runtime_services,
    create_production_app_server,
    initialize_repo_runtime,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode


def _make_repo(workspace: Path, name: str) -> Path:
    repo_root = workspace / name
    (repo_root / ".lean_constellation").mkdir(parents=True)
    return repo_root


def test_production_app_server_exposes_workspace_registry_admin_and_repo_mcp(tmp_path) -> None:
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config, view_keys=["resource_curator"])
    _make_repo(tmp_path / "workspace", "MainRepo")

    assert app_result.ok and app_result.value is not None
    app = app_result.value
    registry = app.state.lean_constellation_registry
    with TestClient(app) as client:
        health = client.get("/health")
        repos = client.get("/admin/workspace/repos")
        loaded = client.post("/admin/workspace/repos/MainRepo/load")
        status = client.get("/admin/repos/MainRepo/runtime/status")
        resumed = client.post("/admin/repos/MainRepo/runtime/resume", json={})
        mcp_index = client.get("/repos/MainRepo/mcp/views")

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["mcp_base_url"] == "http://127.0.0.1:8766"
    assert repos.status_code == 200
    assert repos.json()["value"]["repos"][0]["repo_key"] == "MainRepo"
    assert loaded.status_code == 200
    assert loaded.json()["value"]["runtime_root"].endswith("workspace/MainRepo/.agent_runtime")
    assert status.status_code == 200
    assert status.json()["value"]["paused"] is True
    assert resumed.status_code == 200
    assert resumed.json()["value"]["paused"] is False
    assert mcp_index.status_code == 200
    assert mcp_index.json()["views"] == ["resource_curator"]
    assert app.state.lean_constellation_registry is registry


def test_production_app_server_repo_routes_isolate_flow_state(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_a = _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        started = client.post(
            "/admin/repos/RepoA/flows/start",
            json={
                "flow_type": "requirement_group_repo_bootstrap",
                "scope_id": "repo:RepoA",
                "params": {
                    "target_repo": "RepoA",
                    "repo_root": str(repo_a),
                    "workspace_root": str(workspace),
                    "requirement_refs": [],
                },
            },
        )
        repo_a_tree = client.get("/admin/repos/RepoA/flows/tree")
        repo_b_tree = client.get("/admin/repos/RepoB/flows/tree")

    assert started.status_code == 200
    assert started.json()["value"]["repo_root"] is None
    assert repo_a_tree.status_code == 200
    assert repo_a_tree.json()["value"]["total_flows"] == 1
    assert repo_b_tree.status_code == 200
    assert repo_b_tree.json()["value"]["total_flows"] == 0


def test_production_app_server_workspace_requirement_bootstrap_uses_provider_runtime(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    consumer = _make_repo(workspace, "Consumer")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    control = registry.workspace_runtime()
    assert control.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert control.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need provider source.",
        reason="Expose helper theorem.",
    ).ok

    with TestClient(app_result.value) as client:
        response = client.post(
            "/admin/workspace/requirements/bootstrap",
            json={"workspace_root": str(workspace), "target_repo": "Provider"},
        )
        provider_runtime = registry.try_get_loaded("Provider")
        consumer_runtime = registry.try_get_loaded("Consumer")

    assert response.status_code == 200
    assert response.json()["value"]["flow_type"] == "requirement_group_repo_bootstrap"
    assert provider_runtime is not None
    assert consumer_runtime is None
    provider_flows = provider_runtime.ark.flow_service.list_flows(flow_type="requirement_group_repo_bootstrap")
    assert len(provider_flows) == 1
    assert provider_flows[0].scope_id == "repo:Provider"


def test_production_app_server_workspace_requirement_resume_wakes_consumer_runtime(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    consumer = _make_repo(workspace, "Consumer")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    control = registry.workspace_runtime()
    assert control.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert control.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need provider source.",
        reason="Expose helper theorem.",
    ).ok
    assert control.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        reason="Waiting for provider.",
    ).ok
    assert control.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        note="Provider ready.",
    ).ok
    assert registry.try_get_loaded("Consumer") is None

    with TestClient(app_result.value) as client:
        response = client.post(
            "/admin/workspace/requirements/resume",
            json={
                "consumer_repo_root": str(consumer),
                "requirement_name": "need_provider",
                "provider_repo": "Provider",
                "admin_note": "Resume provider.",
            },
        )
        consumer_runtime = registry.try_get_loaded("Consumer")

    assert response.status_code == 200
    assert response.json()["value"]["observed"] is True
    assert consumer_runtime is not None
    flows = consumer_runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")
    assert len(flows) == 1
    assert flows[0].input.start_mode == "requirement_resume"


def test_production_app_server_workspace_main_repo_admin_routes_create_shell_and_status(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    repo_root = workspace / "MainRepo"
    with TestClient(app_result.value) as client:
        shell = client.post(
            "/admin/workspace/main-repo/shell",
            json={"workspace_root": str(workspace), "repo_name": "MainRepo", "project_name": "MainRepo"},
        )
        written = client.post(
            "/admin/workspace/main-repo/preparation-input",
            json={
                "repo_root": str(repo_root),
                "input": RepoPreparationInput(
                    goal="Prepare main repo.",
                    source_corpus_mode=SourceCorpusMode.EXISTING,
                    source_corpus_relpath=".lean_constellation/source",
                ).model_dump(mode="json"),
            },
        )
        status = client.get("/admin/main-repo/status", params={"repo_root": str(repo_root)})

    assert shell.status_code == 200
    assert shell.json()["value"]["repo_name"] == "MainRepo"
    assert written.status_code == 200
    assert status.status_code == 200
    assert status.json()["value"]["repo_exists"] is True
    assert status.json()["value"]["preparation_input_exists"] is True


def test_production_app_server_legacy_repo_routes_require_repo_key_when_ambiguous(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        ambiguous = client.get("/admin/runtime/status")

    assert ambiguous.status_code == 400
    assert ambiguous.json()["issues"][0]["kind"] == "repo_key_required"


def test_production_app_server_legacy_repo_routes_proxy_when_single_repo(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        status = client.get("/admin/runtime/status")
        tree = client.get("/admin/flows/tree")

    assert status.status_code == 200
    assert tree.status_code == 200
    assert tree.json()["value"]["total_flows"] == 0


def test_production_app_server_repo_snapshot_restore_does_not_touch_other_repo(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_a = _make_repo(workspace, "RepoA")
    repo_b = _make_repo(workspace, "RepoB")
    (repo_a / "Marker.txt").write_text("repo-a-before\n", encoding="utf-8")
    (repo_b / "Marker.txt").write_text("repo-b-before\n", encoding="utf-8")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    runtime_a = registry.get_or_load("RepoA")
    runtime_b = registry.get_or_load("RepoB")
    assert runtime_a.ok and runtime_a.value is not None
    assert runtime_b.ok and runtime_b.value is not None
    assert initialize_repo_runtime(runtime_a.value, repo_a).ok
    assert initialize_repo_runtime(runtime_b.value, repo_b).ok

    with TestClient(app_result.value) as client:
        created = client.post(
            "/admin/repos/RepoA/snapshots/create",
            json={
                "repo_root": str(repo_a),
                "checkpoint_kind": "manual_test_stable_point",
                "label": "repo-a-only",
            },
        )
        assert created.status_code == 200
        snapshot_id = created.json()["value"]["snapshot_id"]
        (repo_a / "Marker.txt").write_text("repo-a-after\n", encoding="utf-8")
        (repo_b / "Marker.txt").write_text("repo-b-after\n", encoding="utf-8")
        restored = client.post(
            "/admin/repos/RepoA/snapshots/restore",
            json={"repo_root": str(repo_a), "snapshot_id": snapshot_id, "leave_runtime_paused": True},
        )

    assert restored.status_code == 200
    assert (repo_a / "Marker.txt").read_text(encoding="utf-8") == "repo-a-before\n"
    assert (repo_b / "Marker.txt").read_text(encoding="utf-8") == "repo-b-after\n"


def test_production_app_server_repo_agent_reports_do_not_cross_repos(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "RepoA")
    _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    runtime_a = registry.get_or_load("RepoA")
    runtime_b = registry.get_or_load("RepoB")
    assert runtime_a.ok and runtime_a.value is not None
    assert runtime_b.ok and runtime_b.value is not None
    rollout = Path(runtime_a.value.ark.agent_service.runtime_root) / "homes" / "codex" / "CoordinatorAgent" / ".codex" / "sessions" / "trace.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text('{"type":"event_msg","payload":{"type":"task_complete","last_agent_message":"done"}}\n', encoding="utf-8")
    agent = runtime_a.value.ark.agent_service.store.create_agent_record(
        scope_id="repo:RepoA",
        agent_type="CoordinatorAgent",
        cli_type="codex",
        home_id="CoordinatorAgent",
        thread_id="thread-a",
        rollout_relpath="sessions/trace.jsonl",
    )

    with TestClient(app_result.value) as client:
        repo_a_response = client.get(f"/admin/repos/RepoA/agents/{agent.agent_id}/rollout")
        repo_b_response = client.get(f"/admin/repos/RepoB/agents/{agent.agent_id}/rollout")

    assert repo_a_response.status_code == 200
    assert repo_b_response.status_code == 400
    assert repo_b_response.json()["issues"][0]["kind"] == "agent_rollout_info_failed"


def test_production_app_server_materializes_repo_local_production_agent_homes_on_load(tmp_path) -> None:
    base_config = tmp_path / "codex" / "config.toml"
    auth_json = tmp_path / "codex" / "auth.json"
    base_config.parent.mkdir(parents=True)
    base_config.write_text("model = \"gpt-5-codex\"\n", encoding="utf-8")
    auth_json.write_text("{}\n", encoding="utf-8")
    repo_root = _make_repo(tmp_path / "workspace", "MainRepo")
    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        scheduler_enabled=False,
        codex_base_config_path=base_config,
        codex_auth_json_path=auth_json,
        admin_http_port=9123,
    )

    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    loaded = app_result.value.state.lean_constellation_registry.get_or_load("MainRepo")
    assert loaded.ok
    homes = app_result.value.state.lean_constellation_registry.get_status("MainRepo").value.agent_homes
    assert homes is not None
    assert homes.total == len(homes.materialized)
    assert homes.failed == []
    coordinator = next(home for home in homes.materialized if home.agent_type == "CoordinatorAgent")
    codex_config = repo_root / ".agent_runtime" / "homes" / "codex" / "CoordinatorAgent" / ".codex" / "config.toml"
    manifest = repo_root / ".agent_runtime" / "homes" / "codex" / "CoordinatorAgent" / ".agents" / "lean_constellation_home.json"
    assert coordinator.codex_config_path == str(codex_config)
    assert codex_config.exists()
    assert "http://127.0.0.1:9123/repos/MainRepo/mcp/views/" in codex_config.read_text(encoding="utf-8")
    assert json.loads(manifest.read_text(encoding="utf-8"))["mcp_transport"] == "http"


def test_production_app_server_scheduler_lifespan_runs_loop(tmp_path) -> None:
    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        scheduler_enabled=True,
        materialize_agent_homes=False,
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
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False, materialize_agent_homes=False)
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    app_result = create_production_app_server(config, runtime=runtime)

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
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False, materialize_agent_homes=False)
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
    config = LeanAppConfig(workspace_root=tmp_path / "workspace", scheduler_enabled=False, materialize_agent_homes=False)
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    app_result = create_production_app_server(config, runtime=runtime)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        response = client.get("/admin/agents/agent-1/turn", params={"index": "not-an-int"})

    assert response.status_code == 422
    assert response.json()["issues"][0]["kind"] == "request_validation_failed"
    assert "index" in response.json()["issues"][0]["message"]
