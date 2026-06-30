from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior
from tests.unit.tools._submit_family_helpers import assert_submit_tools


def test_adapter_submit_tools_registered() -> None:
    assert_submit_tools(
        {"submit_adapter_catalog_ready", "submit_adapter_catalog_blocked"},
        behavior=SubmitBehavior.TERMINAL,
    )
