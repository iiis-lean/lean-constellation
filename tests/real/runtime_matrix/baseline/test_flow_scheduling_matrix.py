from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.app import AdminFlowAdvanceInput, StartFlowInput
from tests.real.runtime_matrix.admin_helpers import (
    assert_flow_completed,
    checkpoint_branch,
    restore_branch,
    run_scripted_submit,
    run_next_created_step,
    set_scripted_provider_override,
    unwrap,
)
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace, create_runtime_matrix_workspace


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_repo_format_native_and_adapter_branches_restore_from_checkpoint(
    tmp_path: Path,
) -> None:
    ws = create_runtime_matrix_workspace(tmp_path, initialize_provider_format=False)
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

    validate_step_id = run_next_created_step(ws.admin, started.flow_id)
    validate_step = ws.runtime.ark.step_service.store.get_step(validate_step_id)
    assert validate_step.step_type == "validate_bootstrap_input_step"

    agent_advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.flow_id)))
    assert agent_advanced.created_step_id is not None
    agent_step_id = agent_advanced.created_step_id
    agent_step = ws.runtime.ark.step_service.store.get_step(agent_step_id)
    assert agent_step.step_type == "repo_format_discovery_agent_step"

    checkpoint = checkpoint_branch(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="runtime_matrix_repo_format_branch",
    )

    _run_repo_format_branch(
        ws,
        flow_id=started.flow_id,
        agent_step_id=agent_step_id,
        tool_name="submit_native_repo_choice",
        arguments={"summary": "Use native branch.", "searched_targets": ["baseline matrix"], "rejected_candidates": []},
        expected_outcome="native_bootstrap_ready",
        expected_submission="repo_format_native_choice",
    )

    restore_branch(ws.admin, ws.provider_repo, checkpoint.snapshot_id)
    restored_step = ws.runtime.ark.step_service.store.get_step(agent_step_id)
    assert restored_step.status.value == "created"
    assert "test_override_spec" not in restored_step.state.variables
    assert ws.runtime.ark.pause_controller.is_paused()

    _run_repo_format_branch(
        ws,
        flow_id=started.flow_id,
        agent_step_id=agent_step_id,
        tool_name="submit_adapter_repo_choice",
        arguments={
            "git_url": "https://github.com/example/runtime-matrix-upstream.git",
            "revision": "HEAD",
            "package_name": "runtime_matrix_upstream",
            "likely_import_module": "RuntimeMatrixUpstream",
            "evidence_summary": "Baseline matrix fixture uses a remote GitHub Lean candidate.",
            "known_risks": ["Fixture does not validate declaration coverage."],
        },
        expected_outcome="adapter_bootstrap_ready",
        expected_submission="repo_format_adapter_choice",
    )


def _run_repo_format_branch(
    ws: RuntimeMatrixWorkspace,
    *,
    flow_id: str,
    agent_step_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    expected_outcome: str,
    expected_submission: str,
) -> None:
    set_scripted_provider_override(
        ws.admin,
        agent_step_id,
        agent_type="RepoFormatDiscoveryControlledTestAgent",
        prompt_overlay=f"Call {tool_name} exactly once.",
    )
    handoff = run_scripted_submit(ws.admin, agent_step_id, tool_name, arguments)
    assert handoff["env"]["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == "repo_format_discovery"
    assert handoff["env"]["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"] == "repo_format_discovery_submit"
    assert "RepoFormatDiscoveryControlledTestAgent" == handoff["env"]["LEAN_CONSTELLATION_AGENT_TYPE"]

    apply_step_id = run_next_created_step(ws.admin, flow_id)
    apply_step = ws.runtime.ark.step_service.store.get_step(apply_step_id)
    assert apply_step.step_type == "apply_repo_format_choice_step"
    flow = assert_flow_completed(ws.runtime, flow_id, outcome=expected_outcome)
    agent_step = ws.runtime.ark.step_service.store.get_step(agent_step_id)
    assert agent_step.submission.submission_type == expected_submission
    assert flow.result.next_preparation_flow in {"native_repo_preparation", "adapter_repo_preparation"}


def test_resource_curator_duplicate_local_external_and_rejected_branches_restore_from_checkpoint(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.create_home("ResourceCuratorControlledTestAgent")
    ws.provider_repo.mkdir(parents=True, exist_ok=True)
    target = ws.resources.web_url
    existing_resource_key = ws.create_active_resource(
        target_kind="local_file",
        target=str(ws.resources.local_file),
    )
    flow_id = _start_resource_flow(ws, target_kind="web", target=target)

    preflight_step_id = run_next_created_step(ws.admin, flow_id)
    preflight = ws.runtime.ark.step_service.store.get_step(preflight_step_id)
    assert preflight.step_type == "resource_curation_preflight_step"
    assert preflight.result.outcome == "continue_to_curator"
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    local_draft_id = flow.state.active_resource_draft_key
    assert local_draft_id
    ws.fill_resource_draft(local_draft_id)

    agent_advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id)))
    assert agent_advanced.created_step_id is not None
    agent_step_id = agent_advanced.created_step_id
    agent_step = ws.runtime.ark.step_service.store.get_step(agent_step_id)
    assert agent_step.step_type == "resource_curator_agent_step"

    checkpoint = checkpoint_branch(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="runtime_matrix_resource_curator_branch",
    )

    _run_resource_branch(
        ws,
        flow_id=flow_id,
        agent_step_id=agent_step_id,
        tool_name="submit_resource_rejected",
        arguments={
            "reason": "Reject this web resource in the Runtime Matrix branch.",
            "details": ["Scripted branch coverage."],
        },
        expected_outcome="rejected",
        expected_submission="resource_rejected",
    )

    restore_branch(ws.admin, ws.provider_repo, checkpoint.snapshot_id)
    _assert_restored_resource_branch(ws, flow_id, agent_step_id)
    _run_resource_branch(
        ws,
        flow_id=flow_id,
        agent_step_id=agent_step_id,
        tool_name="submit_external_repo_required",
        arguments={
            "reason": "The target should become a provider repo.",
            "source_description": "A web-accessible upstream project.",
            "classification_reason": "The target represents an independent reusable provider scope.",
            "relation_to_current_repo_or_node": "The current node consumes the provider theorem.",
            "consumer_need": "A reusable theorem exposed through a stable provider interface.",
            "provider_scope": "Own and prove the reusable theorem independently.",
            "suggested_repo_name": "runtime_matrix_web_provider",
            "required_interfaces_hint": "Expose the reusable theorem.",
        },
        expected_outcome="external_repo_required",
        expected_submission="external_repo_required",
    )

    restore_branch(ws.admin, ws.provider_repo, checkpoint.snapshot_id)
    _assert_restored_resource_branch(ws, flow_id, agent_step_id)
    _run_resource_branch(
        ws,
        flow_id=flow_id,
        agent_step_id=agent_step_id,
        tool_name="submit_local_resource_created",
        arguments={
            "summary": "Promote the prepared runtime matrix resource draft.",
            "draft_id": local_draft_id,
            "classification_reason": "The fixture is supporting material for this repository.",
            "resource_role": "Provide deterministic Runtime Matrix evidence.",
            "consumer_formalization_scope": "The current repository retains the formal theorem work.",
        },
        expected_outcome="local_resource_created",
        expected_submission="local_resource_created",
    )

    restore_branch(ws.admin, ws.provider_repo, checkpoint.snapshot_id)
    _assert_restored_resource_branch(ws, flow_id, agent_step_id)
    _run_resource_branch(
        ws,
        flow_id=flow_id,
        agent_step_id=agent_step_id,
        tool_name="submit_resource_duplicate",
        arguments={
            "existing_kind": "resource",
            "duplicate_reason": "The web target points to the same note already imported.",
            "existing_resource_key": existing_resource_key,
            "preview": "Duplicate of runtime matrix local note.",
        },
        expected_outcome="duplicate",
        expected_submission="resource_duplicate",
    )


def test_resource_curation_preflight_duplicate_hint_runs_curator_agent(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.create_home("ResourceCuratorControlledTestAgent")
    ws.provider_repo.mkdir(parents=True, exist_ok=True)
    target = str(ws.resources.local_file)
    resource_key = ws.create_active_resource(target_kind="local_file", target=target)
    flow_id = _start_resource_flow(ws, target_kind="local_file", target=target)

    preflight_step_id = run_next_created_step(ws.admin, flow_id)
    preflight = ws.runtime.ark.step_service.store.get_step(preflight_step_id)
    assert preflight.step_type == "resource_curation_preflight_step"
    assert preflight.result.outcome == "continue_to_curator"
    assert preflight.result.resource_duplicate_hint.existing_resource_key == resource_key
    agent_advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id)))
    assert agent_advanced.created_step_id is not None
    _run_resource_branch(
        ws,
        flow_id=flow_id,
        agent_step_id=agent_advanced.created_step_id,
        tool_name="submit_resource_duplicate",
        arguments={
            "existing_kind": "resource",
            "duplicate_reason": "The local file target is already registered.",
            "existing_resource_key": resource_key,
        },
        expected_outcome="duplicate",
        expected_submission="resource_duplicate",
    )
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.existing_resource_key == resource_key
    assert ws.runtime.ark.flow_service.list_steps(flow_id=flow_id, step_type="resource_curator_agent_step") != []


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
                    "requested_use": "supporting_material",
                    "consumer_need": "Deterministic material for Runtime Matrix resource branch coverage.",
                    "context_summary": "Runtime Matrix resource request branch coverage.",
                    "node_path": "Main.Core",
                },
            ),
            repo_root=str(ws.provider_repo),
        )
    )
    return started.flow_id


def _run_resource_branch(
    ws: RuntimeMatrixWorkspace,
    *,
    flow_id: str,
    agent_step_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    expected_outcome: str,
    expected_submission: str,
) -> None:
    set_scripted_provider_override(
        ws.admin,
        agent_step_id,
        agent_type="ResourceCuratorControlledTestAgent",
        prompt_overlay=f"Call {tool_name} exactly once.",
    )
    handoff = run_scripted_submit(ws.admin, agent_step_id, tool_name, arguments)
    assert handoff["env"]["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == "resource_curator"
    assert handoff["env"]["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"] == "resource_curator_submit"
    flow = assert_flow_completed(ws.runtime, flow_id, outcome=expected_outcome)
    agent_step = ws.runtime.ark.step_service.store.get_step(agent_step_id)
    assert agent_step.submission.submission_type == expected_submission
    if expected_outcome == "local_resource_created":
        assert flow.result.resource_key is not None
    if expected_outcome == "external_repo_required":
        assert flow.result.external_repo.suggested_repo_name == "runtime_matrix_web_provider"


def _assert_restored_resource_branch(ws: RuntimeMatrixWorkspace, flow_id: str, agent_step_id: str) -> None:
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    step = ws.runtime.ark.step_service.store.get_step(agent_step_id)
    assert flow.status is FlowStatus.RUNNING
    assert flow.current_step_id == agent_step_id
    assert step.status.value == "created"
    assert "test_override_spec" not in step.state.variables
    assert ws.runtime.ark.pause_controller.is_paused()
