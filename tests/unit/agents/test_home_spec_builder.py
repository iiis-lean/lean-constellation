from __future__ import annotations

from agent_runtime_kit.agent.homes import HomeCreateSpec

from lean_constellation.agents import build_agent_home_bootstrap_spec


def test_home_bootstrap_spec_contains_instruction_skills_and_tool_views() -> None:
    spec = build_agent_home_bootstrap_spec(
        "ContentPlanAgent",
        mcp_server_url="http://127.0.0.1:8765/mcp",
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
        mcp_server_url="http://127.0.0.1:8765/mcp",
        required_env={"OPENAI_API_KEY"},
    )
    ark_spec = spec.ark_home_create_spec

    assert isinstance(ark_spec, HomeCreateSpec)
    assert ark_spec.cli_type == "codex"
    assert ark_spec.home_id == "proof-formal-worker-home"
    assert "lean-proof-formalization" in ark_spec.skill_specs
    assert ark_spec.required_env == {"OPENAI_API_KEY"}
    assert len(ark_spec.mcp_servers) == 1
    assert ark_spec.mcp_servers[0].url == "http://127.0.0.1:8765/mcp"
