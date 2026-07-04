from __future__ import annotations

from lean_constellation.services.tool_facade import RawToolCallContext
from tests.unit.mcp._helpers import make_mcp_runtime, runtime_env


def test_context_resolver_reads_runtime_identity_from_mcp_env(tmp_path) -> None:
    runtime = make_mcp_runtime()
    raw = RawToolCallContext(
        endpoint_view_key="resource_curator",
        env=runtime_env(
            tmp_path,
            view="resource_curator",
            agent_type="resource_curator",
            role="worker",
            flow_id="flow_from_env",
            step_id="step_from_env",
            agent_id="agent_from_env",
        ),
    )

    resolved = runtime.tool_facade.context_resolver.resolve_tool_context(raw)

    assert resolved.ok
    assert resolved.value is not None
    assert resolved.value.runtime.flow_id == "flow_from_env"
    assert resolved.value.runtime.step_id == "step_from_env"
    assert resolved.value.runtime.agent_id == "agent_from_env"
    assert resolved.value.expected_view_key == "resource_curator"
    assert resolved.value.actor.role == "worker"
    assert resolved.value.node is not None
    assert resolved.value.node.node_path == "Main.Core"
    assert resolved.value.decl_stage is not None
    assert resolved.value.decl_stage.batch_decls == ["Main.result"]


def test_context_resolver_accepts_header_aliases_and_rejects_incomplete_metadata(tmp_path) -> None:
    runtime = make_mcp_runtime()
    headers = {
        "X-Ark-Flow-Id": "flow_from_header",
        "X-Ark-Step-Id": "step_from_header",
        "X-Ark-Agent-Id": "agent_from_header",
        "X-Ark-Agent-Type": "resource_curator",
        "X-Ark-Agent-Role": "worker",
        "X-Lean-Constellation-Expected-Tool-View": "resource_curator",
        "X-Lean-Constellation-Repo-Root": str(tmp_path),
    }

    resolved = runtime.tool_facade.context_resolver.resolve_tool_context(
        RawToolCallContext(endpoint_view_key="resource_curator", headers=headers)
    )
    assert resolved.ok
    assert resolved.value is not None
    assert resolved.value.runtime.flow_id == "flow_from_header"

    incomplete = runtime.tool_facade.context_resolver.resolve_tool_context(
        RawToolCallContext(endpoint_view_key="resource_curator", env={"ARK_FLOW_ID": "flow_only"})
    )
    assert not incomplete.ok
    assert incomplete.issues[0].kind == "runtime_context_metadata_incomplete"


def test_context_resolver_rejects_repo_route_mismatch(tmp_path) -> None:
    runtime = make_mcp_runtime()
    repo_a = tmp_path / "RepoA"
    repo_b = tmp_path / "RepoB"
    env = runtime_env(
        repo_a,
        view="resource_curator",
        agent_type="resource_curator",
        role="worker",
        flow_id="flow_from_env",
        step_id="step_from_env",
        agent_id="agent_from_env",
    )

    resolved = runtime.tool_facade.context_resolver.resolve_tool_context(
        RawToolCallContext(
            endpoint_view_key="resource_curator",
            env=env,
            expected_repo_key="RepoB",
            expected_repo_root=repo_b,
        )
    )

    assert not resolved.ok
    assert resolved.issues[0].kind == "runtime_repo_route_mismatch"
