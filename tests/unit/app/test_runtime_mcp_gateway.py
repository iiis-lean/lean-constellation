from __future__ import annotations

from agent_runtime_kit.agent.provider_contracts import ProviderHomeSpec
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus, StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.app import create_app_runtime_services
from lean_constellation.agents import derive_agent_type_spec
from lean_constellation.flows.common.agent_steps import RepoFormatDiscoveryAgentStep
from lean_constellation.services.tool_facade import RawToolCallContext


def test_runtime_mcp_gateway_fallback_resolves_repo_context_from_ark_state(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Provider"
    repo_root.mkdir()
    scope_id = "repo:Provider"
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="requirement_group_repo_bootstrap",
            scope_id=scope_id,
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(tmp_path),
                "requirement_refs": [],
                "resolved_provider_route": {"kind": "auto"},
            },
        ),
        enqueue=False,
    )
    runtime.ark.agent_service.home_service.create_home(ProviderHomeSpec(provider_type="codex", home_id="RepoFormatDiscoveryAgent"))
    agent = runtime.ark.agent_service.create_agent(
        scope_id,
        "RepoFormatDiscoveryAgent",
        home_id="RepoFormatDiscoveryAgent",
    )
    step_id = "step_repo_format_discovery"
    step = RepoFormatDiscoveryAgentStep(
        step_id=step_id,
        flow_id=flow_id,
        scope_id=scope_id,
        state=AgentStepState(
            agent_role="repo_format_discovery",
            agent_type="RepoFormatDiscoveryAgent",
            home_id="RepoFormatDiscoveryAgent",
            create_agent_if_missing=False,
            env_overrides={
                "LEAN_CONSTELLATION_AGENT_TYPE": "RepoFormatDiscoveryAgent",
                "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "repo_format_discovery_submit",
            },
            workdir_override=str(repo_root),
        ),
    )
    step.agent_bindings.by_role["repo_format_discovery"] = agent.agent_id
    runtime.ark.step_service.create_step(step, enqueue=False)
    runtime.ark.flow_service.store.update_step_record(step_id, lambda stored: setattr(stored, "status", StepStatus.RUNNING))
    runtime.ark.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: (
            flow.step_ids.append(step_id),
            setattr(flow, "current_step_id", step_id),
            setattr(flow, "status", FlowStatus.RUNNING),
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="repo_format_discovery_submit",
            env={"ARK_FLOW_ID": flow_id, "ARK_STEP_ID": step_id, "ARK_AGENT_ID": agent.agent_id},
        ),
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "Use native.", "searched_targets": ["provider theorem Lean"]},
    )

    assert result.ok and result.value is not None
    assert result.value.ok is True
    submitted = runtime.ark.flow_service.get_step(step_id).submission
    assert submitted is not None
    assert submitted.submission_type == "repo_format_native_choice"


def test_runtime_mcp_gateway_supports_derived_agent_type_tool_view_permissions(tmp_path) -> None:
    controlled = derive_agent_type_spec(
        base_agent_type="RepoFormatDiscoveryAgent",
        agent_type="RepoFormatDiscoveryControlledTestAgent",
    )
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".runtime",
        extra_agent_type_specs=[controlled],
    )
    repo_root = tmp_path / "Provider"
    repo_root.mkdir()
    scope_id = "repo:Provider"
    flow_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="requirement_group_repo_bootstrap",
            scope_id=scope_id,
            params={
                "target_repo": "Provider",
                "repo_root": str(repo_root),
                "workspace_root": str(tmp_path),
                "requirement_refs": [],
                "resolved_provider_route": {"kind": "auto"},
            },
        ),
        enqueue=False,
    )
    runtime.ark.agent_service.home_service.create_home(
        ProviderHomeSpec(provider_type="codex", home_id="RepoFormatDiscoveryControlledTestAgent")
    )
    agent = runtime.ark.agent_service.create_agent(
        scope_id,
        "RepoFormatDiscoveryControlledTestAgent",
        home_id="RepoFormatDiscoveryControlledTestAgent",
    )
    step_id = "step_repo_format_discovery_controlled"
    step = RepoFormatDiscoveryAgentStep(
        step_id=step_id,
        flow_id=flow_id,
        scope_id=scope_id,
        state=AgentStepState(
            agent_role="repo_format_discovery",
            agent_type="RepoFormatDiscoveryControlledTestAgent",
            home_id="RepoFormatDiscoveryControlledTestAgent",
            create_agent_if_missing=False,
            env_overrides={
                "LEAN_CONSTELLATION_AGENT_TYPE": "RepoFormatDiscoveryControlledTestAgent",
                "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "repo_format_discovery_submit",
            },
            workdir_override=str(repo_root),
        ),
    )
    step.agent_bindings.by_role["repo_format_discovery"] = agent.agent_id
    runtime.ark.step_service.create_step(step, enqueue=False)
    runtime.ark.flow_service.store.update_step_record(step_id, lambda stored: setattr(stored, "status", StepStatus.RUNNING))
    runtime.ark.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: (
            flow.step_ids.append(step_id),
            setattr(flow, "current_step_id", step_id),
            setattr(flow, "status", FlowStatus.RUNNING),
        ),
    )

    result = runtime.tool_facade.invoke_agent_tool(
        RawToolCallContext(
            endpoint_view_key="repo_format_discovery_submit",
            env={"ARK_FLOW_ID": flow_id, "ARK_STEP_ID": step_id, "ARK_AGENT_ID": agent.agent_id},
        ),
        tool_name="submit_native_repo_choice",
        flat_args={"summary": "Use native.", "searched_targets": ["provider theorem Lean"]},
    )

    assert result.ok and result.value is not None
    assert result.value.ok is True
    submitted = runtime.ark.flow_service.get_step(step_id).submission
    assert submitted is not None
    assert submitted.submission_type == "repo_format_native_choice"
