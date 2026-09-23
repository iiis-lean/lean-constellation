from lean_constellation.domain.restructure import SectionInput, SectionNL
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lean_constellation.domain.restructure import DeclKind, DeclStatus, Origin, SourceRef, RestructureStage, TaskBinding
from lean_constellation.app.restructure import RestructureSupervisor
from lean_constellation.services.restructure.repair import prepare_declared_repair
from tests.unit.restructure.test_supervisor import _workspace, _runtime, _FakeFlowService


def candidate(tmp_path):
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A"]})
    c = service.content
    c.create_decl("repo", "Result", "Main.A", name="value", lean_name="value", kind=DeclKind.DEF, summary="one")
    c.edit_decl_file("repo", "Main.A", "value", 'def value : Nat := "wrong"\n')
    c.set_sections('repo', 'Main.A', 'value', statement=SectionInput(nl=SectionNL(text='one', origins=[Origin(source_refs=[SourceRef(corpus='tex', path='paper.tex')])])))
    c.capture("repo", "Main.A", "value", status=DeclStatus.DECLARED)
    work = c.submit("repo", "Main.A", stage=RestructureStage.DECLARED)
    old = service.artifacts.seal_content("repo", work)
    run, version = service.store.load_run()
    run.bindings["build:repo:declared"] = TaskBinding(request_id="repair", run_id=run.run_id,
        repo_key="repo", stage=RestructureStage.DECLARED, flow_type="restructure_build")
    service.store.save_run(run, expected_version=version)
    return service, old


@pytest.mark.parametrize("success", [False, True])
def test_declared_repair_accepts_only_compiled_candidate(tmp_path, success):
    service, old = candidate(tmp_path)
    c = service.content
    text = c.read_decl_file("repo", "Main.A", "value")
    c.edit_decl_file("repo", "Main.A", "value", text.replace('"wrong"', '1'))
    c.set_sections("repo", "Main.A", "value", statement=SectionInput(nl=SectionNL(text="one")))
    c.capture("repo", "Main.A", "value")
    assert c.check_submission("repo", "Main.A", stage=RestructureStage.DECLARED)
    with patch("lean_constellation.services.restructure.build.subprocess.run", return_value=SimpleNamespace(returncode=0 if success else 1)):
        result = service.build_repo("repo", stage=RestructureStage.DECLARED, declared_repair=True, request_id="repair")
    assert result.receipt.success is success
    work, _ = c.load("repo", "Main.A")
    assert (work.declared_baseline != old.content["declared_baseline"]) is success
    assert bool(c.check_submission("repo", "Main.A", stage=RestructureStage.DECLARED)) is not success
    assert (service.store.repo_metadata_root("repo") / "artifact_files" / old.artifact_id / work.decls["value"].file).read_text() == text
    if success:
        assert work.accepted_artifact_ids
        c.edit_decl_file("repo", "Main.A", "value", text.replace('"wrong"', '2'), stage=RestructureStage.PROVED)
        c.set_sections("repo", "Main.A", "value", statement=SectionInput(nl=SectionNL(text="one")))
        c.capture("repo", "Main.A", "value")
        assert any("interface changed" in i for i in c.check_submission("repo", "Main.A", stage=RestructureStage.PROVED))


def test_repair_keeps_capture_gate(tmp_path):
    service, _ = candidate(tmp_path)
    c = service.content
    c.edit_decl_file("repo", "Main.A", "value", 'def value : Nat := 1\n')
    with patch("lean_constellation.services.restructure.build.subprocess.run") as run:
        result = service.build_repo("repo", stage=RestructureStage.DECLARED, declared_repair=True, request_id="repair")
    run.assert_not_called()
    assert any("source changed after capture" in i for i in result.receipt.diagnostics)


def test_repair_invalidates_downstream_build_and_redispatches(tmp_path):
    service, _ = _workspace(tmp_path, node_paths={"repo": ["Main.A"], "consumer": ["Main.B"]}, dependencies={"consumer": ["repo"]})
    run, version = service.store.load_run()
    for key in ["repo", "consumer"]:
        run.bindings[f"build:{key}:declared"] = TaskBinding(request_id=key, run_id=run.run_id,
            repo_key=key, stage=RestructureStage.DECLARED, flow_type="restructure_build",
            terminal_consumed=key == "consumer", terminal_outcome="succeeded" if key == "consumer" else None)
    service.store.save_run(run, expected_version=version)
    prepare_declared_repair(service, "repo", "repo")
    run, _ = service.store.load_run()
    assert run.bindings["build:consumer:declared"].stale
    supervisor = RestructureSupervisor(service)
    assert not supervisor._build_succeeded(run, "consumer", RestructureStage.DECLARED)
    supervisor.start_build(_runtime(_FakeFlowService()), repo_key="consumer", stage=RestructureStage.DECLARED, enqueue=False)
    binding = service.store.load_run()[0].bindings["build:consumer:declared"]
    assert not binding.stale
    assert binding.attempt_epoch == 1
    assert binding.request_id != "consumer"


def test_repair_rejects_active_consumers_and_proof_history(tmp_path):
    service, _ = candidate(tmp_path)
    run, version = service.store.load_run()
    run.bindings["content"] = TaskBinding(request_id="content", run_id=run.run_id, repo_key="repo",
        node_path="Main.A", stage=RestructureStage.DECLARED)
    service.store.save_run(run, expected_version=version)
    with pytest.raises(ValueError, match="terminal"):
        prepare_declared_repair(service, "repo", "repair")
    run, version = service.store.load_run()
    run.bindings["content"].terminal_consumed = True
    run.bindings["content"].stage = RestructureStage.PROVED
    service.store.save_run(run, expected_version=version)
    with pytest.raises(ValueError, match="reopen_declared"):
        prepare_declared_repair(service, "repo", "repair")


def test_real_flow_repair_tools_rebuild_and_barrier(tmp_path):
    from lean_constellation.app.runtime import create_app_runtime_services
    from lean_constellation.flows.common.testing import FakeAgentService
    from lean_constellation.services.tool_facade import RawToolCallContext, RuntimeToolContext
    service, _ = candidate(tmp_path)
    run, version = service.store.load_run()
    run.bindings.clear()
    service.store.save_run(run, expected_version=version)
    runtime = create_app_runtime_services(runtime_root=tmp_path / "ark", start_paused=False, restructure_workspace_root=tmp_path)
    fake = FakeAgentService(ark=runtime.ark, app=runtime.app)
    runtime.ark.agent_service = fake
    runtime.tool_facade.submit_submission.submission_gateway = SimpleNamespace(
        accept_step_submission=lambda ctx, submission: fake._accept_submission(fake.start_records[-1], submission))
    supervisor = RestructureSupervisor(service)
    fid = supervisor.start_build(runtime, repo_key="repo", stage=RestructureStage.DECLARED, operation_id="explicit", enqueue=False)
    fs = runtime.ark.flow_service
    sid = fs.advance_flow(fid)
    with patch("lean_constellation.services.restructure.build.subprocess.run", return_value=SimpleNamespace(returncode=1)):
        runtime.ark.step_service.run_step(sid)
    fs.handle_step_terminal(sid)
    assert fs.get_flow(fid).state.position.phase == "repair"
    sid = fs.advance_flow(fid)
    original_wait = fake.wait_agent
    def perform_tools(agent_id, **kwargs):
        def invoke(name, args, submit=False):
            view = "restructure_repo_repair_submit" if submit else "restructure_repo_repair"
            ctx = RuntimeToolContext(flow_id=fid, step_id=sid, agent_id=agent_id, scope_id=fs.get_flow(fid).scope_id,
                agent_type="RestructureCoordinatorAgent", agent_role="coordinator", expected_view_key=view,
                workspace_root=tmp_path, repo_root=tmp_path / "repo", stage="declared")
            result = runtime.tool_facade.invoke_agent_tool(RawToolCallContext(endpoint_view_key=view, runtime_context=ctx), tool_name=name, flat_args=args)
            assert result.ok and result.value.ok, result
        source = service.content.read_decl_file("repo", "Main.A", "value")
        invoke("edit_restructure_decl_file", dict(node_path="Main.A", name="value", content=source.replace('"wrong"', '1'), stage="declared"))
        invoke("set_restructure_decl", dict(node_path="Main.A", name="value", statement={"nl": {"text": "one"}}))
        invoke("check_restructure_content", dict(node_path="Main.A", stage="declared"))
        with patch.object(runtime.lean_projection.lean_check, "run_file_diagnostics", return_value=runtime.foundation.ok({})):
            invoke("run_lean_file_diagnostics", dict(file_path=service.content.load("repo", "Main.A")[0].decls["value"].file))
        invoke("submit_restructure_repo_repair", dict(outcome="repaired"), submit=True)
        return original_wait(agent_id, **kwargs)
    fake.wait_agent = perform_tools
    with patch("lean_constellation.services.restructure.checks.subprocess.run", return_value=SimpleNamespace(returncode=0)):
        runtime.ark.step_service.run_step(sid)
    assert fs.get_step(sid).status.value == "completed", fs.get_step(sid).error
    fs.handle_step_terminal(sid)
    assert fs.get_flow(fid).state.position.phase == "build"
    sid = fs.advance_flow(fid)
    with patch("lean_constellation.services.restructure.build.subprocess.run", return_value=SimpleNamespace(returncode=0)):
        runtime.ark.step_service.run_step(sid)
    fs.handle_step_terminal(sid)
    result = fs.get_flow(fid).result
    assert result.outcome == "succeeded"
    assert result.operation_id != "explicit"
    supervisor.reconcile(runtime)
    run, _ = service.store.load_run()
    assert supervisor._build_succeeded(run, "repo", RestructureStage.DECLARED)


@pytest.mark.parametrize("stage", ["declared", "proved", "final"])
def test_repair_limit_and_blocked_outcomes(tmp_path, stage):
    from lean_constellation.flows.restructure.steps import RestructureBuildStepResult, RestructureRepoRepairStepResult
    from lean_constellation.flows.common.testing import create_fake_lean_flow_runtime
    service, _ = candidate(tmp_path)
    run, version = service.store.load_run()
    run.bindings.clear()
    service.store.save_run(run, expected_version=version)
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    fid = RestructureSupervisor(service).start_build(runtime, repo_key="repo", stage=RestructureStage(stage), enqueue=False)
    flow = runtime.flow_service.get_flow(fid)
    def terminal(result):
        flow.on_step_terminal(SimpleNamespace(step=SimpleNamespace(step_id="test", error=None, result=result)))
    for _ in range(3):
        terminal(RestructureBuildStepResult(outcome="failed", operation_id="failed", diagnostics=["compiler error"]))
        assert flow.state.position.phase == "repair"
        terminal(RestructureRepoRepairStepResult(outcome="repaired"))
        assert flow.state.position.phase == "build"
    terminal(RestructureBuildStepResult(outcome="failed", operation_id="last", diagnostics=["remaining error"]))
    assert flow.result.outcome == "failed"
    assert flow.result.operation_id == "last"
    assert flow.state.repair_attempts == 3
    other = flow.model_copy(deep=True)
    other.result = None
    terminal_result = RestructureRepoRepairStepResult(outcome="blocked", issues=["requires replan"])
    other.on_step_terminal(SimpleNamespace(step=SimpleNamespace(step_id="test", error=None, result=terminal_result)))
    assert other.result.outcome == "blocked"
    assert other.result.diagnostics == ["requires replan"]


def test_final_build_seals_acceptance_and_rejects_unresolvable_origin(tmp_path):
    service, _ = candidate(tmp_path)
    content = service.content
    source = content.read_decl_file('repo', 'Main.A', 'value').replace('"wrong"', '1')
    content.edit_decl_file('repo', 'Main.A', 'value', source)
    content.set_sections('repo', 'Main.A', 'value', statement=SectionInput(nl=SectionNL(text='one')))
    content.capture('repo', 'Main.A', 'value')
    with patch('lean_constellation.services.restructure.build.subprocess.run', return_value=SimpleNamespace(returncode=0)):
        repaired = service.build_repo('repo', stage=RestructureStage.DECLARED, declared_repair=True, request_id='repair')
        assert repaired.receipt.success
        content.submit("repo", "Main.A", stage=RestructureStage.PROVED)
        final = service.build_repo('repo', stage=RestructureStage.FINAL)
        assert final.receipt.success, final.receipt.diagnostics
        acceptance = service.store.repo_metadata_root('repo') / 'accepted' / final.receipt.operation_id
        assert (acceptance / 'accepted.json').is_file()
        content.set_sections('repo', 'Main.A', 'value', statement=SectionInput(nl=SectionNL(text='one', origins=[Origin(source_refs=[SourceRef(corpus='missing', path='no.tex')])])))
        rejected = service.build_repo('repo', stage=RestructureStage.FINAL)
        assert not rejected.receipt.success
        assert 'unregistered material corpus' in rejected.receipt.diagnostics[-1]
        assert not service.store.load_build_receipt('repo', rejected.receipt.operation_id).success
