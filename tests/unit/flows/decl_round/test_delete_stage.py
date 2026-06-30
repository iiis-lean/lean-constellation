from __future__ import annotations

from pathlib import Path

from tests.unit.flows.decl_round._helpers import (
    NODE_PATH,
    advance_and_run,
    assert_completed,
    make_decl_round_runtime,
    seed_committed_theorem,
    start_decl_round_flow,
)


def test_delete_only_round_removes_projection_and_skips_all_stages(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    seed_committed_theorem(lean_runtime, repo_root, decl_name="old_result")
    path_view = lean_runtime.lean_projection.decl_file.derive_decl_file_path(
        repo_root,
        node_path=NODE_PATH,
        decl_name="old_result",
        kind="theorem",
    )
    assert path_view.ok and path_view.value is not None, path_view.issues
    decl_file = Path(path_view.value.path)
    decl_file.parent.mkdir(parents=True, exist_ok=True)
    decl_file.write_text("theorem old_result : True := by\n  trivial\n", encoding="utf-8")
    assert decl_file.exists()

    strategy = lean_runtime.decl_graph.ensure_open_strategy(
        repo_root,
        node_path=NODE_PATH,
        objective="Delete obsolete declarations.",
    )
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = lean_runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Delete old_result.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    deleted = lean_runtime.decl_graph.mark_decl_delete(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name="old_result",
        objective="Remove old_result from the node graph.",
    )
    assert deleted.ok, deleted.issues
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy.value.strategy_id,
        round_id=round_record.value.round_id,
        round_index=round_record.value.round_index,
    )

    advance_and_run(runtime, flow_id)
    delete_step_id = advance_and_run(runtime, flow_id)
    delete_step = runtime.flow_service.get_step(delete_step_id)
    assert delete_step.result.outcome == "normalized"
    assert delete_step.result.deleted_count == 1
    assert not decl_file.exists()

    skipped = []
    for expected_stage in ("statement_nl", "statement_formal", "proof_nl", "proof_formal"):
        step_id = advance_and_run(runtime, flow_id)
        step = runtime.flow_service.get_step(step_id)
        assert step.result.outcome == "skipped"
        assert step.result.stage == expected_stage
        skipped.append(step.result.stage)

    final_step_id = advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_step(final_step_id).result.outcome == "passed"
    advance_and_run(runtime, flow_id)

    assert_completed(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "completed"
    assert flow.result.completed_stages == []
    assert flow.result.skipped_stages == skipped
