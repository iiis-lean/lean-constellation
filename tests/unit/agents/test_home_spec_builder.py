from __future__ import annotations

from agent_runtime_kit.agent.provider_contracts import ModelBackendIdentity, ProviderHomeSpec
from agent_runtime_kit.agent.providers.codex_home import CodexHomeOptions

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


def test_home_bootstrap_spec_embeds_provider_home_spec() -> None:
    spec = build_agent_home_bootstrap_spec(
        "ProofFormalWorkerAgent",
        home_id="proof-formal-worker-home",
        mcp_http_base_url="http://127.0.0.1:8765",
        required_env={"OPENAI_API_KEY"},
    )
    ark_spec = spec.provider_home_spec

    assert isinstance(ark_spec, ProviderHomeSpec)
    assert ark_spec.provider_type == "codex"
    assert ark_spec.home_id == "proof-formal-worker-home"
    assert isinstance(ark_spec.provider_options, CodexHomeOptions)
    assert "lean-proof-formalization" in ark_spec.provider_options.skill_specs
    assert ark_spec.required_env == ("OPENAI_API_KEY",)
    assert len(ark_spec.provider_options.mcp_servers) == 2
    assert {server.name for server in ark_spec.provider_options.mcp_servers} == {
        "lc_app",
        "lc_submit",
    }
    assert {server.url for server in ark_spec.provider_options.mcp_servers} == {
        "http://127.0.0.1:8765/mcp/views/proof_formal_worker/",
        "http://127.0.0.1:8765/mcp/views/decl_stage_worker_submit/",
    }
    assert all(not server.env for server in ark_spec.provider_options.mcp_servers)
    assert all(server.result_profile == "content_only" for server in ark_spec.provider_options.mcp_servers)
    assert all(
        server.http_headers["x-ark-mcp-result-profile"] == "content_only"
        for server in ark_spec.provider_options.mcp_servers
    )
    assert all(server.env_http_headers["x-ark-flow-id"] == "ARK_FLOW_ID" for server in ark_spec.provider_options.mcp_servers)
    assert all(
        server.env_http_headers["x-ark-expected-tool-view"] == "LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW"
        for server in ark_spec.provider_options.mcp_servers
    )
    assert all(server.env_http_headers["x-ark-scope-id"] == "ARK_SCOPE_ID" for server in ark_spec.provider_options.mcp_servers)
    assert all(
        server.env_http_headers["x-ark-retry-attempt"] == "LEAN_CONSTELLATION_RETRY_ATTEMPT"
        for server in ark_spec.provider_options.mcp_servers
    )
    assert all("LEAN_CONSTELLATION_EXPECTED_VIEW_KEY" not in server.env_http_headers.values() for server in ark_spec.provider_options.mcp_servers)


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
    assert spec.provider_home_spec.fixed_env["LEAN_CONSTELLATION_AGENT_TYPE"] == "CoordinatorControlledTestAgent"


def test_home_bootstrap_spec_projects_resources_to_provider_home_spec() -> None:
    identity = ModelBackendIdentity(
        api_provider="deepseek",
        api_mode="openai_chat_completions",
        requested_model="deepseek-chat",
    )

    spec = build_agent_home_bootstrap_spec(
        "ContentPlanAgent",
        provider_type="opencode",
        mcp_http_base_url="http://127.0.0.1:8765",
        model_config=identity,
        required_env={"DEEPSEEK_API_KEY"},
    )

    ark_spec = spec.provider_home_spec
    assert isinstance(ark_spec, ProviderHomeSpec)
    assert spec.home_type == "opencode"
    assert ark_spec.provider_type == "opencode"
    assert ark_spec.model_config == identity
    assert "## Content Plan Agent" in str(ark_spec.instructions[0])
    assert {skill.name for skill in ark_spec.skills} >= {"decl-round-change-planning"}
    assert {server.name for server in ark_spec.mcp_servers} == {"lc_app", "lc_submit"}
    assert ark_spec.required_env == ("DEEPSEEK_API_KEY",)
