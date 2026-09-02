from __future__ import annotations

from agent_runtime_kit.flow import FlowRequest, FlowStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState
from starlette.testclient import TestClient

from lean_constellation.app import (
    LeanAdminApi,
    LeanAppConfig,
    SetAgentStepOperatorInstructionInput,
    create_app_runtime_services,
    create_production_app_server,
)
from lean_constellation.app.interface_docs import build_agent_tools_catalog
from lean_constellation.flows.common.agent_steps import ContentPlanAgentStep


def _current_content_plan_step(runtime, repo_root):  # noqa: ANN001, ANN202
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id="repo:MainRepo",
            params={
                "repo_key": "MainRepo",
                "repo_path": str(repo_root),
                "node_path": "Main.KeyLemma",
                "contract_version": 2,
            },
        ),
        enqueue=False,
    )

    def prepare_flow(flow) -> None:  # noqa: ANN001
        flow.status = FlowStatus.RUNNING
        flow.state.position.phase = "plan_agent"

    runtime.ark.flow_service.store.update_flow_record(flow_id, prepare_flow)
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
    runtime.ark.flow_service.store.create_step(step)

    def attach(target) -> None:  # noqa: ANN001
        target.step_ids.append(step.step_id)
        target.current_step_id = step.step_id

    runtime.ark.flow_service.store.update_flow_record(flow_id, attach)
    return flow_id, step.step_id


def _input(runtime, flow_id: str, step_id: str, instruction: str | None):  # noqa: ANN001, ANN202
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = runtime.ark.flow_service.get_step(step_id)
    return SetAgentStepOperatorInstructionInput(
        step_id=step_id,
        expected_step_updated_at=step.updated_at,
        expected_flow_updated_at=flow.updated_at,
        instruction=instruction,
    )


def test_admin_sets_and_clears_current_created_agent_step_operator_instruction(
    tmp_path,
) -> None:  # noqa: ANN001
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".runtime",
        start_paused=True,
    )
    flow_id, step_id = _current_content_plan_step(runtime, tmp_path / "MainRepo")
    flow_before = runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json")

    set_result = LeanAdminApi(runtime).set_agent_step_operator_instruction(
        _input(runtime, flow_id, step_id, "Inspect exact current sibling truth.")
    )

    assert set_result.ok and set_result.value is not None, set_result.issues
    assert set_result.value.instruction_present is True
    assert set_result.value.instruction_after == "Inspect exact current sibling truth."
    monitor = LeanAdminApi(runtime).get_step_monitor(step_id)
    assert monitor.ok and monitor.value is not None
    assert monitor.value.operator_instruction == "Inspect exact current sibling truth."
    control = LeanAdminApi(runtime).get_agent_step_control_view(step_id)
    assert control.ok and control.value is not None
    assert control.value.operator_instruction == "Inspect exact current sibling truth."
    assert runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json") == flow_before

    clear_result = LeanAdminApi(runtime).set_agent_step_operator_instruction(
        _input(runtime, flow_id, step_id, None)
    )

    assert clear_result.ok and clear_result.value is not None, clear_result.issues
    assert clear_result.value.instruction_present is False
    assert clear_result.value.instruction_after is None
    assert LeanAdminApi(runtime).get_step_monitor(step_id).value.operator_instruction is None


def test_admin_rejects_operator_instruction_boundary_drift_without_mutation(
    tmp_path,
) -> None:  # noqa: ANN001
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".runtime",
        start_paused=True,
    )
    flow_id, step_id = _current_content_plan_step(runtime, tmp_path / "MainRepo")
    input_model = _input(runtime, flow_id, step_id, "Inspect current truth.")
    before_flow = runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json")
    before_step = runtime.ark.flow_service.get_step(step_id).model_dump(mode="json")
    input_model.expected_flow_updated_at = "stale-flow-cas"

    result = LeanAdminApi(runtime).set_agent_step_operator_instruction(input_model)

    assert not result.ok
    assert result.issues[0].kind == "set_agent_step_operator_instruction_failed"
    assert runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json") == before_flow
    assert runtime.ark.flow_service.get_step(step_id).model_dump(mode="json") == before_step


def test_production_http_operator_instruction_is_route_owned_and_admin_only(
    tmp_path,
) -> None:  # noqa: ANN001
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
        flow_id, step_id = _current_content_plan_step(runtime, repo_root)
        flow = runtime.ark.flow_service.get_flow(flow_id)
        step = runtime.ark.flow_service.get_step(step_id)
        body = {
            "expected_step_updated_at": step.updated_at,
            "expected_flow_updated_at": flow.updated_at,
            "instruction": "Inspect current truth.",
        }
        forbidden = client.put(
            f"/admin/repos/MainRepo/steps/{step_id}/operator-instruction",
            json={"step_id": step_id, **body},
        )
        response = client.put(
            f"/admin/repos/MainRepo/steps/{step_id}/operator-instruction",
            json=body,
        )
        monitor = client.get(f"/admin/repos/MainRepo/steps/{step_id}")

    assert forbidden.status_code == 422
    assert "route-owned" in forbidden.json()["issues"][0]["message"]
    assert response.status_code == 200
    assert response.json()["value"]["instruction_present"] is True
    assert monitor.status_code == 200
    assert monitor.json()["value"]["operator_instruction"] == "Inspect current truth."
    agent_tools = build_agent_tools_catalog()
    assert all(
        "operator_instruction" not in str(item)
        and "operator-instruction" not in str(item)
        for item in agent_tools["tools"]
    )
