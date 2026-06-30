from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior
from tests.unit.tools._submit_family_helpers import assert_submit_tools


def test_repo_lifecycle_submit_tools_registered() -> None:
    assert_submit_tools(
        {"submit_adapter_repo_choice", "submit_native_repo_choice"},
        behavior=SubmitBehavior.TERMINAL,
    )
