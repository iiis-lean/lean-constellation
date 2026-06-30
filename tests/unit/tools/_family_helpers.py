from __future__ import annotations

from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.tool_facade import SubmitBehavior
from lean_constellation.tools import build_application_tool_specs


def assert_tools_registered(tool_names: set[str]) -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}
    missing = sorted(tool_names - set(specs))
    assert not missing, f"Missing expected tools: {missing}"
    for tool_name in tool_names:
        assert specs[tool_name].submit_behavior == SubmitBehavior.NONE


def assert_group_contains(group_key: str, tool_names: set[str]) -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    listed = runtime.tool_facade.list_registered_tools(group_key=group_key)

    assert listed.ok
    assert listed.value is not None
    registered = {tool.name for tool in listed.value}
    missing = sorted(tool_names - registered)
    assert not missing, f"{group_key} missing expected tools: {missing}"
