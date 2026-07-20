from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.agents import build_agent_home_bootstrap_spec, build_agent_type_specs, validate_agent_resources
from lean_constellation.agents.testing import build_controlled_test_agent_type_specs
from lean_constellation.app import (
    AdminFlowAdvanceInput,
    AdminStepStartInput,
    ExternalTakeoverCompleteInput,
    ExternalTakeoverToolCallInput,
    ExternalTakeoverToolListInput,
    StartFlowInput,
)
from lean_constellation.mcp import create_mcp_server
from lean_constellation.tools import build_application_tool_groups, build_application_tool_specs, build_application_tool_views
from lean_constellation.tools.submit_registry import build_submit_tool_groups, build_submit_tool_specs, build_submit_tool_views
from tests.real.runtime_matrix.admin_helpers import (
    read_handoff_json,
    run_next_created_step,
    set_external_takeover_override,
    unwrap,
    wait_for_pending_handoff,
)
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace, create_runtime_matrix_workspace


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_all_production_and_controlled_agent_home_specs_validate() -> None:
    production_specs = build_agent_type_specs()
    controlled_specs = build_controlled_test_agent_type_specs(specs=production_specs)
    all_specs = [*production_specs, *controlled_specs]
    report = validate_agent_resources(all_specs)
    assert report.ok, report.issues

    for spec in production_specs:
        home = build_agent_home_bootstrap_spec(
            spec.agent_type,
            mcp_server_command="lean-constellation",
            mcp_server_args=["mcp-stdio", "--view-key", "{view_key}"],
            specs=all_specs,
        )
        assert home.developer_instructions.strip()
        if spec.skill_keys:
            assert home.skill_specs
        assert home.fixed_env["LEAN_CONSTELLATION_AGENT_TYPE"] == spec.agent_type
        assert home.fixed_env["LEAN_CONSTELLATION_AGENT_ROLE"] == spec.role
        assert home.fixed_env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == spec.application_tool_view_key
        assert home.fixed_env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"] == spec.submit_tool_view_key
        assert len(home.mcp_servers) == 2
        assert {server.name for server in home.mcp_servers} == {
            "lc_app",
            "lc_submit",
        }

    controlled_by_base = {item.extends_agent_type: item for item in controlled_specs}
    assert set(controlled_by_base) == {spec.agent_type for spec in production_specs}
    for base in production_specs:
        controlled = controlled_by_base[base.agent_type]
        home = build_agent_home_bootstrap_spec(
            controlled.agent_type,
            mcp_http_base_url="http://127.0.0.1:8765",
            specs=all_specs,
        )
        assert home.fixed_env["LEAN_CONSTELLATION_AGENT_TYPE"] == controlled.agent_type
        assert home.fixed_env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == base.application_tool_view_key
        assert home.fixed_env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"] == base.submit_tool_view_key


def test_all_tool_views_expose_non_empty_mcp_tool_lists(runtime_matrix_workspace: RuntimeMatrixWorkspace) -> None:
    ws = runtime_matrix_workspace
    app_tools = build_application_tool_specs()
    app_groups = build_application_tool_groups(app_tools)
    app_views = build_application_tool_views(app_groups)
    submit_tools = build_submit_tool_specs()
    submit_groups = build_submit_tool_groups(submit_tools)
    submit_views = build_submit_tool_views(submit_groups)
    view_keys = [*(view.key for view in app_views), *(view.key for view in submit_views)]

    server = unwrap(create_mcp_server(ws.runtime, view_keys=view_keys))
    assert server.list_endpoints() == sorted(view_keys)

    for view in app_views:
        listed = unwrap(server.list_tools(view.key))
        names = {tool.name for tool in listed}
        assert names
        assert all(not name.startswith("submit_") for name in names)

    for view in submit_views:
        listed = unwrap(server.list_tools(view.key))
        names = {tool.name for tool in listed}
        assert names
        assert all(name.startswith("submit_") for name in names)


def test_external_takeover_handoff_includes_prompt_env_and_tool_lists(
    tmp_path: Path,
) -> None:
    ws = create_runtime_matrix_workspace(tmp_path, initialize_provider_format=False)
    ws.create_home("RepoFormatDiscoveryControlledTestAgent")
    ws.write_bootstrap_preparation(ws.provider_repo)
    started = unwrap(
        ws.admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="requirement_group_repo_bootstrap",
                scope_id="repo:Provider",
                enqueue=False,
                params={
                    "target_repo": "Provider",
                    "repo_root": str(ws.provider_repo),
                    "workspace_root": str(ws.workspace_root),
                    "requirement_refs": ["Consumer:need_provider"],
                },
            ),
            repo_root=str(ws.provider_repo),
        )
    )
    run_next_created_step(ws.admin, started.flow_id)
    advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=started.flow_id)))
    assert advanced.created_step_id is not None
    set_external_takeover_override(
        ws.admin,
        advanced.created_step_id,
        agent_type="RepoFormatDiscoveryControlledTestAgent",
        prompt_overlay="Runtime Matrix handoff marker: repo-format.",
    )

    started_step = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=advanced.created_step_id, wait=False)))
    assert started_step.status in {"created", "running"}
    handoff = wait_for_pending_handoff(ws.admin)
    payload = read_handoff_json(handoff.handoff_path)
    env = payload["env"]
    assert "Runtime Matrix handoff marker: repo-format." in payload["prompt"]
    assert payload["developer_instructions"].strip()
    assert env["LEAN_CONSTELLATION_AGENT_TYPE"] == "RepoFormatDiscoveryControlledTestAgent"
    assert env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == "repo_format_discovery"
    assert env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"] == "repo_format_discovery_submit"
    assert payload["workdir"] == str(ws.provider_repo)
    assert payload["agent_id"]
    assert payload["home_id"] == "RepoFormatDiscoveryControlledTestAgent"

    app_tools = unwrap(
        ws.admin.list_external_takeover_tools(
            ExternalTakeoverToolListInput(handoff_id=handoff.handoff_id, view_kind="application")
        )
    )
    submit_tools = unwrap(
        ws.admin.list_external_takeover_tools(
            ExternalTakeoverToolListInput(handoff_id=handoff.handoff_id, view_kind="submit")
        )
    )
    assert "get_preparation_input" in {tool.name for tool in app_tools}
    assert "submit_native_repo_choice" in {tool.name for tool in submit_tools}
    called = unwrap(
        ws.admin.call_external_takeover_tool(
            ExternalTakeoverToolCallInput(
                handoff_id=handoff.handoff_id,
                view_kind="submit",
                tool_name="submit_native_repo_choice",
                arguments={"summary": "Use native after handoff inspection.", "searched_targets": ["handoff inspection"], "rejected_candidates": []},
            )
        )
    )
    assert called.ok is True
    completed = unwrap(
        ws.admin.complete_external_takeover(
            ExternalTakeoverCompleteInput(
                handoff_id=handoff.handoff_id,
                final_response="Runtime Matrix handoff resource inspection complete.",
                thread_id=f"runtime-matrix-{handoff.handoff_id}",
            )
        )
    )
    assert completed.status == "completed"
    waited = unwrap(ws.admin.wait_step(AdminStepStartInput(step_id=advanced.created_step_id, wait=True, timeout_s=10)))
    assert waited.status == "completed"
