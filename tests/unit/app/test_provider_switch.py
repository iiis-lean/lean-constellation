from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from agent_runtime_kit.agent.homes import HomeRecord

from lean_constellation.app.config import AgentHomeOverrideAppConfig, LeanAppConfig
from lean_constellation.app.runtime import create_app_runtime_from_config
from lean_constellation.app.provider_switch import ProviderSwitchInput
from lean_constellation.flows.content_node_task.flows import (
    _valid_content_plan_agent_binding,
)


def test_new_agents_use_staged_home_through_config_registry(tmp_path):
    role = "ContentPlanAgent"
    runtime = create_app_runtime_from_config(
        LeanAppConfig(
            workspace_root=tmp_path,
            materialize_agent_homes=False,
            agent_home_overrides={
                role: AgentHomeOverrideAppConfig(
                    provider_type="codex", home_id="ContentPlanLunaBee"
                )
            },
        )
    )
    service = runtime.ark.agent_service
    service.home_service.store.upsert_home(
        HomeRecord("codex", "ContentPlanLunaBee", "homes/new")
    )
    agent = service.create_agent("repo:Example:node:Main", role)
    assert agent.home_id == "ContentPlanLunaBee"
    assert agent.provider_type == "codex"
    assert agent.session_locator is None


@pytest.mark.parametrize(
    "status,expected", [("idle", True), ("closed", False), ("running", False)]
)
def test_content_plan_history_never_revives_retired_identity(status, expected):
    agent = SimpleNamespace(
        agent_type="ContentPlanAgent",
        home_id="ContentPlanLunaBee",
        scope_id="scope",
        status=status,
    )
    ctx = SimpleNamespace(
        ark=SimpleNamespace(agent_service=SimpleNamespace(get_agent=lambda key: agent))
    )
    assert _valid_content_plan_agent_binding(ctx, "old", scope_id="scope") is expected


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_agent_ids": [], "target_provider": "codex"},
        {"source_agent_ids": ["old"], "target_provider": "unknown"},
        {
            "source_agent_ids": ["old"],
            "target_provider": "codex",
            "expected_plan_hash": "x",
        },
        {
            "source_agent_ids": ["old"],
            "target_provider": "codex",
            "scope_id": "another-repo",
        },
    ],
)
def test_input_rejects_invalid_and_route_scope_override(kwargs):
    with pytest.raises(ValidationError):
        ProviderSwitchInput(**kwargs)


def test_eight_shared_roles_migrate_and_semantic_lease_starts_replacement(tmp_path):
    from contextlib import nullcontext
    from agent_runtime_kit.flow.scheduler import RuntimeScheduleService
    from lean_constellation.agents.ark import build_ark_agent_type_registry
    from lean_constellation.app import LeanAdminApi, RuntimeSemanticAdvanceInput
    from lean_constellation.app.provider_switch import provider_switch
    from lean_constellation.flows.content_node_task.decl_round.flow import (
        WORKER_AGENT_TYPES,
        REVIEWER_AGENT_TYPES,
    )
    from tests.unit.app.test_admin_shared_stage_binding_recovery import (
        _shared_stage_suspended_boundary,
    )
    from tests.unit.flows.decl_round._helpers import queue_worker_completed

    boundary = _shared_stage_suspended_boundary(tmp_path)
    runtime, agents = boundary.lean_runtime, boundary.runtime.agent_service
    flows = runtime.ark.flow_service
    for suffix, types in [
        ("worker", WORKER_AGENT_TYPES),
        ("reviewer", REVIEWER_AGENT_TYPES),
    ]:
        for stage, agent_type in types.items():
            role = f"{stage}_{suffix}"
            if role == boundary.role:
                continue
            agent = agents.create_agent(
                boundary.previous_agent.scope_id, agent_type, home_id=agent_type
            )
            flows.store.update_flow_record(
                boundary.parent_flow_id,
                lambda f, r=role, a=agent: f.agent_bindings.by_role.update(
                    {r: a.agent_id}
                ),
            )
    agents.agent_types = build_ark_agent_type_registry()
    for spec in agents.agent_types.list():
        spec.default_home_id = spec.agent_type + "_Luna"
    agents.home_service = SimpleNamespace(
        get_home=lambda *a: SimpleNamespace(
            status="active",
            materialization_manifest_hash="new",
            base_config_fingerprint="new",
        ),
        build_execution_context=lambda *a: None,
    )
    agents.hold_agent_boundary = nullcontext
    agents.has_running_agents = lambda: any(
        a.status == "running" for a in agents.list_agents()
    )
    agents.close_agent = lambda key: setattr(agents.get_agent(key), "status", "closed")
    scheduler = RuntimeScheduleService(
        ark_services=runtime.ark, app_services=runtime.app
    )
    source_ids = [a.agent_id for a in agents.list_agents()]
    request = ProviderSwitchInput(source_agent_ids=source_ids, target_provider="codex")
    preview = provider_switch(runtime, boundary.repo_root.name, request, apply=False)
    assert not preview["blockers"], preview
    request.expected_plan_hash = preview["plan_hash"]
    result = provider_switch(runtime, boundary.repo_root.name, request, apply=True)
    assert result["complete"], result
    parent = flows.get_flow(boundary.parent_flow_id)
    assert len(parent.agent_bindings.by_role) == 8
    assert all(
        agents.get_agent(key).home_id.endswith("_Luna")
        for key in parent.agent_bindings.by_role.values()
    )
    replacement_id = flows.get_flow(boundary.round_flow_id).current_step_id
    replacement = flows.get_step(replacement_id)
    assert parent.agent_bindings.get(boundary.role) == replacement.agent_bindings.get(
        boundary.role
    )
    assert not scheduler.step_candidate_queue
    round_id = flows.get_flow(boundary.round_flow_id).input.round_id
    queue_worker_completed(
        boundary.runtime, boundary.repo_root, stage="statement_nl", round_id=round_id
    )
    admitted = LeanAdminApi(runtime).semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="step", action="agent", step_id=replacement_id
        )
    )
    assert admitted.ok, admitted.issues
    tick = scheduler.schedule_ready()
    assert tick.started_step_ids == [replacement_id]
    terminal = runtime.ark.step_service.wait_step(replacement_id, timeout_s=5)
    assert terminal.submission is not None, terminal.error
    assert agents.start_records[-1].agent_id == replacement.agent_bindings.get(
        boundary.role
    )
    assert agents.start_records[-1].agent_id not in source_ids


def test_production_http_switch_routes_keep_repo_scope_and_pause(tmp_path):
    from starlette.testclient import TestClient
    from lean_constellation.app import create_production_app_server

    workspace = tmp_path / "workspace"
    (workspace / "Repo" / ".lean_constellation").mkdir(parents=True)
    app_result = create_production_app_server(
        LeanAppConfig(
            workspace_root=workspace,
            scheduler_enabled=False,
            materialize_agent_homes=False,
        )
    )
    assert app_result.ok
    app = app_result.value
    with TestClient(app) as client:
        assert client.post("/admin/workspace/repos/Repo/load").status_code == 200
        runtime = app.state.lean_constellation_registry.discover_repo(
            "Repo"
        ).value.runtime
        service = runtime.ark.agent_service
        for spec in service.agent_types.list():
            service.home_service.store.upsert_home(
                HomeRecord("codex", spec.agent_type, f"homes/{spec.agent_type}")
            )
        service.home_service.store.upsert_home(
            HomeRecord("codex", "OldHome", "homes/old")
        )
        old = service.create_agent("repo:Repo", "ContentPlanAgent", home_id="OldHome")
        service.home_service.build_execution_context = lambda *a: None
        body = {"source_agent_ids": [old.agent_id], "target_provider": "codex"}
        plan_response = client.post("/admin/repos/Repo/provider-switch/plan", json=body)
        assert plan_response.status_code == 200, plan_response.text
        assert not plan_response.json()["blockers"]
        assert (
            client.post(
                "/admin/repos/Repo/provider-switch/apply", json=body
            ).status_code
            == 400
        )
        result = client.post(
            "/admin/repos/Repo/provider-switch/apply",
            json={**body, "expected_plan_hash": plan_response.json()["plan_hash"]},
        )
        assert result.status_code == 200, result.text
        assert result.json()["complete"]
        assert service.get_agent(old.agent_id).status == "closed"
        assert runtime.ark.pause_controller.is_paused(None)
        assert (
            client.post(
                "/admin/repos/Repo/provider-switch/plan",
                json={**body, "repo_key": "Other"},
            ).status_code
            == 422
        )


def test_home_overrides_cannot_merge_role_identities(tmp_path):
    with pytest.raises(ValueError, match="distinct"):
        create_app_runtime_from_config(LeanAppConfig(
            workspace_root=tmp_path, materialize_agent_homes=False,
            agent_home_overrides={
                "ContentPlanAgent": AgentHomeOverrideAppConfig(home_id="CoordinatorAgent"),
            },
        ))


@pytest.mark.parametrize("global_config_present", [True, False])
def test_production_codex_homes_honor_channel_base_config(tmp_path, global_config_present):
    import tomllib
    from pathlib import Path
    from lean_constellation.agents import build_agent_type_specs
    from lean_constellation.app import create_app_runtime_services
    from lean_constellation.app.agent_provider_config import apply_agent_home_overrides
    from lean_constellation.app.bootstrap import materialize_production_agent_homes

    runtime = create_app_runtime_services(runtime_root=tmp_path / "runtime")
    auth = tmp_path / "test-auth.json"
    auth.write_text("{}")  # Synthetic fixture; no login credentials.
    elan = tmp_path / "elan"
    elan.mkdir()
    bee = tmp_path / "bee.toml"
    bee.write_text('model_provider = "beeapi"\nmodel = "gpt-5.6-luna"\n[model_providers.beeapi]\nname = "BeeAPI"\nbase_url = "https://beeapi.ai/v1"\nwire_api = "responses"\nenv_key = "TEST_BEE_API_KEY"\n')
    subscription = tmp_path / "subscription.toml"
    subscription.write_text('model_provider = "openai"\nmodel = "gpt-5.6-luna"\n')
    role = "ContentPlanAgent"
    specs = [s for s in build_agent_type_specs() if s.agent_type == role]
    previous = []
    for label, config in [("bee", bee), ("subscription", subscription), ("bee_return", bee)]:
        override = AgentHomeOverrideAppConfig(
            home_id=f"{role}_{label}", base_config_path=config,
            model="gpt-5.6-luna", model_reasoning_effort="max",
        )
        result = materialize_production_agent_homes(
            runtime, mcp_http_base_url="http://127.0.0.1:18766",
            base_config_path=bee if global_config_present else None,
            auth_json_path=auth, shared_elan_home=elan,
            agent_type_specs=apply_agent_home_overrides(specs, {role: override}),
            agent_home_overrides={role: override},
        )
        assert result.ok, result.issues
        root = runtime.ark.agent_service.home_service.resolve_home_root("codex", override.home_id)
        rendered = tomllib.loads((root / ".codex/config.toml").read_text())
        assert rendered["model_provider"] == ("openai" if label == "subscription" else "beeapi")
        assert rendered["model_reasoning_effort"] == "max"
        if label != "subscription":
            assert rendered["model_providers"]["beeapi"]["base_url"] == "https://beeapi.ai/v1"
        for path, content in previous:
            assert path.read_bytes() == content  # Staging never overwrites the old Home.
        path = Path(root / ".codex/config.toml")
        previous.append((path, path.read_bytes()))
    assert auth.read_text() == "{}"


def test_codex_bee_subscription_round_trip_replaces_live_shared_identity(tmp_path):
    from agent_runtime_kit.flow import AgentRoleBindings, AgentStepState, FlowRequest, FlowStatus
    from lean_constellation.app import create_app_runtime_services
    from lean_constellation.app.provider_switch import provider_switch
    from lean_constellation.flows.common.agent_steps import ContentPlanAgentStep

    runtime = create_app_runtime_services(runtime_root=tmp_path / "runtime")
    runtime.ark.pause_controller.pause(None)
    agents, flows = runtime.ark.agent_service, runtime.ark.flow_service
    role = agents.agent_types.get("ContentPlanAgent")
    for spec in agents.agent_types.list():
        agents.home_service.store.upsert_home(HomeRecord("codex", spec.agent_type, f"homes/{spec.agent_type}"))
    for channel in ["ContentPlanBee", "ContentPlanSubscription"]:
        agents.home_service.store.upsert_home(HomeRecord("codex", channel, f"homes/{channel}"))
    agents.home_service.build_execution_context = lambda *a: None
    role.default_home_id = "ContentPlanBee"
    current = agents.create_agent("repo:Repo", "ContentPlanAgent")
    originals = [current.agent_id]
    flow_id = flows.start_flow(FlowRequest(
        flow_type="native_repo_coordinator", scope_id="repo:Repo",
        params={"repo_key": "Repo", "repo_root": str(tmp_path / "Repo"), "start_mode": "admin_start"},
    ), enqueue=False)
    pending = ContentPlanAgentStep(
        step_id="pending", flow_id=flow_id, scope_id="repo:Repo",
        state=AgentStepState(agent_role="content_plan", agent_type="ContentPlanAgent", provider_type="codex", home_id="ContentPlanBee"),
        agent_bindings=AgentRoleBindings(by_role={"content_plan": current.agent_id}),
    )
    flows.store.create_step(pending)
    def attach(flow):
        flow.status = FlowStatus.RUNNING
        flow.current_step_id = pending.step_id
        flow.step_ids.append(pending.step_id)
        flow.agent_bindings.by_role["content_plan"] = current.agent_id
    flows.store.update_flow_record(flow_id, attach)
    for destination in ["ContentPlanSubscription", "ContentPlanBee"]:
        role.default_home_id = destination
        request = ProviderSwitchInput(source_agent_ids=[current.agent_id], target_provider="codex")
        preview = provider_switch(runtime, "Repo", request, apply=False)
        assert not preview["blockers"], preview
        request.expected_plan_hash = preview["plan_hash"]
        result = provider_switch(runtime, "Repo", request, apply=True)
        assert result["complete"], result
        successor = agents.get_agent(flows.get_flow(flow_id).agent_bindings.get("content_plan"))
        assert successor.agent_id not in originals
        assert successor.provider_type == "codex" and successor.home_id == destination
        assert successor.session_locator is None
        assert flows.get_step("pending").agent_bindings.get("content_plan") == successor.agent_id
        assert flows.get_step("pending").state.home_id == destination
        assert agents.get_agent(current.agent_id).status == "closed"
        assert runtime.ark.pause_controller.is_paused(None)
        originals.append(successor.agent_id)
        current = successor
    assert len(set(originals)) == 3  # Returning to Bee never revives its closed Agent.


def test_missing_explicit_channel_config_does_not_fall_back(tmp_path):
    from lean_constellation.agents import build_agent_type_specs
    from lean_constellation.app import create_app_runtime_services
    from lean_constellation.app.bootstrap import materialize_production_agent_homes

    runtime = create_app_runtime_services(runtime_root=tmp_path / "runtime")
    base = tmp_path / "bee.toml"
    base.write_text('model_provider = "beeapi"\n')
    auth = tmp_path / "synthetic-auth.json"
    auth.write_text("{}")
    elan = tmp_path / "elan"
    elan.mkdir()
    role = "ContentPlanAgent"
    result = materialize_production_agent_homes(
        runtime, mcp_http_base_url="http://127.0.0.1:18766",
        base_config_path=base, auth_json_path=auth, shared_elan_home=elan,
        agent_type_specs=[s for s in build_agent_type_specs() if s.agent_type == role],
        agent_home_overrides={role: AgentHomeOverrideAppConfig(base_config_path=tmp_path / "missing-subscription.toml")},
    )
    assert not result.ok
    assert result.issues[0].kind == "codex_config_missing"
    assert role in str(result.issues[0].details["missing"])
    assert runtime.ark.agent_service.home_service.store.list_homes() == []


def test_consumed_failed_children_requires_exact_successful_callback():
    from types import SimpleNamespace as S
    from agent_runtime_kit.flow import FlowStatus, StepStatus
    from lean_constellation.app.provider_switch import _consumed_failed_children

    parent = S(flow_id='parent', flow_type='native_repo_coordinator', status=FlowStatus.RUNNING,
               parent_flow_id=None, parent_dispatch_step_id=None, step_ids=['dispatch', 'callback'],
               state=S(waiting_dispatch_step_id='new_dispatch'))
    child = S(flow_id='child', status=FlowStatus.FAILED, parent_flow_id='parent',
              parent_dispatch_step_id='dispatch')
    nested = S(flow_id='nested', status=FlowStatus.FAILED, parent_flow_id='child',
               parent_dispatch_step_id='nested_dispatch')
    dispatch = S(step_id='dispatch', flow_id='parent', status=StepStatus.COMPLETED,
                 step_type='dispatch_step', submission=None)
    callback = S(step_id='callback', flow_id='parent', status=StepStatus.COMPLETED,
                 step_type='coordinator_agent_step', submission=object(), result=S(outcome='content_tasks'),
                 state=S(callback_dispatch_step_id='dispatch'))
    service = S(store=S(list_flows=lambda: [parent,child,nested], list_steps=lambda: [dispatch,callback]))
    assert _consumed_failed_children(service) == ('child', 'nested')
    callback.state.callback_dispatch_step_id = 'unrelated'
    assert _consumed_failed_children(service) == ()
    callback.state.callback_dispatch_step_id = 'dispatch'
    callback.submission = None
    assert _consumed_failed_children(service) == ()
    callback.submission = object()
    parent.state.waiting_dispatch_step_id = 'dispatch'
    assert _consumed_failed_children(service) == ()
