from __future__ import annotations

from pathlib import Path

import pytest
from agent_runtime_kit.agent.provider_contracts import ProviderHomeSpec
from agent_runtime_kit.flow import AgentStep, FlowStatus, StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState
from pydantic import ValidationError

from lean_constellation.app import (
    LeanAdminApi,
    RuntimeSemanticAdvanceInput,
    StartFlowInput,
    create_app_runtime_services,
)
from lean_constellation.flows.common.agent_steps import RepoFormatDiscoveryAgentStep


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


def test_production_step_logic_reports_flow_terminal_before_idle_fallback(tmp_path: Path) -> None:
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
    policy = runtime.ark.schedule_service._semantic_policy  # noqa: SLF001 - semantic policy fixture.
    assert policy is not None
    with runtime.ark.flow_service.store.edit_session("repo:Repo") as tx:
        flow = tx.load_flow_for_update(flow_id)
        flow.status = FlowStatus.COMPLETED
        flow.current_step_id = None

    decision = policy.decide(runtime.ark.schedule_service)

    assert decision.action == "pause"
    assert decision.reason == f"flow_terminal:{flow_id}"


def test_runtime_lease_monitor_classifies_no_runnable_completed_flow_as_handoff(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_coordinator(admin, repo_root)
    assert admin.pause_runtime().ok
    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert started.ok and started.value is not None and started.value.lease_id is not None
    with runtime.ark.flow_service.store.edit_session("repo:Repo") as tx:
        flow = tx.load_flow_for_update(flow_id)
        flow.status = FlowStatus.COMPLETED
        flow.current_step_id = None
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - scheduler lease fixture.
            status="terminal",
            terminal_reason="no_runnable_candidate",
            advanced_flow_ids=[flow_id],
        )

    view = admin.get_runtime_lease(started.value.lease_id)

    assert view.ok and view.value is not None
    assert view.value.terminal_disposition == "cross_flow_handoff"
    assert view.value.requires_review is False
    assert view.value.suggested_next_action == "inspect_flow_result_and_start_next_lifecycle_entry"


def test_runtime_lease_monitor_keeps_unexplained_no_runnable_reviewable(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    _start_coordinator(admin, repo_root)
    assert admin.pause_runtime().ok
    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert started.ok and started.value is not None and started.value.lease_id is not None
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - scheduler lease fixture.
            status="terminal",
            terminal_reason="no_runnable_candidate",
        )

    view = admin.get_runtime_lease(started.value.lease_id)

    assert view.ok and view.value is not None
    assert view.value.terminal_disposition == "review_required"
    assert view.value.requires_review is True
    assert view.value.suggested_next_action == "audit_candidates_before_next_admission"


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


def test_runtime_lease_monitor_keeps_its_semantic_content_target(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)

    flow_ids = []
    for node_path in ("Main.First", "Main.Second"):
        started = admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="content_node_task",
                scope_id=f"repo:Repo:node:{node_path}",
                params={
                    "repo_key": "Repo",
                    "repo_path": str(repo_root),
                    "node_path": node_path,
                    "contract_version": 1,
                },
            )
        )
        assert started.ok and started.value is not None
        flow_ids.append(started.value.flow_id)

    assert admin.pause_runtime().ok
    first = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_phase",
            action="plan",
            content_task_flow_id=flow_ids[0],
        )
    )
    assert first.ok and first.value is not None and first.value.lease_id is not None
    runtime.ark.schedule_service.clear_run_budget(reason="test_terminal")
    runtime.ark.pause_controller.pause(None)
    second = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_phase",
            action="plan",
            content_task_flow_id=flow_ids[1],
        )
    )
    assert second.ok and second.value is not None

    first_lease = admin.get_runtime_lease(first.value.lease_id)

    assert first_lease.ok and first_lease.value is not None
    assert first_lease.value.current_content_task_flow_id == flow_ids[0]
    assert first_lease.value.current_content_task_phase == "admission"


def test_runtime_lease_monitor_does_not_borrow_running_agent_from_newer_lease(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    flow_ids = []
    for node_path in ("Main.Old", "Main.New"):
        started = admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="content_node_task",
                scope_id=f"repo:Repo:node:{node_path}",
                params={
                    "repo_key": "Repo",
                    "repo_path": str(repo_root),
                    "node_path": node_path,
                    "contract_version": 1,
                },
            )
        )
        assert started.ok and started.value is not None
        flow_ids.append(started.value.flow_id)

    agent_service = runtime.ark.agent_service
    agent_service.home_service.create_home(
        ProviderHomeSpec(provider_type="codex", home_id="RepoFormatDiscoveryAgent")
    )
    agents = [
        agent_service.create_agent(
            f"repo:Repo:node:{node_path}",
            "RepoFormatDiscoveryAgent",
            home_id="RepoFormatDiscoveryAgent",
        )
        for node_path in ("Main.Old", "Main.New")
    ]
    step_ids = []
    for index, (flow_id, agent) in enumerate(zip(flow_ids, agents, strict=True)):
        step = RepoFormatDiscoveryAgentStep(
            step_id=f"lease-agent-step-{index}",
            flow_id=flow_id,
            scope_id=agent.scope_id,
            state=AgentStepState(
                agent_role="repo_format_discovery",
                agent_type="RepoFormatDiscoveryAgent",
                home_id="RepoFormatDiscoveryAgent",
                create_agent_if_missing=False,
            ),
        )
        step.agent_bindings.by_role["repo_format_discovery"] = agent.agent_id
        runtime.ark.step_service.create_step(step, enqueue=False)
        step_ids.append(step.step_id)

    assert admin.pause_runtime().ok
    first = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="agent", step_id=step_ids[0])
    )
    assert first.ok and first.value is not None and first.value.lease_id is not None
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - scheduler lease fixture.
            started_step_ids=[step_ids[0]]
        )
    runtime.ark.schedule_service.clear_run_budget(reason="first_lease_terminal")

    runtime.ark.pause_controller.pause(None)
    second = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="agent", step_id=step_ids[1])
    )
    assert second.ok and second.value is not None and second.value.lease_id is not None
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - scheduler lease fixture.
            started_step_ids=[step_ids[1]]
        )
    agent_service.store.patch_agent(agents[1].agent_id, status="running")

    old_view = admin.get_runtime_lease(first.value.lease_id)
    new_view = admin.get_runtime_lease(second.value.lease_id)

    assert old_view.ok and old_view.value is not None
    assert old_view.value.current_agent_id is None
    assert [step.step_id for step in old_view.value.started_steps] == [step_ids[0]]
    assert new_view.ok and new_view.value is not None
    assert new_view.value.current_agent_id == agents[1].agent_id
    assert [step.step_id for step in new_view.value.started_steps] == [step_ids[1]]
