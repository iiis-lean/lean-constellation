from __future__ import annotations

import json
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from agent_runtime_kit.flow.models import FlowRequest, FlowStatus, StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState
from agent_runtime_kit.agent.provider_contracts import ProviderHomeSpec
from starlette.testclient import TestClient

from lean_constellation.app import (
    LeanAdminApi,
    LeanAppConfig,
    StartFlowInput,
    create_production_app_server,
    initialize_repo_business_truth,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, SourceMaterialInput
from lean_constellation.domain.repo import RepoCompletionMode
from lean_constellation.flows.common.agent_steps import RepoFormatDiscoveryAgentStep
from lean_constellation.services.external_clients import LeanMcpToolkitClient
from tests.unit_services_helpers import publish_native_provider_release


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
        workspace_status = client.get("/admin/workspace/status")
        loaded = client.post("/admin/workspace/repos/MainRepo/load")
        status = client.get("/admin/repos/MainRepo/runtime/status")
        resumed = client.post("/admin/repos/MainRepo/runtime/resume", json={"unbounded": True})
        mcp_index = client.get("/repos/MainRepo/mcp/views")

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert health.json()["mcp_base_url"] == "http://127.0.0.1:8766"
    assert health.json()["process_instance_id"].startswith("lc_")
    assert health.json()["process"]["pid"] > 0
    assert repos.status_code == 200
    assert repos.json()["value"]["repos"][0]["repo_key"] == "MainRepo"
    assert workspace_status.status_code == 200
    assert workspace_status.json()["value"]["scheduler_enabled"] is False
    assert workspace_status.json()["value"]["server_start_paused"] is True
    assert workspace_status.json()["value"]["test_control_enabled"] is False
    assert loaded.status_code == 200
    assert loaded.json()["value"]["runtime_root"].endswith("workspace/MainRepo/.agent_runtime")
    assert status.status_code == 200
    assert status.json()["value"]["paused"] is True
    assert resumed.status_code == 200
    assert resumed.json()["value"]["paused"] is False
    assert mcp_index.status_code == 200
    assert mcp_index.json()["views"] == ["resource_curator"]
    assert app.state.lean_constellation_registry is registry


def test_production_resume_routes_require_explicit_unbounded_or_bounded_control(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(
        workspace_root=workspace,
        scheduler_enabled=False,
        materialize_agent_homes=False,
        server_start_paused=True,
        test_control_enabled=False,
    )
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None

    with TestClient(app_result.value) as client:
        bounded = client.post(
            "/admin/repos/MainRepo/runtime/resume",
            json={"budget": {"flow_advances": 1, "step_starts": 0}},
        )
        status = client.get("/admin/repos/MainRepo/runtime/status")
        paused = client.post("/admin/repos/MainRepo/runtime/pause", json={})
        empty = client.post("/admin/repos/MainRepo/runtime/resume", json={})
        unbounded = client.post("/admin/repos/MainRepo/runtime/resume", json={"unbounded": True})
        active_bounded = client.post(
            "/admin/repos/MainRepo/runtime/resume",
            json={"budget": {"flow_advances": 0, "step_starts": 1}},
        )
        client.post("/admin/repos/MainRepo/runtime/pause", json={})
        workspace_bounded = client.post(
            "/admin/workspace/repos/MainRepo/resume",
            json={"budget": {"flow_advances": 0, "step_starts": 1}},
        )
        scoped_bounded = client.post(
            "/admin/repos/MainRepo/runtime/resume",
            json={
                "scope_id": "repo:MainRepo",
                "budget": {"flow_advances": 1, "step_starts": 0},
            },
        )
        zero_budget = client.post(
            "/admin/repos/MainRepo/runtime/resume",
            json={"budget": {"flow_advances": 0, "step_starts": 0}},
        )

    assert bounded.status_code == 200
    assert bounded.json()["value"]["paused"] is False
    assert bounded.json()["value"]["run_control"]["mode"] == "bounded"
    assert status.status_code == 200
    assert status.json()["value"]["test_control_enabled"] is False
    assert status.json()["value"]["run_control"]["remaining_flow_advances"] == 1
    assert paused.status_code == 200
    assert paused.json()["value"]["run_control"]["pause_reason"] == "manual_pause"
    assert empty.status_code == 422
    assert "exactly one run plan" in empty.json()["issues"][0]["message"]
    assert unbounded.status_code == 200
    assert unbounded.json()["value"]["run_control"]["mode"] == "unbounded"
    assert active_bounded.status_code == 400
    assert active_bounded.json()["issues"][0]["kind"] == "bounded_resume_requires_global_pause"
    assert workspace_bounded.status_code == 200
    assert workspace_bounded.json()["value"]["run_control"]["mode"] == "bounded"
    assert scoped_bounded.status_code == 422
    assert "repo-global" in scoped_bounded.json()["issues"][0]["message"]
    assert zero_budget.status_code == 422
    assert "at least one action" in zero_budget.json()["issues"][0]["message"]


def test_production_running_agent_audit_and_identity_checked_repair(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(
        LeanAppConfig(
            workspace_root=workspace,
            scheduler_enabled=False,
            materialize_agent_homes=False,
        )
    )
    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry

    with TestClient(app_result.value) as client:
        loaded = client.post("/admin/workspace/repos/MainRepo/load")
        assert loaded.status_code == 200
        runtime = registry.try_get_loaded("MainRepo")
        assert runtime is not None
        runtime.ark.agent_service.home_service.create_home(
            ProviderHomeSpec(provider_type="codex", home_id="CoordinatorAgent")
        )
        agent = runtime.ark.agent_service.create_agent(
            "repo:MainRepo",
            "CoordinatorAgent",
        )
        runtime.ark.agent_service.store.patch_agent(agent.agent_id, status="running")

        audit = client.get("/admin/repos/MainRepo/agents/running-audit")
        dry_run = client.post(
            f"/admin/repos/MainRepo/agents/{agent.agent_id}/repair-running",
            json={
                "expected_scope_id": "repo:MainRepo",
                "expected_session_id": None,
                "expected_artifact_ref": None,
                "action": "mark_idle",
                "dry_run": True,
            },
        )
        applied = client.post(
            f"/admin/repos/MainRepo/agents/{agent.agent_id}/repair-running",
            json={
                "expected_scope_id": "repo:MainRepo",
                "expected_session_id": None,
                "expected_artifact_ref": None,
                "action": "mark_idle",
                "dry_run": False,
            },
        )

    assert audit.status_code == 200
    assert audit.json()["value"]["agents"][0]["classification"] == "safe_to_mark_idle"
    assert dry_run.status_code == 200
    assert dry_run.json()["value"]["repaired"] is False
    assert applied.status_code == 200
    assert applied.json()["value"]["repaired"] is True


def test_invalid_bounded_resume_fails_before_repo_runtime_load(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(LeanAppConfig(
        workspace_root=workspace,
        scheduler_enabled=False,
        materialize_agent_homes=False,
    ))
    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry

    with TestClient(app_result.value) as client:
        response = client.post(
            "/admin/repos/MainRepo/runtime/resume",
            json={
                "scope_id": "repo:MainRepo",
                "budget": {"flow_advances": 1, "step_starts": 0},
            },
        )

    assert response.status_code == 422
    assert registry.try_get_loaded("MainRepo") is None


def test_native_source_index_recovery_routes_are_typed_and_route_owned(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(
        LeanAppConfig(
            workspace_root=workspace,
            scheduler_enabled=False,
            materialize_agent_homes=False,
        )
    )
    assert app_result.ok and app_result.value is not None

    with TestClient(app_result.value) as client:
        route_owned = client.post(
            "/admin/repos/MainRepo/runs/recover-source-index/preview",
            json={
                "repo_root": str(repo_root),
                "failed_parent_flow_id": "f_missing",
            },
        )
        missing_parent = client.post(
            "/admin/repos/MainRepo/runs/recover-source-index/preview",
            json={"failed_parent_flow_id": "f_missing"},
        )
        missing_token = client.post(
            "/admin/repos/MainRepo/runs/recover-source-index",
            json={"failed_parent_flow_id": "f_missing"},
        )

    assert route_owned.status_code == 422
    assert "route-owned" in route_owned.json()["issues"][0]["message"]
    assert missing_parent.status_code == 400
    assert missing_parent.json()["issues"][0]["kind"] == (
        "native_source_index_recovery_parent_missing"
    )
    assert missing_token.status_code == 422
    assert "expected_recovery_token" in missing_token.json()["issues"][0]["message"]


def test_failed_agent_step_restart_route_owns_step_id_and_uses_production_admin(
    tmp_path,
) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(
        LeanAppConfig(
            workspace_root=workspace,
            scheduler_enabled=False,
            materialize_agent_homes=False,
        )
    )
    assert app_result.ok and app_result.value is not None

    with TestClient(app_result.value) as client:
        route_owned = client.post(
            "/admin/repos/MainRepo/steps/missing/restart-failed",
            json={"step_id": "other"},
        )
        missing = client.post(
            "/admin/repos/MainRepo/steps/missing/restart-failed",
            json={},
        )

    assert route_owned.status_code == 422
    assert "route-owned" in route_owned.json()["issues"][0]["message"]
    assert missing.status_code == 400
    assert missing.json()["issues"][0]["kind"] == "restart_failed_agent_step_failed"


def test_production_semantic_advance_route_is_typed_and_starts_process_local_lease(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(
        LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    )
    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    runtime_result = registry.get_or_load("MainRepo")
    assert runtime_result.ok and runtime_result.value is not None
    started = LeanAdminApi(runtime_result.value).start_arbitrary_flow(
        StartFlowInput(
            flow_type="native_repo_coordinator",
            scope_id="repo:MainRepo",
            params={"repo_key": "MainRepo", "repo_root": str(repo_root), "start_mode": "admin_start"},
        )
    )
    assert started.ok

    with TestClient(app_result.value) as client:
        invalid = client.post(
            "/admin/repos/MainRepo/runtime/semantic-advance",
            json={"granularity": "step", "action": "logic"},
        )
        semantic = client.post(
            "/admin/repos/MainRepo/runtime/semantic-advance",
            json={"granularity": "step", "action": "logic", "scope_id": "repo:MainRepo"},
        )
        record = registry.discover_repo("MainRepo")
        assert record.ok and record.value is not None
        assert record.value.state == "active"
        lease_id = semantic.json()["value"]["lease_id"]
        lease = client.get(f"/admin/repos/MainRepo/runtime/leases/{lease_id}")
        waited = client.get(
            f"/admin/repos/MainRepo/runtime/leases/{lease_id}/wait",
            params={"after_version": 1, "timeout_s": 0},
        )
        lost = client.get("/admin/repos/MainRepo/runtime/leases/lease_from_old_process")

    assert invalid.status_code == 422
    assert semantic.status_code == 200
    assert semantic.json()["value"]["run_control"]["mode"] == "semantic"
    assert semantic.json()["value"]["run_control"]["semantic_policy"] == "step.logic"
    assert lease_id.startswith("lease_")
    assert semantic.json()["value"]["lease_version"] == 1
    assert semantic.json()["value"]["lease_status"] == "active"
    assert semantic.json()["value"]["wait_url"].endswith(f"/{lease_id}/wait")

    assert lease.status_code == 200
    assert lease.json()["value"]["lease"]["lease_id"] == lease_id
    assert lease.json()["value"]["truth_version"] == 1
    assert waited.status_code == 200
    assert waited.json()["value"]["timed_out"] is True
    assert lost.status_code == 400
    assert lost.json()["issues"][0]["kind"] == "lease_lost"
    assert lost.json()["issues"][0]["details"]["runtime"] is not None


def test_production_progress_and_agent_live_routes_are_repo_prefixed(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(
        LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    )
    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    started = LeanAdminApi(loaded.value).start_arbitrary_flow(
        StartFlowInput(
            flow_type="content_node_task",
            scope_id="repo:MainRepo:node:Main.Core",
            params={
                "repo_key": "MainRepo",
                "repo_path": str(repo_root),
                "node_path": "Main.Core",
                "contract_version": 1,
            },
        )
    )
    assert started.ok and started.value is not None
    loaded.value.ark.agent_service.home_service.create_home(
        ProviderHomeSpec(provider_type="codex", home_id="RepoFormatDiscoveryAgent")
    )
    agent = loaded.value.ark.agent_service.create_agent(
        "repo:MainRepo:node:Main.Core",
        "RepoFormatDiscoveryAgent",
        home_id="RepoFormatDiscoveryAgent",
    )
    owning_step = RepoFormatDiscoveryAgentStep(
        step_id="agent_live_owning_step",
        flow_id=started.value.flow_id,
        scope_id="repo:MainRepo:node:Main.Core",
        state=AgentStepState(
            agent_role="repo_format_discovery",
            agent_type="RepoFormatDiscoveryAgent",
            home_id="RepoFormatDiscoveryAgent",
            create_agent_if_missing=False,
        ),
    )
    owning_step.agent_bindings.by_role["repo_format_discovery"] = agent.agent_id
    loaded.value.ark.step_service.create_step(owning_step, enqueue=False)

    with TestClient(app_result.value) as client:
        progress = client.get(
            f"/admin/repos/MainRepo/content-tasks/{started.value.flow_id}/progress"
        )
        live = client.get(f"/admin/repos/MainRepo/agents/{agent.agent_id}/live")
        assert live.status_code == 200, live.json()["issues"][0]["message"]
        waited = client.get(
            f"/admin/repos/MainRepo/agents/{agent.agent_id}/live",
            params={"after_cursor": live.json()["value"]["next_cursor"], "wait_s": 0},
        )

    assert progress.status_code == 200
    assert progress.json()["value"]["node_path"] == "Main.Core"
    assert progress.json()["value"]["phase"] == "admission"
    assert live.status_code == 200
    assert live.json()["value"]["agent"]["agent_id"] == agent.agent_id
    assert live.json()["value"]["owning_steps"][0]["step_id"] == owning_step.step_id
    assert live.json()["value"]["report_index_url"].startswith("/admin/repos/MainRepo/")
    assert waited.status_code == 200
    assert waited.json()["value"]["timed_out"] is True


def test_production_step_terminal_wait_is_read_only_and_not_test_control_gated(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(
        LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    )
    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    started = LeanAdminApi(loaded.value).start_arbitrary_flow(
        StartFlowInput(
            flow_type="content_node_task",
            scope_id="repo:MainRepo:node:Main.Core",
            params={
                "repo_key": "MainRepo",
                "repo_path": str(repo_root),
                "node_path": "Main.Core",
                "contract_version": 1,
            },
        )
    )
    assert started.ok and started.value is not None
    step = RepoFormatDiscoveryAgentStep(
        step_id="terminal-step",
        flow_id=started.value.flow_id,
        scope_id="repo:MainRepo:node:Main.Core",
        status=StepStatus.COMPLETED,
        state=AgentStepState(
            agent_role="repo_format_discovery",
            agent_type="RepoFormatDiscoveryAgent",
            home_id="RepoFormatDiscoveryAgent",
            create_agent_if_missing=False,
        ),
    )
    loaded.value.ark.step_service.create_step(step, enqueue=False)

    with TestClient(app_result.value) as client:
        response = client.get(
            "/admin/repos/MainRepo/steps/terminal-step/wait",
            params={"timeout_s": 0},
        )
        missing = client.get(
            "/admin/repos/MainRepo/steps/missing/wait",
            params={"timeout_s": 0},
        )

    assert loaded.value.test_control_enabled is False
    assert response.status_code == 200, response.json()
    assert response.json()["value"]["terminal"] is True
    assert response.json()["value"]["runner_state"] == "settled"
    assert response.json()["value"]["step"]["step_id"] == "terminal-step"
    assert missing.status_code == 400
    assert missing.json()["issues"][0]["kind"] == "step_not_found"


def test_production_step_terminal_wait_does_not_block_status_route(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(
        LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    )
    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    started = LeanAdminApi(loaded.value).start_arbitrary_flow(
        StartFlowInput(
            flow_type="content_node_task",
            scope_id="repo:MainRepo:node:Main.Wait",
            params={
                "repo_key": "MainRepo",
                "repo_path": str(repo_root),
                "node_path": "Main.Wait",
                "contract_version": 1,
            },
        )
    )
    assert started.ok and started.value is not None
    waiting_step = RepoFormatDiscoveryAgentStep(
        step_id="created-step",
        flow_id=started.value.flow_id,
        scope_id="repo:MainRepo:node:Main.Wait",
        state=AgentStepState(
            agent_role="repo_format_discovery",
            agent_type="RepoFormatDiscoveryAgent",
            home_id="RepoFormatDiscoveryAgent",
            create_agent_if_missing=False,
        ),
    )
    loaded.value.ark.step_service.create_step(waiting_step, enqueue=False)

    with TestClient(app_result.value) as client, ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(
            client.get,
            "/admin/repos/MainRepo/steps/created-step/wait",
            params={"timeout_s": 0.5},
        )
        time.sleep(0.05)
        started_at = time.monotonic()
        status = client.get("/admin/repos/MainRepo/runtime/status")
        status_elapsed = time.monotonic() - started_at
        waited = waiting.result(timeout=2)

    assert status.status_code == 200
    assert status_elapsed < 0.4
    assert waited.status_code == 200
    assert waited.json()["value"]["timed_out"] is True
    assert waited.json()["value"]["runner_state"] == "not_started"


def test_agent_live_status_mode_uses_status_notification(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    app_result = create_production_app_server(
        LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    )
    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    loaded = registry.get_or_load("MainRepo")
    assert loaded.ok and loaded.value is not None
    service = loaded.value.ark.agent_service
    service.home_service.create_home(
        ProviderHomeSpec(provider_type="codex", home_id="RepoFormatDiscoveryAgent")
    )
    agent = service.create_agent(
        "repo:MainRepo:node:Main.Status",
        "RepoFormatDiscoveryAgent",
        home_id="RepoFormatDiscoveryAgent",
    )

    with TestClient(app_result.value) as client, ThreadPoolExecutor(max_workers=2) as pool:
        initial = client.get(f"/admin/repos/MainRepo/agents/{agent.agent_id}/live")
        assert initial.status_code == 200
        waiting = pool.submit(
            client.get,
            f"/admin/repos/MainRepo/agents/{agent.agent_id}/live",
            params={
                "after_cursor": initial.json()["value"]["next_cursor"],
                "wait_s": 1,
                "wake_on": "status",
            },
        )
        time.sleep(0.05)
        service.close_agent(agent.agent_id)
        observed = waiting.result(timeout=2)

    assert observed.status_code == 200
    assert observed.json()["value"]["wake_on"] == "status"
    assert observed.json()["value"]["timed_out"] is False
    assert observed.json()["value"]["agent"]["status"] == "closed"


def test_repo_lifecycle_route_rejects_cross_repo_body_identity(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_a = _make_repo(workspace, "RepoA")
    repo_b = _make_repo(workspace, "RepoB")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        client.post("/admin/workspace/repos/RepoA/load")
        response = client.post("/admin/repos/RepoA/continue", json={
            "repo_key": "RepoB", "repo_root": str(repo_b), "run_objective": "Cross repo mutation",
        })
    assert response.status_code == 422
    assert "must match" in response.json()["issues"][0]["message"]
    assert repo_a != repo_b


def test_repo_lifecycle_route_serializes_concurrent_continuations(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "Provider")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None
    app = app_result.value
    with TestClient(app) as client:
        assert client.post("/admin/workspace/repos/Provider/load").status_code == 200
        loaded = app.state.lean_constellation_registry.get_or_load("Provider", refresh_homes=False)
        assert loaded.ok and loaded.value is not None
        assert initialize_repo_business_truth(loaded.value, repo_root).ok
        assert loaded.value.foundation.store.write_json_atomic(
            loaded.value.repo_workspace.metadata._repo_publication_path(repo_root),
            {"status": "stable", "latest_release_id": "release-r1"},
        ).ok
        barrier = Barrier(2)

        def start():
            barrier.wait()
            return client.post(
                "/admin/repos/Provider/continue",
                json={"run_objective": "Continue the Provider repository.", "enqueue": False},
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [future.result() for future in [pool.submit(start), pool.submit(start)]]

    assert sorted(response.status_code for response in responses) == [200, 400]
    failed = next(response for response in responses if response.status_code == 400)
    assert failed.json()["issues"][0]["kind"] == "repo_lifecycle_flow_conflict"


def test_canonical_repo_run_route_rejects_route_owned_and_internal_fields(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "Provider")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None

    with TestClient(app_result.value) as client:
        assert client.post("/admin/workspace/repos/Provider/load").status_code == 200
        identity = client.post("/admin/repos/Provider/runs/continue", json={
            "repo_root": str(workspace / "Provider"), "run_objective": "Continue.",
        })
        internal = client.post("/admin/repos/Provider/runs/continue", json={
            "run_objective": "Continue.", "base_release_id": "release-r1",
        })

    assert identity.status_code == 422
    assert "route-owned fields" in identity.json()["issues"][0]["message"]
    assert internal.status_code == 422
    assert "base_release_id" in internal.json()["issues"][0]["message"]


def test_release_routes_list_show_and_isolate_repo_identity(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = _make_repo(workspace, "Provider")
    _make_repo(workspace, "Other")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)
    assert app_result.ok and app_result.value is not None
    app = app_result.value

    with TestClient(app) as client:
        assert client.post("/admin/workspace/repos/Provider/load").status_code == 200
        loaded = app.state.lean_constellation_registry.get_or_load("Provider", refresh_homes=False)
        assert loaded.ok and loaded.value is not None
        assert initialize_repo_business_truth(loaded.value, repo_root).ok
        release = publish_native_provider_release(loaded.value, repo_root, release_id="release-r1")

        listed = client.get("/admin/repos/Provider/releases")
        audited = client.get("/admin/repos/Provider/releases/audit")
        preview_validation = client.post("/admin/repos/Provider/releases/preview", json={})
        shown = client.get(f"/admin/repos/Provider/releases/{release.release_id}")
        unsafe = client.get("/admin/repos/Provider/releases/unsafe!release")
        restore_preview = client.post(
            f"/admin/repos/Provider/releases/{release.release_id}/restore/preview",
            json={},
        )
        restore_rejected = client.post(
            f"/admin/repos/Provider/releases/{release.release_id}/restore/apply",
            json={"expected_recovery_token": "0" * 64},
        )
        workspace_preview = client.post(
            "/admin/workspace/publication/preview",
            json={"repo_keys": ["Provider"]},
        )
        publication_prepared = client.post(
            "/admin/repos/Provider/publication/prepare",
            json={"title": "Provider"},
        )
        github_topics_preview = client.post(
            "/admin/repos/Provider/publication/github-topics/preview",
            json={},
        )
        remote_preview = client.post(
            f"/admin/repos/Provider/publication/remotes/{release.release_id}/preview",
            json={},
        )

    assert listed.status_code == 200
    assert [item["release"]["release_id"] for item in listed.json()["value"]["releases"]] == ["release-r1"]
    assert audited.status_code == 200
    assert audited.json()["ok"] is True
    assert audited.json()["value"]["orphan_checkpoint_ids"] == []
    assert preview_validation.status_code == 422
    assert preview_validation.json()["issues"][0]["kind"] == "request_validation_failed"
    assert shown.status_code == 200
    assert shown.json()["value"]["release"]["release_id"] == "release-r1"
    assert unsafe.status_code == 422
    assert restore_preview.status_code == 200
    assert len(restore_preview.json()["value"]["recovery_token"]) == 64
    assert restore_rejected.status_code == 400
    assert (
        restore_rejected.json()["issues"][0]["kind"]
        == "git_release_restore_token_mismatch"
    )
    assert workspace_preview.status_code == 200
    assert workspace_preview.json()["value"]["superproject_required"] is False
    assert publication_prepared.status_code == 200
    assert publication_prepared.json()["value"]["manifest_path"].endswith(
        ".lean_constellation/publication/manifest.json"
    )
    assert github_topics_preview.status_code == 400
    assert (
        github_topics_preview.json()["issues"][0]["kind"]
        == "github_topics_not_configured"
    )
    assert remote_preview.status_code == 400
    assert (
        remote_preview.json()["issues"][0]["kind"]
        == "publication_remote_not_configured"
    )
def test_production_app_server_exposes_workspace_external_health(tmp_path) -> None:
    toolkit = LeanMcpToolkitClient(dispatcher=lambda tool, payload: {"ok": True})
    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        scheduler_enabled=False,
        materialize_agent_homes=False,
    )
    app_result = create_production_app_server(
        config,
        external_overrides={"lean_mcp_toolkit": toolkit},
    )

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        canonical = client.get("/admin/workspace/external/health")

    assert canonical.status_code == 200
    assert canonical.json()["value"]["health"]["lean_toolkit_available"] is True


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
                    "resolved_provider_route": {"kind": "auto"},
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


def test_production_app_server_exposes_repo_config_routes(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    _make_repo(workspace, "MainRepo")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    with TestClient(app_result.value) as client:
        default_config = client.get("/admin/repos/MainRepo/config")
        updated = client.patch(
            "/admin/repos/MainRepo/config",
            json={
                "completion_mode": RepoCompletionMode.INTERFACE_DECLARED.value,
            },
        )
        publication = client.get("/admin/repos/MainRepo/publication")

    assert default_config.status_code == 200
    assert default_config.json()["value"]["config"]["completion_mode"] == "graph_proved"
    assert updated.status_code == 200
    assert updated.json()["value"]["config"]["completion_mode"] == "interface_declared"
    assert publication.status_code == 200
    assert publication.json()["value"]["publication"]["status"] == "developing"


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
    provider = _make_repo(workspace, "Provider")
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    registry = app_result.value.state.lean_constellation_registry
    loaded_consumer = registry.get_or_load("Consumer", refresh_homes=False)
    assert loaded_consumer.ok and loaded_consumer.value is not None
    consumer_runtime = loaded_consumer.value
    assert consumer_runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert consumer_runtime.repo_workspace.metadata.ensure_repo_model(provider).ok
    assert consumer_runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need provider source.",
        reason="Expose helper theorem.",
    ).ok
    assert consumer_runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        reason="Waiting for provider.",
    ).ok
    assert consumer_runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
        note="Provider ready.",
    ).ok
    publish_native_provider_release(consumer_runtime, provider, summary="Provider ready.")
    scope_id = "repo:Consumer"
    original_flow_id = consumer_runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id=scope_id,
            params={
                "repo_key": "Consumer",
                "repo_root": str(consumer),
                "start_mode": "admin_start",
                "start_reason": "HTTP resume test",
            },
        ),
        enqueue=False,
    )
    coordinator = consumer_runtime.ark.agent_service.store.create_agent_record(
        scope_id=scope_id,
        agent_type="CoordinatorAgent",
        provider_type="codex",
        home_id="CoordinatorAgent",
    )

    def mark_waiting(flow) -> None:
        flow.status = FlowStatus.WAITING
        flow.state.position.phase = "waiting_requirement"
        flow.state.waiting_requirement_name = "need_provider"
        flow.agent_bindings.by_role["coordinator"] = coordinator.agent_id

    consumer_runtime.ark.flow_service.store.update_flow_record(original_flow_id, mark_waiting)
    assert registry.unload("Consumer", require_stable=False).ok
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
    assert flows[0].flow_id == original_flow_id
    assert flows[0].input.start_mode == "admin_start"
    assert flows[0].agent_bindings.get("coordinator") == coordinator.agent_id


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
        source_root = repo_root / ".lean_constellation" / "source"
        source_root.mkdir(parents=True)
        (source_root / "README.md").write_text(
            "# Mathematical source\n\n"
            "Source provenance: transcribed from the supplied article.\n\n"
            "Reading order: read this main material first.\n\n"
            "Main theorem and definitions are stated here.\n\n"
            "Known gaps and extraction limits: no known gaps.\n",
            encoding="utf-8",
        )
        validated = client.post(
            "/admin/workspace/main-repo/source-corpus/validate",
            json={
                "repo_root": str(repo_root),
                "require_files": True,
                "check_draft_gate": True,
                "entry_path": "README.md",
            },
        )
        status = client.get("/admin/main-repo/status", params={"repo_root": str(repo_root)})

    assert shell.status_code == 200
    assert shell.json()["value"]["repo_name"] == "MainRepo"
    assert written.status_code == 200
    assert validated.status_code == 200
    assert validated.json()["value"]["draft_gate"]["passed"] is True
    assert status.status_code == 200
    assert status.json()["value"]["repo_exists"] is True
    assert status.json()["value"]["preparation_input_exists"] is True


def test_workspace_bootstrap_native_forwards_run_request(tmp_path) -> None:
    from tests.unit.app.test_admin_main_repo import FakeLakeClient

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = LeanAppConfig(workspace_root=workspace, scheduler_enabled=False, materialize_agent_homes=False)
    app_result = create_production_app_server(config)

    assert app_result.ok and app_result.value is not None
    app = app_result.value
    fake_lake = FakeLakeClient()
    workspace_runtime = app.state.lean_constellation_registry.workspace_runtime()
    workspace_runtime.external.lake = fake_lake
    workspace_runtime.external.lean_toolchain.lake = fake_lake
    with TestClient(app) as client:
        response = client.post(
            "/admin/workspace/main-repo/bootstrap-native",
            json={
                "workspace_root": str(workspace),
                "repo_name": "DeclaredRepo",
                "project_name": "DeclaredRepo",
                "preparation_input": RepoPreparationInput(
                    goal="Prepare the declared graph.",
                    source_corpus_mode=SourceCorpusMode.PREPARE,
                    source_material_inputs=[
                        SourceMaterialInput(
                            target="https://example.test/paper.pdf",
                            included_scope="Complete supplied paper.",
                            role="primary_source",
                        )
                    ],
                ).model_dump(mode="json"),
                "validate_source_corpus": False,
                "enqueue": False,
                "run_request": {
                    "run_objective": "Build only the declared graph.",
                    "completion_mode": "graph_declared",
                    "max_parallel_content_node_tasks": 1,
                },
            },
        )
        assert response.status_code == 200, response.text
        flow_id = response.json()["value"]["preparation_flow"]["flow_id"]
        runtime = app.state.lean_constellation_registry.try_get_loaded("DeclaredRepo")
        assert runtime is not None
        flow = runtime.ark.flow_service.get_flow(flow_id)
        assert flow.input.run_spec.completion_mode == RepoCompletionMode.GRAPH_DECLARED
        assert flow.input.run_spec.run_objective == "Build only the declared graph."


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
    assert initialize_repo_business_truth(runtime_a.value, repo_a).ok
    assert initialize_repo_business_truth(runtime_b.value, repo_b).ok

    with TestClient(app_result.value) as client:
        forged_snapshot = client.post(
            "/admin/repos/RepoA/snapshots/list",
            json={"repo_root": str(repo_b)},
        )
        forged_adapter = client.post(
            "/admin/repos/RepoA/preparation/adapter/start",
            json={"repo_root": str(repo_b), "enqueue": False},
        )
        created = client.post(
            "/admin/repos/RepoA/snapshots/create",
            json={
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
            json={"snapshot_id": snapshot_id, "leave_runtime_paused": True},
        )

    assert forged_snapshot.status_code == 422
    assert "route-owned fields" in forged_snapshot.json()["issues"][0]["message"]
    assert forged_adapter.status_code == 422
    assert "must match" in forged_adapter.json()["issues"][0]["message"]
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
    agent = runtime_a.value.ark.agent_service.store.create_agent_record(
        scope_id="repo:RepoA",
        agent_type="CoordinatorAgent",
        provider_type="codex",
        home_id="CoordinatorAgent",
    )

    with TestClient(app_result.value) as client:
        repo_a_response = client.get(f"/admin/repos/RepoA/agents/{agent.agent_id}/live")
        repo_b_response = client.get(f"/admin/repos/RepoB/agents/{agent.agent_id}/live")

    assert repo_a_response.status_code == 200
    assert repo_b_response.status_code == 400
    assert repo_b_response.json()["issues"][0]["kind"] == "agent_live_failed"


def test_production_app_server_materializes_repo_local_production_agent_homes_on_load(tmp_path) -> None:
    base_config = tmp_path / "codex" / "config.toml"
    auth_json = tmp_path / "codex" / "auth.json"
    base_config.parent.mkdir(parents=True)
    base_config.write_text("model = \"gpt-5-codex\"\n", encoding="utf-8")
    auth_json.write_text("{}\n", encoding="utf-8")
    shared_elan_home = tmp_path / "shared_elan"
    shared_elan_home.mkdir()
    repo_root = _make_repo(tmp_path / "workspace", "MainRepo")
    config = LeanAppConfig(
        workspace_root=tmp_path / "workspace",
        scheduler_enabled=False,
        codex_base_config_path=base_config,
        codex_auth_json_path=auth_json,
        shared_elan_home=shared_elan_home,
        codex_force_full_access=True,
        admin_http_port=9123,
        agent_home_overrides={
            "ContentPlanAgent": {
                "model": "gpt-5.6-sol",
                "model_reasoning_effort": "high",
            }
        },
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
    assert codex_config.exists()
    assert "http://127.0.0.1:9123/repos/MainRepo/mcp/views/" in codex_config.read_text(encoding="utf-8")
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["mcp_transport"] == "http"
    assert manifest_payload["fixed_env"]["ELAN_HOME"] == str(shared_elan_home.resolve())
    content_plan = next(home for home in homes.materialized if home.agent_type == "ContentPlanAgent")
    assert content_plan.effective_model == "gpt-5.6-sol"
    assert content_plan.effective_reasoning_effort == "high"
    content_plan_manifest = json.loads(
        (
            repo_root
            / ".agent_runtime"
            / "homes"
            / "codex"
            / "ContentPlanAgent"
            / ".agents"
            / "lean_constellation_home.json"
        ).read_text(encoding="utf-8")
    )
    assert content_plan_manifest["effective_model"] == "gpt-5.6-sol"
    assert content_plan_manifest["effective_reasoning_effort"] == "high"
    assert coordinator.effective_model == "gpt-5-codex"
    for home in homes.materialized:
        home_config = tomllib.loads(
            (
                repo_root
                / ".agent_runtime"
                / "homes"
                / "codex"
                / home.agent_type
                / ".codex"
                / "config.toml"
            ).read_text(encoding="utf-8")
        )
        assert home_config["sandbox_mode"] == "danger-full-access"


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
