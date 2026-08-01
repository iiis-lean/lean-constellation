from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import BaseStepError, FlowStatus, StepStatus, utc_now_iso

from lean_constellation.services.decl_graph import DeclRoundResultKind, DeclRoundStatus
from tests.unit.flows.decl_round._helpers import (
    advance_and_run,
    assert_completed,
    create_round_with_decl,
    make_decl_round_runtime,
    queue_worker_blocked,
    start_decl_round_flow,
)


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
