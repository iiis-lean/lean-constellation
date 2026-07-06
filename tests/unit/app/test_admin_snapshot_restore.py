from __future__ import annotations

from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import (
    LeanAdminApi,
    SnapshotCreateInput,
    SnapshotRestoreInput,
    create_app_runtime_services,
    initialize_repo_runtime,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.common.agent_steps import DeclStageReviewerAgentStep
from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services.decl_graph import DeclReviewMarkRecord, DeclStage


def test_admin_snapshot_create_and_restore_leaves_runtime_paused(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Repo"
    assert initialize_repo_runtime(runtime, repo_root).ok
    prep = RepoPreparationInput(
        goal="Prepare provider.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
    )
    assert runtime.repo_workspace.preparation.write_preparation_input(repo_root, input=prep).ok
    marker = repo_root / "Marker.txt"
    marker.write_text("before\n", encoding="utf-8")
    admin = LeanAdminApi(runtime)

    created = admin.create_snapshot(
        SnapshotCreateInput(repo_root=repo_root, checkpoint_kind="requirement_bootstrap_terminal", label="unit")
    )
    assert created.ok and created.value is not None
    marker.write_text("after\n", encoding="utf-8")

    restored = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=created.value.snapshot_id, leave_runtime_paused=True)
    )

    assert restored.ok and restored.value is not None
    assert marker.read_text(encoding="utf-8") == "before\n"
    assert restored.value.leave_runtime_paused is True
    assert runtime.ark.pause_controller.is_paused() is True


def test_admin_snapshot_restore_restores_decl_review_step_state(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Repo"
    assert initialize_repo_runtime(runtime, repo_root).ok
    scope_id = "repo:Repo"
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id=scope_id,
            params={"repo_key": "Repo", "repo_path": str(repo_root), "node_path": "Main.Topic.Core"},
        ),
        enqueue=False,
    )
    step_id = "step_review_state"
    step = DeclStageReviewerAgentStep(
        step_id=step_id,
        flow_id=flow_id,
        scope_id=scope_id,
        state=DeclStageReviewerStepState(
            agent_role="statement_nl_reviewer",
            agent_type="StatementNLReviewerAgent",
            review_marks=[
                DeclReviewMarkRecord(
                    round_id="round_1",
                    node_path="Main.Topic.Core",
                    stage=DeclStage.STATEMENT_NL,
                    decl_name="main_result",
                    passed=True,
                    summary="Statement accepted.",
                )
            ],
        ),
    )
    runtime.ark.step_service.create_step(step, enqueue=False)
    runtime.ark.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: (
            flow.step_ids.append(step_id),
            setattr(flow, "current_step_id", step_id),
            setattr(flow, "status", FlowStatus.RUNNING),
        ),
    )
    admin = LeanAdminApi(runtime)

    created = admin.create_snapshot(
        SnapshotCreateInput(
            repo_root=repo_root,
            checkpoint_kind="manual_test_stable_point",
            scope_ids=[scope_id],
            label="review_state",
        )
    )
    assert created.ok and created.value is not None, created.issues

    def clear_review_marks(stored_step) -> None:
        assert isinstance(stored_step.state, DeclStageReviewerStepState)
        stored_step.state.review_marks = []

    runtime.ark.step_service.store.update_step_record(step_id, clear_review_marks)
    assert runtime.ark.step_service.store.get_step(step_id).state.review_marks == []

    restored = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=created.value.snapshot_id, leave_runtime_paused=True)
    )

    assert restored.ok and restored.value is not None, restored.issues
    restored_step = runtime.ark.step_service.store.get_step(step_id)
    assert isinstance(restored_step.state, DeclStageReviewerStepState)
    assert [mark.decl_name for mark in restored_step.state.review_marks] == ["main_result"]
