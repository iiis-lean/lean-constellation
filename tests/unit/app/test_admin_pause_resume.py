from __future__ import annotations

from lean_constellation.app import LeanAdminApi, StartFlowInput, create_app_runtime_services


def test_admin_pause_resume_uses_runtime_pause_controller_without_mutating_flow_state(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    started = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={"repo_key": "Repo", "repo_root": str(tmp_path / "Repo"), "start_mode": "admin_start"},
        )
    )
    assert started.ok and started.value is not None
    flow = runtime.ark.flow_service.get_flow(started.value.flow_id)
    phase_before = flow.state.position.phase

    paused = admin.pause_runtime()
    assert paused.ok and paused.value is not None
    assert runtime.ark.pause_controller.is_paused()
    assert runtime.ark.flow_service.can_advance_flow(started.value.flow_id) is False

    resumed = admin.resume_runtime()
    assert resumed.ok and resumed.value is not None
    assert runtime.ark.pause_controller.is_paused() is False
    assert runtime.ark.flow_service.can_advance_flow(started.value.flow_id) is True
    assert runtime.ark.flow_service.get_flow(started.value.flow_id).state.position.phase == phase_before
