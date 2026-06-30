from __future__ import annotations

from lean_constellation.mcp import build_mcp_tool_registrations
from tests.unit.mcp._helpers import make_mcp_runtime


def test_tool_spec_description_and_argument_schema_map_to_mcp_registration() -> None:
    runtime = make_mcp_runtime()

    submit = build_mcp_tool_registrations(runtime, view_key="repo_format_discovery_submit")
    resource = build_mcp_tool_registrations(runtime, view_key="resource_curator")

    assert submit.ok and submit.value is not None
    assert resource.ok and resource.value is not None

    native = {tool.name: tool for tool in submit.value}["submit_native_repo_choice"]
    normalize = {tool.name: tool for tool in resource.value}["normalize_resource_target"]

    assert "native Lean repo" in native.description
    assert native.capability == "submit"
    assert native.submit_behavior == "terminal"
    assert native.input_schema["properties"]["summary"]["description"] == "Concise summary of the submitted result."
    assert "Source corpus mode" in native.input_schema["properties"]["source_corpus_mode"]["description"]

    assert normalize.capability == "read"
    assert normalize.submit_behavior == "none"
    assert "Normalize a resource target" in normalize.description
    assert "target" in normalize.input_schema["properties"]
