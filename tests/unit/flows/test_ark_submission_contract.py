from __future__ import annotations

from agent_runtime_kit.flow.models import BaseSubmission, ChildFlowDispatchSubmission, FlowRequest
from agent_runtime_kit.flow.registry import StepTypeRegistry
from agent_runtime_kit.flow.standard_steps.agent_step import AgentStep


def test_ark_submission_contract_preflight() -> None:
    submission = ChildFlowDispatchSubmission(
        submission_id="sub_1",
        tool_name="submit_child",
        requests=[FlowRequest(flow_type="unit_flow", scope_id="scope_1", params={"x": 1})],
    )

    assert isinstance(submission, BaseSubmission)
    assert submission.submission_type == "child_flow_dispatch"
    assert submission.requests[0].params == {"x": 1}

    registry = StepTypeRegistry()
    registry.register(AgentStep)
    parsed = registry.parse_submission("agent_step", submission.model_dump(mode="json"))

    assert isinstance(parsed, ChildFlowDispatchSubmission)
    assert parsed.requests[0].flow_type == "unit_flow"
