from __future__ import annotations

from agent_runtime_kit.agent.provider_contracts import ProviderHomeSpec
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus, StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState
from starlette.testclient import TestClient

from lean_constellation.app import (
    LeanAdminApi,
    LeanAppConfig,
    ResetCoordinatorForCurrentTruthInput,
    create_app_runtime_services,
    create_production_app_server,
)
from lean_constellation.flows.common.agent_steps import CoordinatorAgentStep


def _create_callback_boundary(runtime, repo_root, *, repo_key: str = "MainRepo"):
    scope_id = f"repo:{repo_key}"
    runtime.ark.agent_service.home_service.create_home(
        ProviderHomeSpec(provider_type="codex", home_id="CoordinatorAgent")
    )
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id=scope_id,
            params={
                "repo_key": repo_key,
                "repo_root": str(repo_root),
                "start_mode": "admin_start",
                "start_reason": "current-truth recovery test",
            },
        ),
        enqueue=False,
    )
    agent = runtime.ark.agent_service.create_agent(scope_id, "CoordinatorAgent")

    def patch(flow) -> None:
        flow.status = FlowStatus.RUNNING
        flow.state.position.phase = "coordinator_callback"
        flow.state.pending_dispatch_kind = "repo_exploration"
        flow.state.pending_dispatch_source_step_id = "historical_coordinator_step"
        flow.state.pending_dispatch_source_submission_id = "historical_submission"
        flow.state.waiting_dispatch_step_id = "historical_dispatch_step"
        flow.agent_bindings.by_role["coordinator"] = agent.agent_id

    runtime.ark.flow_service.store.update_flow_record(flow_id, patch)
    return flow_id, agent


def _reset_boundary_identity(runtime, flow_id: str):
    flow = runtime.ark.flow_service.get_flow(flow_id)
    agents = tuple(
        sorted(
            (
                agent.agent_id,
                agent.scope_id,
                agent.agent_type,
                agent.provider_type,
                agent.home_id,
                agent.status,
                agent.session_locator.session_id if agent.session_locator is not None else None,
            )
            for agent in runtime.ark.agent_service.list_agents()
        )
    )
    return flow.model_dump(mode="json"), agents


def test_reset_coordinator_for_current_truth_at_idle_callback_boundary(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(
            flow_id=flow_id,
            expected_agent_id=previous.agent_id,
        )
    )

    assert result.ok and result.value is not None
    assert result.value.previous_agent_id == previous.agent_id
    assert result.value.replacement_agent_id != previous.agent_id
    assert result.value.previous_phase == "coordinator_callback"
    assert result.value.current_phase == "coordinator_agent"
    assert result.value.enqueued is False
    unchanged_previous = runtime.ark.agent_service.get_agent(previous.agent_id)
    assert unchanged_previous.status == previous.status
    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.agent_bindings.get("coordinator") == result.value.replacement_agent_id
    assert flow.state.position.phase == "coordinator_agent"
    assert flow.state.pending_dispatch_source_step_id == "historical_coordinator_step"
    assert flow.state.waiting_dispatch_step_id == "historical_dispatch_step"

    runtime.ark.pause_controller.resume(None)
    step_id = runtime.ark.flow_service.advance_flow(flow_id)
    assert step_id is not None
    step = runtime.ark.step_service.store.get_step(step_id)
    assert step.state.agent_role == "coordinator"
    assert step.state.prompt_mode == "initial"
    assert step.state.callback_dispatch_step_id is None
    assert runtime.ark.flow_service.get_flow(flow_id).agent_bindings.get("coordinator") == result.value.replacement_agent_id


def test_reset_coordinator_for_current_truth_rejects_unpaused_runtime(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=False)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert result.issues[0].kind == "reset_coordinator_for_current_truth_failed"
    assert "paused" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_binding_drift(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, _ = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id="stale_agent")
    )

    assert not result.ok
    assert "binding changed" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_active_flow_advance(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    runtime.ark.schedule_service.active_flow_advances.add(flow_id)
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert "active Flow advance" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_missing_schedule_service(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    before = _reset_boundary_identity(runtime, flow_id)
    runtime.ark.schedule_service = None

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert result.issues[0].kind == "coordinator_recovery_service_missing"
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_wrong_phase(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    runtime.ark.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: setattr(flow.state.position, "phase", "coordinator_agent"),
    )
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert "coordinator_callback phase" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_orphan_created_step(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    flow = runtime.ark.flow_service.get_flow(flow_id)
    runtime.ark.step_service.create_step(
        CoordinatorAgentStep(
            step_id="orphan_created_coordinator_step",
            flow_id=flow_id,
            scope_id=flow.scope_id,
            state=AgentStepState(
                agent_role="coordinator",
                agent_type="CoordinatorAgent",
                home_id="CoordinatorAgent",
            ),
        ),
        enqueue=False,
    )
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert "running or created Step" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_orphan_running_step(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step_id = "orphan_running_coordinator_step"
    runtime.ark.step_service.create_step(
        CoordinatorAgentStep(
            step_id=step_id,
            flow_id=flow_id,
            scope_id=flow.scope_id,
            state=AgentStepState(
                agent_role="coordinator",
                agent_type="CoordinatorAgent",
                home_id="CoordinatorAgent",
            ),
        ),
        enqueue=False,
    )
    runtime.ark.step_service.store.update_step_record(
        step_id,
        lambda step: setattr(step, "status", StepStatus.RUNNING),
    )
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert "running or created Step" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_current_step(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    runtime.ark.pause_controller.resume(None)
    step_id = runtime.ark.flow_service.advance_flow(flow_id)
    runtime.ark.pause_controller.pause(None)
    assert step_id is not None
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert "no current Step" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_running_agent(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    runtime.ark.agent_service.store.patch_agent(previous.agent_id, status="running")
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert "no running Agent" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_agent_scope_mismatch(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    foreign = runtime.ark.agent_service.store.create_agent_record(
        scope_id="repo:OtherRepo",
        agent_type="CoordinatorAgent",
        provider_type=previous.provider_type,
        home_id=previous.home_id,
    )
    runtime.ark.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: flow.agent_bindings.by_role.__setitem__("coordinator", foreign.agent_id),
    )
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=foreign.agent_id)
    )

    assert not result.ok
    assert "scope does not match" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_reset_coordinator_for_current_truth_rejects_non_coordinator_binding(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    runtime.ark.agent_service.store.patch_agent(previous.agent_id, agent_type="ResourceCuratorAgent")
    before = _reset_boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(flow_id=flow_id, expected_agent_id=previous.agent_id)
    )

    assert not result.ok
    assert "not a CoordinatorAgent" in result.issues[0].message
    assert _reset_boundary_identity(runtime, flow_id) == before


def test_production_http_resets_coordinator_for_current_truth(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = workspace / "MainRepo"
    (repo_root / ".lean_constellation").mkdir(parents=True)
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
        assert client.post("/admin/workspace/repos/MainRepo/load").status_code == 200
        runtime = registry.try_get_loaded("MainRepo")
        assert runtime is not None
        flow_id, previous = _create_callback_boundary(runtime, repo_root)
        forbidden = client.post(
            f"/admin/repos/MainRepo/flows/{flow_id}/coordinator/reset-current-truth",
            json={"flow_id": flow_id, "expected_agent_id": previous.agent_id},
        )
        response = client.post(
            f"/admin/repos/MainRepo/flows/{flow_id}/coordinator/reset-current-truth",
            json={"expected_agent_id": previous.agent_id},
        )

    assert forbidden.status_code == 422
    assert "route-owned" in forbidden.json()["issues"][0]["message"]
    assert response.status_code == 200
    assert response.json()["value"]["previous_agent_id"] == previous.agent_id
    assert response.json()["value"]["current_phase"] == "coordinator_agent"
