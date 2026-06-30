from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior, ToolCapability
from lean_constellation.tools import build_submit_tool_specs


def submit_specs():
    return {spec.name: spec for spec in build_submit_tool_specs()}


def assert_submit_tools(names: set[str], *, behavior: SubmitBehavior | None = None) -> None:
    specs = submit_specs()
    missing = sorted(names - set(specs))
    assert not missing, f"Missing submit tools: {missing}"
    for name in names:
        spec = specs[name]
        assert spec.capability == ToolCapability.SUBMIT
        assert spec.submit_behavior != SubmitBehavior.NONE
        if behavior is not None:
            assert spec.submit_behavior == behavior
        assert spec.tool_groups
        assert spec.allowed_roles
