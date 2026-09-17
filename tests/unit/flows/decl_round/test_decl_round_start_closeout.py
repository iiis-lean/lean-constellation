from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.domain.repo_run import RepoRunWorkflowControls
from lean_constellation.flows.common.testing import create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.decl_round.flow import _review_mode_for_stage
from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepResult
from lean_constellation.services.decl_graph import DeclReviewMarkRecord, DeclRoundStatus, DeclStage, DeclState
from tests.unit.flows.decl_round._helpers import (
    NODE_PATH,
    advance_and_run,
    assert_completed,
    commit_content_contract_head,
    create_round_with_decl,
    make_decl_round_runtime,
    queue_review,
    queue_worker_completed,
    record_passed_review,
    start_decl_round_flow,
)
from tests.unit_services_helpers import (
    lean_check_payload,
    set_current_decl_lean_name_for_test,
    write_proof_formal_for_test,
    write_statement_formal_for_test,
)


def test_decl_stage_reviewer_agent_fields_hide_nested_round_identity() -> None:
    round_id = "round-secret"
    result = DeclStageReviewerStepResult(
        outcome="rejected",
        stage="statement_nl",
        round_id=round_id,
        node_path=NODE_PATH,
        accepted=False,
        retry_required=True,
        reviewed_decl_names=["main_result"],
        failed_decl_names=["main_result"],
        feedback=[
            DeclReviewMarkRecord(
                round_id=round_id,
                node_path=NODE_PATH,
                stage=DeclStage.STATEMENT_NL,
                decl_name="main_result",
                passed=False,
                summary="Statement requires repair.",
                required_changes=["Clarify the quantified domain."],
            )
        ],
        summary="Statement review rejected.",
    )

    internal_fields = result.model_dump(mode="json")
    agent_fields = result.agent_fields()

    assert internal_fields["round_id"] == round_id
    assert internal_fields["feedback"][0]["round_id"] == round_id
    assert "round_id" not in agent_fields
    assert "round_id" not in agent_fields["feedback"][0]
    assert round_id not in result.render_for_agent(None)
    assert "Statement requires repair." in result.render_for_agent(None)


def test_each_decl_stage_has_an_independent_review_control() -> None:
    controls = RepoRunWorkflowControls(
        statement_nl_review=False,
        statement_formal_review=True,
        proof_nl_review=False,
        proof_formal_review=True,
    )

    assert _review_mode_for_stage(controls, "statement_nl") == "deterministic_only"
    assert _review_mode_for_stage(controls, "statement_formal") == "agent"
    assert _review_mode_for_stage(controls, "proof_nl") == "deterministic_only"
    assert _review_mode_for_stage(controls, "proof_formal") == "agent"


def test_decl_round_runs_full_theorem_stage_sequence(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
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
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "revision_normalize"

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
    _seed_open_proof_planned_theorem(lean_runtime, repo_root, decl_name="missing_helper")
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
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
    assert "1 readiness failures" in final_audit_step.result.error.message


def test_statement_nl_stage_gate_rejects_missing_statement_dependency(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
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
    mutation = lean_runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
        deps=["missing_helper"],
    )
    assert mutation.ok, mutation.issues
    queue_worker_completed(runtime, repo_root, stage="statement_nl", round_id=round_id)
    advance_and_run(runtime, flow_id)
    record_passed_review(lean_runtime, repo_root, stage=DeclStage.STATEMENT_NL, round_id=round_id)
    queue_review(runtime, repo_root, stage="statement_nl", round_id=round_id, accepted=True)
    advance_and_run(runtime, flow_id)

    gate_step_id = advance_and_run(runtime, flow_id)
    gate_step = runtime.flow_service.get_step(gate_step_id)
    assert gate_step.result.outcome == "failed"
    assert "missing_helper" in gate_step.result.error.message
    revision = lean_runtime.decl_graph.get_decl_revision(repo_root, node_path=NODE_PATH, name="main_result", revision=1)
    assert revision.ok and revision.value is not None
    assert revision.value.state is DeclState.PLANNED


def test_statement_nl_can_use_deterministic_gate_without_reviewer(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
    )
    controls = RepoRunWorkflowControls(statement_nl_review=False)
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
        workflow_controls=controls,
    )

    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    advance_and_run(runtime, flow_id)
    mutation = lean_runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    )
    assert mutation.ok, mutation.issues
    queue_worker_completed(runtime, repo_root, stage="statement_nl", round_id=round_id)
    worker_step_id = advance_and_run(runtime, flow_id)

    assert runtime.flow_service.get_step(worker_step_id).result.outcome == "completed"
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_gate_audit"
    restarted = create_fake_lean_flow_runtime(
        runtime.root,
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    reloaded = restarted.flow_service.get_flow(flow_id)
    assert reloaded.input.workflow_controls.statement_nl_review is False
    assert reloaded.state.position.phase == "stage_gate_audit"

    gate_step_id = advance_and_run(restarted, flow_id)
    gate = restarted.flow_service.get_step(gate_step_id).result
    assert gate.outcome == "stage_passed"
    assert gate.review_mode == "deterministic_only"
    assert "no Agent Reviewer ran" in gate.summary
    assert restarted.flow_service.get_flow(flow_id).state.stage_summaries[-1].review_mode == "deterministic_only"
    assert not any(
        record.variables.get("agent_role") == "reviewer"
        for record in restarted.agent_service.start_records
    )


def test_deterministic_gate_rejects_worker_batch_mismatch(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
    )
    flow_id = start_decl_round_flow(
        runtime,
        repo_root,
        strategy_id=strategy_id,
        round_id=round_id,
        round_index=round_index,
        workflow_controls=RepoRunWorkflowControls(statement_nl_review=False),
    )
    for _ in range(3):
        advance_and_run(runtime, flow_id)
    assert lean_runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    ).ok
    queue_worker_completed(runtime, repo_root, stage="statement_nl", round_id=round_id)
    advance_and_run(runtime, flow_id)
    runtime.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: setattr(
            flow.state.latest_worker_result,
            "completed_decl_names",
            ["different_decl"],
        ),
    )

    gate_step_id = advance_and_run(runtime, flow_id)
    gate = runtime.flow_service.get_step(gate_step_id).result
    assert gate.outcome == "failed"
    assert "worker result does not match" in gate.error.message


def test_stage_gate_rejects_reviewer_result_context_mismatch(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
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
    mutation = lean_runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        nl="The main result states True.",
    )
    assert mutation.ok, mutation.issues
    queue_worker_completed(runtime, repo_root, stage="statement_nl", round_id=round_id)
    advance_and_run(runtime, flow_id)
    record_passed_review(lean_runtime, repo_root, stage=DeclStage.STATEMENT_NL, round_id=round_id)
    queue_review(runtime, repo_root, stage="proof_nl", round_id=round_id, accepted=True)
    advance_and_run(runtime, flow_id)

    gate_step_id = advance_and_run(runtime, flow_id)
    gate_step = runtime.flow_service.get_step(gate_step_id)
    assert gate_step.result.outcome == "failed"
    assert "Reviewer result context mismatch" in gate_step.result.error.message
    revision = lean_runtime.decl_graph.get_decl_revision(repo_root, node_path=NODE_PATH, name="main_result", revision=1)
    assert revision.ok and revision.value is not None
    assert revision.value.state is DeclState.PLANNED


def test_decl_round_final_audit_allows_unsatisfied_target_when_opted_out(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _seed_open_proof_planned_theorem(lean_runtime, repo_root, decl_name="missing_helper")
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
        require_target_state_satisfied=False,
        round_execution_constraints="Keep this stage batch within the assigned declarations.",
        change_execution_constraints="Preserve the source binders for main_result.",
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


def test_top_down_proved_round_becomes_satisfied_after_helper_is_proved(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    _seed_open_proof_planned_theorem(lean_runtime, repo_root, decl_name="missing_helper")
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
        require_target_state_satisfied=False,
        round_execution_constraints="Keep the accepted batch fixed through closeout.",
        change_execution_constraints="Preserve the accepted statement during proof work.",
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
    build_step_id = advance_and_run(runtime, flow_id)
    build_step = runtime.flow_service.get_step(build_step_id)
    assert build_step.result.flow_outcome == "completed"
    _close_executed_round(
        lean_runtime,
        repo_root,
        round_id=round_id,
        result_kind="success",
        decl_name="main_result",
    )
    closed_round = lean_runtime.decl_graph.get_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
    )
    closed_revision = lean_runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert closed_round.ok and closed_round.value is not None
    assert closed_round.value.execution_constraints == (
        "Keep the accepted batch fixed through closeout."
    )
    assert closed_revision.ok and closed_revision.value is not None
    assert closed_revision.value.change is not None
    assert closed_revision.value.change.execution_constraints == (
        "Preserve the accepted statement during proof work."
    )
    before_helper = lean_runtime.decl_graph.check_decl_proof_policy_satisfied(
        repo_root,
        node_path=NODE_PATH,
        decl_name="main_result",
    )
    assert before_helper.ok and before_helper.value is not None
    assert before_helper.value.ready is False
    assert before_helper.value.blocker is not None
    assert before_helper.value.blocker.blocking_decl is not None
    assert before_helper.value.blocker.blocking_decl.name == "missing_helper"
    _prove_committed_helper_theorem(lean_runtime, repo_root, decl_name="missing_helper")
    commit_content_contract_head(lean_runtime, repo_root, decl_graph_head={"missing_helper": 2})
    after_helper = lean_runtime.decl_graph.check_decl_proof_policy_satisfied(
        repo_root,
        node_path=NODE_PATH,
        decl_name="main_result",
    )

    assert after_helper.ok and after_helper.value is not None
    assert after_helper.value.ready is True
    assert after_helper.value.blocker is None


def test_decl_stage_agent_prompts_include_change_metadata(tmp_path: Path) -> None:
    runtime, lean_runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy_id, round_id, round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        target_state=DeclState.PROVED,
        require_target_state_satisfied=False,
        strategy_execution_constraints="ContentPlan should split later frontiers.",
        round_execution_constraints="Keep this stage batch within the assigned declarations.",
        change_execution_constraints="Preserve the source binders for main_result.",
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
    start_agent_fields = start_step.result.agent_fields()
    assert start_agent_fields["round_sequence"] == round_index
    assert "strategy_id" not in start_agent_fields
    assert "round_id" not in start_agent_fields
    assert strategy_id not in start_step.result.render_for_agent(None)
    assert round_id not in start_step.result.render_for_agent(None)
    advance_and_run(runtime, flow_id)
    prepare_step_id = advance_and_run(runtime, flow_id)
    prepare_step = runtime.flow_service.get_step(prepare_step_id)
    assert prepare_step.result.outcome == "targets_ready"
    assert prepare_step.result.target_decl_names == ["main_result"]
    assert "target_metadata" not in prepare_step.result.model_dump(mode="json")

    queue_worker_completed(runtime, repo_root, stage="statement_nl", round_id=round_id)
    worker_step_id = advance_and_run(runtime, flow_id)
    worker_step = runtime.flow_service.get_step(worker_step_id)
    worker_record = runtime.agent_service.start_records[-1]
    assert worker_record.variables["round_id"] == round_id
    assert "target_metadata" not in worker_record.variables
    assert "context_brief" not in worker_record.variables
    assert f"Round sequence: {round_index}." in (worker_record.prompt or "")
    assert round_id not in (worker_record.prompt or "")
    assert "Assigned declarations:" in (worker_record.prompt or "")
    assert "$lc-field-semantics" in (worker_record.prompt or "")
    assert "Pipeline position: planned --Statement NL--> specified" in (worker_record.prompt or "")
    assert "global target_state does not expand this stage's authority" in (worker_record.prompt or "")
    assert "Change: create" in (worker_record.prompt or "")
    assert "Objective: Create main_result." in (worker_record.prompt or "")
    assert (
        "Round execution constraints: Keep this stage batch within the assigned declarations."
        in (worker_record.prompt or "")
    )
    assert (
        "Execution constraints: Preserve the source binders for main_result."
        in (worker_record.prompt or "")
    )
    assert "ContentPlan should split later frontiers." not in (worker_record.prompt or "")
    assert "Required through: Proof Formal" in (worker_record.prompt or "")
    assert "known_statement_deps" not in (worker_record.prompt or "")
    assert "known_proof_deps" not in (worker_record.prompt or "")
    assert "state=planned" not in (worker_record.prompt or "")
    assert "round_id" not in worker_step.result.agent_fields()
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_reviewer"

    queue_review(runtime, repo_root, stage="statement_nl", round_id=round_id, accepted=True)
    reviewer_step_id = advance_and_run(runtime, flow_id)
    reviewer_step = runtime.flow_service.get_step(reviewer_step_id)
    reviewer_record = runtime.agent_service.start_records[-1]
    assert reviewer_record.variables["round_id"] == round_id
    assert "target_metadata" not in reviewer_record.variables
    assert "context_brief" not in reviewer_record.variables
    assert f"Round sequence: {round_index}." in (reviewer_record.prompt or "")
    assert round_id not in (reviewer_record.prompt or "")
    assert "Review decl stage statement_nl." in (reviewer_record.prompt or "")
    assert "Pipeline position: planned --Statement NL--> specified" in (reviewer_record.prompt or "")
    assert "Review only this layer" in (reviewer_record.prompt or "")
    assert "Assigned declarations:" in (reviewer_record.prompt or "")
    assert "$lc-field-semantics" in (reviewer_record.prompt or "")
    assert "Required through: Proof Formal" in (reviewer_record.prompt or "")
    assert (
        "Round execution constraints: Keep this stage batch within the assigned declarations."
        in (reviewer_record.prompt or "")
    )
    assert (
        "Execution constraints: Preserve the source binders for main_result."
        in (reviewer_record.prompt or "")
    )
    assert "ContentPlan should split later frontiers." not in (reviewer_record.prompt or "")
    assert "round_id" not in reviewer_step.result.agent_fields()


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


def _seed_open_proof_planned_theorem(lean_runtime, repo_root: Path, *, decl_name: str) -> str:
    strategy_id, round_id, _round_index = create_round_with_decl(
        lean_runtime,
        repo_root,
        decl_name=decl_name,
        target_state=DeclState.PROVED,
    )
    assert lean_runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    assert lean_runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        nl=f"{decl_name} states True.",
    ).ok
    assert write_statement_formal_for_test(lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        lean_code=f"theorem {decl_name} : True := by trivial",
        lean_check=lean_check_payload(allow_sorry=True),
    ).ok
    set_current_decl_lean_name_for_test(
        lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        decl_name=decl_name,
    )
    assert lean_runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        nl="Use triviality.",
    ).ok
    advanced = lean_runtime.decl_graph.advance_stage_state(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage="proof_nl",
        decl_names=[decl_name],
    )
    assert advanced.ok, advanced.issues
    _close_executed_round(
        lean_runtime,
        repo_root,
        round_id=round_id,
        result_kind="blocked",
        reason="The helper proof remains open.",
        decl_name=decl_name,
    )
    return round_id


def _prove_committed_helper_theorem(lean_runtime, repo_root: Path, *, decl_name: str) -> None:
    strategy = lean_runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective=f"Prove {decl_name}.")
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = lean_runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective=f"Prove {decl_name}.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    change = lean_runtime.decl_graph.open_decl_update(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name=decl_name,
        objective=f"Prove {decl_name}.",
        start_stage="proof_formal",
        target_state=DeclState.PROVED,
    )
    assert change.ok, change.issues
    assert lean_runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_record.value.round_id).ok
    assert write_proof_formal_for_test(lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=decl_name,
        lean_code=f"theorem {decl_name} : True := by trivial",
        lean_check=lean_check_payload(),
    ).ok
    advanced = lean_runtime.decl_graph.advance_stage_state(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        stage="proof_formal",
        decl_names=[decl_name],
    )
    assert advanced.ok, advanced.issues
    _close_executed_round(
        lean_runtime,
        repo_root,
        round_id=round_record.value.round_id,
        result_kind="success",
        decl_name=decl_name,
    )


def _close_executed_round(
    lean_runtime,
    repo_root: Path,
    *,
    round_id: str,
    result_kind: str,
    decl_name: str,
    reason: str | None = None,
) -> None:
    seeded_round = lean_runtime.decl_graph.get_round(repo_root, node_path=NODE_PATH, round_id=round_id)
    assert seeded_round.ok and seeded_round.value is not None, seeded_round.issues
    for change_id in seeded_round.value.change_ids:
        assert lean_runtime.decl_graph.write_decl_change_summary(
            repo_root,
            node_path=NODE_PATH,
            round_id=round_id,
            change_id=change_id,
            summary=f"Seeded {decl_name}.",
        ).ok
    assert lean_runtime.decl_graph.write_round_summary(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        summary=f"Seeded theorem {decl_name}.",
    ).ok
    outcome = {
        "success": "completed",
        "blocked": "blocked",
        "failed": "failed",
    }[result_kind]
    recorded = lean_runtime.decl_graph.record_round_execution_result(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        outcome=outcome,
        reason=reason,
    )
    assert recorded.ok, recorded.issues
    terminal = lean_runtime.decl_graph.closeout_round_by_plan(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        result_kind=result_kind,
        reason=reason,
        acknowledged_by="test-content-plan",
    )
    assert terminal.ok, terminal.issues


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
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(repo_root)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_reviewer"

    record_passed_review(lean_runtime, repo_root, stage=review_stage, round_id=round_id)
    queue_review(runtime, repo_root, stage=stage, round_id=round_id, accepted=True)
    advance_and_run(runtime, flow_id)
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(repo_root)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "stage_gate_audit"

    gate_step_id = advance_and_run(runtime, flow_id)
    gate_step = runtime.flow_service.get_step(gate_step_id)
    assert gate_step.result.outcome == "stage_passed"
    assert set(gate_step.result.timings_ms) == {
        "review_context",
        "stage_candidate_validation",
        "stage_candidate_validation.nl_origin",
        "stage_candidate_validation.formal_sync_consistency",
        "stage_candidate_validation.dependency_visibility_readiness",
        "round_local_audit",
        "stage_state_mutation",
    }


def _expected_node_workdir(repo_root: Path) -> str:
    return str(repo_root.joinpath(repo_root.name, *NODE_PATH.split(".")))


def _write_statement_formal(lean_runtime, repo_root: Path, round_id: str):
    path_view = lean_runtime.lean_projection.decl_file.derive_decl_file_path(
        repo_root,
        node_path=NODE_PATH,
        decl_name="main_result",
        kind="theorem",
    )
    assert path_view.ok and path_view.value is not None, path_view.issues
    lean_code = Path(path_view.value.path).read_text(encoding="utf-8")
    result = write_statement_formal_for_test(lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        lean_code=lean_code,
        lean_check=lean_check_payload(contains_sorry=True),
    )
    if result.ok:
        set_current_decl_lean_name_for_test(
            lean_runtime,
            repo_root,
            node_path=NODE_PATH,
            decl_name="main_result",
        )
    return result


def _write_proof_formal(lean_runtime, repo_root: Path, round_id: str, *, deps: list[str] | None = None):
    path_view = lean_runtime.lean_projection.decl_file.derive_decl_file_path(
        repo_root,
        node_path=NODE_PATH,
        decl_name="main_result",
        kind="theorem",
    )
    assert path_view.ok and path_view.value is not None, path_view.issues
    lean_code = Path(path_view.value.path).read_text(encoding="utf-8").replace("sorry", "trivial")
    return write_proof_formal_for_test(lean_runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="main_result",
        lean_code=lean_code,
        lean_check=lean_check_payload(),
        deps=deps,
    )
