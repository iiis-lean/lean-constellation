from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.domain.preparation import AutoProviderRoute
from lean_constellation.flows.common.agent_steps import CoordinatorAgentStep, _coordinator_content_callback_guidance
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.coordinator.steps import CoordinatorStepResult
from lean_constellation.flows.coordinator.submissions import (
    CoordinatorContentTasksSubmission,
    CoordinatorRepoReadySubmission,
    CoordinatorRepoRequirementSubmission,
    CoordinatorResourceRequestSubmission,
)
from lean_constellation.flows.common.flow_requests import build_content_node_task_request, build_resource_curation_request


def _start_host_flow(runtime: FakeLeanFlowRuntime, tmp_path: Path) -> str:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir(exist_ok=True)
    return runtime.start_flow(
        "native_repo_coordinator",
        {
            "repo_key": "Repo",
            "repo_root": str(repo_root),
            "start_mode": "admin_start",
            "start_reason": "unit",
        },
        scope_id="repo:Repo",
    )


def _state() -> AgentStepState:
    return AgentStepState(
        agent_role="coordinator",
        agent_type="CoordinatorAgent",
        create_agent_if_missing=True,
        bind_created_agent_to="flow",
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


def test_coordinator_agent_step_dispatch_results(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_host_flow(runtime, tmp_path)

    content = _run_step(
        runtime,
        CoordinatorAgentStep(step_id="coordinator_content_step", flow_id=flow_id, scope_id="repo:Repo", state=_state()),
        CoordinatorContentTasksSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_content_tasks",
            tool_name="submit_content_node_tasks",
            repo_key="Repo",
            node_paths=["Main.Core"],
            requests=[build_content_node_task_request(repo_key="Repo", node_path="Main.Core", scope_id="repo:Repo:node:Main.Core")],
            continuation="wait_for_callback",
            summary="Run content task.",
        ),
    )
    assert isinstance(content.result, CoordinatorStepResult)
    assert content.result.outcome == "content_tasks"
    assert content.result.content_tasks.node_paths == ["Main.Core"]
    assert content.result.content_tasks.request_count == 1
    assert (
        runtime.agent_service.start_records[-1]
        .context_maintenance_policy.threshold
        == 0.80
    )

    resource = _run_step(
        runtime,
        CoordinatorAgentStep(step_id="coordinator_resource_step", flow_id=flow_id, scope_id="repo:Repo", state=_state()),
        CoordinatorResourceRequestSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_resource_request",
            tool_name="submit_resource_request",
            repo_key="Repo",
            target_kind="arxiv",
            target="2501.12345",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo",
                    repo_key="Repo",
                    repo_root=str(tmp_path / "Repo"),
                    target_kind="arxiv",
                    target="2501.12345",
                    requested_by="coordinator",
                )
            ],
            continuation="wait_for_callback",
            summary="Curate source.",
        ),
    )
    assert isinstance(resource.result, CoordinatorStepResult)
    assert resource.result.outcome == "resource_request"
    assert resource.result.resource_request.target == "2501.12345"


def test_coordinator_agent_step_requirement_ready_and_incomplete_results(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_host_flow(runtime, tmp_path)

    requirement = _run_step(
        runtime,
        CoordinatorAgentStep(step_id="coordinator_requirement_step", flow_id=flow_id, scope_id="repo:Repo", state=_state()),
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            repo_key="Repo",
                requirement_name="provider_req",
                target_repo="Provider",
                provider_route=AutoProviderRoute(),
                reason="Need provider theorem.",
            summary="Need provider.",
        ),
    )
    assert isinstance(requirement.result, CoordinatorStepResult)
    assert requirement.result.outcome == "repo_requirement"
    assert requirement.result.repo_requirement.requirement_name == "provider_req"

    ready_flow_id = _start_host_flow(runtime, tmp_path)
    ready = _run_step(
        runtime,
        CoordinatorAgentStep(step_id="coordinator_ready_step", flow_id=ready_flow_id, scope_id="repo:Repo", state=_state()),
        CoordinatorRepoReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_ready",
            tool_name="submit_repo_ready",
            repo_key="Repo",
            summary="Repo exposes the completed public interface.",
        ),
    )
    assert isinstance(ready.result, CoordinatorStepResult)
    assert ready.result.outcome == "repo_ready"
    assert ready.result.repo_ready.repo_summary == "Repo exposes the completed public interface."

    incomplete_flow_id = _start_host_flow(runtime, tmp_path)
    incomplete = _run_step(
        runtime,
        CoordinatorAgentStep(step_id="coordinator_incomplete_step", flow_id=incomplete_flow_id, scope_id="repo:Repo", state=_state()),
    )
    assert isinstance(incomplete.result, CoordinatorStepResult)
    assert incomplete.result.outcome == "incomplete"


def test_coordinator_content_callback_guidance_routes_ready_blocked_mixed_and_failed() -> None:
    def child(outcome: str):
        return SimpleNamespace(
            status=None,
            result=SimpleNamespace(outcome=outcome, reason="SENTINEL PRIVATE RESULT BODY"),
        )

    ready = _coordinator_content_callback_guidance([child("ready")])
    assert "$coordinator-content-result-closeout" in ready
    assert "actual bound public signature" in ready
    assert "original private consumer" in ready
    assert "authoritative private consumer revision" not in ready

    blocked = _coordinator_content_callback_guidance([child("blocked")])
    assert "authoritative private consumer revision" in blocked
    assert "accepted proof route" in blocked
    assert "actual bound public signature" not in blocked

    mixed = _coordinator_content_callback_guidance([child("ready"), child("blocked")])
    assert "authoritative private consumer revision" in mixed
    assert "actual bound public signature" in mixed

    failed = _coordinator_content_callback_guidance([child("failed")])
    assert "authoritative private consumer revision" in failed
    assert "Classify all content outcomes (failed)" in failed

    for prompt in (ready, blocked, mixed, failed):
        assert "SENTINEL PRIVATE RESULT BODY" not in prompt
        assert "new ordinary Content boundary" in prompt
        assert "support node" not in prompt
        assert "adapter node" not in prompt
