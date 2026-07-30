from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior
from tests.unit.tools._submit_family_helpers import assert_submit_tools, submit_specs


def test_coordinator_submit_tools_registered() -> None:
    assert_submit_tools(
        {
            "submit_repo_requirement",
            "submit_adapter_repo_requirement",
            "submit_native_repo_requirement",
            "submit_repo_ready",
        },
        behavior=SubmitBehavior.TERMINAL,
    )
    specs = submit_specs()
    assert specs["submit_content_node_tasks"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS
    assert specs["submit_resource_request"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS
    assert specs["submit_repo_ready"].description == (
        "Submit the current repository as a release candidate after the repository-ready preview passes."
    )
