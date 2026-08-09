from __future__ import annotations

import json

from agent_runtime_kit.agent.homes import MCP_RESULT_PROFILE_ENV, MCP_RESULT_PROFILE_HTTP_HEADER
from lean_constellation.mcp import create_mcp_server
from lean_constellation.mcp.stdio import (
    _compact_agent_tool_result,
    create_mcp_protocol_server,
    mcp_protocol_call_tool,
    mcp_protocol_tools,
)
from tests.unit.mcp._helpers import FakeSubmissionGateway, make_mcp_runtime, runtime_env


def test_stdio_bridge_exposes_tools_with_tool_spec_schema(tmp_path) -> None:
    runtime = make_mcp_runtime()
    protocol = create_mcp_protocol_server(runtime, view_key="repo_format_discovery_submit")
    assert protocol.ok and protocol.value is not None
    server = create_mcp_server(runtime, view_keys=["repo_format_discovery_submit"])
    endpoint = server.value.endpoint("repo_format_discovery_submit").value

    tools = {tool.name: tool for tool in mcp_protocol_tools(endpoint)}

    assert "submit_native_repo_choice" in tools
    native = tools["submit_native_repo_choice"]
    assert native.inputSchema["required"] == ["summary", "searched_targets"]
    assert "source_corpus_mode" not in native.inputSchema["properties"]
    assert "native_repo_name" not in native.inputSchema["properties"]


def test_stdio_bridge_exposes_self_contained_repo_discovery_schemas() -> None:
    runtime = make_mcp_runtime()
    server = create_mcp_server(
        runtime,
        view_keys=[
            "repo_resource_discovery_submit",
            "native_repo_coordinator_submit",
            "repo_mathlib_recon",
        ],
    )
    assert server.ok and server.value is not None

    resource_endpoint = server.value.endpoint("repo_resource_discovery_submit").value
    coordinator_endpoint = server.value.endpoint("native_repo_coordinator_submit").value
    mathlib_endpoint = server.value.endpoint("repo_mathlib_recon").value
    resource = {tool.name: tool for tool in mcp_protocol_tools(resource_endpoint)}[
        "submit_repo_resource_discovery_result"
    ]
    coordinator = {tool.name: tool for tool in mcp_protocol_tools(coordinator_endpoint)}[
        "submit_repo_exploration"
    ]
    mathlib = {tool.name: tool for tool in mcp_protocol_tools(mathlib_endpoint)}[
        "record_mathlib_batch"
    ]

    for schema in (resource.inputSchema, coordinator.inputSchema, mathlib.inputSchema):
        _assert_no_schema_refs(schema)

    candidate = resource.inputSchema["properties"]["candidates"]["items"]
    assert candidate["type"] == "object"
    assert candidate["properties"]["target"]["description"]
    assert "canonical_locator" not in candidate["properties"]

    coordinator_properties = coordinator.inputSchema["properties"]
    assert "explorations" not in coordinator_properties
    assert coordinator_properties["resource_objective"]["description"]
    assert coordinator_properties["lean_provider_objective"]["description"]
    assert coordinator_properties["mathlib_objective"]["description"]

    declaration = mathlib.inputSchema["properties"]["declarations"]["items"]
    assert declaration["type"] == "object"
    assert set(declaration["properties"]) == {"decl_name", "summary", "source"}


def test_stdio_bridge_reports_nested_argument_errors_with_field_paths(tmp_path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = make_mcp_runtime(gateway)
    server = create_mcp_server(runtime, view_keys=["repo_resource_discovery_submit"])
    endpoint = server.value.endpoint("repo_resource_discovery_submit").value
    env = runtime_env(
        tmp_path,
        view="repo_resource_discovery_submit",
        agent_type="RepoResourceDiscoveryAgent",
        role="worker",
    )

    rejected = mcp_protocol_call_tool(
        endpoint,
        "submit_repo_resource_discovery_result",
        {
            "summary": "Incomplete nested candidate.",
            "outcome": "completed",
            "candidates": [{"title": "Only a title"}],
        },
        env=env,
    )

    assert rejected.isError is True
    fields = {issue["field"] for issue in rejected.structuredContent["issues"]}
    assert "candidates[0].target" in fields
    assert "candidates[0].support_summary" in fields
    assert "candidates[0].recommended_handling" in fields
    assert "candidates[0].title" in fields
    assert all(issue["kind"] == "tool_arguments_invalid" for issue in rejected.structuredContent["issues"])
    assert gateway.accepted == []


def test_stdio_bridge_converts_tool_result_for_success_and_gate_failure(tmp_path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = make_mcp_runtime(gateway)
    server = create_mcp_server(runtime, view_keys=["repo_format_discovery_submit"])
    endpoint = server.value.endpoint("repo_format_discovery_submit").value
    env = runtime_env(
        tmp_path,
        view="repo_format_discovery_submit",
        agent_type="RepoFormatDiscoveryAgent",
        role="coordinator",
        agent_id="agent_stdio",
    )

    rejected = mcp_protocol_call_tool(
        endpoint,
        "submit_adapter_repo_choice",
        {"git_url": "https://example.com/project", "evidence_summary": "Remote probe found a lakefile."},
        env=env,
    )
    assert rejected.isError is True
    assert rejected.structuredContent["ok"] is False
    assert rejected.structuredContent["issues"][0]["kind"] == "git_url_invalid"

    submitted = mcp_protocol_call_tool(
        endpoint,
        "submit_native_repo_choice",
        {"summary": "Use native.", "searched_targets": ["No repo found"]},
        env=env,
    )
    assert submitted.isError is False
    assert submitted.structuredContent["ok"] is True
    assert len(gateway.accepted) == 1


def test_stdio_bridge_content_only_preserves_complete_success_and_error_payloads(tmp_path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = make_mcp_runtime(gateway)
    server = create_mcp_server(runtime, view_keys=["repo_format_discovery_submit"])
    endpoint = server.value.endpoint("repo_format_discovery_submit").value
    env = {
        **runtime_env(
            tmp_path,
            view="repo_format_discovery_submit",
            agent_type="RepoFormatDiscoveryAgent",
            role="coordinator",
            agent_id="agent_content_only",
        ),
        MCP_RESULT_PROFILE_ENV: "content_only",
    }

    rejected = mcp_protocol_call_tool(
        endpoint,
        "submit_adapter_repo_choice",
        {"git_url": "https://example.com/project", "evidence_summary": "Remote probe found a lakefile."},
        env=env,
    )
    submitted = mcp_protocol_call_tool(
        endpoint,
        "submit_native_repo_choice",
        {"summary": "Use native.", "searched_targets": ["provider theorem Lean"]},
        env=env,
    )

    assert rejected.isError is True
    assert rejected.structuredContent is None
    rejected_payload = json.loads(rejected.content[0].text)
    assert rejected_payload["ok"] is False
    assert rejected_payload["issues"][0]["kind"] == "git_url_invalid"
    assert submitted.isError is False
    assert submitted.structuredContent is None
    submitted_payload = json.loads(submitted.content[0].text)
    assert submitted_payload["ok"] is True
    assert "issues" not in submitted_payload
    assert "submission" not in submitted_payload["value"]
    assert "agent_view" not in submitted_payload["value"]
    assert submitted_payload["value"]["submission_type"] == "repo_format_native_choice"


def test_content_only_compaction_removes_only_representation_duplicates() -> None:
    compact = _compact_agent_tool_result(
        {
            "ok": True,
            "summary": "Loaded current truth.",
            "issues": [],
            "value": {
                "summary": "Loaded current truth.",
                "items": [{"name": "main"}],
            },
        }
    )
    distinct = _compact_agent_tool_result(
        {
            "ok": True,
            "summary": "Mutation completed.",
            "issues": [{"kind": "warning", "message": "Review the result."}],
            "value": {
                "summary": "The managed projection changed.",
                "changed": True,
            },
        }
    )

    assert compact == {
        "ok": True,
        "summary": "Loaded current truth.",
        "value": {"items": [{"name": "main"}]},
    }
    assert distinct["issues"] == [{"kind": "warning", "message": "Review the result."}]
    assert distinct["value"]["summary"] == "The managed projection changed."


def test_stdio_bridge_result_profile_header_and_invalid_configuration() -> None:
    runtime = make_mcp_runtime()
    server = create_mcp_server(runtime, view_keys=["repo_format_discovery_submit"])
    endpoint = server.value.endpoint("repo_format_discovery_submit").value

    content_only = mcp_protocol_call_tool(
        endpoint,
        "missing_tool",
        {},
        headers={MCP_RESULT_PROFILE_HTTP_HEADER.upper(): "content_only"},
    )
    invalid = mcp_protocol_call_tool(
        endpoint,
        "missing_tool",
        {},
        headers={MCP_RESULT_PROFILE_HTTP_HEADER: "summary"},
    )

    assert content_only.structuredContent is None
    assert json.loads(content_only.content[0].text)["ok"] is False
    assert invalid.structuredContent is not None
    assert invalid.structuredContent["issues"][0]["kind"] == "mcp_result_profile_invalid"


def _assert_no_schema_refs(value) -> None:  # noqa: ANN001 - recursive JSON helper.
    if isinstance(value, dict):
        assert "$ref" not in value
        assert "$dynamicRef" not in value
        for child in value.values():
            _assert_no_schema_refs(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_schema_refs(child)
