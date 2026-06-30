from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior
from tests.unit.tools._submit_family_helpers import assert_submit_tools, submit_specs


def test_preparation_recon_submit_tools_registered() -> None:
    assert_submit_tools(
        {
            "submit_node_dir_dependency_recon_completed",
            "submit_mathlib_recon_completed",
            "submit_resource_recon_completed",
            "submit_resource_recon_blocked",
        },
        behavior=SubmitBehavior.TERMINAL,
    )
    specs = submit_specs()
    assert specs
    assert specs["submit_resource_request"].submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS
