from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior
from tests.unit.tools._submit_family_helpers import submit_specs


def test_content_plan_dispatch_submit_tools_registered() -> None:
    specs = submit_specs()
    assert specs["submit_content_preparation_recon"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS
    assert specs["submit_current_decl_round"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS
    assert specs["submit_resource_request"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS
