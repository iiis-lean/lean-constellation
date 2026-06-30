from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior
from tests.unit.tools._submit_family_helpers import assert_submit_tools


def test_content_completion_submit_tools_registered() -> None:
    assert_submit_tools(
        {"submit_content_node_ready", "submit_content_node_blocked", "submit_content_node_failed"},
        behavior=SubmitBehavior.TERMINAL,
    )
