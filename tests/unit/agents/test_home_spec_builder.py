from __future__ import annotations

from agent_runtime_kit.agent.homes import HomeCreateSpec

from lean_constellation.agents import build_agent_home_bootstrap_spec, build_agent_type_specs, derive_agent_type_spec


def test_home_bootstrap_spec_contains_instruction_skills_and_tool_views() -> None:
    spec = build_agent_home_bootstrap_spec(
        "ContentPlanAgent",
        mcp_http_base_url="http://127.0.0.1:8765",
    )

    assert spec.agent_type == "ContentPlanAgent"
    assert "## Content Plan Agent" in spec.developer_instructions
    assert "decl-round-change-planning" in spec.skill_specs
    assert spec.tool_view_config.application_view_key == "content_plan"
    assert spec.tool_view_config.submit_view_key == "content_plan_submit"
    assert spec.fixed_env["LEAN_CONSTELLATION_AGENT_TYPE"] == "ContentPlanAgent"
    assert spec.fixed_env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == "content_plan"


def test_home_bootstrap_spec_embeds_ark_home_create_spec() -> None:
    spec = build_agent_home_bootstrap_spec(
        "ProofFormalWorkerAgent",
        home_id="proof-formal-worker-home",
        mcp_http_base_url="http://127.0.0.1:8765",
        required_env={"OPENAI_API_KEY"},
    )
    ark_spec = spec.ark_home_create_spec

    assert isinstance(ark_spec, HomeCreateSpec)
    assert ark_spec.cli_type == "codex"
    assert ark_spec.home_id == "proof-formal-worker-home"
    assert "lean-proof-formalization" in ark_spec.skill_specs
    assert ark_spec.required_env == {"OPENAI_API_KEY"}
    assert len(ark_spec.mcp_servers) == 2
    assert {server.name for server in ark_spec.mcp_servers} == {
        "lean-constellation-tools-application",
        "lean-constellation-tools-submit",
    }
    assert {server.url for server in ark_spec.mcp_servers} == {
        "http://127.0.0.1:8765/mcp/views/proof_formal_worker/",
        "http://127.0.0.1:8765/mcp/views/decl_stage_worker_submit/",
    }
    assert all(not server.env for server in ark_spec.mcp_servers)
    assert all(server.env_http_headers["x-ark-flow-id"] == "ARK_FLOW_ID" for server in ark_spec.mcp_servers)
    assert all(
        server.env_http_headers["x-ark-expected-tool-view"] == "LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW"
        for server in ark_spec.mcp_servers
    )
    assert all(server.env_http_headers["x-ark-scope-id"] == "ARK_SCOPE_ID" for server in ark_spec.mcp_servers)
    assert all(
        server.env_http_headers["x-ark-retry-attempt"] == "LEAN_CONSTELLATION_RETRY_ATTEMPT"
        for server in ark_spec.mcp_servers
    )
    assert all("LEAN_CONSTELLATION_EXPECTED_VIEW_KEY" not in server.env_http_headers.values() for server in ark_spec.mcp_servers)


def test_home_bootstrap_spec_supports_derived_agent_type_identity() -> None:
    controlled = derive_agent_type_spec(
        base_agent_type="CoordinatorAgent",
        agent_type="CoordinatorControlledTestAgent",
    )
    specs = build_agent_type_specs(extra_specs=[controlled])

    spec = build_agent_home_bootstrap_spec(
        "CoordinatorControlledTestAgent",
        mcp_http_base_url="http://127.0.0.1:8765",
        specs=specs,
    )

    assert spec.agent_type == "CoordinatorControlledTestAgent"
    assert spec.fixed_env["LEAN_CONSTELLATION_AGENT_TYPE"] == "CoordinatorControlledTestAgent"
    assert spec.fixed_env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == "native_repo_coordinator"
    assert spec.ark_home_create_spec.fixed_env["LEAN_CONSTELLATION_AGENT_TYPE"] == "CoordinatorControlledTestAgent"


def test_home_bootstrap_spec_keeps_legacy_single_http_server_url() -> None:
    spec = build_agent_home_bootstrap_spec(
        "ContentPlanAgent",
        mcp_server_url="http://127.0.0.1:8765/mcp",
    )

    assert len(spec.mcp_servers) == 1
    assert spec.mcp_servers[0].name == "lean-constellation-tools"
    assert spec.mcp_servers[0].url == "http://127.0.0.1:8765/mcp"
