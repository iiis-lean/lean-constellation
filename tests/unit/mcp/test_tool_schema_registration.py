from __future__ import annotations

from lean_constellation.mcp import build_mcp_tool_registrations
from lean_constellation.services.tool_facade import FastMcpViewApp, SubmitBehavior, ToolCapability, ToolSpecView
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
    assert "source_corpus_mode" not in native.input_schema["properties"]
    assert "native_repo_name" not in native.input_schema["properties"]

    adapter = {tool.name: tool for tool in submit.value}["submit_adapter_repo_choice"]
    assert {"git_url", "evidence_summary", "known_risks"} <= set(adapter.input_schema["properties"])
    assert "upstream_github_url" not in adapter.input_schema["properties"]
    assert "adapter_repo_name" not in adapter.input_schema["properties"]

    assert normalize.capability == "read"
    assert normalize.submit_behavior == "none"
    assert "Normalize a resource target" in normalize.description
    assert "target" in normalize.input_schema["properties"]


def test_nested_tool_input_schemas_are_materialized_for_all_discovery_surfaces() -> None:
    runtime = make_mcp_runtime()

    resource = _tool(runtime, "repo_resource_discovery_submit", "submit_repo_resource_discovery_result")
    provider = _tool(runtime, "repo_lean_provider_discovery_submit", "submit_repo_lean_provider_discovery_result")
    coordinator = _tool(runtime, "native_repo_coordinator_submit", "submit_repo_exploration")
    mathlib = _tool(runtime, "repo_mathlib_recon", "record_mathlib_batch")

    for tool in (resource, provider, coordinator, mathlib):
        _assert_self_contained(tool.input_schema)

    resource_item = resource.input_schema["properties"]["candidates"]["items"]
    assert resource_item["type"] == "object"
    assert resource_item["properties"]["canonical_locator"]["description"]
    assert resource_item["properties"]["recommended_handling"]["enum"] == [
        "local_resource",
        "provider_requirement",
        "inspect_later",
        "ignore",
    ]

    provider_item = provider.input_schema["properties"]["candidates"]["items"]
    assert provider_item["type"] == "object"
    assert provider_item["properties"]["resolved_revision"]["description"]

    exploration_item = coordinator.input_schema["properties"]["explorations"]["items"]
    assert exploration_item["type"] == "object"
    assert exploration_item["properties"]["objective"]["description"]

    module_item = mathlib.input_schema["properties"]["modules"]["items"]
    declaration_item = mathlib.input_schema["properties"]["declarations"]["items"]
    assert module_item["type"] == "object"
    assert declaration_item["type"] == "object"
    assert declaration_item["properties"]["decl_name"]["description"]


def test_malformed_tool_schema_fails_registration_with_tool_identity(monkeypatch) -> None:
    runtime = make_mcp_runtime()
    malformed = ToolSpecView(
        name="submit_malformed",
        description="Malformed test tool.",
        capability=ToolCapability.SUBMIT,
        args_schema={
            "type": "object",
            "properties": {"payload": {"$ref": "#/$defs/Missing"}},
        },
        backing_service="test",
        backing_method="submit",
        result_view="test",
        submit_behavior=SubmitBehavior.TERMINAL,
    )
    app = FastMcpViewApp(
        view_key="malformed",
        tool_names=[malformed.name],
        tools=[malformed],
        summary="Malformed test view.",
    )
    monkeypatch.setattr(
        runtime.tool_facade,
        "build_mcp_view_server",
        lambda _view_key: runtime.foundation.ok(app),
    )

    result = build_mcp_tool_registrations(runtime, view_key="malformed")

    assert not result.ok
    issue = result.issues[0]
    assert issue.kind == "tool_schema_materialization_failed"
    assert issue.object_ref == "submit_malformed"
    assert issue.field == "input_schema"
    assert issue.details["code"] == "schema_ref_unresolved"
    assert issue.details["ref"] == "#/$defs/Missing"


def _tool(runtime, view_key: str, tool_name: str):  # noqa: ANN001 - compact test helper.
    registrations = build_mcp_tool_registrations(runtime, view_key=view_key)
    assert registrations.ok and registrations.value is not None
    return {tool.name: tool for tool in registrations.value}[tool_name]


def _assert_self_contained(value) -> None:  # noqa: ANN001 - recursive JSON test helper.
    if isinstance(value, dict):
        assert "$ref" not in value
        assert "$defs" not in value
        assert "definitions" not in value
        for child in value.values():
            _assert_self_contained(child)
    elif isinstance(value, list):
        for child in value:
            _assert_self_contained(child)
