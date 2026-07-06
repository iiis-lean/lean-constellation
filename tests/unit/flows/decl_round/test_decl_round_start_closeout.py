from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.services.decl_graph import DeclRoundStatus, DeclStage, DeclState
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


def test_decl_round_runs_full_theorem_stage_sequence(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        end_after_state=DeclState.PROVED,
    )
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
    )

    start_step_id = advance_and_run(runtime, flow_id)
    start_step = runtime.flow_service.get_step(start_step_id)
    assert start_step.result.outcome == "valid"
    round_record = lean_runtime.decl_graph.get_round(repo_root, node_path=NODE_PATH, round_id=round_id)
    assert round_record.value.status is DeclRoundStatus.RUNNING
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "delete_normalize"

    advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_prepare"

    _run_stage(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        stage="statement_nl",
        review_stage=DeclStage.STATEMENT_NL,
        mutate=lambda: lean_runtime.decl_graph.write_statement_nl(
            repo_root,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            nl="The main result states True.",
        ),
    )
    _run_stage(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        stage="statement_formal",
        review_stage=DeclStage.STATEMENT_FORMAL,
        mutate=lambda: _write_statement_formal(lean_runtime, repo_root, round_id),
    )
    _run_stage(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        stage="proof_nl",
        review_stage=DeclStage.PROOF_NL,
        mutate=lambda: lean_runtime.decl_graph.write_proof_nl(
            repo_root,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            nl="Use triviality.",
        ),
    )
    _run_stage(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        stage="proof_formal",
        review_stage=DeclStage.PROOF_FORMAL,
        mutate=lambda: _write_proof_formal(lean_runtime, repo_root, round_id),
    )

    final_audit_step_id = advance_and_run(runtime, flow_id)
    final_audit_step = runtime.flow_service.get_step(final_audit_step_id)
    assert final_audit_step.result.outcome == "passed"
    build_step_id = advance_and_run(runtime, flow_id)
    build_step = runtime.flow_service.get_step(build_step_id)
    assert build_step.result.flow_outcome == "completed"

    assert_completed(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "completed"
    assert flow.result.completed_stages == [
        "statement_nl",
        "statement_formal",
        "proof_nl",
        "proof_formal",
    ]
    revision = lean_runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert revision.value.state is DeclState.PROVED


def test_decl_round_final_audit_rejects_unsatisfied_target_by_default(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        end_after_state=DeclState.PROVED,
    )
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
    )

    _run_main_result_theorem_stages(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        proof_deps=["missing_helper"],
    )

    final_audit_step_id = advance_and_run(runtime, flow_id)
    final_audit_step = runtime.flow_service.get_step(final_audit_step_id)
    assert final_audit_step.result.outcome == "failed"
    assert final_audit_step.result.error.affected_decl_names == ["main_result"]
    assert "did not satisfy proof policy" in final_audit_step.result.error.message


def test_decl_round_final_audit_allows_unsatisfied_target_when_opted_out(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        end_after_state=DeclState.PROVED,
        require_target_state_satisfied=False,
    )
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
    )

    _run_main_result_theorem_stages(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        proof_deps=["missing_helper"],
    )

    final_audit_step_id = advance_and_run(runtime, flow_id)
    final_audit_step = runtime.flow_service.get_step(final_audit_step_id)
    assert final_audit_step.result.outcome == "passed"


def test_decl_stage_agent_prompts_include_change_metadata(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        end_after_state=DeclState.PROVED,
        require_target_state_satisfied=False,
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
    prepare_step_id = advance_and_run(runtime, flow_id)
    prepare_step = runtime.flow_service.get_step(prepare_step_id)
    assert prepare_step.result.outcome == "targets_ready"
    assert prepare_step.result.target_metadata[0].decl_name == "main_result"
    assert prepare_step.result.target_metadata[0].end_after_state == "proved"
    assert prepare_step.result.target_metadata[0].require_target_state_satisfied is False

    queue_worker_completed(runtime, repo_root, stage="statement_nl", round_id=round_id)
    advance_and_run(runtime, flow_id)
    worker_record = runtime.agent_service.start_records[-1]
    assert worker_record.variables["target_metadata"][0]["decl_name"] == "main_result"
    assert worker_record.variables["target_metadata"][0]["end_after_state"] == "proved"
    assert worker_record.variables["target_metadata"][0]["require_target_state_satisfied"] is False
    assert "Target change metadata:" in (worker_record.prompt or "")
    assert "change_kind=create" in (worker_record.prompt or "")
    assert "end_after_state=proved" in (worker_record.prompt or "")
    assert "require_target_state_satisfied=False" in (worker_record.prompt or "")
    assert "current_state=planned" in (worker_record.prompt or "")
    assert "known_statement_deps=[none]" in (worker_record.prompt or "")
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_reviewer"

    queue_review(runtime, repo_root, stage="statement_nl", round_id=round_id, accepted=True)
    advance_and_run(runtime, flow_id)
    reviewer_record = runtime.agent_service.start_records[-1]
    assert reviewer_record.variables["target_metadata"][0]["decl_name"] == "main_result"
    assert "Review decl stage statement_nl." in (reviewer_record.prompt or "")
    assert "Target change metadata:" in (reviewer_record.prompt or "")
    assert "require_target_state_satisfied=False" in (reviewer_record.prompt or "")


def test_decl_round_stale_contract_fails_before_mutation(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(lean_runtime, repo_root)
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
        contract_version=999,
    )

    start_step_id = advance_and_run(runtime, flow_id)
    start_step = runtime.flow_service.get_step(start_step_id)
    assert start_step.result.outcome == "invalid"
    assert "Contract version is stale" in start_step.result.error.message

    advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "failed"
    assert flow.result.terminal_reason.code == "invalid_round_state"


def _run_main_result_theorem_stages(
    runtime,
    lean_runtime,
    repo_root: Path,
    *,
    flow_id: str,
    round_id: str,
    proof_deps: list[str] | None = None,
) -> None:
    start_step_id = advance_and_run(runtime, flow_id)
    start_step = runtime.flow_service.get_step(start_step_id)
    assert start_step.result.outcome == "valid"
    advance_and_run(runtime, flow_id)
    _run_stage(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        stage="statement_nl",
        review_stage=DeclStage.STATEMENT_NL,
        mutate=lambda: lean_runtime.decl_graph.write_statement_nl(
            repo_root,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            nl="The main result states True.",
        ),
    )
    _run_stage(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        stage="statement_formal",
        review_stage=DeclStage.STATEMENT_FORMAL,
        mutate=lambda: _write_statement_formal(lean_runtime, repo_root, round_id),
    )
    _run_stage(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        stage="proof_nl",
        review_stage=DeclStage.PROOF_NL,
        mutate=lambda: lean_runtime.decl_graph.write_proof_nl(
            repo_root,
            node_path=NODE_PATH,
            round_id=round_id,
            decl_name="main_result",
            nl="Use triviality.",
        ),
    )
    _run_stage(
        runtime,
        lean_runtime,
        repo_root,
        flow_id=flow_id,
        round_id=round_id,
        stage="proof_formal",
        review_stage=DeclStage.PROOF_FORMAL,
        mutate=lambda: _write_proof_formal(lean_runtime, repo_root, round_id, deps=proof_deps),
    )


def _run_stage(
    runtime,
    lean_runtime,
    repo_root: Path,
    *,
    flow_id: str,
    round_id: str,
    stage: str,
    review_stage: DeclStage,
    mutate,
) -> None:
    prepare_step_id = advance_and_run(runtime, flow_id)
    prepare_step = runtime.flow_service.get_step(prepare_step_id)
    assert prepare_step.result.outcome == "targets_ready"
    assert prepare_step.result.stage == stage
    assert prepare_step.result.target_decl_names == ["main_result"]
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_worker"

    mutation = mutate()
    assert mutation.ok, mutation.issues
    if stage in {"statement_formal", "proof_formal"}:
        synced = lean_runtime.lean_projection.sync_decl_file_after_revision_reset(
            repo_root,
            node_path=NODE_PATH,
            decl_name="main_result",
        )
        assert synced.ok, synced.issues
    queue_worker_completed(runtime, repo_root, stage=stage, round_id=round_id)
    advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_reviewer"

    record_passed_review(lean_runtime, repo_root, stage=review_stage, round_id=round_id)
    queue_review(runtime, repo_root, stage=stage, round_id=round_id, accepted=True)
    advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_gate_audit"

    gate_step_id = advance_and_run(runtime, flow_id)
    gate_step = runtime.flow_service.get_step(gate_step_id)
    assert gate_step.result.outcome == "stage_passed"


def _write_statement_formal(lean_runtime, repo_root: Path, round_id: str):
    path_view = lean_runtime.lean_projection.decl_file.derive_decl_file_path(
        repo_root,
        node_path=NODE_PATH,
        decl_name="main_result",
        kind="theorem",
    )
    assert path_view.ok and path_view.value is not None, path_view.issues
    lean_code = Path(path_view.value.path).read_text(encoding="utf-8")
    return lean_runtime.decl_graph.write_statement_formal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        lean_code=lean_code,
        lean_check={"status": "passed", "allow_sorry": "true", "contains_sorry": "true"},
    )


def _write_proof_formal(lean_runtime, repo_root: Path, round_id: str, *, deps: list[str] | None = None):
    path_view = lean_runtime.lean_projection.decl_file.derive_decl_file_path(
        repo_root,
        node_path=NODE_PATH,
        decl_name="main_result",
        kind="theorem",
    )
    assert path_view.ok and path_view.value is not None, path_view.issues
    lean_code = Path(path_view.value.path).read_text(encoding="utf-8").replace("sorry", "trivial")
    return lean_runtime.decl_graph.write_proof_formal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        lean_code=lean_code,
        lean_check={"status": "passed", "allow_sorry": "false", "contains_sorry": "false"},
        deps=deps,
    )
