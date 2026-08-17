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
    assert "## Filesystem Scope" in spec.developer_instructions
    assert "decl-round-change-planning" in spec.skill_specs
    assert spec.tool_view_config.application_view_key == "content_plan"
    assert spec.tool_view_config.submit_view_key == "content_plan_submit"
    assert spec.fixed_env["LEAN_CONSTELLATION_AGENT_TYPE"] == "ContentPlanAgent"
    assert spec.fixed_env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == "content_plan"


def test_generated_homes_distinguish_local_visibility_from_stable_scope_inputs() -> None:
    content_plan = build_agent_home_bootstrap_spec(
        "ContentPlanAgent",
        mcp_http_base_url="http://127.0.0.1:8765",
    )
    coordinator = build_agent_home_bootstrap_spec(
        "CoordinatorAgent",
        mcp_http_base_url="http://127.0.0.1:8765",
    )

    local = content_plan.skill_specs[
        "current-node-public-boundary-curation"
    ].body
    scope = coordinator.skill_specs["scope-export-interface-curation"].body
    assert "exact current committed revision" in local
    assert "first open-only Content task" in local
    assert "active committed Content head" not in local
    assert "active committed contract head" in scope
    assert "Open child candidates are not export sources" in scope
    assert "must already have a caller-owned open revision" in scope
    assert "never creates or commits the target" in scope


def test_coordinator_home_carries_concise_contract_field_semantics() -> None:
    coordinator = build_agent_home_bootstrap_spec(
        "CoordinatorAgent",
        mcp_http_base_url="http://127.0.0.1:8765",
    )

    contract_design = coordinator.skill_specs["node-contract-design"].body
    decomposition = coordinator.skill_specs["coordinator-node-decomposition"].body

    assert "goal is stable mathematical ownership or capability" in contract_design
    assert "objective is the current contract-version action" in contract_design
    assert "exact Content terminal depth in task_completion_mode" in contract_design
    assert "stable repository purpose from the current run objective" in decomposition
    assert "Content terminal depth stays in task_completion_mode" in decomposition


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


def test_codex_and_opencode_repo_discovery_homes_use_the_same_mcp_tool_views() -> None:
    codex = build_agent_home_bootstrap_spec(
        "RepoResourceDiscoveryAgent",
        provider_type="codex",
        mcp_http_base_url="http://127.0.0.1:8765",
    )
    opencode = build_agent_home_bootstrap_spec(
        "RepoResourceDiscoveryAgent",
        provider_type="opencode",
        mcp_http_base_url="http://127.0.0.1:8765",
    )

    assert codex.tool_view_config == opencode.tool_view_config
    assert codex.mcp_servers == opencode.mcp_servers
    assert codex.provider_home_spec.mcp_servers == opencode.provider_home_spec.mcp_servers
    assert isinstance(codex.provider_home_spec.provider_options, CodexHomeOptions)
    assert codex.provider_home_spec.provider_options.mcp_servers == tuple(opencode.mcp_servers)


def test_codex_and_opencode_source_builder_homes_share_material_contract() -> None:
    for agent_type in (
        "SourceCorpusBuilderAgent",
        "SourceCorpusReviewerAgent",
        "ResourceCuratorAgent",
    ):
        codex = build_agent_home_bootstrap_spec(
            agent_type,
            provider_type="codex",
            mcp_http_base_url="http://127.0.0.1:8765",
        )
        opencode = build_agent_home_bootstrap_spec(
            agent_type,
            provider_type="opencode",
            mcp_http_base_url="http://127.0.0.1:8765",
        )

        assert codex.developer_instructions == opencode.developer_instructions
        assert codex.skill_specs == opencode.skill_specs
        assert codex.tool_view_config == opencode.tool_view_config
        assert codex.mcp_servers == opencode.mcp_servers
        shared_skill_key = (
            "source-corpus-draft-curation"
            if agent_type.startswith("SourceCorpus")
            else "resource-draft-curation"
        )
        assert "independent BibTeX" in codex.skill_specs[shared_skill_key].body
        assert "Separate Work Evidence From Durable Material" in codex.skill_specs[
            "faithful-material-preservation"
        ].body

    builder = build_agent_home_bootstrap_spec(
        "SourceCorpusBuilderAgent",
        provider_type="codex",
        mcp_http_base_url="http://127.0.0.1:8765",
    )
    reviewer = build_agent_home_bootstrap_spec(
        "SourceCorpusReviewerAgent",
        provider_type="codex",
        mcp_http_base_url="http://127.0.0.1:8765",
    )

    assert "structured material requests" in builder.developer_instructions
    assert "complete compilation closure" in builder.developer_instructions
    assert "You are read-only" in reviewer.developer_instructions
    assert "isolated temporary copy outside the Source draft" in reviewer.developer_instructions
    assert "never write Reviewer build products into the durable candidate or `_work/`" in reviewer.developer_instructions
    assert "pdf-faithful-transcription" not in reviewer.skill_specs
