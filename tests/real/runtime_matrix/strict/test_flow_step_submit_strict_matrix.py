from __future__ import annotations

from typing import Any

import pytest
from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.app import AdminFlowAdvanceInput, StartFlowInput
from tests.real.runtime_matrix.admin_helpers import (
    assert_flow_completed,
    run_next_created_step,
    set_external_takeover_override,
    unwrap,
)
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.strict_helpers import (
    checkpoint_with_evidence,
    restore_with_evidence,
    run_external_submit_with_evidence,
)


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_repo_format_and_resource_branches_emit_actual_evidence(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = runtime_matrix_workspace

    repo_flow_id, repo_agent_step_id, repo_checkpoint = _prepare_repo_format_branch(ws, evidence_recorder)
    _run_repo_format_branch(
        ws,
        evidence_recorder,
        flow_id=repo_flow_id,
        agent_step_id=repo_agent_step_id,
        tool_name="submit_native_repo_choice",
        arguments={"summary": "Strict native branch.", "source_corpus_mode": "prepare"},
        expected_outcome="native_bootstrap_ready",
    )
    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        repo_checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_repo_format_branch",
        recorder=evidence_recorder,
    )
    _run_repo_format_branch(
        ws,
        evidence_recorder,
        flow_id=repo_flow_id,
        agent_step_id=repo_agent_step_id,
        tool_name="submit_adapter_repo_choice",
        arguments={
            "summary": "Strict adapter branch.",
            "upstream_github_url": "https://github.com/example/runtime-matrix-upstream.git",
            "upstream_revision": "HEAD",
            "adapter_repo_name": "ProviderAdapter",
        },
        expected_outcome="adapter_bootstrap_ready",
    )

    resource_flow_id, resource_agent_step_id, resource_checkpoint, local_draft_id, existing_resource_key = _prepare_resource_branch(
        ws, evidence_recorder
    )
    target = ws.resources.web_url
    _run_resource_branch(
        ws,
        evidence_recorder,
        flow_id=resource_flow_id,
        agent_step_id=resource_agent_step_id,
        tool_name="submit_resource_rejected",
        arguments={"reason": "Strict rejected branch.", "target_kind": "web", "target": target},
        expected_outcome="rejected",
    )
    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        resource_checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_resource_branch",
        recorder=evidence_recorder,
    )
    _assert_restored_resource_branch(ws, resource_flow_id, resource_agent_step_id)
    _run_resource_branch(
        ws,
        evidence_recorder,
        flow_id=resource_flow_id,
        agent_step_id=resource_agent_step_id,
        tool_name="submit_local_resource_created",
        arguments={"summary": "Strict local resource.", "target_kind": "web", "target": target, "draft_id": local_draft_id},
        expected_outcome="local_resource_created",
    )
    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        resource_checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_resource_branch",
        recorder=evidence_recorder,
    )
    _assert_restored_resource_branch(ws, resource_flow_id, resource_agent_step_id)
    _run_resource_branch(
        ws,
        evidence_recorder,
        flow_id=resource_flow_id,
        agent_step_id=resource_agent_step_id,
        tool_name="submit_external_repo_required",
        arguments={
            "reason": "Strict external provider branch.",
            "target_kind": "web",
            "target": target,
            "source_description": "Strict web-accessible provider.",
            "suggested_repo_name": "strict_web_provider",
            "required_interfaces_hint": "Expose the reusable theorem.",
        },
        expected_outcome="external_repo_required",
    )
    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        resource_checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_resource_branch",
        recorder=evidence_recorder,
    )
    _assert_restored_resource_branch(ws, resource_flow_id, resource_agent_step_id)
    _run_resource_branch(
        ws,
        evidence_recorder,
        flow_id=resource_flow_id,
        agent_step_id=resource_agent_step_id,
        tool_name="submit_resource_duplicate",
        arguments={
            "target_kind": "web",
            "target": target,
            "existing_kind": "resource",
            "duplicate_reason": "Strict duplicate of an existing resource.",
            "existing_resource_key": existing_resource_key,
            "preview": "Strict duplicate preview.",
        },
        expected_outcome="duplicate",
    )

    evidence_recorder.record_runtime_state(ws.runtime)
    evidence = evidence_recorder.evidence
    assert {"requirement_group_repo_bootstrap", "resource_curation"} <= evidence.flow_types
    assert {
        "validate_bootstrap_input_step",
        "apply_repo_format_choice_step",
        "resource_curation_preflight_step",
    } <= evidence.logic_step_types
    assert {"repo_format_discovery_agent_step", "resource_curator_agent_step"} <= evidence.agent_step_types
    assert {
        "submit_native_repo_choice",
        "submit_adapter_repo_choice",
        "submit_resource_rejected",
        "submit_local_resource_created",
        "submit_external_repo_required",
        "submit_resource_duplicate",
    } <= evidence.submit_tool_names
    assert any(item.event == "checkpoint" for item in evidence.snapshots)
    assert any(item.event == "restore" and item.pruned is True for item in evidence.snapshots)


def test_strict_resource_curation_input_kinds_and_preflight_duplicate_evidence(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = runtime_matrix_workspace
    ws.create_home("ResourceCuratorControlledTestAgent")
    ws.provider_repo.mkdir(parents=True, exist_ok=True)

    local_target = str(ws.resources.local_file)
    existing_key = ws.create_active_resource(target_kind="local_file", target=local_target)
    local_flow_id = _start_resource_flow(ws, target_kind="local_file", target=local_target)
    local_preflight_step_id = run_next_created_step(ws.admin, local_flow_id)
    local_preflight = ws.runtime.ark.step_service.store.get_step(local_preflight_step_id)
    assert local_preflight.step_type == "resource_curation_preflight_step"
    assert local_preflight.result.outcome == "continue_to_curator"
    assert local_preflight.result.resource_duplicate_hint.existing_resource_key == existing_key
    local_advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=local_flow_id)))
    assert local_advanced.created_step_id is not None
    _run_resource_branch(
        ws,
        evidence_recorder,
        flow_id=local_flow_id,
        agent_step_id=local_advanced.created_step_id,
        tool_name="submit_resource_duplicate",
        arguments={
            "existing_kind": "resource",
            "duplicate_reason": "The local file target is already registered.",
            "existing_resource_key": existing_key,
        },
        expected_outcome="duplicate",
    )
    local_flow = ws.runtime.ark.flow_service.get_flow(local_flow_id)
    assert local_flow.result.existing_resource_key == existing_key
    assert ws.runtime.ark.flow_service.list_steps(flow_id=local_flow_id, step_type="resource_curator_agent_step") != []

    web_flow_id = _start_resource_flow(ws, target_kind="web", target=ws.resources.web_url)
    run_next_created_step(ws.admin, web_flow_id)
    web_advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=web_flow_id)))
    assert web_advanced.created_step_id is not None
    _run_resource_branch(
        ws,
        evidence_recorder,
        flow_id=web_flow_id,
        agent_step_id=web_advanced.created_step_id,
        tool_name="submit_resource_rejected",
        arguments={"reason": "Strict web input rejected.", "target_kind": "web", "target": ws.resources.web_url},
        expected_outcome="rejected",
    )

    arxiv_flow_id = _start_resource_flow(ws, target_kind="arxiv", target=ws.resources.arxiv_id)
    run_next_created_step(ws.admin, arxiv_flow_id)
    arxiv_advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=arxiv_flow_id)))
    assert arxiv_advanced.created_step_id is not None
    _run_resource_branch(
        ws,
        evidence_recorder,
        flow_id=arxiv_flow_id,
        agent_step_id=arxiv_advanced.created_step_id,
        tool_name="submit_resource_rejected",
        arguments={"reason": "Strict arXiv input rejected.", "target_kind": "arxiv", "target": ws.resources.arxiv_id},
        expected_outcome="rejected",
    )

    evidence_recorder.record_runtime_state(ws.runtime)
    assert "resource_curation_preflight_step" in evidence_recorder.evidence.logic_step_types
    assert "resource_curator_agent_step" in evidence_recorder.evidence.agent_step_types
    assert "submit_resource_rejected" in evidence_recorder.evidence.submit_tool_names


def _prepare_repo_format_branch(ws: RuntimeMatrixWorkspace, recorder: EvidenceRecorder):
    ws.create_home("RepoFormatDiscoveryControlledTestAgent")
    ws.write_bootstrap_preparation(ws.provider_repo)
    started = unwrap(
        ws.admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="requirement_group_repo_bootstrap",
                scope_id="repo:Provider",
                enqueue=False,
                params={
                    "target_repo": "Provider",
                    "repo_root": str(ws.provider_repo),
                    "workspace_root": str(ws.workspace_root),
                    "requirement_refs": ["Consumer:need_provider"],
                },
            ),
            repo_root=str(ws.provider_repo),
        )
    )
    run_next_created_step(ws.admin, started.flow_id)
    advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.flow_id)))
    assert advanced.created_step_id is not None
    checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_repo_format_branch",
        recorder=recorder,
    )
    recorder.record_runtime_state(ws.runtime)
    return started.flow_id, advanced.created_step_id, checkpoint


def _run_repo_format_branch(
    ws: RuntimeMatrixWorkspace,
    recorder: EvidenceRecorder,
    *,
    flow_id: str,
    agent_step_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    expected_outcome: str,
) -> None:
    set_external_takeover_override(
        ws.admin,
        agent_step_id,
        agent_type="RepoFormatDiscoveryControlledTestAgent",
        prompt_overlay=f"Strict Runtime Matrix: call {tool_name} exactly once.",
    )
    run_external_submit_with_evidence(ws.admin, agent_step_id, tool_name, arguments, recorder=recorder)
    run_next_created_step(ws.admin, flow_id)
    assert_flow_completed(ws.runtime, flow_id, outcome=expected_outcome)
    recorder.record_runtime_state(ws.runtime)


def _prepare_resource_branch(ws: RuntimeMatrixWorkspace, recorder: EvidenceRecorder):
    ws.create_home("ResourceCuratorControlledTestAgent")
    ws.provider_repo.mkdir(parents=True, exist_ok=True)
    target = ws.resources.web_url
    existing_resource_key = ws.create_active_resource(target_kind="local_file", target=str(ws.resources.local_file))
    flow_id = _start_resource_flow(ws, target_kind="web", target=target)
    run_next_created_step(ws.admin, flow_id)
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    local_draft_id = flow.state.active_resource_draft_key
    assert local_draft_id
    ws.fill_resource_draft(local_draft_id)
    advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id)))
    assert advanced.created_step_id is not None
    checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_resource_branch",
        recorder=recorder,
    )
    recorder.record_runtime_state(ws.runtime)
    return flow_id, advanced.created_step_id, checkpoint, local_draft_id, existing_resource_key


def _run_resource_branch(
    ws: RuntimeMatrixWorkspace,
    recorder: EvidenceRecorder,
    *,
    flow_id: str,
    agent_step_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    expected_outcome: str,
) -> None:
    set_external_takeover_override(
        ws.admin,
        agent_step_id,
        agent_type="ResourceCuratorControlledTestAgent",
        prompt_overlay=f"Strict Runtime Matrix: call {tool_name} exactly once.",
    )
    run_external_submit_with_evidence(ws.admin, agent_step_id, tool_name, arguments, recorder=recorder)
    flow = assert_flow_completed(ws.runtime, flow_id, outcome=expected_outcome)
    if expected_outcome == "local_resource_created":
        assert flow.result.resource_key is not None
        loaded = ws.runtime.material.resource_library.get_resource(ws.provider_repo, resource_key=flow.result.resource_key)
        assert loaded.ok and loaded.value is not None, loaded.issues
    if expected_outcome == "external_repo_required":
        assert flow.result.external_repo.suggested_repo_name == "strict_web_provider"
    recorder.record_runtime_state(ws.runtime)


def _start_resource_flow(ws: RuntimeMatrixWorkspace, *, target_kind: str, target: str) -> str:
    started = unwrap(
        ws.admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="resource_curation",
                scope_id="repo:Provider",
                enqueue=False,
                params={
                    "repo_key": "Provider",
                    "repo_root": str(ws.provider_repo),
                    "target_kind": target_kind,
                    "target": target,
                    "requested_by": "content_plan",
                    "context_summary": "Strict Runtime Matrix resource branch coverage.",
                    "node_path": "Main.Core",
                },
            ),
            repo_root=str(ws.provider_repo),
        )
    )
    return started.flow_id


def _assert_restored_resource_branch(ws: RuntimeMatrixWorkspace, flow_id: str, agent_step_id: str) -> None:
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    step = ws.runtime.ark.step_service.store.get_step(agent_step_id)
    assert flow.status is FlowStatus.RUNNING
    assert flow.current_step_id == agent_step_id
    assert step.status.value == "created"
    assert ws.runtime.ark.pause_controller.is_paused()
