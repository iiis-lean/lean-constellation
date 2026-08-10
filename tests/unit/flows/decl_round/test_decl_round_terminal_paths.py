from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import BaseStepError, FlowStatus, StepStatus, utc_now_iso
from agent_runtime_kit.runtime import RuntimePauseController

from lean_constellation.app import LeanAdminApi, RestartFailedAgentStepInput
from lean_constellation.services.decl_graph import DeclRoundResultKind, DeclRoundStatus
from tests.unit.flows.decl_round._helpers import (
    advance_and_run,
    assert_completed,
    create_round_with_decl,
    make_decl_round_runtime,
    queue_worker_blocked,
    start_decl_round_flow,
)


class _RestartSchedule:
    def __init__(self) -> None:
        self.step_ids: list[str] = []
        self.flow_ids: list[str] = []

    def enqueue_step(self, step_id: str) -> None:
        self.step_ids.append(step_id)

    def enqueue_flow(self, flow_id: str) -> None:
        self.flow_ids.append(flow_id)


def test_worker_step_exception_records_failed_round_for_plan_closeout(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(lean_runtime, repo_root)
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
    )

    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    failed_step_id = runtime.flow_service.advance_flow(flow_id)
    assert failed_step_id is not None

    def fail_step(step) -> None:  # noqa: ANN001
        now = utc_now_iso()
        step.status = StepStatus.FAILED
        step.error = BaseStepError(
            error_type="step_run_exception",
            message="home materialized file hash mismatch: node_modules/example/LICENSE",
        )
        step.started_at = now
        step.finished_at = now

    runtime.flow_service.store.update_step_record(failed_step_id, fail_step)
    runtime.flow_service.handle_step_terminal(failed_step_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.FAILED
    assert flow.error is not None
    assert flow.error.message == "home materialized file hash mismatch: node_modules/example/LICENSE"
    round_result = lean_runtime.decl_graph.get_round(
        repo_root,
        node_path="Main.Topic.Core",
        round_id=round_id,
    )
    assert round_result.ok and round_result.value is not None
    assert round_result.value.status is DeclRoundStatus.AWAITING_CLOSEOUT
    assert round_result.value.execution_result_kind is DeclRoundResultKind.FAILED
    assert round_result.value.execution_reason == (
        f"Step {failed_step_id} failed before DeclGraph round completion: "
        "home materialized file hash mismatch: node_modules/example/LICENSE"
    )


def test_admin_restart_reuses_failed_worker_and_reopens_only_round_failure_marker(
    tmp_path: Path,
) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(lean_runtime, repo_root)
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
    )
    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    failed_step_id = runtime.flow_service.advance_flow(flow_id)
    assert failed_step_id is not None
    failed_step = runtime.flow_service.get_step(failed_step_id)
    failed_state = failed_step.state
    agent = runtime.agent_service.create_agent(
        failed_step.scope_id,
        failed_state.agent_type,
        provider_type=failed_state.provider_type or "codex",
        home_id=failed_state.home_id,
    )

    def fail_bound_step(step) -> None:  # noqa: ANN001
        now = utc_now_iso()
        step.agent_bindings.by_role[failed_state.agent_role] = agent.agent_id
        step.status = StepStatus.FAILED
        step.error = BaseStepError(
            error_type="step_run_exception",
            message="stream disconnected before completion",
        )
        step.started_at = now
        step.finished_at = now

    runtime.flow_service.store.update_step_record(failed_step_id, fail_bound_step)
    runtime.flow_service.handle_step_terminal(failed_step_id)
    revision_refs = list(
        lean_runtime.decl_graph.get_round(
            repo_root,
            node_path="Main.Topic.Core",
            round_id=round_id,
        ).value.revision_refs
    )
    runtime.ark.pause_controller = RuntimePauseController(global_paused=True)
    schedule = _RestartSchedule()
    runtime.ark.schedule_service = schedule

    restarted = LeanAdminApi(lean_runtime).restart_failed_agent_step(
        RestartFailedAgentStepInput(step_id=failed_step_id)
    )

    assert restarted.ok and restarted.value is not None, restarted.issues
    assert restarted.value.agent_reused is True
    assert restarted.value.agent_id == agent.agent_id
    assert restarted.value.reopened_round_id == round_id
    assert schedule.step_ids == [restarted.value.replacement_step_id]
    old_step = runtime.flow_service.get_step(failed_step_id)
    replacement = runtime.flow_service.get_step(restarted.value.replacement_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert old_step.status is StepStatus.FAILED
    assert replacement.step_type == old_step.step_type
    assert replacement.state.restart_of_step_id == failed_step_id
    assert replacement.agent_bindings.get(failed_state.agent_role) == agent.agent_id
    assert flow.status is FlowStatus.RUNNING
    assert flow.current_step_id == replacement.step_id
    reopened_round = lean_runtime.decl_graph.get_round(
        repo_root,
        node_path="Main.Topic.Core",
        round_id=round_id,
    )
    assert reopened_round.ok and reopened_round.value is not None
    assert reopened_round.value.status is DeclRoundStatus.RUNNING
    assert reopened_round.value.execution_result_kind is None
    assert reopened_round.value.execution_reason is None
    assert reopened_round.value.revision_refs == revision_refs

    queue_worker_blocked(
        runtime,
        repo_root,
        stage="statement_nl",
        round_id=round_id,
        reason="Planner action is still required.",
    )
    runtime.step_service.run_step(replacement.step_id, bypass_pause=True)

    assert runtime.flow_service.get_step(replacement.step_id).status is StepStatus.COMPLETED
    assert runtime.flow_service.get_flow(flow_id).status is FlowStatus.RUNNING
    assert runtime.agent_service.start_records[-1].agent_id == agent.agent_id
    assert "previous execution of this AgentStep ended unexpectedly" in (
        runtime.agent_service.start_records[-1].prompt or ""
    )


def test_worker_blocked_submission_completes_round_flow_as_blocked(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(lean_runtime, repo_root)
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
    )

    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    prepare_step_id = advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_step(prepare_step_id).result.outcome == "targets_ready"

    queue_worker_blocked(
        runtime,
        repo_root,
        stage="statement_nl",
        round_id=round_id,
        reason="Need an external lemma before continuing.",
    )
    worker_step_id = advance_and_run(runtime, flow_id)
    worker_step = runtime.flow_service.get_step(worker_step_id)
    assert worker_step.result.outcome == "blocked"
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "build_result"

    advance_and_run(runtime, flow_id)
    assert_completed(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "blocked"
    assert flow.result.terminal_reason.code == "worker_blocked"
    assert flow.result.terminal_reason.affected_decl_names == ["main_result"]
