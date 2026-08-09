from __future__ import annotations

from lean_constellation.mcp import create_mcp_server
from tests.unit.mcp._helpers import FakeSubmissionGateway, make_mcp_runtime, runtime_env


def test_mcp_handler_invokes_tool_facade_for_regular_tool(tmp_path) -> None:
    runtime = make_mcp_runtime()
    server = create_mcp_server(runtime, view_keys=["resource_curator"])
    assert server.ok and server.value is not None

    result = server.value.call_tool(
        "resource_curator",
        "normalize_resource_target",
        {"target": "https://example.com/paper"},
        env=runtime_env(tmp_path, view="resource_curator", agent_type="resource_curator", role="worker"),
    )

    assert result.ok
    assert result.value is not None
    assert result.value.ok is True
    assert result.value.value is not None
    assert result.value.value["kind"] == "web_url"


def test_mcp_submit_path_returns_gate_errors_and_records_successful_submit(tmp_path) -> None:
    gateway = FakeSubmissionGateway()
    runtime = make_mcp_runtime(gateway)
    server = create_mcp_server(runtime, view_keys=["repo_format_discovery_submit"])
    assert server.ok and server.value is not None
    env = runtime_env(
        tmp_path,
        view="repo_format_discovery_submit",
        agent_type="RepoFormatDiscoveryAgent",
        role="coordinator",
        agent_id="agent_submit",
    )

    rejected = server.value.call_tool(
        "repo_format_discovery_submit",
        "submit_adapter_repo_choice",
        {"git_url": "https://example.com/project", "evidence_summary": "Remote probe found lakefile.lean."},
        env=env,
    )
    assert rejected.ok
    assert rejected.value is not None
    assert rejected.value.ok is False
    assert rejected.value.issues[0].kind == "git_url_invalid"
    assert gateway.accepted == []

    submitted = server.value.call_tool(
        "repo_format_discovery_submit",
        "submit_native_repo_choice",
        {"summary": "Use native.", "searched_targets": ["provider theorem Lean"]},
        env=env,
    )
    assert submitted.ok
    assert submitted.value is not None
    assert submitted.value.ok is True
    assert len(gateway.accepted) == 1
    assert gateway.accepted[0].submission_type == "repo_format_native_choice"
    assert gateway.accepted[0].submitted_by_agent_id == "agent_submit"
