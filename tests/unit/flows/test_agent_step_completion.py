from __future__ import annotations

from agent_runtime_kit.flow.models import StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.flows.common.agent_steps import ResourceCuratorAgentStep
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import create_fake_lean_flow_runtime
from lean_constellation.flows.resource_request.submissions import ResourceRejectedSubmission


def _start_resource_flow(runtime) -> str:
    return runtime.start_flow(
        "resource_curation",
        {
            "repo_key": "Repo",
            "target_kind": "web",
            "target": "https://example.com/source",
            "requested_use": "supporting_material",
            "consumer_need": "Need supporting source context.",
        },
        scope_id="repo:Repo",
    )


def _resource_step(flow_id: str, *, max_auto_continue_turns: int = 2) -> ResourceCuratorAgentStep:
    return ResourceCuratorAgentStep(
        step_id=f"step_resource_curator_{max_auto_continue_turns}",
        flow_id=flow_id,
        scope_id="repo:Repo",
        state=AgentStepState(
            agent_role="resource_curator",
            agent_type="resource_curator",
            create_agent_if_missing=True,
            max_auto_continue_turns=max_auto_continue_turns,
        ),
    )


def _rejected_submission() -> ResourceRejectedSubmission:
    return ResourceRejectedSubmission(
        submission_id=new_submission_id("sub"),
        submission_type="resource_rejected",
        tool_name="submit_resource_rejected",
        target_kind="web",
        target="https://example.com/source",
        reason="Out of scope.",
        summary="Rejected.",
    )


def test_agent_step_completes_after_successful_submission(tmp_path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path)
    step = _resource_step(_start_resource_flow(runtime))
    runtime.attach_step(step)
    runtime.agent_service.queue_submission(_rejected_submission())

    runtime.run_step(step.step_id)

    saved = runtime.flow_service.get_step(step.step_id)
    assert saved.status is StepStatus.COMPLETED
    assert saved.result is not None
    assert saved.result.result_type == "resource_curator"
    assert saved.result.outcome == "rejected"
    assert len(runtime.agent_service.start_records) == 1


def test_agent_step_auto_continues_when_turn_ends_without_submission(tmp_path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path)
    step = _resource_step(_start_resource_flow(runtime))
    runtime.attach_step(step)
    runtime.agent_service.queue_incomplete_turn()
    runtime.agent_service.queue_submission(_rejected_submission())

    runtime.run_step(step.step_id)

    saved = runtime.flow_service.get_step(step.step_id)
    assert saved.status is StepStatus.COMPLETED
    assert saved.result is not None
    assert saved.result.result_type == "resource_curator"
    assert saved.result.outcome == "rejected"
    assert len(runtime.agent_service.start_records) == 2
    assert "Continue the current task" in (runtime.agent_service.start_records[1].prompt or "")


def test_agent_step_writes_incomplete_result_after_auto_continue_limit(tmp_path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path)
    step = _resource_step(_start_resource_flow(runtime), max_auto_continue_turns=1)
    runtime.attach_step(step)
    runtime.agent_service.queue_incomplete_turn()
    runtime.agent_service.queue_incomplete_turn()

    runtime.run_step(step.step_id)

    saved = runtime.flow_service.get_step(step.step_id)
    assert saved.status is StepStatus.COMPLETED
    assert saved.submission is None
    assert saved.result is not None
    assert saved.result.result_type == "resource_curator"
    assert saved.result.outcome == "incomplete"
    assert len(runtime.agent_service.start_records) == 2
