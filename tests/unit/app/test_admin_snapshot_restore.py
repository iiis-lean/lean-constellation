from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import (
    LeanAppConfig,
    LeanAdminApi,
    RepoRuntimeRegistry,
    RepoConfigUpdateInput,
    RepoPublicationPrepareInput,
    RequirementResumeInput,
    RuntimeSemanticAdvanceInput,
    SnapshotCreateInput,
    SnapshotListInput,
    SnapshotRestoreInput,
    create_app_runtime_services,
    initialize_repo_business_truth,
)
from lean_constellation.app.runtime import PairedRestoreInterlock, repo_restore_interlock_path
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.common.agent_steps import DeclStageReviewerAgentStep
from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services.decl_graph import DeclReviewMarkRecord, DeclStage
from lean_constellation.services.concurrency import RepoActivityRecoveryRequiredError
from tests.unit_services_helpers import publish_native_provider_release


def test_workspace_admin_rejects_restore_interlock_without_ark_history(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Repo"
    registry = RepoRuntimeRegistry(
        LeanAppConfig(workspace_root=workspace, materialize_agent_homes=False)
    )
    runtime = registry.workspace_runtime()
    assert initialize_repo_business_truth(runtime, repo_root).ok
    repo_record = repo_root / ".lean_constellation" / "repo.json"
    before = repo_record.read_bytes()
    interlock = PairedRestoreInterlock(
        repo_root=str(repo_root.resolve()),
        snapshot_id="snapshot-interrupted",
        recovery_required=True,
    )
    assert runtime.foundation.store.write_json_atomic(
        repo_restore_interlock_path(repo_root),
        interlock,
    ).ok
    admin = LeanAdminApi(
        runtime,
        workspace_root=workspace,
        repo_runtime_registry=registry,
    )

    result = admin.update_repo_config(RepoConfigUpdateInput(repo_root=repo_root))

    assert not result.ok
    assert result.issues[0].kind == "paired_restore_recovery_required"
    assert repo_record.read_bytes() == before
    assert not (repo_root / ".agent_runtime").exists()


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


@pytest.mark.parametrize("manifest_name", ["snapshot.json", "files_manifest.json"])
@pytest.mark.parametrize("schema_version", [None, 0], ids=["missing", "mismatch"])
def test_admin_snapshot_list_rejects_noncurrent_manifest_schema(
    tmp_path: Path,
    manifest_name: str,
    schema_version: int | None,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Repo"
    assert initialize_repo_business_truth(runtime, repo_root).ok
    admin = LeanAdminApi(runtime)
    created = admin.create_snapshot(
        SnapshotCreateInput(
            repo_root=repo_root,
            checkpoint_kind="manual_test_stable_point",
            label="current schema list",
        )
    )
    assert created.ok and created.value is not None, created.issues
    target_path = Path(created.value.root) / manifest_name
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    if schema_version is None:
        payload.pop("schema_version")
    else:
        payload["schema_version"] = schema_version
    target_path.write_text(json.dumps(payload), encoding="utf-8")
    before = target_path.read_bytes()

    listed = admin.list_snapshots(SnapshotListInput(repo_root=repo_root))

    assert not listed.ok
    assert listed.value is None
    assert listed.issues[0].kind == "repo_checkpoint_snapshot_schema_version_invalid"
    assert target_path.read_bytes() == before


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
    second_lc_manifest = json.loads((Path(second.value.root) / "snapshot.json").read_text(encoding="utf-8"))
    assert second_lc_manifest["ark_runtime_snapshot_id"] == second.value.ark_runtime_snapshot_id
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
    assert restored.value.ark_runtime_snapshot_id == second.value.ark_runtime_snapshot_id
    assert set(store.list_scope_ids()) == {repo_scope, node_scope}
    assert not node_report.exists()


def test_restore_previous_paired_checkpoint_rewinds_later_failed_round_truth(tmp_path) -> None:
    repo_root = tmp_path / "Repo"
    runtime_root = repo_root / ".agent_runtime"
    runtime = create_app_runtime_services(runtime_root=runtime_root)
    assert initialize_repo_business_truth(runtime, repo_root).ok
    scope_id = "repo:Repo:node:Main.Core"
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id=scope_id,
            params={
                "repo_key": "Repo",
                "repo_path": str(repo_root),
                "node_path": "Main.Core",
                "contract_version": 1,
            },
        ),
        enqueue=False,
    )

    def mark_round_boundary(flow, *, count: int, child_id: str, outcome: str, summary: str) -> None:  # noqa: ANN001
        flow.state.position = flow.state.position.model_copy(
            update={"phase": "callback_plan_agent", "round_index": count}
        )
        flow.state.decl_round_count = count
        flow.state.waiting_child_kind = "decl_graph_round"
        flow.state.completed_child_flow_id = child_id
        flow.state.completed_child_outcome = outcome
        flow.state.latest_callback_summary = summary

    runtime.ark.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: mark_round_boundary(
            flow,
            count=1,
            child_id="round_child_1",
            outcome="completed",
            summary="round_1 completed",
        ),
    )
    admin = LeanAdminApi(runtime)
    before_failed = admin.create_snapshot(
        SnapshotCreateInput(
            repo_root=repo_root,
            checkpoint_kind="after_content_decl_round_terminal",
            scope_ids=[scope_id],
            label="round_1 completed",
        )
    )
    assert before_failed.ok and before_failed.value is not None, before_failed.issues
    assert before_failed.value.ark_runtime_snapshot_id is not None

    runtime.ark.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: mark_round_boundary(
            flow,
            count=2,
            child_id="round_child_2",
            outcome="failed",
            summary="round_2 failed",
        ),
    )
    after_failed = admin.create_snapshot(
        SnapshotCreateInput(
            repo_root=repo_root,
            checkpoint_kind="after_content_decl_round_terminal",
            scope_ids=[scope_id],
            label="round_2 failed",
        )
    )
    assert after_failed.ok and after_failed.value is not None, after_failed.issues
    assert after_failed.value.ark_runtime_snapshot_id is not None
    assert after_failed.value.snapshot_id != before_failed.value.snapshot_id
    assert after_failed.value.ark_runtime_snapshot_id != before_failed.value.ark_runtime_snapshot_id

    before_manifest = json.loads((Path(before_failed.value.root) / "snapshot.json").read_text(encoding="utf-8"))
    assert before_manifest["ark_runtime_snapshot_id"] == before_failed.value.ark_runtime_snapshot_id
    restored = admin.restore_snapshot(
        SnapshotRestoreInput(
            repo_root=repo_root,
            snapshot_id=before_failed.value.snapshot_id,
            leave_runtime_paused=True,
        )
    )

    assert restored.ok and restored.value is not None, restored.issues
    assert restored.value.ark_runtime_snapshot_id == before_failed.value.ark_runtime_snapshot_id
    restored_flow = runtime.ark.flow_service.get_flow(flow_id)
    assert restored_flow.state.decl_round_count == 1
    assert restored_flow.state.completed_child_flow_id == "round_child_1"
    assert restored_flow.state.completed_child_outcome == "completed"
    assert restored_flow.state.latest_callback_summary == "round_1 completed"
    assert runtime.ark.pause_controller.is_paused() is True


def test_active_multi_node_batch_and_catalog_transaction_reject_physical_snapshot(tmp_path) -> None:
    repo_root = tmp_path / "Repo"
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    assert initialize_repo_business_truth(runtime, repo_root).ok
    runtime.repo_activity.reserve_content_batch(
        repo_root,
        batch_id="content_batch_test",
        node_paths=["Main.A", "Main.B"],
    )
    admin = LeanAdminApi(runtime)
    blocked_config = admin.update_repo_config(RepoConfigUpdateInput(repo_root=repo_root))
    assert blocked_config.ok is False
    assert blocked_config.issues[0].kind == "repo_maintenance_conflict"
    blocked_release = admin.preview_repo_release(repo_root)
    assert blocked_release.ok is False
    assert blocked_release.issues[0].kind == "repo_maintenance_conflict"
    active_batch = runtime.app.snapshot_runtime.check_repo_stable_point(
        repo_root,
        checkpoint_kind="manual_test_stable_point",
    )
    assert active_batch.ok and active_batch.value is not None
    assert active_batch.value.passed is False
    assert active_batch.value.issues[0].kind == "active_content_batch_snapshot_ineligible"

    runtime.repo_activity.release_content_batch(repo_root, batch_id="content_batch_test")
    with runtime.repo_activity.catalog_write(repo_root, "mathlib"):
        active_transaction = runtime.app.snapshot_runtime.check_repo_stable_point(
            repo_root,
            checkpoint_kind="manual_test_stable_point",
        )
    assert active_transaction.ok and active_transaction.value is not None
    assert active_transaction.value.passed is False
    assert active_transaction.value.issues[0].kind == "repo_catalog_transaction_active"


def test_admin_and_snapshot_map_unknown_frontier_to_recovery_required(
    tmp_path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "Repo"
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    assert initialize_repo_business_truth(runtime, repo_root).ok
    existing = runtime.app.snapshot_runtime.create_repo_stable_point_snapshot(
        repo_root,
        checkpoint_kind="manual_test_stable_point",
    )
    assert existing.ok and existing.value is not None

    def fail_frontier(_repo_root: Path):
        raise RepoActivityRecoveryRequiredError("frontier recovery required")

    monkeypatch.setattr(runtime.repo_activity, "_persisted_content_batches", fail_frontier)
    admin_result = LeanAdminApi(runtime).update_repo_config(
        RepoConfigUpdateInput(repo_root=repo_root)
    )
    snapshot_result = runtime.app.snapshot_runtime.create_repo_stable_point_snapshot(
        repo_root,
        checkpoint_kind="manual_test_stable_point",
    )
    eligibility = runtime.app.snapshot_runtime.check_repo_stable_point(
        repo_root,
        checkpoint_kind="manual_test_stable_point",
    )
    restore_result = runtime.app.snapshot_runtime.restore_repo_checkpoint_snapshot(
        repo_root,
        snapshot_id=existing.value.snapshot_id,
    )

    assert not admin_result.ok
    assert admin_result.issues[0].kind == "repo_activity_recovery_required"
    assert not snapshot_result.ok
    assert snapshot_result.issues[0].kind == "repo_activity_recovery_required"
    assert eligibility.ok and eligibility.value is not None
    assert not eligibility.value.passed
    assert eligibility.value.issues[0].kind == "repo_activity_recovery_required"
    assert not restore_result.ok
    assert restore_result.issues[0].kind == "repo_activity_recovery_required"


def test_snapshot_eligibility_maps_second_frontier_read_failure(
    tmp_path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "Repo"
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    assert initialize_repo_business_truth(runtime, repo_root).ok
    calls = 0

    def list_flows(**filters):  # noqa: ANN003, ANN201
        nonlocal calls
        del filters
        calls += 1
        if calls == 1:
            return []
        raise RuntimeError("second frontier read failed")

    monkeypatch.setattr(runtime, "list_flows", list_flows)
    result = runtime.app.snapshot_runtime.check_repo_stable_point(
        repo_root,
        checkpoint_kind="manual_test_stable_point",
    )

    assert calls == 2
    assert result.ok and result.value is not None
    assert not result.value.passed
    assert result.value.issues[0].kind == "repo_activity_recovery_required"


def test_paired_restore_half_failure_persists_interlock_and_exact_retry_skips_completed_half(
    tmp_path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "Repo"
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    assert initialize_repo_business_truth(runtime, repo_root).ok
    marker = repo_root / "Marker.txt"
    marker.write_text("before\n", encoding="utf-8")
    admin = LeanAdminApi(runtime)
    coordinator_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={"repo_key": "Repo", "repo_root": str(repo_root), "start_mode": "admin_start"},
        ),
        enqueue=False,
    )
    created = admin.create_snapshot(
        SnapshotCreateInput(repo_root=repo_root, checkpoint_kind="manual_test_stable_point")
    )
    assert created.ok and created.value is not None
    marker.write_text("after\n", encoding="utf-8")

    ark_calls = 0
    original_ark_restore = runtime.app.snapshot_runtime.ark_snapshot.restore_runtime_snapshot

    def counted_ark_restore(*args, **kwargs):
        nonlocal ark_calls
        ark_calls += 1
        return original_ark_restore(*args, **kwargs)

    original_lc_restore = runtime.validation_snapshot.restore_repo_checkpoint_snapshot
    lc_calls = 0

    def fail_lc_once(*args, **kwargs):
        nonlocal lc_calls
        lc_calls += 1
        if not kwargs.get("dry_run") and lc_calls == 1:
            return runtime.foundation.fail(
                runtime.foundation.issue("injected_lc_restore_failure", "Injected LC restore failure.")
            )
        return original_lc_restore(*args, **kwargs)

    monkeypatch.setattr(runtime.app.snapshot_runtime.ark_snapshot, "restore_runtime_snapshot", counted_ark_restore)
    monkeypatch.setattr(runtime.validation_snapshot, "restore_repo_checkpoint_snapshot", fail_lc_once)

    first = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=created.value.snapshot_id)
    )
    assert first.ok is False
    assert runtime.app.snapshot_runtime._restore_interlock_path(repo_root).exists()  # noqa: SLF001
    blocked_snapshot = admin.create_snapshot(
        SnapshotCreateInput(repo_root=repo_root, checkpoint_kind="manual_test_stable_point")
    )
    assert blocked_snapshot.ok is False
    assert blocked_snapshot.issues[0].kind == "paired_restore_recovery_required"
    restarted_runtime = create_app_runtime_services(runtime_root=tmp_path / ".restarted-runtime")
    restarted_gate = restarted_runtime.app.snapshot_runtime.check_repo_recovery_interlock(repo_root)
    assert restarted_gate.ok is False
    assert restarted_gate.issues[0].kind == "paired_restore_recovery_required"

    blocked_admission = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_batch",
            repo_key="Repo",
            coordinator_flow_id=coordinator_id,
            expected_source_submission_id="sub_exact",
        )
    )
    assert blocked_admission.ok is False
    assert blocked_admission.issues[0].kind == "paired_restore_recovery_required"
    blocked_step_admission = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="step",
            action="logic",
            scope_id="repo:Repo",
        )
    )
    assert blocked_step_admission.ok is False
    assert blocked_step_admission.issues[0].kind == "paired_restore_recovery_required"
    blocked_repo_mutation = admin.update_repo_config(
        RepoConfigUpdateInput(repo_root=repo_root)
    )
    assert blocked_repo_mutation.ok is False
    assert blocked_repo_mutation.issues[0].kind == "paired_restore_recovery_required"
    blocked_release = admin.preview_repo_release(repo_root)
    assert blocked_release.ok is False
    assert blocked_release.issues[0].kind == "paired_restore_recovery_required"
    blocked_publication = admin.prepare_repo_publication(
        RepoPublicationPrepareInput(repo_root=repo_root)
    )
    assert blocked_publication.ok is False
    assert blocked_publication.issues[0].kind == "paired_restore_recovery_required"

    retried = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=created.value.snapshot_id)
    )
    assert retried.ok and retried.value is not None, retried.issues
    assert ark_calls == 1
    assert marker.read_text(encoding="utf-8") == "before\n"
    assert not runtime.app.snapshot_runtime._restore_interlock_path(repo_root).exists()  # noqa: SLF001


def test_size_one_internal_checkpoint_requires_exact_terminal_current_child(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "Repo"
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    assert initialize_repo_business_truth(runtime, repo_root).ok
    coordinator_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={
                "repo_key": "Repo",
                "repo_root": str(repo_root),
                "start_mode": "admin_start",
            },
        ),
        enqueue=False,
    )

    def mark_batch(flow) -> None:  # noqa: ANN001
        flow.state.position = flow.state.position.model_copy(
            update={"phase": "waiting_content_tasks"}
        )
        flow.state.pending_dispatch_kind = "content_tasks"
        flow.state.pending_dispatch_source_step_id = "coordinator-callback"
        flow.state.pending_dispatch_source_submission_id = "sub-batch"
        flow.state.pending_content_node_paths = ["Main.Core"]
        flow.state.waiting_dispatch_step_id = "dispatch-batch"

    runtime.ark.flow_service.store.update_flow_record(coordinator_id, mark_batch)
    task_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id="repo:Repo:node:Main.Core",
            params={
                "repo_key": "Repo",
                "repo_path": str(repo_root),
                "node_path": "Main.Core",
                "contract_version": 1,
                "max_parallel_content_node_tasks": 1,
            },
        ),
        parent_flow_id=coordinator_id,
        parent_dispatch_step_id="dispatch-batch",
        enqueue=False,
    )

    def fake_internal_boundary(flow) -> None:  # noqa: ANN001
        flow.state.position = flow.state.position.model_copy(
            update={"phase": "callback_plan_agent"}
        )
        flow.state.waiting_dispatch_step_id = "round-dispatch"
        flow.state.waiting_child_kind = "decl_graph_round"
        flow.state.completed_child_flow_id = "missing-round-child"

    runtime.ark.flow_service.store.update_flow_record(task_id, fake_internal_boundary)

    created = LeanAdminApi(runtime).create_snapshot(
        SnapshotCreateInput(
            repo_root=repo_root,
            checkpoint_kind="after_content_decl_round_terminal",
            node_paths=["Main.Core"],
        )
    )

    assert created.ok is False
    assert created.issues[0].kind == "active_content_batch_snapshot_ineligible"


def test_restore_runtime_preflight_failure_does_not_create_recovery_interlock(
    tmp_path,
    monkeypatch,
) -> None:
    repo_root = tmp_path / "Repo"
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    assert initialize_repo_business_truth(runtime, repo_root).ok
    admin = LeanAdminApi(runtime)
    created = admin.create_snapshot(
        SnapshotCreateInput(repo_root=repo_root, checkpoint_kind="manual_test_stable_point")
    )
    assert created.ok and created.value is not None

    monkeypatch.setattr(
        runtime.app.snapshot_runtime.runtime_stability,
        "check_repo_stable_point",
        lambda *_args, **_kwargs: runtime.foundation.ok(
            runtime.foundation.gate_failed(
                "ark_runtime_stability",
                runtime.foundation.issue("runtime_not_stable", "A runner is still active."),
                summary="Runtime is not stable.",
            )
        ),
    )
    restored = admin.restore_snapshot(
        SnapshotRestoreInput(repo_root=repo_root, snapshot_id=created.value.snapshot_id)
    )

    assert restored.ok is False
    assert restored.issues[0].kind == "runtime_not_stable"
    assert not runtime.app.snapshot_runtime._restore_interlock_path(repo_root).exists()  # noqa: SLF001
