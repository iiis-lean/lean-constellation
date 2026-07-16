from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime_kit.flow import SchedulerRunBudget
from lean_constellation.app import (
    LeanAdminApi,
    RuntimeResumeInput,
    StartFlowInput,
    create_app_runtime_services,
)


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


def test_runtime_resume_input_rejects_empty_budget_scope_and_skip_rebuild_conflicts() -> None:
    with pytest.raises(ValidationError, match="at least one action"):
        RuntimeResumeInput(budget={"flow_advances": 0, "step_starts": 0})
    with pytest.raises(ValidationError, match="repo-global"):
        RuntimeResumeInput(
            scope_id="repo:Repo",
            budget={"flow_advances": 1, "step_starts": 0},
        )
    with pytest.raises(ValidationError, match="requires candidate queue rebuild"):
        RuntimeResumeInput(
            budget={"flow_advances": 0, "step_starts": 1},
            skip_rebuild=True,
        )


def test_admin_bounded_resume_is_available_without_test_control_and_reports_auto_pause(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    assert runtime.test_control_enabled is False
    assert admin.pause_runtime().ok

    resumed = admin.resume_runtime(
        RuntimeResumeInput(budget=SchedulerRunBudget(flow_advances=0, step_starts=1))
    )

    assert resumed.ok and resumed.value is not None
    assert resumed.value.paused is False
    assert resumed.value.run_control is not None
    assert resumed.value.run_control.mode == "bounded"
    tick = runtime.ark.schedule_service.schedule_ready()
    assert tick.auto_paused is True
    status = admin.get_runtime_status()
    assert status.ok and status.value is not None
    assert status.value.paused is True
    assert status.value.run_control is not None
    assert status.value.run_control.pause_reason == "no_runnable_candidate"
    assert status.value.run_control.remaining_step_starts == 1


def test_admin_bounded_resume_requires_global_pause_without_mutation(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    before = runtime.ark.schedule_service.get_run_control_view()

    resumed = admin.resume_runtime(
        RuntimeResumeInput(budget=SchedulerRunBudget(flow_advances=1, step_starts=0))
    )

    assert not resumed.ok
    assert resumed.issues[0].kind == "bounded_resume_requires_global_pause"
    assert runtime.ark.schedule_service.get_run_control_view() == before


def test_admin_manual_pause_cancels_active_budget_and_unbounded_resume_clears_evidence(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    assert admin.pause_runtime().ok
    assert admin.resume_runtime(
        RuntimeResumeInput(budget=SchedulerRunBudget(flow_advances=2, step_starts=1))
    ).ok

    paused = admin.pause_runtime()

    assert paused.ok and paused.value is not None
    assert paused.value.run_control is not None
    assert paused.value.run_control.pause_reason == "manual_pause"
    assert paused.value.run_control.remaining_flow_advances == 2
    resumed = admin.resume_runtime()
    assert resumed.ok and resumed.value is not None
    assert resumed.value.run_control is not None
    assert resumed.value.run_control.mode == "unbounded"
    assert resumed.value.run_control.requested_flow_advances is None
