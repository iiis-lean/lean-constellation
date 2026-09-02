from __future__ import annotations

import pytest
from agent_runtime_kit.agent.provider_contracts import ProviderHomeSpec
from agent_runtime_kit.flow import FlowRequest, FlowStatus, StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState
from pydantic import ValidationError
from starlette.testclient import TestClient

from lean_constellation.app import (
    LeanAdminApi,
    LeanAppConfig,
    ResetContentPlanForCurrentTruthInput,
    ResetCoordinatorForCurrentTruthInput,
    SetAgentStepOperatorInstructionInput,
    create_app_runtime_services,
    create_production_app_server,
)
from lean_constellation.flows.common.agent_steps import ContentPlanAgentStep
from lean_constellation.flows.common.agent_steps import CoordinatorAgentStep


def _content_plan_boundary(runtime, repo_root, *, phase: str = "plan_agent"):
    scope_id = "repo:MainRepo"
    runtime.ark.agent_service.home_service.create_home(
        ProviderHomeSpec(provider_type="codex", home_id="ContentPlanAgent")
    )
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id=scope_id,
            params={
                "repo_key": "MainRepo",
                "repo_path": str(repo_root),
                "node_path": "Main.KeyLemma",
                "contract_version": 2,
            },
        ),
        enqueue=False,
    )
    previous = runtime.ark.agent_service.create_agent(scope_id, "ContentPlanAgent")

    def patch(flow) -> None:
        flow.status = FlowStatus.RUNNING
        flow.state.position.phase = phase
        flow.agent_bindings.by_role["content_plan"] = previous.agent_id

    runtime.ark.flow_service.store.update_flow_record(flow_id, patch)
    return flow_id, previous


def _boundary_identity(runtime, flow_id: str):
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
    steps = tuple(
        sorted(
            (
                step.step_id,
                str(step.status),
                step.agent_bindings.get("content_plan"),
            )
            for step in runtime.ark.flow_service.list_steps(flow_id=flow_id)
        )
    )
    return flow.model_dump(mode="json"), agents, steps


def test_reset_content_plan_for_current_truth_replaces_flow_binding(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _content_plan_boundary(runtime, tmp_path / "MainRepo")

    result = LeanAdminApi(runtime).reset_content_plan_for_current_truth(
        ResetContentPlanForCurrentTruthInput(
            flow_id=flow_id,
            expected_agent_id=previous.agent_id,
        )
    )

    assert result.ok and result.value is not None, result.issues
    assert result.value.previous_agent_id == previous.agent_id
    assert result.value.replacement_agent_id != previous.agent_id
    assert result.value.previous_phase == "plan_agent"
    assert result.value.current_phase == "plan_agent"
    assert result.value.enqueued is False
    assert runtime.ark.agent_service.get_agent(previous.agent_id) == previous
    replacement = runtime.ark.agent_service.get_agent(result.value.replacement_agent_id)
    assert replacement.agent_type == previous.agent_type
    assert replacement.provider_type == previous.provider_type
    assert replacement.home_id == previous.home_id
    assert replacement.session_locator is None
    assert runtime.ark.flow_service.get_flow(flow_id).agent_bindings.get("content_plan") == replacement.agent_id


def test_reset_content_plan_for_current_truth_updates_current_created_step(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _content_plan_boundary(runtime, tmp_path / "MainRepo", phase="callback_plan_agent")
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = ContentPlanAgentStep(
        step_id="content-plan-created",
        flow_id=flow_id,
        scope_id=flow.scope_id,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            home_id="ContentPlanAgent",
        ),
    )
    step.agent_bindings.by_role["content_plan"] = previous.agent_id
    runtime.ark.flow_service.store.create_step(step)

    def attach(target) -> None:
        target.step_ids.append(step.step_id)
        target.current_step_id = step.step_id

    runtime.ark.flow_service.store.update_flow_record(flow_id, attach)

    current_flow = runtime.ark.flow_service.get_flow(flow_id)
    current_step = runtime.ark.flow_service.get_step(step.step_id)
    instruction_result = LeanAdminApi(runtime).set_agent_step_operator_instruction(
        SetAgentStepOperatorInstructionInput(
            step_id=step.step_id,
            expected_step_updated_at=current_step.updated_at,
            expected_flow_updated_at=current_flow.updated_at,
            instruction="Inspect exact current sibling truth.",
        )
    )
    assert instruction_result.ok, instruction_result.issues

    result = LeanAdminApi(runtime).reset_content_plan_for_current_truth(
        ResetContentPlanForCurrentTruthInput(
            flow_id=flow_id,
            expected_agent_id=previous.agent_id,
        )
    )

    assert result.ok and result.value is not None, result.issues
    assert result.value.updated_step_id == step.step_id
    replacement_id = result.value.replacement_agent_id
    assert runtime.ark.flow_service.get_flow(flow_id).agent_bindings.get("content_plan") == replacement_id
    assert runtime.ark.flow_service.get_step(step.step_id).agent_bindings.get("content_plan") == replacement_id
    assert runtime.ark.flow_service.get_step(step.step_id).status is StepStatus.CREATED
    assert (
        runtime.ark.flow_service.get_step(step.step_id).state.operator_instruction
        == "Inspect exact current sibling truth."
    )


def test_reset_content_plan_for_current_truth_rolls_back_gate_drift_and_binding(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _content_plan_boundary(
        runtime,
        tmp_path / "MainRepo",
        phase="callback_plan_agent",
    )
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = ContentPlanAgentStep(
        step_id="content-plan-created",
        flow_id=flow_id,
        scope_id=flow.scope_id,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            home_id="ContentPlanAgent",
        ),
    )
    step.agent_bindings.by_role["content_plan"] = previous.agent_id
    runtime.ark.flow_service.store.create_step(step)

    def attach(target) -> None:
        target.step_ids.append(step.step_id)
        target.current_step_id = step.step_id

    runtime.ark.flow_service.store.update_flow_record(flow_id, attach)
    before_flow = runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json")
    before_step = runtime.ark.flow_service.get_step(step.step_id).model_dump(mode="json")
    before_agent_ids = {agent.agent_id for agent in runtime.ark.agent_service.list_agents()}
    original = runtime.ark.flow_service.replace_bound_agent

    def inject_gate_drift(**kwargs):
        boundary_mutator = kwargs["boundary_mutator"]

        def drift_before_lc_validation(target_flow, target_step) -> None:
            target_flow.state.position.phase = "completion_audit"
            boundary_mutator(target_flow, target_step)

        kwargs["boundary_mutator"] = drift_before_lc_validation
        return original(**kwargs)

    monkeypatch.setattr(runtime.ark.flow_service, "replace_bound_agent", inject_gate_drift)

    result = LeanAdminApi(runtime).reset_content_plan_for_current_truth(
        ResetContentPlanForCurrentTruthInput(
            flow_id=flow_id,
            expected_agent_id=previous.agent_id,
        )
    )

    assert not result.ok
    assert "changed during current-truth reset" in result.issues[0].message
    assert runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json") == before_flow
    assert runtime.ark.flow_service.get_step(step.step_id).model_dump(mode="json") == before_step
    after_agent_ids = {agent.agent_id for agent in runtime.ark.agent_service.list_agents()}
    assert len(after_agent_ids - before_agent_ids) == 1


@pytest.mark.parametrize("failure", ["phase", "unpaused", "binding"])
def test_reset_content_plan_for_current_truth_rejects_unsafe_boundary_without_mutation(
    tmp_path,
    failure: str,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    expected = previous.agent_id
    if failure == "phase":
        runtime.ark.flow_service.store.update_flow_record(
            flow_id,
            lambda flow: setattr(flow.state.position, "phase", "completion_audit"),
        )
    elif failure == "unpaused":
        runtime.ark.pause_controller.resume(None)
    elif failure == "binding":
        expected = "stale-agent"
    before = _boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_content_plan_for_current_truth(
        ResetContentPlanForCurrentTruthInput(
            flow_id=flow_id,
            expected_agent_id=expected,
        )
    )

    assert not result.ok
    assert result.issues[0].kind == "reset_content_plan_for_current_truth_failed"
    assert _boundary_identity(runtime, flow_id) == before


@pytest.mark.parametrize(
    "failure",
    ["status", "current_step_status", "current_step_type", "active_flow", "running_agent"],
)
def test_reset_content_plan_for_current_truth_rejects_invalid_business_boundary_without_mutation(
    tmp_path,
    failure: str,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    flow = runtime.ark.flow_service.get_flow(flow_id)
    if failure == "status":
        runtime.ark.flow_service.store.update_flow_record(
            flow_id,
            lambda target: setattr(target, "status", FlowStatus.WAITING),
        )
    elif failure in {"current_step_status", "current_step_type"}:
        step_type = ContentPlanAgentStep if failure == "current_step_status" else CoordinatorAgentStep
        step = step_type(
            step_id="invalid-current-step",
            flow_id=flow_id,
            scope_id=flow.scope_id,
            state=AgentStepState(
                agent_role="content_plan",
                agent_type="ContentPlanAgent",
                home_id="ContentPlanAgent",
            ),
        )
        runtime.ark.flow_service.store.create_step(step)
        if failure == "current_step_status":
            runtime.ark.flow_service.store.update_step_record(
                step.step_id,
                lambda target: setattr(target, "status", StepStatus.RUNNING),
            )

        def attach(target) -> None:
            target.step_ids.append(step.step_id)
            target.current_step_id = step.step_id

        runtime.ark.flow_service.store.update_flow_record(flow_id, attach)
    elif failure == "active_flow":
        runtime.ark.schedule_service.active_flow_advances.add(flow_id)
    elif failure == "running_agent":
        runtime.ark.agent_service.store.patch_agent(previous.agent_id, status="running")
    before = _boundary_identity(runtime, flow_id)

    result = LeanAdminApi(runtime).reset_content_plan_for_current_truth(
        ResetContentPlanForCurrentTruthInput(
            flow_id=flow_id,
            expected_agent_id=previous.agent_id,
        )
    )

    assert not result.ok
    assert result.issues[0].kind == "reset_content_plan_for_current_truth_failed"
    assert _boundary_identity(runtime, flow_id) == before


def test_content_plan_reset_input_rejects_fork_current() -> None:
    with pytest.raises(ValidationError):
        ResetContentPlanForCurrentTruthInput(
            flow_id="flow",
            expected_agent_id="agent",
            replacement_mode="fork_current",
        )


def test_coordinator_reset_delegates_binding_replacement_to_ark(tmp_path, monkeypatch) -> None:
    from tests.unit.app.test_admin_coordinator_agent_recovery import _create_callback_boundary

    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, previous = _create_callback_boundary(runtime, tmp_path / "MainRepo")
    original = runtime.ark.flow_service.replace_bound_agent
    calls: list[dict[str, object]] = []

    def observed(**kwargs):
        calls.append(dict(kwargs))
        return original(**kwargs)

    monkeypatch.setattr(runtime.ark.flow_service, "replace_bound_agent", observed)

    result = LeanAdminApi(runtime).reset_coordinator_for_current_truth(
        ResetCoordinatorForCurrentTruthInput(
            flow_id=flow_id,
            expected_agent_id=previous.agent_id,
        )
    )

    assert result.ok and result.value is not None, result.issues
    assert len(calls) == 1
    call = calls[0]
    assert callable(call.pop("boundary_mutator"))
    assert call == {
        "flow_id": flow_id,
        "role": "coordinator",
        "expected_agent_id": previous.agent_id,
        "replacement_mode": "fresh",
    }


def test_production_http_resets_content_plan_for_current_truth(tmp_path) -> None:
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
        flow_id, previous = _content_plan_boundary(runtime, repo_root)
        forbidden = client.post(
            f"/admin/repos/MainRepo/flows/{flow_id}/content-plan/reset-current-truth",
            json={"flow_id": flow_id, "expected_agent_id": previous.agent_id},
        )
        response = client.post(
            f"/admin/repos/MainRepo/flows/{flow_id}/content-plan/reset-current-truth",
            json={"expected_agent_id": previous.agent_id},
        )

    assert forbidden.status_code == 422
    assert "route-owned" in forbidden.json()["issues"][0]["message"]
    assert response.status_code == 200
    assert response.json()["value"]["previous_agent_id"] == previous.agent_id
    assert response.json()["value"]["current_phase"] == "plan_agent"
