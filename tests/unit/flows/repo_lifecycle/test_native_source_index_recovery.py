from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_runtime_kit.flow.models import BaseStepError, FlowStatus, StepStatus, utc_now_iso
from pydantic import ValidationError

from lean_constellation.app.admin_api import (
    LeanAdminApi,
    NativeSourceIndexRecoveryPreviewInput,
    NativeSourceIndexRecoveryStartInput,
    StartFlowInput,
)
from lean_constellation.domain.repo_recovery import NativeSourceIndexRecoveryContract
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.repo_lifecycle.submissions import (
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)
from tests.unit.flows.repo_lifecycle.test_native_repo_preparation_flow import (
    _advance_and_run,
    _prepare_native_repo,
    _run_to_source_child_waiting,
    _runtime,
)


def _complete_rejected_draft(lean_runtime, repo_root: Path) -> None:  # noqa: ANN001
    material = lean_runtime.material
    assert material.set_source_index_overview(repo_root, overview="Topology source index.").ok
    created = material.create_source_block(
        repo_root,
        parent_id="root",
        kind="statement",
        title="Topology fact",
        summary="The first rejected draft.",
    )
    assert created.ok and created.value is not None
    block_id = created.value.block_id
    assert material.add_source_block_ref(
        repo_root,
        block_id=block_id,
        path="README.md",
        start_line=1,
        end_line=5,
        role="primary",
    ).ok
    assert material.mark_block_refs_done(repo_root, block_id=block_id).value.passed
    assert material.mark_block_links_done(repo_root, block_id=block_id).value.passed
    assert material.mark_block_completed(repo_root, block_id=block_id).value.passed
    assert material.set_file_survey_status(
        repo_root,
        path="README.md",
        status="surveyed",
        summary="Surveyed.",
    ).ok
    assert material.set_file_indexing_status(repo_root, path="README.md", status="indexed").ok


def _fail_builder_after_reviewer_rejection(
    runtime,
    child_flow_id: str,
    *,
    failure_message: str = "home materialized file hash mismatch: .codex/config.toml",
) -> str:  # noqa: ANN001
    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("builder"),
            tool_name="submit_source_index_builder_round",
            summary="Builder round one.",
            validation_summary="Draft ready for review.",
        )
    )
    _advance_and_run(runtime, child_flow_id)
    runtime.agent_service.queue_submission(
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("reviewer"),
            tool_name="submit_source_index_review_round",
            summary="Reviewer rejected round one.",
            approved=False,
            feedback="Repair the exact source range without replacing the rejected draft.",
        )
    )
    _advance_and_run(runtime, child_flow_id)
    failed_step_id = runtime.flow_service.advance_flow(child_flow_id)
    assert failed_step_id is not None

    def fail_step(step) -> None:  # noqa: ANN001
        now = utc_now_iso()
        step.status = StepStatus.FAILED
        step.error = BaseStepError(
            error_type="step_run_exception",
            message=failure_message,
        )
        step.started_at = now
        step.finished_at = now

    runtime.flow_service.store.update_step_record(failed_step_id, fail_step)
    runtime.flow_service.handle_step_terminal(failed_step_id)
    failed = runtime.flow_service.get_flow(child_flow_id)
    assert failed.status is FlowStatus.FAILED
    assert failed.state.review_round == 2
    assert failed.state.latest_reviewer_feedback
    return failed_step_id


def _failed_native_source_index(
    runtime,
    lean_runtime,
    repo_root: Path,
    *,
    failure_message: str = "home materialized file hash mismatch: .codex/config.toml",
):  # noqa: ANN001
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=False)
    parent_id, child_id = _run_to_source_child_waiting(runtime, repo_root)
    for _ in range(4):
        _advance_and_run(runtime, child_id)
    _complete_rejected_draft(lean_runtime, repo_root)
    failed_step_id = _fail_builder_after_reviewer_rejection(
        runtime,
        child_id,
        failure_message=failure_message,
    )
    assert runtime.flow_service.prepare_flow_for_advance(parent_id)
    assert runtime.flow_service.get_flow(parent_id).status is FlowStatus.FAILED
    return parent_id, child_id, failed_step_id


def test_recovery_preview_and_successor_preserve_rejected_draft_semantics(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    parent_id, child_id, failed_step_id = _failed_native_source_index(
        runtime, lean_runtime, repo_root
    )
    old_parent = runtime.flow_service.get_flow(parent_id).model_dump(mode="json")
    old_child = runtime.flow_service.get_flow(child_id).model_dump(mode="json")
    old_step = runtime.flow_service.get_step(failed_step_id).model_dump(mode="json")
    old_flow_ids = {flow.flow_id for flow in runtime.flow_service.list_flows()}
    old_agent_ids = set(runtime.agent_service.agents)
    old_agents = deepcopy(runtime.agent_service.agents)
    old_start_records = {
        agent_id: deepcopy(
            [record for record in runtime.agent_service.start_records if record.agent_id == agent_id]
        )
        for agent_id in old_agent_ids
    }

    admin = LeanAdminApi(lean_runtime)
    preview = admin.preview_native_source_index_recovery(
        NativeSourceIndexRecoveryPreviewInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
        )
    )
    assert preview.ok and preview.value is not None
    plan = preview.value
    failed_child = runtime.flow_service.get_flow(child_id)
    current_draft = lean_runtime.material.source_index.get_source_index_model(repo_root).value
    assert plan.failed_source_index_flow_id == child_id
    assert plan.failed_step_id == failed_step_id
    assert plan.failed_step_error_type == "step_run_exception"
    assert plan.failed_step_error_message == (
        "home materialized file hash mismatch: .codex/config.toml"
    )
    assert plan.pre_run_mutation_checkpoint_id == failed_child.state.pre_update_checkpoint_id
    assert plan.baseline_digest == failed_child.state.baseline_digest
    assert plan.draft_digest == lean_runtime.material.source_index.canonical_source_index_digest(
        current_draft
    )
    assert plan.review_round == 2
    assert plan.reviewer_feedback == (
        "Repair the exact source range without replacing the rejected draft."
    )

    started = admin.recover_native_source_index(
        NativeSourceIndexRecoveryStartInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
            expected_recovery_token=plan.recovery_token,
            enqueue=False,
        )
    )
    assert started.ok and started.value is not None
    successor_parent_id = started.value.flow_id
    assert successor_parent_id not in old_flow_ids
    successor_parent = runtime.flow_service.get_flow(successor_parent_id)
    assert successor_parent.input.start_reason == "repair_resume"
    assert successor_parent.input.recovery.recovery_token == plan.recovery_token

    _advance_and_run(runtime, successor_parent_id)
    dispatch_step_id = _advance_and_run(runtime, successor_parent_id)
    children = runtime.flow_service.store.list_child_flows(
        parent_flow_id=successor_parent_id,
        parent_dispatch_step_id=dispatch_step_id,
    )
    assert len(children) == 1
    successor_child_id = children[0].flow_id
    successor_child = runtime.flow_service.get_flow(successor_child_id)
    assert successor_child.input.start_reason == "recovery"
    assert successor_child.input.recovery.recovery_token == plan.recovery_token

    # A bounded scheduler resumes the runtime while this deterministic Step is
    # running. Revalidation may ignore only its own running Step, not other work.
    lean_runtime.ark.schedule_service = SimpleNamespace(
        active_flow_advances=set(),
        enqueue_flow=lambda _flow_id: None,
        enqueue_step=lambda _step_id: None,
    )
    lean_runtime.ark.pause_controller = SimpleNamespace(is_paused=lambda _scope_id=None: False)
    _advance_and_run(runtime, successor_child_id)
    recovered = runtime.flow_service.get_flow(successor_child_id)
    assert recovered.state.position.phase == "builder"
    assert recovered.state.review_round == 2
    assert recovered.state.latest_reviewer_feedback == plan.reviewer_feedback
    assert recovered.state.pre_update_checkpoint_id == plan.pre_run_mutation_checkpoint_id
    assert recovered.state.baseline_digest == plan.baseline_digest
    assert recovered.state.baseline_digest != plan.draft_digest
    runtime.flow_service.assert_restorable_flows()

    rejected_block_id = next(
        block_id for block_id in current_draft.blocks if block_id != current_draft.root_block_id
    )
    repaired = lean_runtime.material.update_source_block(
        repo_root,
        block_id=rejected_block_id,
        summary="The rejected draft was repaired in place.",
    )
    assert repaired.ok
    assert lean_runtime.material.mark_block_refs_done(
        repo_root, block_id=rejected_block_id
    ).value.passed
    assert lean_runtime.material.mark_block_links_done(
        repo_root, block_id=rejected_block_id
    ).value.passed
    assert lean_runtime.material.mark_block_completed(
        repo_root, block_id=rejected_block_id
    ).value.passed
    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("recovery_builder"),
            tool_name="submit_source_index_builder_round",
            summary="Repaired the rejected draft in place.",
            validation_summary="Recovery draft is ready.",
        )
    )
    builder_step_id = runtime.flow_service.advance_flow(successor_child_id)
    assert builder_step_id is not None
    runtime.run_step(builder_step_id)
    builder_agent_id = runtime.flow_service.get_step(builder_step_id).agent_bindings.get(
        "source_index_builder"
    )
    builder_record = next(
        record
        for record in runtime.agent_service.start_records
        if record.agent_id == builder_agent_id and plan.reviewer_feedback in (record.prompt or "")
    )
    assert plan.reviewer_feedback in (builder_record.prompt or "")
    assert builder_record.variables["round_index"] == 2
    assert builder_record.agent_id not in old_agent_ids

    runtime.agent_service.queue_submission(
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("recovery_reviewer"),
            tool_name="submit_source_index_review_round",
            summary="Recovered draft approved.",
            approved=True,
        )
    )
    _advance_and_run(runtime, successor_child_id)
    _advance_and_run(runtime, successor_child_id)
    terminal_successor_child = runtime.flow_service.get_flow(successor_child_id)
    assert terminal_successor_child.status is FlowStatus.COMPLETED
    assert terminal_successor_child.result.outcome == "committed", terminal_successor_child.result
    _advance_and_run(runtime, successor_parent_id)
    resumed_parent = runtime.flow_service.get_flow(successor_parent_id)
    assert resumed_parent.state.position.phase == "dispatch_preparation_child"
    assert resumed_parent.state.source_index_child_result.outcome == "committed"

    assert runtime.flow_service.get_flow(parent_id).model_dump(mode="json") == old_parent
    assert runtime.flow_service.get_flow(child_id).model_dump(mode="json") == old_child
    assert runtime.flow_service.get_step(failed_step_id).model_dump(mode="json") == old_step
    assert {agent_id: runtime.agent_service.agents[agent_id] for agent_id in old_agent_ids} == old_agents
    for agent_id, records in old_start_records.items():
        assert [
            record for record in runtime.agent_service.start_records if record.agent_id == agent_id
        ] == records


def test_recovery_token_rejects_draft_drift_without_creating_flow(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    parent_id, _, _ = _failed_native_source_index(runtime, lean_runtime, repo_root)
    admin = LeanAdminApi(lean_runtime)
    preview = admin.preview_native_source_index_recovery(
        NativeSourceIndexRecoveryPreviewInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
        )
    )
    assert preview.ok and preview.value is not None
    before = {flow.flow_id for flow in runtime.flow_service.list_flows()}
    assert lean_runtime.material.set_source_index_overview(
        repo_root,
        overview="Drift after preview.",
    ).ok

    rejected = admin.recover_native_source_index(
        NativeSourceIndexRecoveryStartInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
            expected_recovery_token=preview.value.recovery_token,
            enqueue=False,
        )
    )
    assert not rejected.ok
    assert rejected.issues[0].kind == "native_source_index_recovery_token_mismatch"
    assert {flow.flow_id for flow in runtime.flow_service.list_flows()} == before

    failed_parent = runtime.flow_service.get_flow(parent_id)
    bypass = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="native_repo_preparation",
            scope_id="repo:Provider",
            enqueue=False,
            params={
                "repo_key": "Provider",
                "repo_root": str(repo_root),
                "start_reason": "repair_resume",
                "run_spec": failed_parent.input.run_spec.model_dump(mode="json"),
                "recovery": preview.value.model_dump(mode="json"),
            },
        )
    )
    assert not bypass.ok
    assert {flow.flow_id for flow in runtime.flow_service.list_flows()} == before


def test_recovery_contract_rejects_a_token_that_does_not_cover_its_fields(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    parent_id, _, _ = _failed_native_source_index(runtime, lean_runtime, repo_root)
    preview = LeanAdminApi(lean_runtime).preview_native_source_index_recovery(
        NativeSourceIndexRecoveryPreviewInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
        )
    )
    assert preview.ok and preview.value is not None
    tampered = preview.value.model_dump(mode="json")
    tampered["reviewer_feedback"] = "Silently replace the preserved feedback."

    with pytest.raises(ValidationError, match="token does not cover"):
        NativeSourceIndexRecoveryContract.model_validate(tampered)


def test_recovery_child_revalidates_token_before_creating_agent_step(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    parent_id, _, _ = _failed_native_source_index(runtime, lean_runtime, repo_root)
    admin = LeanAdminApi(lean_runtime)
    preview = admin.preview_native_source_index_recovery(
        NativeSourceIndexRecoveryPreviewInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
        )
    )
    assert preview.ok and preview.value is not None
    started = admin.recover_native_source_index(
        NativeSourceIndexRecoveryStartInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
            expected_recovery_token=preview.value.recovery_token,
            enqueue=False,
        )
    )
    assert started.ok and started.value is not None
    successor_parent_id = started.value.flow_id
    _advance_and_run(runtime, successor_parent_id)
    dispatch_step_id = _advance_and_run(runtime, successor_parent_id)
    successor_child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=successor_parent_id,
        parent_dispatch_step_id=dispatch_step_id,
    )[0]
    agent_count = len(runtime.agent_service.agents)
    assert lean_runtime.material.set_source_index_overview(
        repo_root,
        overview="Drift after successor creation.",
    ).ok

    _advance_and_run(runtime, successor_child.flow_id)
    blocked = runtime.flow_service.get_flow(successor_child.flow_id)
    assert blocked.status is FlowStatus.COMPLETED
    assert blocked.result.outcome == "blocked"
    assert len(runtime.agent_service.agents) == agent_count


def test_recovery_rejects_failed_child_without_preserved_reviewer_feedback(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    parent_id, child_id, _ = _failed_native_source_index(runtime, lean_runtime, repo_root)
    runtime.flow_service.store.update_flow_record(
        child_id,
        lambda flow: setattr(flow.state, "latest_reviewer_feedback", None),
    )

    preview = LeanAdminApi(lean_runtime).preview_native_source_index_recovery(
        NativeSourceIndexRecoveryPreviewInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
        )
    )
    assert not preview.ok
    assert preview.issues[0].kind == "native_source_index_recovery_review_state_invalid"


def test_recovery_rejects_a_non_home_builder_failure(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    parent_id, _, _ = _failed_native_source_index(
        runtime,
        lean_runtime,
        repo_root,
        failure_message="provider request timed out",
    )

    preview = LeanAdminApi(lean_runtime).preview_native_source_index_recovery(
        NativeSourceIndexRecoveryPreviewInput(
            repo_root=repo_root,
            repo_key="Provider",
            failed_parent_flow_id=parent_id,
        )
    )
    assert not preview.ok
    assert preview.issues[0].kind == "native_source_index_recovery_failure_cause_ineligible"
