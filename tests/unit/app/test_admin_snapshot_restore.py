from __future__ import annotations

import json
from pathlib import Path

from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import (
    LeanAdminApi,
    RequirementResumeInput,
    SnapshotCreateInput,
    SnapshotRestoreInput,
    create_app_runtime_services,
    initialize_repo_business_truth,
)
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.common.agent_steps import DeclStageReviewerAgentStep
from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services.decl_graph import DeclReviewMarkRecord, DeclStage
from tests.unit_services_helpers import publish_native_provider_release


def test_admin_snapshot_create_and_restore_leaves_runtime_paused(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Repo"
    assert initialize_repo_business_truth(runtime, repo_root).ok
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
    assert runtime.ark.pause_controller is not None
    assert runtime.ark.pause_controller.is_paused()
    assert runtime.ark.pause_controller.is_paused() is True


def test_admin_snapshot_restore_restores_decl_review_step_state(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Repo"
    assert initialize_repo_business_truth(runtime, repo_root).ok
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


def test_requirement_resume_after_snapshot_restore_uses_original_flow_and_agent(tmp_path) -> None:
    consumer = tmp_path / "Consumer"
    provider = tmp_path / "Provider"
    runtime = create_app_runtime_services(runtime_root=consumer / ".agent_runtime")
    assert initialize_repo_business_truth(runtime, consumer).ok
    assert initialize_repo_business_truth(runtime, provider).ok
    assert runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        reason="Need provider result.",
    ).ok
    assert runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
    ).ok
    scope_id = "repo:Consumer"
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id=scope_id,
            params={
                "repo_key": "Consumer",
                "repo_root": str(consumer),
                "start_mode": "admin_start",
                "start_reason": "snapshot resume test",
            },
        ),
        enqueue=False,
    )
    agent = runtime.ark.agent_service.store.create_agent_record(
        scope_id=scope_id,
        agent_type="CoordinatorAgent",
        provider_type="codex",
        home_id="CoordinatorAgent",
    )

    def mark_waiting(flow) -> None:
        flow.status = FlowStatus.WAITING
        flow.state.position.phase = "waiting_requirement"
        flow.state.waiting_requirement_name = "need_provider"
        flow.agent_bindings.by_role["coordinator"] = agent.agent_id

    runtime.ark.flow_service.store.update_flow_record(flow_id, mark_waiting)
    admin = LeanAdminApi(runtime)
    created = admin.create_snapshot(
        SnapshotCreateInput(
            repo_root=consumer,
            checkpoint_kind="manual_test_stable_point",
            scope_ids=[scope_id],
            label="waiting requirement",
        )
    )
    assert created.ok and created.value is not None, created.issues

    def corrupt(flow) -> None:
        flow.status = FlowStatus.RUNNING
        flow.state.position.phase = "coordinator_agent"
        flow.state.waiting_requirement_name = None
        flow.agent_bindings.by_role.clear()

    runtime.ark.flow_service.store.update_flow_record(flow_id, corrupt)
    restored = admin.restore_snapshot(
        SnapshotRestoreInput(
            repo_root=consumer,
            snapshot_id=created.value.snapshot_id,
            leave_runtime_paused=True,
        )
    )
    assert restored.ok and restored.value is not None, restored.issues
    restored_flow = runtime.ark.flow_service.get_flow(flow_id)
    assert restored_flow.status is FlowStatus.WAITING
    assert restored_flow.state.position.phase == "waiting_requirement"
    assert restored_flow.agent_bindings.get("coordinator") == agent.agent_id
    assert runtime.ark.agent_service.get_agent(agent.agent_id).provider_type == "codex"

    publish_native_provider_release(runtime, provider, summary="Provider ready.")
    assert runtime.repo_workspace.requirement.mark_requirement_satisfied(
        consumer,
        requirement_name="need_provider",
        provider_repo="Provider",
    ).ok
    resumed = admin.resume_requirement(
        RequirementResumeInput(
            consumer_repo_root=consumer,
            requirement_name="need_provider",
            provider_repo="Provider",
            enqueue=False,
        )
    )

    assert resumed.ok and resumed.value is not None, resumed.issues
    assert resumed.value.resume_flow.flow_id == flow_id
    assert len(runtime.ark.flow_service.list_flows(flow_type="native_repo_coordinator")) == 1


def test_repo_checkpoint_captures_all_runtime_scopes_and_prunes_later_scopes(tmp_path) -> None:
    repo_root = tmp_path / "Repo"
    runtime_root = repo_root / ".agent_runtime"
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    assert initialize_repo_business_truth(runtime, repo_root).ok
    store = runtime.ark.agent_service.store
    repo_scope = "repo:Repo"
    node_scope = "repo:Repo:node:n_core"
    late_scope = "repo:Repo:node:n_late"
    store.create_agent_record(scope_id=repo_scope, agent_type="CoordinatorAgent", provider_type="codex")
    node_agent = store.create_agent_record(
        scope_id=node_scope,
        agent_type="ContentPlanAgent",
        provider_type="codex",
    )
    node_report = Path(
        runtime.ark.agent_service.get_default_trace_report_paths(node_agent.agent_id).latest_json_path
    )
    node_report.parent.mkdir(parents=True)
    node_report.write_text('{"version": "before"}\n', encoding="utf-8")
    admin = LeanAdminApi(runtime)

    created = admin.create_snapshot(
        SnapshotCreateInput(
            repo_root=repo_root,
            checkpoint_kind="manual_test_stable_point",
            scope_ids=[repo_scope],
            label="all runtime scopes",
        )
    )

    assert created.ok and created.value is not None, created.issues
    assert created.value.ark_runtime_snapshot_id is not None
    ark_manifest_path = (
        runtime_root
        / "snapshots"
        / "runtime"
        / created.value.ark_runtime_snapshot_id
        / "snapshot.json"
    )
    ark_manifest = json.loads(ark_manifest_path.read_text(encoding="utf-8"))
    assert set(ark_manifest["scope_snapshot_ids"]) == {repo_scope, node_scope}

    second = admin.create_snapshot(
        SnapshotCreateInput(
            repo_root=repo_root,
            checkpoint_kind="manual_test_stable_point",
            scope_ids=[repo_scope],
            label="refresh repo scope only",
        )
    )
    assert second.ok and second.value is not None, second.issues
    assert second.value.ark_runtime_snapshot_id is not None
    second_ark_manifest_path = (
        runtime_root
        / "snapshots"
        / "runtime"
        / second.value.ark_runtime_snapshot_id
        / "snapshot.json"
    )
    second_ark_manifest = json.loads(second_ark_manifest_path.read_text(encoding="utf-8"))
    assert set(second_ark_manifest["scope_snapshot_ids"]) == {repo_scope, node_scope}
    assert second_ark_manifest["scope_snapshot_ids"][repo_scope] != ark_manifest["scope_snapshot_ids"][repo_scope]
    assert second_ark_manifest["scope_snapshot_ids"][node_scope] == ark_manifest["scope_snapshot_ids"][node_scope]

    node_report.write_text('{"version": "after"}\n', encoding="utf-8")
    store.create_agent_record(scope_id=late_scope, agent_type="ContentPlanAgent", provider_type="codex")
    assert late_scope in store.list_scope_ids()
    restored = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=second.value.snapshot_id)
    )

    assert restored.ok and restored.value is not None, restored.issues
    assert set(store.list_scope_ids()) == {repo_scope, node_scope}
    assert not node_report.exists()
