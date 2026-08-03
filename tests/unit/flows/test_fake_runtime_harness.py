from __future__ import annotations

from agent_runtime_kit.flow.models import StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.flows.common.agent_steps import ResourceCuratorAgentStep
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import create_fake_lean_flow_runtime
from lean_constellation.flows.resource_request.submissions import ResourceRejectedSubmission


def test_fake_runtime_starts_registered_business_flow(tmp_path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path)

    flow_id = runtime.start_flow(
        "resource_curation",
        {
            "repo_key": "Repo",
            "target_kind": "web",
            "target": "https://example.com/source",
            "requested_use": "supporting_material",
            "consumer_need": "Need supporting source context.",
            "requested_by": "content_plan",
            "context_summary": "Need source.",
        },
        scope_id="repo:Repo",
    )

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.flow_type == "resource_curation"
    assert flow.input is not None
    assert flow.input.render_for_agent(object()).startswith("Curate resource https://example.com/source")


def test_fake_agent_service_accepts_queued_submission_during_step_run(tmp_path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path)
    flow_id = runtime.start_flow(
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
    step = ResourceCuratorAgentStep(
        step_id="step_resource_curator",
        flow_id=flow_id,
        scope_id="repo:Repo",
        state=AgentStepState(
            agent_role="resource_curator",
            agent_type="resource_curator",
            create_agent_if_missing=True,
        ),
    )
    runtime.attach_step(step)
    runtime.agent_service.queue_submission(
        ResourceRejectedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_rejected",
            tool_name="submit_resource_rejected",
            target_kind="web",
            target="https://example.com/source",
            reason="Out of scope.",
            summary="Rejected.",
        )
    )

    runtime.run_step(step.step_id)

    saved = runtime.flow_service.get_step(step.step_id)
    assert saved.status is StepStatus.COMPLETED
    assert saved.submission is not None
    assert saved.submission.submission_type == "resource_rejected"
    assert saved.result is not None
    assert saved.result.result_type == "resource_curator"
    assert saved.result.outcome == "rejected"


def test_fake_snapshot_service_records_runtime_snapshots(tmp_path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path)

    snapshot_id = runtime.snapshot_service.create_runtime_snapshot(tmp_path, scope_ids=["repo:Repo"], label="unit")

    assert snapshot_id == "fake_snapshot_1"
    assert runtime.snapshot_service.records == [
        {
            "snapshot_id": "fake_snapshot_1",
            "repo_root": str(tmp_path),
            "scope_ids": ["repo:Repo"],
            "label": "unit",
        }
    ]
