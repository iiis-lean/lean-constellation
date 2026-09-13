from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from agent_runtime_kit.flow.models import BaseStepError, FlowRequest, FlowStatus, StepStatus, utc_now_iso
from agent_runtime_kit.flow.standard_steps import AgentStepState
from agent_runtime_kit.runtime import RuntimePauseController

from lean_constellation.app import LeanAdminApi, RecoverAgentStepInput
from lean_constellation.flows.common.agent_steps import ContentPlanAgentStep
from tests.unit.flows.decl_round._helpers import (
    advance_and_run,
    create_round_with_decl,
    make_decl_round_runtime,
)


class _RecoverySchedule:
    def __init__(self) -> None:
        self.step_ids: list[str] = []
        self.flow_ids: list[str] = []
        self.active_flow_advances: set[str] = set()

    def enqueue_step(self, step_id: str) -> None:
        self.step_ids.append(step_id)

    def enqueue_flow(self, flow_id: str) -> None:
        self.flow_ids.append(flow_id)


@dataclass
class _SharedStageBoundary:
    runtime: Any
    lean_runtime: Any
    repo_root: Path
    parent_flow_id: str
    round_flow_id: str
    suspended_step_id: str
    role: str
    previous_agent: Any
    schedule: _RecoverySchedule


def _start_content_task_parent(runtime, repo_root: Path, *, role: str, agent_id: str) -> str:
    scope_id = f"repo:{repo_root.name}:node:Main.Topic.Core"
    parent_flow_id = runtime.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id=scope_id,
            params={
                "repo_key": repo_root.name,
                "repo_path": str(repo_root),
                "node_path": "Main.Topic.Core",
                "contract_version": 1,
            },
        ),
        enqueue=False,
    )

    def mark_waiting(parent) -> None:  # noqa: ANN001
        parent.status = FlowStatus.WAITING
        parent.agent_bindings.by_role[role] = agent_id

    runtime.flow_service.store.update_flow_record(parent_flow_id, mark_waiting)
    return parent_flow_id


def _start_child_round(
    runtime,
    repo_root: Path,
    *,
    parent_flow_id: str,
    strategy_id: str,
    round_id: str,
    round_index: int,
) -> str:
    return runtime.flow_service.start_flow(
        FlowRequest(
            flow_type="decl_graph_round",
            scope_id=f"repo:{repo_root.name}:node:Main.Topic.Core",
            params={
                "repo_key": repo_root.name,
                "repo_path": str(repo_root),
                "node_path": "Main.Topic.Core",
                "contract_version": 1,
                "strategy_id": strategy_id,
                "round_id": round_id,
                "round_index": round_index,
            },
        ),
        parent_flow_id=parent_flow_id,
        parent_dispatch_step_id="dispatch-round",
        enqueue=False,
    )


def _shared_stage_suspended_boundary(tmp_path: Path) -> _SharedStageBoundary:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    role = "statement_nl_worker"
    previous = runtime.agent_service.create_agent(
        f"repo:{repo_root.name}:node:Main.Topic.Core",
        "StatementNLWorkerAgent",
        provider_type="codex",
        home_id="StatementNLWorkerAgent",
    )
    parent_flow_id = _start_content_task_parent(
        runtime,
        repo_root,
        role=role,
        agent_id=previous.agent_id,
    )
    strategy_id, round_id, round_index = create_round_with_decl(lean_runtime, repo_root)
    flow_id = _start_child_round(
        runtime,
        repo_root,
        parent_flow_id=parent_flow_id,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
    )
    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    suspended_step_id = runtime.flow_service.advance_flow(flow_id)
    assert suspended_step_id is not None
    assert runtime.flow_service.get_flow(flow_id).agent_bindings.get(role) == previous.agent_id

    def suspend(step) -> None:  # noqa: ANN001
        step.agent_bindings.by_role[role] = previous.agent_id
        step.status = StepStatus.SUSPENDED
        step.error = BaseStepError(
            error_type="agent_context_maintenance_blocked",
            message="context maintenance unresolved",
        )
        step.started_at = utc_now_iso()

    runtime.flow_service.store.update_step_record(suspended_step_id, suspend)
    runtime.ark.pause_controller = RuntimePauseController(global_paused=True)
    schedule = _RecoverySchedule()
    runtime.ark.schedule_service = schedule
    return _SharedStageBoundary(
        runtime=runtime,
        lean_runtime=lean_runtime,
        repo_root=repo_root,
        parent_flow_id=parent_flow_id,
        round_flow_id=flow_id,
        suspended_step_id=suspended_step_id,
        role=role,
        previous_agent=previous,
        schedule=schedule,
    )


def _recover_fresh(boundary: _SharedStageBoundary):
    admin = LeanAdminApi(boundary.lean_runtime)
    preview = admin.inspect_agent_step_recovery(boundary.suspended_step_id)
    assert preview.ok and preview.value is not None, preview.issues
    return admin.recover_agent_step(
        RecoverAgentStepInput(
            step_id=boundary.suspended_step_id,
            expected_status="suspended",
            expected_recovery_token=preview.value.recovery.recovery_token,
            action="resume_suspended",
            agent_mode="fresh",
        )
    )


def test_fresh_shared_stage_recovery_updates_created_step_parent_and_next_round(
    tmp_path: Path,
) -> None:
    boundary = _shared_stage_suspended_boundary(tmp_path)

    recovered = _recover_fresh(boundary)

    assert recovered.ok and recovered.value is not None, recovered.issues
    replacement_agent_id = recovered.value.replacement_agent_id
    replacement_step_id = recovered.value.replacement_step_id
    assert replacement_agent_id not in {None, boundary.previous_agent.agent_id}
    assert replacement_step_id is not None
    assert boundary.schedule.step_ids == [replacement_step_id]
    parent = boundary.runtime.flow_service.get_flow(boundary.parent_flow_id)
    current_round = boundary.runtime.flow_service.get_flow(boundary.round_flow_id)
    replacement = boundary.runtime.flow_service.get_step(replacement_step_id)
    assert parent.agent_bindings.get(boundary.role) == replacement_agent_id
    assert current_round.agent_bindings.get(boundary.role) == replacement_agent_id
    assert replacement.status is StepStatus.CREATED
    assert replacement.agent_bindings.get(boundary.role) == replacement_agent_id

    next_flow_id = _start_child_round(
        boundary.runtime,
        boundary.repo_root,
        parent_flow_id=boundary.parent_flow_id,
        strategy_id="next-strategy",
        round_id="next-round",
        round_index=2,
    )

    def move_to_worker(next_flow) -> None:  # noqa: ANN001
        next_flow.status = FlowStatus.RUNNING
        next_flow.state.position.phase = "stage_worker"
        next_flow.state.current_stage = "statement_nl"
        next_flow.state.current_target_decl_names = ["main_result"]

    boundary.runtime.flow_service.store.update_flow_record(next_flow_id, move_to_worker)
    with boundary.runtime.ark.pause_controller.bypass_current_thread():
        next_step_id = boundary.runtime.flow_service.advance_flow(next_flow_id)
    assert next_step_id is not None
    next_round = boundary.runtime.flow_service.get_flow(next_flow_id)
    assert next_round.agent_bindings.get(boundary.role) == replacement_agent_id
    assert boundary.runtime.flow_service.get_step(next_step_id).state.agent_role == boundary.role


def test_shared_stage_recovery_compensates_parent_when_ark_commit_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    boundary = _shared_stage_suspended_boundary(tmp_path)
    flow_before = boundary.runtime.flow_service.get_flow(
        boundary.round_flow_id
    ).model_dump(mode="json")
    step_before = boundary.runtime.flow_service.get_step(
        boundary.suspended_step_id
    ).model_dump(mode="json")
    original_quiescent = boundary.runtime.flow_service._assert_scope_quiescent
    quiescent_checks = 0

    def fail_after_parent_update(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal quiescent_checks
        quiescent_checks += 1
        if quiescent_checks == 2:
            raise RuntimeError("injected failure after shared parent update")
        return original_quiescent(*args, **kwargs)

    monkeypatch.setattr(
        boundary.runtime.flow_service,
        "_assert_scope_quiescent",
        fail_after_parent_update,
    )

    result = _recover_fresh(boundary)

    assert not result.ok
    assert "injected failure after shared parent update" in result.issues[0].message
    parent = boundary.runtime.flow_service.get_flow(boundary.parent_flow_id)
    assert parent.agent_bindings.get(boundary.role) == boundary.previous_agent.agent_id
    assert (
        boundary.runtime.flow_service.get_flow(boundary.round_flow_id).model_dump(mode="json")
        == flow_before
    )
    assert (
        boundary.runtime.flow_service.get_step(boundary.suspended_step_id).model_dump(mode="json")
        == step_before
    )


def test_shared_stage_recovery_rejects_parent_binding_mismatch(tmp_path: Path) -> None:
    boundary = _shared_stage_suspended_boundary(tmp_path)
    foreign = boundary.runtime.agent_service.create_agent(
        boundary.previous_agent.scope_id,
        boundary.previous_agent.agent_type,
        provider_type=boundary.previous_agent.provider_type,
        home_id=boundary.previous_agent.home_id,
    )
    boundary.runtime.flow_service.store.update_flow_record(
        boundary.parent_flow_id,
        lambda parent: parent.agent_bindings.by_role.__setitem__(
            boundary.role,
            foreign.agent_id,
        ),
    )
    flow_before = boundary.runtime.flow_service.get_flow(
        boundary.round_flow_id
    ).model_dump(mode="json")

    result = _recover_fresh(boundary)

    assert not result.ok
    assert "shared stage Agent binding changed" in result.issues[0].message
    parent = boundary.runtime.flow_service.get_flow(boundary.parent_flow_id)
    assert parent.agent_bindings.get(boundary.role) == foreign.agent_id
    assert (
        boundary.runtime.flow_service.get_flow(boundary.round_flow_id).model_dump(mode="json")
        == flow_before
    )


def test_non_shared_decl_round_recovery_does_not_update_parent(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    role = "content_plan"
    previous = runtime.agent_service.create_agent(
        f"repo:{repo_root.name}:node:Main.Topic.Core",
        "ContentPlanAgent",
        provider_type="codex",
        home_id="ContentPlanAgent",
    )
    parent_flow_id = _start_content_task_parent(
        runtime,
        repo_root,
        role=role,
        agent_id=previous.agent_id,
    )
    flow_id = _start_child_round(
        runtime,
        repo_root,
        parent_flow_id=parent_flow_id,
        strategy_id="strategy",
        round_id="round",
        round_index=1,
    )
    flow = runtime.flow_service.get_flow(flow_id)
    step = ContentPlanAgentStep(
        step_id="non-shared-suspended",
        flow_id=flow_id,
        scope_id=flow.scope_id,
        status=StepStatus.SUSPENDED,
        state=AgentStepState(
            agent_role=role,
            agent_type="ContentPlanAgent",
            provider_type="codex",
            home_id="ContentPlanAgent",
        ),
        error=BaseStepError(
            error_type="agent_context_maintenance_blocked",
            message="context maintenance unresolved",
        ),
    )
    step.agent_bindings.by_role[role] = previous.agent_id
    runtime.flow_service.store.create_step(step)

    def attach_step(target) -> None:  # noqa: ANN001
        target.status = FlowStatus.RUNNING
        target.step_ids.append(step.step_id)
        target.current_step_id = step.step_id
        target.agent_bindings.by_role[role] = previous.agent_id

    runtime.flow_service.store.update_flow_record(flow_id, attach_step)
    runtime.ark.pause_controller = RuntimePauseController(global_paused=True)
    runtime.ark.schedule_service = _RecoverySchedule()
    admin = LeanAdminApi(lean_runtime)
    preview = admin.inspect_agent_step_recovery(step.step_id)
    assert preview.ok and preview.value is not None, preview.issues

    recovered = admin.recover_agent_step(
        RecoverAgentStepInput(
            step_id=step.step_id,
            expected_status="suspended",
            expected_recovery_token=preview.value.recovery.recovery_token,
            action="resume_suspended",
            agent_mode="fresh",
        )
    )

    assert recovered.ok and recovered.value is not None, recovered.issues
    assert recovered.value.replacement_agent_id != previous.agent_id
    assert runtime.flow_service.get_flow(parent_flow_id).agent_bindings.get(role) == previous.agent_id


@pytest.mark.parametrize("unsafe_boundary", ["unpaused", "active_flow"])
def test_shared_stage_recovery_preserves_pause_and_quiescence_gate(
    tmp_path: Path,
    unsafe_boundary: str,
) -> None:
    boundary = _shared_stage_suspended_boundary(tmp_path)
    if unsafe_boundary == "unpaused":
        boundary.runtime.ark.pause_controller.resume(None)
    else:
        boundary.schedule.active_flow_advances.add(boundary.round_flow_id)

    result = _recover_fresh(boundary)

    assert not result.ok
    parent = boundary.runtime.flow_service.get_flow(boundary.parent_flow_id)
    current_round = boundary.runtime.flow_service.get_flow(boundary.round_flow_id)
    assert parent.agent_bindings.get(boundary.role) == boundary.previous_agent.agent_id
    assert current_round.agent_bindings.get(boundary.role) == boundary.previous_agent.agent_id
    assert len(boundary.runtime.flow_service.list_steps(flow_id=boundary.round_flow_id)) == 4
