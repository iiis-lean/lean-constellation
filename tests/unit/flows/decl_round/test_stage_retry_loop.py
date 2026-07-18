from __future__ import annotations

from pathlib import Path

from tests.unit.flows.decl_round._helpers import (
    advance_and_run,
    assert_completed,
    create_round_with_decl,
    make_decl_round_runtime,
    queue_review,
    queue_worker_completed,
    start_decl_round_flow,
)


def test_reviewer_rejection_retries_worker_until_budget_is_exhausted(tmp_path: Path) -> None:
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

    for attempt in range(3):
        queue_worker_completed(runtime, repo_root, stage="statement_nl", round_id=round_id)
        advance_and_run(runtime, flow_id)
        assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_reviewer"
        if attempt == 0:
            worker_start = runtime.agent_service.start_records[-1]
            assert "objective: Strategy objective." in worker_start.prompt
            assert "Round objective." in worker_start.prompt
            assert worker_start.variables["context_brief"]["strategy_round"][
                "strategy_objective"
            ] == "Strategy objective."

        queue_review(runtime, repo_root, stage="statement_nl", round_id=round_id, accepted=False)
        advance_and_run(runtime, flow_id)
        assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_gate_audit"
        if attempt == 0:
            reviewer_start = runtime.agent_service.start_records[-1]
            assert "Worker receipt (navigation only, not review evidence)" in reviewer_start.prompt

        gate_step_id = advance_and_run(runtime, flow_id)
        gate_step = runtime.flow_service.get_step(gate_step_id)
        flow = runtime.flow_service.get_flow(flow_id)
        if attempt < 2:
            assert gate_step.result.outcome == "retry_worker"
            assert gate_step.result.retry_count == attempt + 1
            assert flow.state.position.phase == "stage_worker"
        else:
            assert gate_step.result.outcome == "failed"
            assert gate_step.result.error.code == "review_retry_exhausted"
            assert flow.state.position.phase == "build_result"

    advance_and_run(runtime, flow_id)
    assert_completed(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "failed"
    assert flow.result.terminal_reason.code == "review_retry_exhausted"
    assert flow.result.terminal_stage == "statement_nl"
