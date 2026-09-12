from __future__ import annotations

from types import SimpleNamespace

from agent_runtime_kit.agent.models import AgentContextMaintenanceBlocked
from agent_runtime_kit.flow import (
    AgentStep,
    AgentStepState,
    BaseFlowError,
    BaseStepError,
    FlowRequest,
    FlowStatus,
    StepStatus,
)
from agent_runtime_kit.agent.provider_contracts import ProviderRunState
from starlette.testclient import TestClient

from lean_constellation.app import (
    LeanAdminApi,
    LeanAppConfig,
    ReconcileAgentStepContextMaintenanceInput,
    RecoverAgentStepInput,
    create_app_runtime_services,
    create_production_app_server,
)
from lean_constellation.flows.common.agent_steps import (
    BUSINESS_AGENT_STEP_TYPES,
    ContentPlanAgentStep,
)
from lean_constellation.flows.content_node_task.submissions import ContentNodeReadySubmission
from lean_constellation.flows.repo_exploration.steps import REPO_EXPLORATION_AGENT_STEP_TYPES

from tests.unit.app.test_admin_content_plan_agent_recovery import _content_plan_boundary


def _lost_content_plan_step(runtime, flow_id: str, agent_id: str, *, submission=None) -> str:
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = ContentPlanAgentStep(
        step_id="lost-content-plan-step",
        flow_id=flow_id,
        scope_id=flow.scope_id,
        status=StepStatus.RUNNING,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            home_id="ContentPlanAgent",
        ),
        submission=submission,
    )
    step.agent_bindings.by_role["content_plan"] = agent_id
    runtime.ark.flow_service.store.create_step(step)

    def attach(target) -> None:
        target.step_ids.append(step.step_id)
        target.current_step_id = step.step_id

    runtime.ark.flow_service.store.update_flow_record(flow_id, attach)
    return step.step_id


def _failed_content_plan_step(runtime, flow_id: str, agent_id: str) -> str:
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = ContentPlanAgentStep(
        step_id="failed-content-plan-step",
        flow_id=flow_id,
        scope_id=flow.scope_id,
        status=StepStatus.FAILED,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            home_id="ContentPlanAgent",
        ),
        error=BaseStepError(error_type="step_run_exception", message="provider stopped"),
    )
    step.agent_bindings.by_role["content_plan"] = agent_id
    runtime.ark.flow_service.store.create_step(step)

    def attach(target) -> None:
        target.step_ids.append(step.step_id)
        target.current_step_id = None
        target.status = FlowStatus.FAILED
        target.error = BaseFlowError(error_type="step_failed", message="provider stopped")

    runtime.ark.flow_service.store.update_flow_record(flow_id, attach)
    return step.step_id


def _suspended_content_plan_step(runtime, flow_id: str, agent_id: str) -> str:
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = ContentPlanAgentStep(
        step_id="suspended-content-plan-step",
        flow_id=flow_id,
        scope_id=flow.scope_id,
        status=StepStatus.SUSPENDED,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            provider_type="codex",
            home_id="ContentPlanAgent",
        ),
        error=BaseStepError(
            error_type="agent_provider_turn_failed",
            message="Agent provider execution suspended before business completion.",
            code="502",
            details={
                "provider_type": "codex",
                "provider_error_type": "provider_rate_limit",
                "retryable": True,
                "operator_action_required": False,
                "run_id": "run-safe",
                "session_id": "session-safe",
                "turn_id": "turn-safe",
            },
        ),
    )
    step.agent_bindings.by_role["content_plan"] = agent_id
    runtime.ark.flow_service.store.create_step(step)

    def attach(target) -> None:
        target.step_ids.append(step.step_id)
        target.current_step_id = step.step_id
        target.status = FlowStatus.RUNNING
        target.error = None

    runtime.ark.flow_service.store.update_flow_record(flow_id, attach)
    return step.step_id


def test_inspect_agent_step_recovery_wraps_ark_view_without_mutation(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    step_id = _lost_content_plan_step(runtime, flow_id, agent.agent_id)
    before_flow = runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json")
    before_step = runtime.ark.flow_service.get_step(step_id).model_dump(mode="json")

    result = LeanAdminApi(runtime).inspect_agent_step_recovery(step_id)

    assert result.ok and result.value is not None, result.issues
    assert result.value.recovery.step_id == step_id
    assert result.value.recovery.runner_state == "lost"
    assert result.value.recovery.available_actions == ["restart", "settle_runner_lost"]
    assert result.value.decl_graph_round_gate is None
    assert runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json") == before_flow
    assert runtime.ark.flow_service.get_step(step_id).model_dump(mode="json") == before_step


def test_inspect_agent_step_recovery_includes_sanitized_context_maintenance(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    step_id = _suspended_content_plan_step(runtime, flow_id, agent.agent_id)
    monkeypatch.setattr(
        runtime.ark.flow_service,
        "inspect_agent_step_context_maintenance",
        lambda target_step_id: SimpleNamespace(
            agent_id=agent.agent_id,
            provider_type="codex",
            session_id="session-safe",
            status="unknown_terminal",
            unresolved=True,
            reconciliation_token="a" * 64,
            baseline={"secret": "must-not-be-exposed"},
        )
        if target_step_id == step_id
        else None,
    )

    result = LeanAdminApi(runtime).inspect_agent_step_recovery(step_id)

    assert result.ok and result.value is not None, result.issues
    maintenance = result.value.context_maintenance
    assert maintenance is not None
    assert maintenance.agent_id == agent.agent_id
    assert maintenance.status == "unknown_terminal"
    assert maintenance.unresolved is True
    assert maintenance.reconciliation_token == "a" * 64
    assert "baseline" not in maintenance.model_dump(mode="json")


def test_admin_reconciles_context_maintenance_without_resuming_step(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    step_id = _suspended_content_plan_step(runtime, flow_id, agent.agent_id)
    before_step = runtime.ark.flow_service.get_step(step_id).model_dump(mode="json")
    before_flow = runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json")
    calls: list[tuple[str, str]] = []

    def reconcile(*, step_id: str, expected_reconciliation_token: str):
        calls.append((step_id, expected_reconciliation_token))
        return SimpleNamespace(
            agent_id=agent.agent_id,
            provider_type="codex",
            session_id="session-safe",
            status="confirmed",
            unresolved=False,
            reconciliation_token="b" * 64,
        )

    monkeypatch.setattr(
        runtime.ark.flow_service,
        "reconcile_agent_step_context_maintenance",
        reconcile,
    )

    result = LeanAdminApi(runtime).reconcile_agent_step_context_maintenance(
        ReconcileAgentStepContextMaintenanceInput(
            step_id=step_id,
            expected_context_maintenance_token="a" * 64,
        )
    )

    assert result.ok and result.value is not None, result.issues
    assert calls == [(step_id, "a" * 64)]
    assert result.value.step_id == step_id
    assert result.value.agent_id == agent.agent_id
    assert result.value.status == "confirmed"
    assert result.value.unresolved is False
    assert result.value.next_action == "resume_suspended"
    assert runtime.ark.flow_service.get_step(step_id).model_dump(mode="json") == before_step
    assert runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json") == before_flow


def test_admin_reports_context_reconciliation_still_unresolved_without_resuming(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    step_id = _suspended_content_plan_step(runtime, flow_id, agent.agent_id)
    before_step = runtime.ark.flow_service.get_step(step_id).model_dump(mode="json")
    before_flow = runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json")
    monkeypatch.setattr(
        runtime.ark.flow_service,
        "reconcile_agent_step_context_maintenance",
        lambda **_kwargs: SimpleNamespace(
            agent_id=agent.agent_id,
            provider_type="codex",
            session_id="session-safe",
            status="unknown_terminal",
            unresolved=True,
            reconciliation_token="a" * 64,
        ),
    )

    result = LeanAdminApi(runtime).reconcile_agent_step_context_maintenance(
        ReconcileAgentStepContextMaintenanceInput(
            step_id=step_id,
            expected_context_maintenance_token="a" * 64,
        )
    )

    assert result.ok is False
    assert [issue.kind for issue in result.issues] == [
        "agent_context_maintenance_reconciliation_required"
    ]
    assert runtime.ark.flow_service.get_step(step_id).model_dump(mode="json") == before_step
    assert runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json") == before_flow


def test_admin_sanitizes_unconfirmed_context_reconciliation_error(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    step_id = _suspended_content_plan_step(runtime, flow_id, agent.agent_id)

    def unconfirmed(**_kwargs):
        raise AgentContextMaintenanceBlocked("raw-provider-secret")

    monkeypatch.setattr(
        runtime.ark.flow_service,
        "reconcile_agent_step_context_maintenance",
        unconfirmed,
    )

    result = LeanAdminApi(runtime).reconcile_agent_step_context_maintenance(
        ReconcileAgentStepContextMaintenanceInput(
            step_id=step_id,
            expected_context_maintenance_token="a" * 64,
        )
    )

    assert result.ok is False
    assert result.issues[0].kind == "agent_context_maintenance_reconciliation_required"
    assert "raw-provider-secret" not in result.issues[0].message


def test_admin_surfaces_suspended_provider_boundary_and_fresh_resume(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    step_id = _suspended_content_plan_step(runtime, flow_id, agent.agent_id)
    source_preimage = runtime.ark.flow_service.get_step(step_id).model_dump(mode="json")
    admin = LeanAdminApi(runtime)

    step_view = admin.get_step_monitor(step_id)
    waited = admin.wait_step_terminal(step_id, timeout_s=0)
    tree = admin.list_flow_tree(scope_id=runtime.ark.flow_service.get_flow(flow_id).scope_id)
    preview = admin.inspect_agent_step_recovery(step_id)

    assert step_view.ok and step_view.value is not None
    assert step_view.value.status == "suspended"
    assert step_view.value.provider_type == "codex"
    assert step_view.value.provider_error_type == "provider_rate_limit"
    assert step_view.value.provider_retryable is True
    assert step_view.value.operator_action_required is False
    assert step_view.value.available_recovery_actions == ["resume_suspended"]
    assert waited.ok and waited.value is not None
    assert waited.value.terminal is False
    assert waited.value.timed_out is False
    assert waited.value.runner_state == "settled"
    assert waited.value.step.status == "suspended"
    assert tree.ok and tree.value is not None
    flow_nodes = [node["flow"] for node in tree.value.roots]
    current = next(node for node in flow_nodes if node.flow_id == flow_id)
    tree_step = next(step for step in current.steps if step.step_id == step_id)
    assert tree_step.status == "suspended"
    assert tree_step.provider_error_type == "provider_rate_limit"
    assert tree_step.available_recovery_actions == ["resume_suspended"]
    assert preview.ok and preview.value is not None

    recovered = admin.recover_agent_step(
        RecoverAgentStepInput(
            step_id=step_id,
            expected_status="suspended",
            expected_recovery_token=preview.value.recovery.recovery_token,
            action="resume_suspended",
            agent_mode="fresh",
        )
    )

    assert recovered.ok and recovered.value is not None, recovered.issues
    assert recovered.value.replacement_step_id is not None
    assert recovered.value.replacement_agent_id != agent.agent_id
    assert runtime.ark.flow_service.get_step(step_id).model_dump(mode="json") == source_preimage
    replacement = runtime.ark.flow_service.get_step(recovered.value.replacement_step_id)
    assert replacement.status is StepStatus.CREATED
    assert replacement.state.restart_of_step_id == step_id


def test_production_health_reports_suspended_steps(tmp_path) -> None:
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
        flow_id, agent = _content_plan_boundary(runtime, repo_root)
        step_id = _suspended_content_plan_step(runtime, flow_id, agent.agent_id)
        health = client.get("/health")

    assert health.status_code == 200
    repo_status = health.json()["repo_runtimes"]["repos"][0]
    assert repo_status["suspended_step_count"] == 1
    assert repo_status["suspended_step_ids"] == [step_id]


def test_registered_lc_agent_steps_explicitly_opt_in_to_offline_submission_finalize() -> None:
    assert AgentStep.offline_submission_finalize_supported is False
    registered = (*BUSINESS_AGENT_STEP_TYPES, *REPO_EXPLORATION_AGENT_STEP_TYPES)
    assert registered
    for step_cls in registered:
        assert step_cls.__dict__.get("offline_submission_finalize_supported") is True, step_cls.step_type


def test_admin_finalizes_lost_content_plan_submission_once(tmp_path, monkeypatch) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    submission = ContentNodeReadySubmission(
        submission_id="content-ready",
        tool_name="submit_content_node_ready",
        submitted_by_agent_id=agent.agent_id,
        repo_key="MainRepo",
        node_path="Main.KeyLemma",
        summary="Current node proof is ready.",
    )
    step_id = _lost_content_plan_step(
        runtime,
        flow_id,
        agent.agent_id,
        submission=submission,
    )
    provider_result = SimpleNamespace(
        status=ProviderRunState.COMPLETED,
        error=None,
        run_id="content-plan-final",
        started_at="2026-09-02T00:00:00Z",
        completed_at="2026-09-02T00:00:01Z",
        session_locator=None,
        turn_locator=None,
    )
    monkeypatch.setattr(
        runtime.ark.agent_service,
        "query_turn",
        lambda *_args, **_kwargs: SimpleNamespace(result=provider_result, locator=None),
    )
    flow_cls = type(runtime.ark.flow_service.get_flow(flow_id))
    original_hook = flow_cls.after_step_terminal_stable
    stable_calls: list[str] = []

    def record_stable(self, ctx):  # noqa: ANN001
        stable_calls.append(ctx.step.step_id)
        return original_hook(self, ctx)

    monkeypatch.setattr(flow_cls, "after_step_terminal_stable", record_stable)
    preview = LeanAdminApi(runtime).inspect_agent_step_recovery(step_id)
    assert preview.ok and preview.value is not None, preview.issues
    assert preview.value.recovery.available_actions == ["finalize_submission"]

    result = LeanAdminApi(runtime).recover_agent_step(
        RecoverAgentStepInput(
            step_id=step_id,
            expected_status="running",
            expected_recovery_token=preview.value.recovery.recovery_token,
            action="finalize_submission",
        )
    )

    assert result.ok and result.value is not None, result.issues
    assert result.value.replacement_step_id is None
    assert result.value.replacement_agent_id is None
    assert result.value.submission_disposition == "accepted_finalized"
    completed = runtime.ark.flow_service.get_step(step_id)
    assert completed.status is StepStatus.COMPLETED
    assert completed.submission == submission
    assert completed.result is not None and completed.result.result_type == "content_plan"
    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.current_step_id is None
    assert flow.state.position.phase == "completion_audit"
    assert stable_calls == [step_id]

    repeated = LeanAdminApi(runtime).recover_agent_step(
        RecoverAgentStepInput(
            step_id=step_id,
            expected_status="running",
            expected_recovery_token=preview.value.recovery.recovery_token,
            action="finalize_submission",
        )
    )
    assert repeated.ok is False
    assert stable_calls == [step_id]


def test_admin_reports_typed_finalize_unavailable_without_mutation(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    submission = ContentNodeReadySubmission(
        submission_id="content-ready",
        tool_name="submit_content_node_ready",
        submitted_by_agent_id=agent.agent_id,
        repo_key="MainRepo",
        node_path="Main.KeyLemma",
    )
    step_id = _lost_content_plan_step(
        runtime,
        flow_id,
        agent.agent_id,
        submission=submission,
    )
    preview = LeanAdminApi(runtime).inspect_agent_step_recovery(step_id)
    assert preview.ok and preview.value is not None, preview.issues
    flow_before = runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json")
    step_before = runtime.ark.flow_service.get_step(step_id).model_dump(mode="json")

    result = LeanAdminApi(runtime).recover_agent_step(
        RecoverAgentStepInput(
            step_id=step_id,
            expected_status="running",
            expected_recovery_token=preview.value.recovery.recovery_token,
            action="finalize_submission",
        )
    )

    assert result.ok is False
    assert [issue.kind for issue in result.issues] == [
        "lost_step_submission_finalize_unavailable"
    ]
    assert runtime.ark.flow_service.get_flow(flow_id).model_dump(mode="json") == flow_before
    assert runtime.ark.flow_service.get_step(step_id).model_dump(mode="json") == step_before


def test_inspect_agent_step_recovery_reports_decl_graph_round_gate_read_only(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    repo_root = tmp_path / "MainRepo"
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="decl_graph_round",
            scope_id="repo:MainRepo",
            params={
                "repo_key": "MainRepo",
                "repo_path": str(repo_root),
                "node_path": "Main.Topic.Core",
                "strategy_id": "strategy-1",
                "round_id": "round-1",
            },
        ),
        enqueue=False,
    )
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = ContentPlanAgentStep(
        step_id="failed-round-agent-step",
        flow_id=flow_id,
        scope_id=flow.scope_id,
        status=StepStatus.FAILED,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            home_id="ContentPlanAgent",
        ),
        error=BaseStepError(error_type="step_run_exception", message="provider stopped"),
    )
    runtime.ark.flow_service.store.create_step(step)

    def mark_failed(target) -> None:
        target.step_ids.append(step.step_id)
        target.current_step_id = None
        target.status = FlowStatus.FAILED
        target.error = BaseFlowError(error_type="round_step_failed", message="provider stopped")

    runtime.ark.flow_service.store.update_flow_record(flow_id, mark_failed)

    result = LeanAdminApi(runtime).inspect_agent_step_recovery(step.step_id)

    assert result.ok and result.value is not None, result.issues
    gate = result.value.decl_graph_round_gate
    assert gate is not None
    assert gate.round_id == "round-1"
    assert gate.node_path == "Main.Topic.Core"
    assert gate.eligible is False
    assert gate.issue_kinds
    assert runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.FAILED


def test_production_http_gets_agent_step_recovery(tmp_path) -> None:
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
        flow_id, agent = _content_plan_boundary(runtime, repo_root)
        step_id = _lost_content_plan_step(runtime, flow_id, agent.agent_id)
        response = client.get(f"/admin/repos/MainRepo/steps/{step_id}/recovery")

    assert response.status_code == 200
    payload = response.json()["value"]
    assert payload["recovery"]["step_id"] == step_id
    assert payload["recovery"]["runner_state"] == "lost"
    assert payload["decl_graph_round_gate"] is None


def test_recover_agent_step_restarts_failed_step_with_explicit_mode(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    flow_id, agent = _content_plan_boundary(runtime, tmp_path / "MainRepo")
    step_id = _failed_content_plan_step(runtime, flow_id, agent.agent_id)
    recovery = LeanAdminApi(runtime).inspect_agent_step_recovery(step_id)
    assert recovery.ok and recovery.value is not None

    result = LeanAdminApi(runtime).recover_agent_step(
        RecoverAgentStepInput(
            step_id=step_id,
            expected_status="failed",
            expected_recovery_token=recovery.value.recovery.recovery_token,
            action="restart",
            agent_mode="fresh",
        )
    )

    assert result.ok and result.value is not None, result.issues
    assert result.value.source_step_id == step_id
    assert result.value.action == "restart"
    assert result.value.agent_mode == "fresh"
    assert result.value.previous_agent_id == agent.agent_id
    assert result.value.replacement_agent_id != agent.agent_id
    assert result.value.reopened_round_id is None
    assert result.value.submission_disposition == "not_present"
    assert result.value.flow_status_before == "failed"
    assert result.value.flow_current_step_id_before is None
    recovered_flow = runtime.ark.flow_service.get_flow(flow_id)
    assert result.value.flow_status_after == "running"
    assert result.value.flow_current_step_id_after == result.value.replacement_step_id
    assert result.value.flow_updated_at_after == recovered_flow.updated_at
    assert recovered_flow.current_step_id == result.value.replacement_step_id


def test_production_http_recovers_agent_step_with_route_owned_step_id(tmp_path) -> None:
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
        flow_id, agent = _content_plan_boundary(runtime, repo_root)
        step_id = _failed_content_plan_step(runtime, flow_id, agent.agent_id)
        preview = client.get(f"/admin/repos/MainRepo/steps/{step_id}/recovery").json()["value"]
        body = {
            "expected_status": "failed",
            "expected_recovery_token": preview["recovery"]["recovery_token"],
            "action": "restart",
            "agent_mode": "reuse",
        }
        forbidden = client.post(
            f"/admin/repos/MainRepo/steps/{step_id}/recover",
            json={**body, "step_id": "other"},
        )
        response = client.post(
            f"/admin/repos/MainRepo/steps/{step_id}/recover",
            json=body,
        )

    assert forbidden.status_code == 422
    assert "route-owned" in forbidden.json()["issues"][0]["message"]
    assert response.status_code == 200
    assert response.json()["value"]["source_step_id"] == step_id


def test_production_http_reconciles_context_maintenance_with_route_owned_step_id(
    tmp_path,
    monkeypatch,
) -> None:
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
        flow_id, agent = _content_plan_boundary(runtime, repo_root)
        step_id = _suspended_content_plan_step(runtime, flow_id, agent.agent_id)

        def reconcile(*, step_id: str, expected_reconciliation_token: str):
            assert expected_reconciliation_token == "a" * 64
            return SimpleNamespace(
                agent_id=agent.agent_id,
                provider_type="codex",
                session_id="session-safe",
                status="confirmed",
                unresolved=False,
                reconciliation_token="b" * 64,
            )

        monkeypatch.setattr(
            runtime.ark.flow_service,
            "reconcile_agent_step_context_maintenance",
            reconcile,
        )
        body = {"expected_context_maintenance_token": "a" * 64}
        forbidden = client.post(
            f"/admin/repos/MainRepo/steps/{step_id}/context-maintenance/reconcile",
            json={**body, "step_id": "other"},
        )
        response = client.post(
            f"/admin/repos/MainRepo/steps/{step_id}/context-maintenance/reconcile",
            json=body,
        )

    assert forbidden.status_code == 422
    assert "route-owned" in forbidden.json()["issues"][0]["message"]
    assert response.status_code == 200
    value = response.json()["value"]
    assert value["step_id"] == step_id
    assert value["status"] == "confirmed"
    assert value["next_action"] == "resume_suspended"
