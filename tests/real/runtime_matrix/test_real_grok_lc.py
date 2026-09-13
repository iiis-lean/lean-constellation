"""Real Grok on LC application and submission paths, in isolated fixtures."""
import os
from pathlib import Path

import pytest
from agent_runtime_kit.agent.providers import build_grok_provider_bundle, GrokHomeOptions
from agent_runtime_kit.agent.provider_contracts import ArtifactCaptureRequest, ArtifactRestoreRequest
from agent_runtime_kit.flow.models import FlowStatus, StepStatus
from lean_constellation.app import AdminStepStartInput, SetAgentStepOverrideInput, materialize_agent_home
from lean_constellation.flows.testing import ControlledAgentOverrideSpec
from tests.real.runtime_matrix.fixtures import create_runtime_matrix_workspace
from tests.real.runtime_matrix.admin_helpers import unwrap, run_scripted_flow_until, run_next_created_step
from tests.real.runtime_matrix.strict.real_codex_helpers import strict_controlled_agent_specs
from tests.real.runtime_matrix.strict.test_real_codex_agent_resource_matrix import _start_coordinator
from tests.real.runtime_matrix.transport import start_runtime_mcp_http_server
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig
from lean_constellation.services.decl_graph import DeclState
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.admin_helpers import run_until_step_created
from tests.real.runtime_matrix.strict.test_real_codex_agent_resource_matrix import (
    _start_decl_round, _complete_statement_nl_stage_for_real_codex,
)

pytestmark = [pytest.mark.real, pytest.mark.skipif(os.environ.get("ARK_RUN_REAL_GROK") != "1", reason="Explicit real Grok opt-in required")]


@pytest.mark.parametrize("maintenance", [False, True], ids=["fresh", "maintained"])
def test_real_grok_coordinator_application_and_submit(tmp_path: Path, monkeypatch, maintenance: bool) -> None:
    ws = create_runtime_matrix_workspace(tmp_path)
    prepare_index = ws._prepare_minimal_source_index

    def prepare_current_source_index(*, path: str) -> None:
        prepared = ws.runtime.material.submit_source_corpus_prepared(ws.provider_repo,
            entry_path="README.md", overview="Real Grok LC fixture", preparation_summary="Local fixture source.")
        assert prepared.ok, prepared.issues
        prepare_index(path=path)

    monkeypatch.setattr(ws, "_prepare_minimal_source_index", prepare_current_source_index)
    ws.prepare_provider_ready_repo_for_coordinator_smoke()
    service = ws.runtime.ark.agent_service
    bundle = build_grok_provider_bundle(runtime_root=ws.runtime_root)
    service.provider_registry.register(bundle)
    service.home_service.renderers["grok"] = bundle.home_renderer
    specs = [s.model_copy(update={"home_type": "grok"}) for s in strict_controlled_agent_specs("CoordinatorAgent")]
    name = "CoordinatorControlledTestAgent"
    server = start_runtime_mcp_http_server(ws.runtime)
    try:
        for agent_type in ("CoordinatorAgent", name):
            result = materialize_agent_home(ws.runtime, agent_type, provider_type="grok", agent_type_specs=specs,
                provider_options=GrokHomeOptions(reasoning_effort="low"), mcp_http_base_url=server.base_url)
            assert result.ok, result.issues
        unwrap(ws.admin.resume_runtime())
        flow_id = _start_coordinator(ws)
        run_scripted_flow_until(ws.runtime, flow_id, lambda f: f.status is FlowStatus.COMPLETED,
            stop_before_step_type="coordinator_agent_step", limit=20)
        step_id = next(s.step_id for s in ws.runtime.ark.flow_service.list_steps(flow_id=flow_id,
            step_type="coordinator_agent_step") if s.status is StepStatus.CREATED)
        strategy = "fresh_test_agent_type"
        if maintenance:
            agent = service.create_agent("repo:Provider", name, provider_type="grok", home_id=name)
            service.start_agent(agent.agent_id, workdir=str(ws.provider_repo), prompt="Remember code LC_MAINTAIN_3912. Reply SAVED only; do not call tools.")
            first = service.wait_agent(agent.agent_id, timeout_s=240).provider_result
            assert first.status.value == "completed", first
            snapshot = bundle.artifacts.capture(ArtifactCaptureRequest(session=first.session_locator, snapshot_root=str(tmp_path / "native-snapshot")))
            compacted = service.compact_agent(agent.agent_id, workdir=str(ws.provider_repo), timeout_s=180)
            assert compacted.status.value == "compacted", compacted
            unwrap(ws.admin.pause_runtime())
            child = service.fork_agent_for_recovery(agent.agent_id)
            unwrap(ws.admin.resume_runtime())
            # The child must remain runnable after compaction; the parent restores independently.
            service.start_agent(child.agent_id, workdir=str(ws.provider_repo), prompt="What code did I ask you to remember? Reply only that code, no tools.")
            child_result = service.wait_agent(child.agent_id, timeout_s=240).provider_result
            assert child_result.status.value == "completed" and "LC_MAINTAIN_3912" in child_result.final_text
            bundle.artifacts.restore(ArtifactRestoreRequest(manifest=snapshot.manifest, snapshot_root=snapshot.snapshot_root))
            service.start_agent(agent.agent_id, workdir=str(ws.provider_repo), prompt="What code did I ask you to remember? Reply only that code, no tools.")
            restored = service.wait_agent(agent.agent_id, timeout_s=240).provider_result
            assert restored.status.value == "completed" and "LC_MAINTAIN_3912" in restored.final_text
            pending = ws.runtime.ark.flow_service.get_step(step_id)
            pending.agent_bindings.by_role[pending.state.agent_role] = child.agent_id
            ws.runtime.ark.flow_service.store.update_step(pending)
            strategy = "reuse_bound_agent"
        unwrap(ws.admin.set_agent_step_override(SetAgentStepOverrideInput(step_id=step_id,
            override=ControlledAgentOverrideSpec(strategy=strategy, agent_type_override=name,
                provider_type_override="grok", prompt_overlay=(
                    "This is a controlled LC integration fixture, not autonomous planning. "
                    "Read the coordinator-content-result-closeout Skill from your available skills. "
                    "Use application MCP tools inspect_workspace_for_coordinator and get_node_tree. "
                    "Then call submit_repo_ready with summary 'Real Grok LC integration fixture ready'. "
                    "Use search_tool/use_tool for MCP discovery and invocation. Do not edit files. "
                    "End your response after the accepted submission."
                )))))
        started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=240)))
        assert started.status == "completed", started
        step = ws.runtime.ark.flow_service.get_step(step_id)
        assert step.submission is not None and step.submission.tool_name == "submit_repo_ready"
        run_next_created_step(ws.admin, flow_id, timeout_s=30)
        flow = ws.runtime.ark.flow_service.get_flow(flow_id)
        assert flow.status is FlowStatus.COMPLETED
        assert flow.result.outcome == "candidate_prepared"
    finally:
        server.close()


def test_real_grok_statement_formal_worker(tmp_path: Path) -> None:
    ws = create_runtime_matrix_workspace(tmp_path, lake_client=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)))
    build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert build.ok, build
    fixture = ws.create_decl_round(target_state=DeclState.PROVED)
    ws.create_homes("StatementNLWorkerControlledTestAgent", "StatementNLReviewerControlledTestAgent")
    service = ws.runtime.ark.agent_service
    bundle = build_grok_provider_bundle(runtime_root=ws.runtime_root)
    service.provider_registry.register(bundle)
    service.home_service.renderers["grok"] = bundle.home_renderer
    specs = [s.model_copy(update={"home_type": "grok"}) for s in strict_controlled_agent_specs("StatementFormalWorkerAgent")]
    name = "StatementFormalWorkerControlledTestAgent"
    server = start_runtime_mcp_http_server(ws.runtime)
    try:
        result = materialize_agent_home(ws.runtime, name, provider_type="grok", agent_type_specs=specs,
            provider_options=GrokHomeOptions(reasoning_effort="low"), fixed_env={"ELAN_HOME": "/root/.elan"},
            mcp_http_base_url=server.base_url)
        assert result.ok, result.issues
        flow_id = _start_decl_round(ws, fixture)
        _complete_statement_nl_stage_for_real_codex(ws, flow_id, fixture, EvidenceRecorder())
        run_next_created_step(ws.admin, flow_id, timeout_s=20)
        run_next_created_step(ws.admin, flow_id, timeout_s=20)
        step_id = run_until_step_created(ws.admin, flow_id, "decl_stage_worker_agent_step", max_advances=5)
        unwrap(ws.admin.set_agent_step_override(SetAgentStepOverrideInput(step_id=step_id,
            override=ControlledAgentOverrideSpec(strategy="fresh_test_agent_type", agent_type_override=name,
                provider_type_override="grok", prompt_overlay=(
                    f"Controlled LC formalization fixture. Declaration {fixture.decl_name} states True. "
                    "Read lean-statement-formalization skill. Call prepare_statement_formal_file for this decl. "
                    "Read the returned file and replace its sorry proof with trivial using file tools. "
                    "Call scan_lean_sorry_axiom, capture_statement_formal_file and check_formal_stage_consistency "
                    "with stage statement. Only after the real Lean check and consistency pass, "
                    "call submit_stage_worker_completed. Use MCP tools, not direct JSON truth edits."
                )))))
        started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=300)))
        assert started.status == "completed", started
        step = ws.runtime.ark.flow_service.get_step(step_id)
        assert step.submission is not None and step.submission.tool_name == "submit_stage_worker_completed"
        revision = ws.runtime.decl_graph.get_decl_revision(ws.provider_repo, node_path=fixture.node_path, name=fixture.decl_name, revision=1)
        assert revision.ok, revision.issues
        assert revision.value.statement.formal.check.status == "passed"
        flow = ws.runtime.ark.flow_service.get_flow(flow_id)
        assert flow.status is not FlowStatus.FAILED
        assert flow.state.position.phase == "stage_reviewer"
    finally:
        server.close()
