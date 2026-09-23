from lean_constellation.domain.restructure import SectionInput, SectionNL
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.app import LeanAdminApi, LeanAppConfig, create_production_app_server
from lean_constellation.app.admin_api import RestructureStartRepoPlanInput
from lean_constellation.app.restructure import RestructureSupervisor
from lean_constellation.domain.restructure import RestructureStage, DeclKind, DeclStatus, Origin, SourceRef
from lean_constellation.flows.common.testing import create_fake_lean_flow_runtime
from tests.unit.restructure.test_supervisor import _workspace, _runtime, _FakeFlowService, _set_stage


def test_eight_agent_limit_and_terminal_refill(tmp_path):
    service, _ = _workspace(tmp_path, node_paths={"repo": [f"Main.C{i}" for i in range(12)]})
    supervisor = RestructureSupervisor(service)
    fs = _FakeFlowService()
    runtime = _runtime(fs)
    ids = supervisor.start_workspace_frontier(runtime, stage=RestructureStage.DECLARED)
    assert len(ids) == 8
    fs.flows[ids[0]].status = FlowStatus.FAILED
    fs.flows[ids[0]].error = SimpleNamespace(message="local failure")
    result = supervisor.reconcile(runtime)
    assert len(result["advance"]["started"]) == 1
    assert len([r for r in service.store.load_reservations() if r.released_at is None]) == 8


def test_proved_frontier_and_local_declared_failure(tmp_path):
    service, plan = _workspace(tmp_path, node_paths={"repo": ["Main.A", "Main.B", "Main.C"]})
    plan.repos["repo"].plan.nodes["Main.B"].dependencies = ["Main.A"]
    service.store.save_workspace_plan(plan)
    supervisor = RestructureSupervisor(service)
    for name in ["Main.A", "Main.B", "Main.C"]:
        _set_stage(service, "repo", name, RestructureStage.DECLARED)
    assert supervisor.frontier("repo", RestructureStage.PROVED) == ["Main.A", "Main.B", "Main.C"]
    for name in ["Main.A", "Main.B", "Main.C"]:
        _set_stage(service, "repo", name, RestructureStage.PLAN)
    fs = _FakeFlowService()
    runtime = _runtime(fs)
    ids = supervisor.start_workspace_frontier(runtime, stage=RestructureStage.DECLARED)
    fs.flows[ids[0]].status = FlowStatus.COMPLETED
    fs.flows[ids[0]].result = SimpleNamespace(outcome="declared")
    _set_stage(service, "repo", "Main.A", RestructureStage.DECLARED)
    fs.flows[ids[1]].status = FlowStatus.FAILED
    fs.flows[ids[1]].error = SimpleNamespace(message="C failed")
    supervisor.reconcile(runtime)
    assert fs.requests[-1].params["node_path"] == "Main.B"


def test_real_ark_agent_creation_and_intent_adoption(tmp_path):
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A"]})
    supervisor = RestructureSupervisor(service)
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    original = service.store.save_run
    def fail_binding(run, **kwargs):
        if any(b.flow_id for b in run.bindings.values()):
            raise RuntimeError("binding write failed")
        return original(run, **kwargs)
    with patch.object(service.store, "save_run", side_effect=fail_binding):
        with pytest.raises(RuntimeError, match="binding write"):
            supervisor.start_repo_plan(runtime, repo_key="repo")
    flows = runtime.flow_service.list_flows()
    assert len(flows) == 1
    fid = supervisor.start_repo_plan(runtime, repo_key="repo")
    assert fid == flows[0].flow_id
    assert len(runtime.flow_service.list_flows()) == 1
    sid = runtime.flow_service.advance_flow(fid)
    runtime.run_step(sid)
    assert len(runtime.agent_service.agents) == 1
    assert runtime.agent_service.start_records
    assert runtime.flow_service.get_step(sid).status.value != "suspended"


def test_production_http_starts_repo_local_flow(tmp_path):
    from starlette.testclient import TestClient
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A"]})
    config = LeanAppConfig(workspace_root=tmp_path, scheduler_enabled=False, materialize_agent_homes=False)
    result = create_production_app_server(config, view_keys=["restructure_coordinator"])
    assert result.ok
    with TestClient(result.value) as client:
        response = client.post("/admin/restructure/start-repo-plan", json={"repo_key": "repo"})
        assert response.json()["ok"], response.json()
        fid = response.json()["value"]["flow_id"]
        registry = result.value.state.lean_constellation_registry
        runtime = registry.get_or_load("repo", refresh_homes=False).value
        assert runtime.ark.flow_service.get_flow(fid).flow_type == "restructure_repo_plan"
        assert not (tmp_path / "repo/.lean_constellation/repo.json").exists()


def test_declared_contract_is_frozen_and_projections_are_generated(tmp_path):
    service, plan = _workspace(tmp_path, node_paths={"repo": ["Main.A"]})
    from lean_constellation.domain.restructure import DeclRef
    plan.repos["repo"].plan.main_exports = [DeclRef(repo="repo", node="Main.A", name="claim")]
    service.store.save_workspace_plan(plan)
    content = service.content
    decl = content.create_decl("repo", "Result", "Main.A", name="claim", lean_name="claim", kind=DeclKind.THEOREM, summary="claim")
    content.set_sections('repo', 'Main.A', 'claim', statement=SectionInput(nl=SectionNL(text='True', origins=[Origin(source_refs=[SourceRef(corpus='tex', path='paper.tex')])])), proof=SectionInput(nl=SectionNL(text='trivial', origins=[])))
    content.capture("repo", "Main.A", "claim", status=DeclStatus.DECLARED)
    content.submit("repo", "Main.A", stage=RestructureStage.DECLARED)
    assert "import Result.Main.A.Theorems.claim" in (tmp_path / "repo/Result/Main/Interfaces.lean").read_text()
    source = content.read_decl_file("repo", "Main.A", "claim")
    content.edit_decl_file("repo", "Main.A", "claim", source.replace("sorry", "trivial"), stage=RestructureStage.PROVED)
    content.set_sections("repo", "Main.A", "claim", proof=SectionInput(nl=SectionNL(text="trivial")))
    content.capture("repo", "Main.A", "claim", status=DeclStatus.PROVED)
    assert not content.check_submission("repo", "Main.A", stage=RestructureStage.PROVED)
    content.edit_decl_file("repo", "Main.A", "claim", source.replace("claim : True", "different : True").replace("sorry", "trivial"), stage=RestructureStage.PROVED)
    content.set_sections("repo", "Main.A", "claim", proof=SectionInput(nl=SectionNL(text="trivial")))
    content.capture("repo", "Main.A", "claim", status=DeclStatus.PROVED)
    assert any("declared interface changed" in i for i in content.check_submission("repo", "Main.A", stage=RestructureStage.PROVED))


def test_content_agents_use_real_tool_facade_and_finish_both_stages(tmp_path):
    from lean_constellation.app.runtime import create_app_runtime_services
    from lean_constellation.flows.common.testing import FakeAgentService
    from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
    from lean_constellation.domain.restructure import TaskBinding
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A"]})
    runtime = create_app_runtime_services(runtime_root=tmp_path / "ark", start_paused=False,
                                          restructure_workspace_root=tmp_path)
    fake = FakeAgentService(ark=runtime.ark, app=runtime.app)
    runtime.ark.agent_service = fake
    supervisor = RestructureSupervisor(service)
    # FakeAgent is deliberately not ARK's provider Agent model. Use ARK's real
    # StepRunContext submission API at this test boundary, as the native fake harness does.
    runtime.tool_facade.submit_submission.submission_gateway = SimpleNamespace(
        accept_step_submission=lambda ctx, submission: fake._accept_submission(fake.start_records[-1], submission))
    original_wait = fake.wait_agent
    tool_results = []
    for stage in ["declared", "proved"]:
        if stage == "proved":
            run, version = service.store.load_run()
            run.bindings["build:repo:declared"] = TaskBinding(request_id="barrier", run_id=run.run_id,
                repo_key="repo", stage=RestructureStage.DECLARED, terminal_consumed=True, terminal_outcome="succeeded")
            service.store.save_run(run, expected_version=version)
        fid = supervisor.start_workspace_frontier(runtime, stage=RestructureStage(stage), enqueue=False)[0]
        sid = runtime.ark.flow_service.advance_flow(fid)
        def perform_tools(agent_id, **kwargs):
            def invoke(name, args, submit=False):
                view = "restructure_content_submit" if submit else "restructure_content"
                ctx = RuntimeToolContext(flow_id=fid, step_id=sid, agent_id=agent_id,
                    scope_id=runtime.ark.flow_service.get_flow(fid).scope_id,
                    agent_type="RestructureContentPlanAgent" if stage == "declared" else "RestructureContentImplementationAgent",
                    agent_role="plan" if stage == "declared" else "worker", expected_view_key=view,
                    workspace_root=tmp_path, repo_root=tmp_path / "repo", node_path="Main.A", stage=stage)
                result = runtime.tool_facade.invoke_agent_tool(RawToolCallContext(endpoint_view_key=view, runtime_context=ctx),
                                                        tool_name=name, flat_args=args)
                tool_results.append((name, result.model_dump(mode="json")))
                assert result.ok and result.value.ok, result
                return result
            if stage == "declared":
                invoke("create_restructure_decl", dict(node_path="Main.A", name="claim", lean_name="claim", kind="theorem", summary="claim"))
                invoke("set_restructure_decl", dict(node_path='Main.A', name='claim', statement={"nl": {"text": 'True', "origins": [{'source_refs': [{'corpus': 'tex', 'path': 'paper.tex'}]}]}}))
            else:
                source = service.content.read_decl_file("repo", "Main.A", "claim")
                invoke("edit_restructure_decl_file", dict(node_path="Main.A", name="claim", content=source.replace("sorry", "trivial"), stage=stage))
                invoke("set_restructure_decl", dict(node_path='Main.A', name='claim', proof={"nl": {"text": 'True.intro', "origins": []}}))
            invoke("submit_restructure_content", dict(node_path="Main.A", stage=stage, outcome=stage), submit=True)
            return original_wait(agent_id, **kwargs)
        fake.wait_agent = perform_tools
        with patch("lean_constellation.services.restructure.checks.subprocess.run", return_value=SimpleNamespace(returncode=0)):
            runtime.ark.step_service.run_step(sid)
        step = runtime.ark.flow_service.get_step(sid)
        assert step.status.value == "completed", (step.error, tool_results)
        runtime.ark.flow_service.handle_step_terminal(sid)
        assert runtime.ark.flow_service.get_flow(fid).result.outcome == stage
        # Consume without automatic build dispatch; this test isolates Agent + ToolFacade.
        run, version = service.store.load_run()
        binding = run.bindings[f"repo:Main.A:{stage}"]
        binding.terminal_consumed = True
        binding.terminal_outcome = stage
        service.store.save_run(run, expected_version=version)
    assert len(fake.agents) == 2
    assert {agent.agent_type for agent in fake.agents.values()} == {"RestructureContentPlanAgent", "RestructureContentImplementationAgent"}


def test_proof_failure_does_not_close_declared_barrier_and_retry_keeps_it(tmp_path):
    from lean_constellation.domain.restructure import TaskBinding
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A", "Main.B"]})
    for node in ["Main.A", "Main.B"]:
        _set_stage(service, "repo", node, RestructureStage.DECLARED)
    run, version = service.store.load_run()
    run.bindings["build:repo:declared"] = TaskBinding(request_id="barrier",run_id=run.run_id,repo_key="repo",
        stage=RestructureStage.DECLARED,terminal_consumed=True,terminal_outcome="succeeded")
    run.bindings["repo:Main.A:declared"] = TaskBinding(request_id="declared",run_id=run.run_id,repo_key="repo",node_path="Main.A",
        stage=RestructureStage.DECLARED,terminal_consumed=True,terminal_outcome="declared")
    service.store.save_run(run, expected_version=version)
    work, v = service.content.load("repo", "Main.A")
    work.declared_baseline = {"test": "fixture"}
    service.store.save_content("repo", "Main.A", work, expected_version=v)
    fs = _FakeFlowService()
    runtime = _runtime(fs)
    supervisor = RestructureSupervisor(service)
    ids = supervisor.start_workspace_frontier(runtime,stage=RestructureStage.PROVED)
    fs.flows[ids[0]].status = FlowStatus.FAILED
    fs.flows[ids[0]].error = SimpleNamespace(message="proof failed")
    supervisor.reconcile(runtime)
    assert "Main.B" in supervisor.frontier("repo",RestructureStage.PROVED)
    run, version = service.store.load_run()
    run.bindings["build:repo:final"] = TaskBinding(request_id="old-final", run_id=run.run_id,
        repo_key="repo", stage=RestructureStage.FINAL, flow_type="restructure_build",
        terminal_consumed=True, terminal_outcome="succeeded")
    service.store.save_run(run, expected_version=version)
    supervisor.retry_content("repo", "Main.A")
    assert not service.store.load_run()[0].bindings["repo:Main.A:declared"].stale
    assert service.store.load_run()[0].bindings["build:repo:final"].stale
    assert not service.store.load_run()[0].bindings["build:repo:declared"].stale
    assert len(supervisor.start_workspace_frontier(runtime,stage=RestructureStage.PROVED)) == 1


def test_interface_reopen_invalidates_only_dependent_closure(tmp_path):
    service, plan = _workspace(tmp_path,node_paths={"repo":["Main.A","Main.B","Main.C"]})
    plan.repos["repo"].plan.nodes["Main.B"].dependencies = ["Main.A"]
    service.store.save_workspace_plan(plan)
    for node in ["Main.A","Main.B","Main.C"]:
        _set_stage(service,"repo",node,RestructureStage.PROVED)
    supervisor=RestructureSupervisor(service)
    supervisor.retry_content("repo","Main.A",reopen_declared=True)
    assert service.content.load("repo","Main.A")[0].stage is RestructureStage.PLAN
    assert service.content.load("repo","Main.B")[0].stage is RestructureStage.PLAN
    assert service.content.load("repo","Main.C")[0].stage is RestructureStage.PROVED


def test_concurrent_supervisors_share_workspace_budget(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from lean_constellation.services.restructure import RestructureService
    service, _ = _workspace(tmp_path,node_paths={"repo":[f"Main.N{i}" for i in range(12)]})
    fs = _FakeFlowService()
    supervisors = [RestructureSupervisor(RestructureService(tmp_path)) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        counts = list(pool.map(lambda sup: len(sup.start_workspace_frontier(_runtime(fs),stage=RestructureStage.DECLARED)), supervisors))
    assert sum(counts) == 8
    assert len(fs.flows) == 8
    assert len([r for r in service.store.load_reservations() if not r.released_at]) == 8
