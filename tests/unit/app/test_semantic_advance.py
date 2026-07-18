from __future__ import annotations

from pathlib import Path

import pytest
from agent_runtime_kit.flow import AgentStep, StepStatus
from pydantic import ValidationError

from lean_constellation.app import (
    LeanAdminApi,
    RuntimeSemanticAdvanceInput,
    StartFlowInput,
    create_app_runtime_services,
)


def _start_coordinator(admin: LeanAdminApi, repo_root: Path) -> str:
    result = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={"repo_key": "Repo", "repo_root": str(repo_root), "start_mode": "admin_start"},
        )
    )
    assert result.ok and result.value is not None
    return result.value.flow_id


def test_semantic_advance_input_has_strict_discriminated_shapes() -> None:
    assert RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo").action == "logic"
    assert RuntimeSemanticAdvanceInput(granularity="step", action="agent", step_id="s_1").action == "agent"
    assert RuntimeSemanticAdvanceInput(
        granularity="content_phase", action="plan", content_task_flow_id="f_1"
    ).action == "plan"
    assert RuntimeSemanticAdvanceInput(granularity="content_task", content_task_flow_id="f_1").action is None

    with pytest.raises(ValidationError, match="step.logic requires scope_id"):
        RuntimeSemanticAdvanceInput(granularity="step", action="logic")
    with pytest.raises(ValidationError, match="content_task semantic advance does not accept action"):
        RuntimeSemanticAdvanceInput(granularity="content_task", action="plan", content_task_flow_id="f_1")


def test_production_step_logic_runs_to_agent_boundary_and_auto_pauses(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_coordinator(admin, repo_root)
    assert admin.pause_runtime().ok

    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert started.ok and started.value is not None
    assert started.value.run_control is not None
    assert started.value.run_control.mode == "semantic"
    assert started.value.run_control.semantic_policy == "step.logic"

    tick = runtime.ark.schedule_service.schedule_ready()

    assert tick.auto_paused is True
    assert tick.advanced_flow_ids == [flow_id]
    assert tick.started_step_ids == []
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = runtime.ark.step_service.store.get_step(flow.current_step_id)
    assert isinstance(step, AgentStep)
    assert step.status is StepStatus.CREATED
    assert tick.run_control is not None
    assert tick.run_control.pause_reason == f"agent_step_created:{step.step_id}"


def test_semantic_advance_requires_global_pause_and_valid_target(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    _start_coordinator(admin, repo_root)

    unpaused = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert not unpaused.ok
    assert unpaused.issues[0].kind == "semantic_advance_requires_global_pause"

    assert admin.pause_runtime().ok
    invalid = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="content_task", content_task_flow_id="missing")
    )
    assert not invalid.ok
    assert invalid.issues[0].kind == "semantic_advance_failed"
    assert runtime.ark.pause_controller.is_paused(None)
