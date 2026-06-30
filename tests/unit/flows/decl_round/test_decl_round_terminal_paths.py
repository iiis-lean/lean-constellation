from __future__ import annotations

from pathlib import Path

from tests.unit.flows.decl_round._helpers import (
    advance_and_run,
    assert_completed,
    create_round_with_decl,
    make_decl_round_runtime,
    queue_worker_blocked,
    start_decl_round_flow,
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
