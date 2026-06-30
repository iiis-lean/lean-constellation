from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import BaseSubmission
from lean_constellation.services import LeanProviderOverrides, create_test_runtime_services
from lean_constellation.services.tool_facade import SubmitBehavior
from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
from lean_constellation.tools import register_submit_tooling
from tests.unit.tools._submit_family_helpers import assert_submit_tools, submit_specs


class FakeSubmissionGateway:
    def __init__(self) -> None:
        self.accepted: list[BaseSubmission] = []

    def accept_step_submission(self, ctx, submission: BaseSubmission):
        del ctx
        self.accepted.append(submission)
        return {"accepted": True}


def test_resource_submit_tools_registered() -> None:
    assert_submit_tools(
        {
            "submit_resource_duplicate",
            "submit_local_resource_created",
            "submit_external_repo_required",
            "submit_resource_rejected",
        },
        behavior=SubmitBehavior.TERMINAL,
    )
    specs = submit_specs()
    assert specs["submit_resource_request"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS


def test_resource_request_submit_injects_runtime_repo_context(tmp_path: Path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = create_test_runtime_services(providers=LeanProviderOverrides(submission_gateway=gateway))
    assert register_submit_tooling(runtime).ok
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    raw = RawToolCallContext(
        endpoint_view_key="content_plan_submit",
        runtime_context=RuntimeToolContext(
            flow_id="flow_1",
            step_id="step_1",
            agent_id="agent_1",
            scope_id="repo:Repo:node:Main.Core",
            agent_type="ContentPlanAgent",
            agent_role="plan",
            expected_view_key="content_plan_submit",
            repo_root=repo_root,
            node_path="Main.Core",
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        raw,
        tool_name="submit_resource_request",
        flat_args={
            "target_kind": "web",
            "target": "https://example.com/source",
            "context_summary": "Need background.",
            "summary": "Request resource curation.",
        },
    )

    assert result.ok and result.value is not None
    assert result.value.ok is True
    assert len(gateway.accepted) == 1
    submission = gateway.accepted[0]
    assert submission.submission_type == "content_resource_request"
    request = submission.requests[0]
    assert request.flow_type == "resource_curation"
    assert request.params["repo_key"] == "Repo"
    assert request.params["repo_root"] == str(repo_root)
    assert request.params["node_path"] == "Main.Core"
