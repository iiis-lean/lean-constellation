from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.services.decl_graph import DeclStage, DeclState
from tests.unit.flows.decl_round._helpers import (
    NODE_PATH,
    advance_and_run,
    assert_completed,
    create_round_with_decl,
    make_decl_round_runtime,
    queue_review,
    queue_worker_completed,
    record_passed_review,
    start_decl_round_flow,
)
from tests.unit_services_helpers import lean_check_payload, write_statement_formal_for_test


@pytest.mark.parametrize(
    ("kind", "lean_code", "lean_check"),
    [
        (
            "definition",
            "def main_result : True := by\n  trivial",
            lean_check_payload(),
        ),
        (
            "theorem",
            "theorem main_result : True := by\n  sorry",
            lean_check_payload(contains_sorry=True),
        ),
    ],
)
def test_declared_round_skips_proof_stages(
    tmp_path: Path,
    kind: str,
    lean_code: str,
    lean_check: dict[str, object],
) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        kind=kind,
        end_after_state=DeclState.DECLARED,
    )
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
    statement_nl = lean_runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="Define the main result as a trivial truth witness.",
    )
    assert statement_nl.ok, statement_nl.issues
    queue_worker_completed(runtime, repo_root, stage="statement_nl", round_id=round_id)
    advance_and_run(runtime, flow_id)
    record_passed_review(lean_runtime, repo_root, stage=DeclStage.STATEMENT_NL, round_id=round_id)
    queue_review(runtime, repo_root, stage="statement_nl", round_id=round_id, accepted=True)
    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)

    prepare_formal_step_id = advance_and_run(runtime, flow_id)
    prepare_formal_step = runtime.flow_service.get_step(prepare_formal_step_id)
    assert prepare_formal_step.result.outcome == "targets_ready"
    assert prepare_formal_step.result.stage == "statement_formal"
    statement_formal = write_statement_formal_for_test(lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        lean_code=lean_code,
        lean_check=lean_check,
    )
    assert statement_formal.ok, statement_formal.issues
    synced = lean_runtime.lean_projection.sync_decl_file_after_revision_reset(
        repo_root,
        node_path=NODE_PATH,
        decl_name="main_result",
    )
    assert synced.ok, synced.issues
    queue_worker_completed(runtime, repo_root, stage="statement_formal", round_id=round_id)
    advance_and_run(runtime, flow_id)
    record_passed_review(lean_runtime, repo_root, stage=DeclStage.STATEMENT_FORMAL, round_id=round_id)
    queue_review(runtime, repo_root, stage="statement_formal", round_id=round_id, accepted=True)
    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)

    skipped = []
    for expected_stage in ("proof_nl", "proof_formal"):
        step_id = advance_and_run(runtime, flow_id)
        step = runtime.flow_service.get_step(step_id)
        assert step.result.outcome == "skipped"
        assert step.result.stage == expected_stage
        skipped.append(step.result.stage)

    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)

    assert_completed(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "completed"
    assert flow.result.completed_stages == ["statement_nl", "statement_formal"]
    assert flow.result.skipped_stages == skipped
    revision = lean_runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert revision.value.state is DeclState.DECLARED
