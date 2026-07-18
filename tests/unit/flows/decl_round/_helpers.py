from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.decl_round.submissions import (
    DeclStageReviewSubmittedSubmission,
    DeclStageWorkerBlockedSubmission,
    DeclStageWorkerCompletedSubmission,
)
from lean_constellation.services.decl_graph import DeclRoundResultKind, DeclStage, DeclState
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.runtime import LeanRuntimeServices
from tests.unit_services_helpers import (
    initialize_native_test_repo,
    lean_check_payload,
    make_runtime,
    set_current_decl_lean_name_for_test,
    write_proof_formal_for_test,
    write_statement_formal_for_test,
)


NODE_PATH = "Main.Topic.Core"


def make_decl_round_runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, LeanRuntimeServices, Path]:
    lean_runtime = make_runtime()
    repo_root = tmp_path / "Repo"
    repo_root.mkdir(parents=True)
    setup_content_node(lean_runtime, repo_root)
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    return flow_runtime, lean_runtime, repo_root


def setup_content_node(runtime: LeanRuntimeServices, repo_root: Path) -> None:
    initialize_native_test_repo(repo_root, project_name=repo_root.name)
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(
        repo_root,
        path="Main.Topic",
        goal="Topic goal.",
        boundary="Topic boundary.",
    ).ok
    content = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    )
    assert content.ok, content.issues


def create_round_with_decl(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    decl_name: str = "main_result",
    kind: str = "theorem",
    target_state: DeclState = DeclState.PROVED,
    require_target_state_satisfied: bool = True,
    public: bool = False,
) -> tuple[str, str, int]:
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Strategy objective.")
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Round objective.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name=decl_name,
        kind=kind,
        objective=f"Create {decl_name}.",
        summary=f"{decl_name} summary.",
        public=public,
        target_state=target_state,
        require_target_state_satisfied=require_target_state_satisfied,
    )
    assert created.ok, created.issues
    return strategy.value.strategy_id, round_record.value.round_id, round_record.value.round_index


def seed_committed_theorem(runtime: LeanRuntimeServices, repo_root: Path, *, decl_name: str = "old_result") -> None:
    strategy_id, round_id, _round_index = create_round_with_decl(runtime, repo_root, decl_name=decl_name)
    started = runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id)
    assert started.ok, started.issues
    assert runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        nl="The old result states True.",
    ).ok
    assert write_statement_formal_for_test(runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        lean_code=f"theorem {decl_name} : True := by trivial",
        lean_check=lean_check_payload(),
    ).ok
    set_current_decl_lean_name_for_test(
        runtime,
        repo_root,
        node_path=NODE_PATH,
        decl_name=decl_name,
    )
    assert runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        nl="Use triviality.",
    ).ok
    assert write_proof_formal_for_test(runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=decl_name,
        lean_code=f"theorem {decl_name} : True := by trivial",
        lean_check=lean_check_payload(),
    ).ok
    assert runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name=decl_name, state=DeclState.PROVED).ok
    seeded_round = runtime.decl_graph.get_round(repo_root, node_path=NODE_PATH, round_id=round_id)
    assert seeded_round.ok and seeded_round.value is not None, seeded_round.issues
    for change_id in seeded_round.value.change_ids:
        assert runtime.decl_graph.write_decl_change_summary(
            repo_root,
            node_path=NODE_PATH,
            round_id=round_id,
            change_id=change_id,
            summary=f"Seeded {decl_name}.",
        ).ok
    assert runtime.decl_graph.write_round_summary(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        summary=f"Seeded theorem {decl_name}.",
    ).ok
    terminal = runtime.decl_graph.mark_round_terminal(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
        reason=f"{strategy_id} seed completed.",
    )
    assert terminal.ok, terminal.issues


def commit_content_contract_head(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    decl_graph_head: dict[str, int],
) -> None:
    for decl_name in decl_graph_head:
        synced = runtime.lean_projection.sync_decl_file_after_revision_reset(
            repo_root,
            node_path=NODE_PATH,
            decl_name=decl_name,
        )
        assert synced.ok, synced.issues
    current = runtime.node.contract.get_edit_contract(repo_root, node_path=NODE_PATH)
    assert current.ok and current.value is not None, current.issues
    current.value.contract.decl_graph_head.update(decl_graph_head)
    path = runtime.node.node_tree.node_store.contract_path(
        repo_root,
        node_id=current.value.node_id,
        version=current.value.contract.version,
    )
    written = runtime.foundation.store.write_json_atomic(
        path,
        current.value.contract,
        mode=WriteMode.UPDATE_EXISTING,
    )
    assert written.ok, written.issues
    committed = runtime.node.commit_content_contract(repo_root, node_path=NODE_PATH, summary="Commit tested declaration heads.")
    assert committed.ok, committed.issues


def start_decl_round_flow(
    runtime: FakeLeanFlowRuntime,
    repo_root: Path,
    *,
    strategy_id: str,
    round_id: str,
    round_index: int = 1,
    contract_version: int = 1,
) -> str:
    return runtime.start_flow(
        "decl_graph_round",
        {
            "repo_key": repo_root.name,
            "repo_path": str(repo_root),
            "node_path": NODE_PATH,
            "contract_version": contract_version,
            "strategy_id": strategy_id,
            "round_id": round_id,
            "round_index": round_index,
            "summary": "Run test decl round.",
        },
        scope_id=f"repo:{repo_root.name}:node:{NODE_PATH}",
    )


def advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def queue_worker_completed(runtime: FakeLeanFlowRuntime, repo_root: Path, *, stage: str, round_id: str, decl_name: str = "main_result") -> None:
    runtime.agent_service.queue_submission(
        DeclStageWorkerCompletedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="decl_stage_worker_completed",
            tool_name="submit_stage_worker_completed",
            repo_key=repo_root.name,
            node_path=NODE_PATH,
            stage=stage,
            round_id=round_id,
            completed_decl_names=[decl_name],
            summary=f"{stage} worker completed.",
        )
    )


def queue_worker_blocked(runtime: FakeLeanFlowRuntime, repo_root: Path, *, stage: str, round_id: str, reason: str, decl_name: str = "main_result") -> None:
    runtime.agent_service.queue_submission(
        DeclStageWorkerBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="decl_stage_worker_blocked",
            tool_name="submit_stage_worker_blocked",
            repo_key=repo_root.name,
            node_path=NODE_PATH,
            stage=stage,
            round_id=round_id,
            reason=reason,
            affected_decl_names=[decl_name],
            summary=reason,
        )
    )


def queue_review(runtime: FakeLeanFlowRuntime, repo_root: Path, *, stage: str, round_id: str, accepted: bool, decl_name: str = "main_result") -> None:
    runtime.agent_service.queue_submission(
        DeclStageReviewSubmittedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="decl_stage_review_submitted",
            tool_name="submit_stage_review",
            repo_key=repo_root.name,
            node_path=NODE_PATH,
            stage=stage,
            round_id=round_id,
            accepted=accepted,
            retry_required=not accepted,
            reviewed_decl_names=[decl_name],
            failed_decl_names=[] if accepted else [decl_name],
            missing_decl_names=[],
            summary=f"{stage} review {'accepted' if accepted else 'rejected'}.",
        )
    )


def record_passed_review(runtime: LeanRuntimeServices, repo_root: Path, *, stage: DeclStage, round_id: str, decl_name: str = "main_result") -> None:
    mark = runtime.decl_graph.record_decl_review(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage=stage,
        decl_name=decl_name,
        passed=True,
        summary=f"{stage.value} accepted.",
    )
    assert mark.ok, mark.issues


def assert_completed(flow_runtime: FakeLeanFlowRuntime, flow_id: str) -> None:
    flow = flow_runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
