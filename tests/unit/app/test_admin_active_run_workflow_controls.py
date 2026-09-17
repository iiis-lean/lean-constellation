from __future__ import annotations

from agent_runtime_kit.flow.models import FlowRequest
from starlette.testclient import TestClient

from lean_constellation.app import (
    LeanAppConfig,
    LeanAdminApi,
    UpdateActiveRunWorkflowControlsInput,
    create_app_runtime_services,
    create_production_app_server,
    initialize_repo_business_truth,
)
from lean_constellation.domain.repo import RepoCompletionMode
from lean_constellation.domain.repo_run import (
    RepoRunContext,
    RepoRunSpec,
    RepoRunWorkflowControls,
    SourceScope,
)


def _start_coordinator(runtime, repo_root, controls: RepoRunWorkflowControls) -> str:  # noqa: ANN001
    run_context = RepoRunContext(
        start_kind="initial",
        run_spec=RepoRunSpec(
            run_objective="Reconstruct the complete source proof.",
            completion_mode=RepoCompletionMode.GRAPH_PROVED,
            source_scope=SourceScope(mode="all"),
            index_policy="reuse",
            root_interface_policy="reuse",
            max_parallel_content_node_tasks=4,
            workflow_controls=controls,
        ),
    )
    return runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id=f"repo:{repo_root.name}",
            params={
                "repo_key": repo_root.name,
                "repo_root": str(repo_root),
                "start_mode": "admin_start",
                "start_reason": "unit",
                "run_context": run_context.model_dump(mode="json"),
            },
        ),
        enqueue=False,
    )


def _request(runtime, repo_root, flow_id: str, target: RepoRunWorkflowControls):  # noqa: ANN001
    flow = runtime.ark.flow_service.get_flow(flow_id)
    return UpdateActiveRunWorkflowControlsInput(
        repo_root=repo_root,
        repo_key=repo_root.name,
        coordinator_flow_id=flow_id,
        expected_flow_updated_at=flow.updated_at,
        expected_workflow_controls=flow.input.run_context.run_spec.workflow_controls,
        workflow_controls=target,
        reason="Use the bounded reconstruction workflow for the remaining source coverage.",
    )


def _reconstruction_controls() -> RepoRunWorkflowControls:
    return RepoRunWorkflowControls(
        initial_repo_resource_discovery=False,
        initial_repo_lean_provider_discovery=False,
        initial_repo_mathlib_recon=True,
        content_node_dir_dependency_recon=True,
        content_mathlib_recon=True,
        content_resource_recon=False,
        statement_nl_review=False,
        statement_formal_review=True,
        proof_nl_review=True,
        proof_formal_review=False,
    )


def test_active_run_controls_update_only_future_content_tasks(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    repo_root = tmp_path / "Repo"
    assert initialize_repo_business_truth(runtime, repo_root).ok
    original = RepoRunWorkflowControls()
    coordinator_id = _start_coordinator(runtime, repo_root, original)
    existing_child_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id="repo:Repo",
            params={
                "repo_key": "Repo",
                "repo_path": str(repo_root),
                "node_path": "Main.Existing",
                "workflow_controls": original.model_dump(mode="json"),
            },
        ),
        parent_flow_id=coordinator_id,
        enqueue=False,
    )
    runtime.repo_activity.reserve_content_batch(
        repo_root,
        batch_id="existing-batch",
        node_paths=["Main.Existing"],
    )

    target = _reconstruction_controls()
    result = LeanAdminApi(runtime).update_active_run_workflow_controls(
        _request(runtime, repo_root, coordinator_id, target)
    )

    assert result.ok and result.value is not None
    assert result.value.workflow_controls_before == original
    assert result.value.workflow_controls_after == target
    assert result.value.applies_to == "future_content_tasks"
    coordinator = runtime.ark.flow_service.get_flow(coordinator_id)
    assert coordinator.input.run_context.run_spec.workflow_controls == target
    assert coordinator.input.run_context.config_change_summary is not None
    existing_child = runtime.ark.flow_service.get_flow(existing_child_id)
    assert existing_child.input.workflow_controls == original
    status = LeanAdminApi(runtime).get_repo_run_status(repo_root, repo_key="Repo")
    assert status.ok and status.value is not None and status.value.run_spec is not None
    assert status.value.run_spec.workflow_controls == target


def test_active_run_controls_reject_unpaused_or_busy_runtime(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=False)
    repo_root = tmp_path / "Repo"
    assert initialize_repo_business_truth(runtime, repo_root).ok
    coordinator_id = _start_coordinator(runtime, repo_root, RepoRunWorkflowControls())
    admin = LeanAdminApi(runtime)
    target = _reconstruction_controls()

    unpaused = admin.update_active_run_workflow_controls(
        _request(runtime, repo_root, coordinator_id, target)
    )
    assert not unpaused.ok
    assert unpaused.issues[0].kind == "active_run_workflow_control_update_failed"
    assert "globally paused" in unpaused.issues[0].message

    runtime.ark.pause_controller.pause(None)
    runtime.ark.schedule_service.active_flow_advances.add(coordinator_id)
    busy = admin.update_active_run_workflow_controls(
        _request(runtime, repo_root, coordinator_id, target)
    )
    assert not busy.ok
    assert "zero active Flow advances" in busy.issues[0].message


def test_active_run_controls_reject_stale_expected_identity(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime", start_paused=True)
    repo_root = tmp_path / "Repo"
    assert initialize_repo_business_truth(runtime, repo_root).ok
    coordinator_id = _start_coordinator(runtime, repo_root, RepoRunWorkflowControls())
    request = _request(runtime, repo_root, coordinator_id, _reconstruction_controls())
    request = request.model_copy(update={"expected_flow_updated_at": "stale"})

    result = LeanAdminApi(runtime).update_active_run_workflow_controls(request)

    assert not result.ok
    assert result.issues[0].kind == "active_run_workflow_control_update_failed"
    assert "updated_at changed" in result.issues[0].message
    flow = runtime.ark.flow_service.get_flow(coordinator_id)
    assert flow.input.run_context.run_spec.workflow_controls == RepoRunWorkflowControls()


def test_active_run_controls_http_route_binds_repo_identity(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Repo"
    (repo_root / ".lean_constellation").mkdir(parents=True)
    app_result = create_production_app_server(
        LeanAppConfig(
            workspace_root=workspace,
            scheduler_enabled=False,
            materialize_agent_homes=False,
            server_start_paused=True,
        )
    )
    assert app_result.ok and app_result.value is not None

    with TestClient(app_result.value) as client:
        assert client.post("/admin/workspace/repos/Repo/load").status_code == 200
        runtime = app_result.value.state.lean_constellation_registry.try_get_loaded("Repo")
        assert runtime is not None
        assert initialize_repo_business_truth(runtime, repo_root).ok
        coordinator_id = _start_coordinator(runtime, repo_root, RepoRunWorkflowControls())
        request = _request(runtime, repo_root, coordinator_id, _reconstruction_controls())
        response = client.patch(
            "/admin/repos/Repo/run/workflow-controls",
            json=request.model_dump(
                mode="json",
                exclude={"repo_root", "repo_key"},
            ),
        )

    assert response.status_code == 200
    assert response.json()["value"]["coordinator_flow_id"] == coordinator_id
    assert response.json()["value"]["workflow_controls_after"]["proof_formal_review"] is False
