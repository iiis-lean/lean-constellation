from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.flows.common.agent_steps import ResourceCuratorAgentStep
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.resource_request.steps import ResourceCuratorStepResult
from lean_constellation.flows.resource_request.submissions import (
    ExternalRepoRequiredSubmission,
    LocalResourceCreatedSubmission,
    ResourceDuplicateSubmission,
    ResourceRejectedSubmission,
)


def _start_host_flow(runtime: FakeLeanFlowRuntime, tmp_path: Path) -> str:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir(exist_ok=True)
    return runtime.start_flow(
        "resource_curation",
        {
            "repo_key": "Repo",
            "repo_root": str(repo_root),
            "target_kind": "web",
            "target": "https://example.com/a",
            "requested_use": "supporting_material",
            "consumer_need": "Need supporting background.",
            "requested_by": "content_plan",
        },
        scope_id="repo:Repo",
    )


def _state() -> AgentStepState:
    return AgentStepState(
        agent_role="resource_curator",
        agent_type="ResourceCuratorAgent",
        create_agent_if_missing=True,
        bind_created_agent_to="step",
        max_auto_continue_turns=0,
    )


def _run_step(runtime: FakeLeanFlowRuntime, step, submission=None):
    step_id = runtime.attach_step(step)
    if submission is not None:
        runtime.agent_service.queue_submission(submission)
    else:
        runtime.agent_service.queue_incomplete_turn()
    runtime.run_step(step_id)
    return runtime.flow_service.get_step(step_id)


def test_resource_curator_agent_step_duplicate_and_local_results(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_host_flow(runtime, tmp_path)

    duplicate = _run_step(
        runtime,
        ResourceCuratorAgentStep(
            step_id="resource_curator_duplicate_step",
            flow_id=flow_id,
            scope_id="repo:Repo",
            state=_state(),
        ),
        ResourceDuplicateSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_duplicate",
            tool_name="submit_resource_duplicate",
            repo_key="Repo",
            target_kind="web",
            target="https://example.com/a",
            existing_kind="resource",
            duplicate_reason="Already curated.",
            existing_resource_key="res_a",
            summary="Duplicate.",
        ),
    )
    assert isinstance(duplicate.result, ResourceCuratorStepResult)
    assert duplicate.result.outcome == "duplicate"
    assert duplicate.result.duplicate.existing_resource_key == "res_a"

    flow_id = _start_host_flow(runtime, tmp_path)
    local = _run_step(
        runtime,
        ResourceCuratorAgentStep(
            step_id="resource_curator_local_step",
            flow_id=flow_id,
            scope_id="repo:Repo",
            state=_state(),
        ),
        LocalResourceCreatedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="local_resource_created",
            tool_name="submit_local_resource_created",
            repo_key="Repo",
            target_kind="web",
            target="https://example.com/b",
            draft_id="draft_b",
            resource_key="res_b",
            classification_reason="This page is supporting material.",
            resource_role="Background reference.",
            consumer_formalization_scope="The current repo proves the target theorem.",
            summary="Created.",
        ),
    )
    assert isinstance(local.result, ResourceCuratorStepResult)
    assert local.result.outcome == "local_resource_created"
    assert local.result.local_resource.resource_key == "res_b"


def test_resource_curator_agent_step_external_rejected_and_incomplete(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_host_flow(runtime, tmp_path)

    external = _run_step(
        runtime,
        ResourceCuratorAgentStep(
            step_id="resource_curator_external_step",
            flow_id=flow_id,
            scope_id="repo:Repo",
            state=_state(),
        ),
        ExternalRepoRequiredSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="external_repo_required",
            tool_name="submit_external_repo_required",
            repo_key="Repo",
            target_kind="arxiv",
            target="2501.12345",
            reason="Provider repo required.",
            source_description="Paper-scale source.",
            classification_reason="The paper is an independent theory package.",
            relation_to_current_repo_or_node="The current node consumes its main theorem.",
            consumer_need="A stable public main theorem.",
            provider_scope="Formalize the reusable paper theory.",
            suggested_repo_name="provider_repo",
            summary="External.",
        ),
    )
    assert isinstance(external.result, ResourceCuratorStepResult)
    assert external.result.outcome == "external_repo_required"
    assert external.result.external_repo.suggested_repo_name == "provider_repo"

    flow_id = _start_host_flow(runtime, tmp_path)
    rejected = _run_step(
        runtime,
        ResourceCuratorAgentStep(
            step_id="resource_curator_rejected_step",
            flow_id=flow_id,
            scope_id="repo:Repo",
            state=_state(),
        ),
        ResourceRejectedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_rejected",
            tool_name="submit_resource_rejected",
            repo_key="Repo",
            target_kind="web",
            target="https://example.com/bad",
            reason="Unreadable.",
            details=["No useful text."],
            summary="Rejected.",
        ),
    )
    assert isinstance(rejected.result, ResourceCuratorStepResult)
    assert rejected.result.outcome == "rejected"
    assert rejected.result.rejected.reason == "Unreadable."

    flow_id = _start_host_flow(runtime, tmp_path)
    incomplete = _run_step(
        runtime,
        ResourceCuratorAgentStep(
            step_id="resource_curator_incomplete_step",
            flow_id=flow_id,
            scope_id="repo:Repo",
            state=_state(),
        ),
    )
    assert isinstance(incomplete.result, ResourceCuratorStepResult)
    assert incomplete.result.outcome == "incomplete"
