from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_runtime_kit.flow.models import BaseFlowError, FlowStatus
from pydantic import ValidationError

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.app.config import AutomaticCheckpointAppConfig
from lean_constellation.flows.common.flow_requests import build_decl_round_request, build_preparation_recon_request, build_resource_curation_request
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.decl_round.flow import DeclGraphRoundResult
from lean_constellation.flows.content_node_task.decl_round.submissions import DeclRoundDispatchSubmission
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskState
from lean_constellation.flows.content_node_task.preparation.mathlib_recon.flow import MathlibReconResult
from lean_constellation.flows.content_node_task.preparation.node_dir_recon.flow import NodeDirDependencyReconResult
from lean_constellation.flows.content_node_task.preparation.resource_recon.flow import ResourceReconResult
from lean_constellation.flows.content_node_task.submissions import (
    ContentNodeBlockedSubmission,
    ContentNodeReadySubmission,
    ContentPreparationDispatchSubmission,
    ContentResourceRequestSubmission,
)
from lean_constellation.flows.resource_request.flows import ResourceCurationResult
from lean_constellation.services.validation_snapshot.readiness_gate import ContentNodeCompletionGateView
from tests.unit_services_helpers import initialize_native_test_repo, make_runtime


class RecordingContentSnapshotRuntime:
    def __init__(self, lean_runtime) -> None:  # noqa: ANN001
        self.lean_runtime = lean_runtime
        self.calls: list[dict[str, object]] = []
        self.fail = False

    def create_repo_stable_point_snapshot(self, repo_root, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"repo_root": str(repo_root), **kwargs})
        if self.fail:
            return self.lean_runtime.foundation.fail(
                self.lean_runtime.foundation.issue("injected_snapshot_failure", "Injected snapshot failure.")
            )
        return self.lean_runtime.foundation.ok(
            SimpleNamespace(snapshot_id=f"content_snapshot_{len(self.calls)}")
        )


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, object]:
    lean_runtime = make_runtime()
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    return flow_runtime, lean_runtime


def _prepare_content_repo(lean_runtime, repo_root: Path, *, native_project_name: str | None = None) -> None:
    repo_root.mkdir(parents=True)
    if native_project_name is not None:
        initialize_native_test_repo(repo_root, project_name=native_project_name)
    assert lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    written = lean_runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Formalize a small source corpus.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            source_corpus_relpath=".lean_constellation/source",
            interface_inputs=[],
        ),
    )
    assert written.ok
    (repo_root / ".lean_constellation" / "source").mkdir(parents=True, exist_ok=True)
    assert lean_runtime.node.ensure_native_root_main_contract(repo_root).ok
    content = lean_runtime.node.create_content_node(
        repo_root,
        path="Main.Core",
        goal="Formalize core facts.",
        boundary="Core facts only.",
        objective="Prove the core facts.",
        success_criteria="The core facts are proved.",
    )
    assert content.ok


def _start_content_task(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    return _start_content_task_for_node(runtime, repo_root, "Main.Core")


def _start_content_task_for_node(runtime: FakeLeanFlowRuntime, repo_root: Path, node_path: str) -> str:
    return runtime.start_flow(
        "content_node_task",
        {
            "repo_key": repo_root.name,
            "repo_path": str(repo_root),
            "node_path": node_path,
            "contract_version": 1,
        },
        scope_id=f"repo:{repo_root.name}:node:{node_path}",
    )


def _expected_node_workdir(repo_root: Path, node_path: str = "Main.Core") -> str:
    return str(repo_root.joinpath(*node_path.split(".")))


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _complete_child_flow(runtime: FakeLeanFlowRuntime, child_flow_id: str, result) -> None:
    runtime.flow_service.store.update_flow_record(
        child_flow_id,
        lambda flow: (
            setattr(flow, "result", result),
            setattr(flow, "status", FlowStatus.COMPLETED),
            setattr(flow, "current_step_id", None),
        ),
    )


def _fail_child_flow(runtime: FakeLeanFlowRuntime, child_flow_id: str) -> None:
    runtime.flow_service.store.update_flow_record(
        child_flow_id,
        lambda flow: (
            setattr(flow, "error", BaseFlowError(error_type="injected_child_failure", message="Injected child failure.")),
            setattr(flow, "status", FlowStatus.FAILED),
            setattr(flow, "current_step_id", None),
        ),
    )


def _node_dir_submission(repo_root: Path) -> ContentPreparationDispatchSubmission:
    return ContentPreparationDispatchSubmission(
        submission_id=new_submission_id("sub"),
        submission_type="content_preparation_dispatch",
        tool_name="submit_content_preparation_recon",
        repo_key=repo_root.name,
        node_path="Main.Core",
        recon_kind="node_dir_dependency",
        objective="Check visible dependencies.",
        requests=[
            build_preparation_recon_request(
                recon_kind="node_dir_dependency",
                repo_key=repo_root.name,
                node_path="Main.Core",
                repo_path=str(repo_root),
                scope_id=f"repo:{repo_root.name}:node:Main.Core",
                objective="Check visible dependencies.",
            )
        ],
        summary="Dispatch node dependency recon.",
    )


def _preparation_submission(repo_root: Path, recon_kind: str) -> ContentPreparationDispatchSubmission:
    return ContentPreparationDispatchSubmission(
        submission_id=new_submission_id("sub"),
        submission_type="content_preparation_dispatch",
        tool_name="submit_content_preparation_recon",
        repo_key=repo_root.name,
        node_path="Main.Core",
        recon_kind=recon_kind,
        objective=f"Run {recon_kind} preparation.",
        requests=[
            build_preparation_recon_request(
                recon_kind=recon_kind,
                repo_key=repo_root.name,
                node_path="Main.Core",
                repo_path=str(repo_root),
                scope_id=f"repo:{repo_root.name}:node:Main.Core",
                objective=f"Run {recon_kind} preparation.",
            )
        ],
        summary=f"Dispatch {recon_kind} preparation.",
    )


def test_content_node_task_preparation_dispatch_callback_and_blocked_completion(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)

    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "plan_agent"

    runtime.agent_service.queue_submission(_node_dir_submission(repo_root))
    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "dispatch_child"
    assert flow.state.used_preparation_kinds == ["node_dir_dependency"]

    dispatch_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    child_flows = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)
    assert len(child_flows) == 1
    assert child_flows[0].flow_type == "node_dir_dependency_recon"

    _complete_child_flow(
        runtime,
        child_flows[0].flow_id,
        NodeDirDependencyReconResult(
            outcome="completed",
            repo_key=repo_root.name,
            node_path="Main.Core",
            dependency_change_summary="Added Main.Base.",
            checked_boundary_summary="Checked same-repo visible node boundaries.",
            useful_findings=["Main.Base"],
            unresolved_within_visible_boundaries=[],
            summary="Node deps found.",
        ),
    )
    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    assert callback_step_id is not None
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "callback_plan_agent"

    blocked_reason = """Blocked object
- Main.Core::consumer@2

Concrete gap
- Existing public theorem has a different index representation.

Consumer-side formal context
- ⊢ consumerGoal x"""
    runtime.agent_service.queue_submission(
        ContentNodeBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_blocked",
            tool_name="submit_content_node_blocked",
            repo_key=repo_root.name,
            node_path="Main.Core",
            reason=blocked_reason,
            summary=blocked_reason,
        )
    )
    runtime.run_step(callback_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "blocked"
    assert flow.result.reason == blocked_reason
    assert len(runtime.agent_service.start_records) == 2
    assert runtime.agent_service.start_records[0].agent_id == runtime.agent_service.start_records[1].agent_id
    assert runtime.agent_service.start_records[0].workdir == _expected_node_workdir(repo_root)
    assert runtime.agent_service.start_records[1].workdir == _expected_node_workdir(repo_root)
    initial_prompt = runtime.agent_service.start_records[0].prompt or ""
    callback_prompt = runtime.agent_service.start_records[1].prompt or ""
    assert "content-plan-completion-policy" in initial_prompt
    assert initial_prompt.count("Repository:") == 1
    assert "Current assignment" not in initial_prompt
    assert "Current result summary" not in initial_prompt
    assert "Current node state" in initial_prompt
    assert "content-preparation-orchestration" in callback_prompt
    assert "decl-strategy-planning" in callback_prompt
    assert "Current node state" in callback_prompt
    assert "- Work mode:" not in callback_prompt
    assert "reuse its verified findings without broad rediscovery" in (
        callback_prompt
    )
    assert "Node deps found." in callback_prompt


def test_content_node_task_plan_agent_uses_native_project_projection_workdir(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root, native_project_name="Repo")
    expected = repo_root / "Repo" / "Main" / "Core"
    assert expected.is_dir()
    expected.rmdir()
    assert not expected.exists()
    flow_id = _start_content_task(runtime, repo_root)

    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(
        ContentNodeBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_blocked",
            tool_name="submit_content_node_blocked",
            repo_key=repo_root.name,
            node_path="Main.Core",
            reason="Stop after verifying the native projection workdir.",
            summary="Native projection workdir verified.",
        )
    )
    _advance_and_run(runtime, flow_id)

    assert expected.is_dir()
    assert runtime.agent_service.start_records[-1].workdir == str(expected)


def test_content_plan_without_accepted_submission_fails_owning_flow(
    tmp_path: Path,
) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)
    for _ in range(3):
        runtime.agent_service.queue_incomplete_turn()

    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.FAILED
    assert flow.result is None
    assert flow.error.error_type == "content_plan_agent_incomplete"
    assert flow.state.position.phase == "plan_agent"


def test_content_node_task_rejects_duplicate_preparation_dispatch(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)

    runtime.agent_service.queue_submission(_node_dir_submission(repo_root))
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    child_flow = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)[0]
    _complete_child_flow(
        runtime,
        child_flow.flow_id,
        NodeDirDependencyReconResult(outcome="completed", repo_key=repo_root.name, node_path="Main.Core", summary="Done."),
    )
    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    assert callback_step_id is not None

    runtime.agent_service.queue_submission(_node_dir_submission(repo_root))
    runtime.run_step(callback_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "failed"
    assert "already been used" in flow.result.reason


def test_content_node_task_reuses_content_plan_agent_from_prior_same_node_task(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)

    first_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, first_flow_id)
    assert runtime.flow_service.get_flow(first_flow_id).state.position.phase == "plan_agent"
    runtime.agent_service.queue_submission(
        ContentNodeBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_blocked",
            tool_name="submit_content_node_blocked",
            repo_key=repo_root.name,
            node_path="Main.Core",
            reason="Stop first task.",
            summary="Stop first task.",
        )
    )
    _advance_and_run(runtime, first_flow_id)
    first_agent_id = runtime.agent_service.start_records[-1].agent_id
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(repo_root)
    assert runtime.flow_service.get_flow(first_flow_id).status is FlowStatus.COMPLETED

    second_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, second_flow_id)
    assert runtime.flow_service.get_flow(second_flow_id).state.position.phase == "plan_agent"
    runtime.agent_service.queue_submission(
        ContentNodeBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_blocked",
            tool_name="submit_content_node_blocked",
            repo_key=repo_root.name,
            node_path="Main.Core",
            reason="Stop second task.",
            summary="Stop second task.",
        )
    )
    _advance_and_run(runtime, second_flow_id)
    second_agent_id = runtime.agent_service.start_records[-1].agent_id
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(repo_root)

    assert second_agent_id == first_agent_id
    second_flow = runtime.flow_service.get_flow(second_flow_id)
    assert second_flow.agent_bindings.get("content_plan") == first_agent_id


def test_content_node_task_skips_missing_prior_content_plan_binding(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)

    prior_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, prior_flow_id)
    runtime.flow_service.store.update_flow_record(
        prior_flow_id,
        lambda flow: flow.agent_bindings.by_role.__setitem__("content_plan", "missing_agent"),
    )

    current_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, current_flow_id)
    _advance_and_run(runtime, current_flow_id)
    current_agent_id = runtime.agent_service.start_records[-1].agent_id

    assert current_agent_id != "missing_agent"
    assert runtime.flow_service.get_flow(current_flow_id).agent_bindings.get("content_plan") == current_agent_id


def test_content_node_task_skips_prior_content_plan_binding_with_wrong_agent_type(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    wrong_agent = runtime.agent_service.create_agent(
        f"repo:{repo_root.name}:node:Main.Core",
        "MathlibReconAgent",
        home_id="MathlibReconAgent",
    )

    prior_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, prior_flow_id)
    runtime.flow_service.store.update_flow_record(
        prior_flow_id,
        lambda flow: flow.agent_bindings.by_role.__setitem__("content_plan", wrong_agent.agent_id),
    )

    current_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, current_flow_id)
    _advance_and_run(runtime, current_flow_id)
    current_agent_id = runtime.agent_service.start_records[-1].agent_id

    assert current_agent_id != wrong_agent.agent_id
    assert runtime.flow_service.get_flow(current_flow_id).agent_bindings.get("content_plan") == current_agent_id


def test_content_node_task_skips_prior_content_plan_binding_with_wrong_scope(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    wrong_scope_agent = runtime.agent_service.create_agent(
        f"repo:{repo_root.name}:node:Main.Other",
        "ContentPlanAgent",
        home_id="ContentPlanAgent",
    )

    prior_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, prior_flow_id)
    runtime.flow_service.store.update_flow_record(
        prior_flow_id,
        lambda flow: flow.agent_bindings.by_role.__setitem__("content_plan", wrong_scope_agent.agent_id),
    )

    current_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, current_flow_id)
    _advance_and_run(runtime, current_flow_id)
    current_agent_id = runtime.agent_service.start_records[-1].agent_id

    assert current_agent_id != wrong_scope_agent.agent_id
    assert runtime.flow_service.get_flow(current_flow_id).agent_bindings.get("content_plan") == current_agent_id


def test_content_node_task_does_not_inherit_content_plan_agent_from_different_node_scope(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    created_other = lean_runtime.node.create_content_node(
        repo_root,
        path="Main.Other",
        goal="Formalize other facts.",
        boundary="Other facts only.",
        objective="Prove the other facts.",
        success_criteria="The other facts are proved.",
    )
    assert created_other.ok

    first_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, first_flow_id)
    runtime.agent_service.queue_submission(
        ContentNodeBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_blocked",
            tool_name="submit_content_node_blocked",
            repo_key=repo_root.name,
            node_path="Main.Core",
            reason="Stop first task.",
            summary="Stop first task.",
        )
    )
    _advance_and_run(runtime, first_flow_id)
    first_agent_id = runtime.agent_service.start_records[-1].agent_id
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(repo_root)

    other_flow_id = _start_content_task_for_node(runtime, repo_root, "Main.Other")
    _advance_and_run(runtime, other_flow_id)
    runtime.agent_service.queue_submission(
        ContentNodeBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_blocked",
            tool_name="submit_content_node_blocked",
            repo_key=repo_root.name,
            node_path="Main.Other",
            reason="Stop other task.",
            summary="Stop other task.",
        )
    )
    _advance_and_run(runtime, other_flow_id)
    other_agent_id = runtime.agent_service.start_records[-1].agent_id
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(repo_root, "Main.Other")

    assert other_agent_id != first_agent_id


def test_content_node_task_existing_content_plan_binding_is_not_overwritten(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)

    prior_flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, prior_flow_id)
    runtime.agent_service.queue_submission(
        ContentNodeBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_blocked",
            tool_name="submit_content_node_blocked",
            repo_key=repo_root.name,
            node_path="Main.Core",
            reason="Stop prior task.",
            summary="Stop prior task.",
        )
    )
    _advance_and_run(runtime, prior_flow_id)

    current_flow_id = _start_content_task(runtime, repo_root)
    runtime.flow_service.store.update_flow_record(
        current_flow_id,
        lambda flow: flow.agent_bindings.by_role.__setitem__("content_plan", "agent_explicit"),
    )
    _advance_and_run(runtime, current_flow_id)
    step_id = runtime.flow_service.advance_flow(current_flow_id)
    assert step_id is not None

    current_flow = runtime.flow_service.get_flow(current_flow_id)
    assert current_flow.agent_bindings.get("content_plan") == "agent_explicit"


def test_content_node_task_decl_round_dispatch_ensures_stage_agents(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)

    runtime.agent_service.queue_submission(
        DeclRoundDispatchSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="decl_round_dispatch",
            tool_name="submit_current_decl_round",
            repo_key=repo_root.name,
            node_path="Main.Core",
            strategy_id="strategy_1",
            round_id="round_1",
            round_index=1,
            requests=[
                build_decl_round_request(
                    repo_key=repo_root.name,
                    node_path="Main.Core",
                    scope_id=f"repo:{repo_root.name}:node:Main.Core",
                    strategy_id="strategy_1",
                    round_id="round_1",
                    round_index=1,
                )
            ],
            summary="Dispatch decl round.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "ensure_stage_agents"

    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.stage_agent_bindings_initialized is True
    assert flow.state.position.phase == "dispatch_child"

    dispatch_step_id = _advance_and_run(runtime, flow_id)
    child_flow = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)[0]
    assert child_flow.flow_type == "decl_graph_round"
    _complete_child_flow(
        runtime,
        child_flow.flow_id,
        DeclGraphRoundResult(
            outcome="completed",
            repo_key=repo_root.name,
            node_path="Main.Core",
            round_id="round_1",
            completed_stages=["statement_nl"],
            summary="Round completed.",
        ),
    )
    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    runtime.agent_service.queue_submission(
        ContentNodeReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_ready",
            tool_name="submit_content_node_ready",
            repo_key=repo_root.name,
            node_path="Main.Core",
            summary="Content ready.",
        )
    )
    runtime.run_step(callback_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "completion_audit"

    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.RUNNING
    assert flow.state.position.phase == "callback_plan_agent"
    assert flow.result is None
    audit_steps = runtime.flow_service.store.list_steps(
        flow_id=flow_id,
        step_type="content_completion_audit_step",
    )
    assert len(audit_steps) == 1
    assert audit_steps[0].result.outcome == "failed"
    callback_prompt = runtime.agent_service.start_records[-1].prompt or ""
    assert callback_prompt.index("decl-round-closeout") < callback_prompt.index(
        "content-plan-completion-policy"
    )
    assert "decl-strategy-planning" in callback_prompt
    assert "reassess whether the strategy still explains the next round" in callback_prompt


def test_content_ready_intent_completes_only_after_deterministic_audit(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)

    def passed_completion(repo_root_arg: Path, *, node_path: str):
        del repo_root_arg
        gate = lean_runtime.foundation.gate_passed(
            "content_node_completion",
            summary="Deterministic completion audit passed.",
        )
        return lean_runtime.foundation.ok(
            ContentNodeCompletionGateView(
                node_path=node_path,
                target_proof_availability=ProofAvailability.PROVED,
                gate=gate,
                ready_to_submit=True,
                summary="Deterministic completion audit passed.",
            )
        )

    lean_runtime.validation_snapshot.check_content_node_completion = passed_completion
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(
        ContentNodeReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_ready",
            tool_name="submit_content_node_ready",
            repo_key=repo_root.name,
            node_path="Main.Core",
            summary="Content ready intent.",
        )
    )

    _advance_and_run(runtime, flow_id)
    pending = runtime.flow_service.get_flow(flow_id)
    assert pending.status is FlowStatus.RUNNING
    assert pending.state.position.phase == "completion_audit"

    _advance_and_run(runtime, flow_id)
    completed = runtime.flow_service.get_flow(flow_id)
    assert completed.status is FlowStatus.COMPLETED
    assert completed.result.outcome == "ready"
    assert completed.result.summary == "Content ready intent."


def test_content_progress_checkpoints_run_before_callback_and_narrow_later_scope(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    lean_runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(
        content_task_progress_enabled=True,
    )
    snapshots = RecordingContentSnapshotRuntime(lean_runtime)
    lean_runtime.app.snapshot_runtime = snapshots
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)

    runtime.agent_service.queue_submission(_node_dir_submission(repo_root))
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=dispatch_step_id,
    )[0]
    _complete_child_flow(
        runtime,
        child.flow_id,
        NodeDirDependencyReconResult(
            outcome="completed",
            repo_key=repo_root.name,
            node_path="Main.Core",
            summary="Preparation complete.",
        ),
    )

    preparation_checkpoint_step_id = runtime.flow_service.advance_flow(flow_id)
    assert preparation_checkpoint_step_id is not None
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "after_child_terminal_checkpoint"
    assert flow.state.completed_child_flow_id == child.flow_id
    runtime.run_step(preparation_checkpoint_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "callback_plan_agent"
    assert flow.state.progress_checkpoint_repo_scope_captured is True
    assert snapshots.calls[0]["checkpoint_kind"] == "after_content_preparation_terminal"
    assert snapshots.calls[0]["scope_ids"] == ["repo:Repo", "repo:Repo:node:Main.Core"]

    runtime.agent_service.queue_submission(
        DeclRoundDispatchSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="decl_round_dispatch",
            tool_name="submit_current_decl_round",
            repo_key=repo_root.name,
            node_path="Main.Core",
            strategy_id="strategy_1",
            round_id="round_1",
            round_index=1,
            requests=[
                build_decl_round_request(
                    repo_key=repo_root.name,
                    node_path="Main.Core",
                    scope_id="repo:Repo:node:Main.Core",
                    strategy_id="strategy_1",
                    round_id="round_1",
                    round_index=1,
                )
            ],
            summary="Dispatch decl round.",
        )
    )
    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    assert callback_step_id is not None
    runtime.run_step(callback_step_id)
    _advance_and_run(runtime, flow_id)
    round_dispatch_step_id = _advance_and_run(runtime, flow_id)
    round_child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=round_dispatch_step_id,
    )[0]
    _complete_child_flow(
        runtime,
        round_child.flow_id,
        DeclGraphRoundResult(
            outcome="completed",
            repo_key=repo_root.name,
            node_path="Main.Core",
            round_id="round_1",
            round_index=7,
            completed_stages=["statement_nl"],
            summary="Round complete.",
        ),
    )

    round_checkpoint_step_id = runtime.flow_service.advance_flow(flow_id)
    assert round_checkpoint_step_id is not None
    runtime.run_step(round_checkpoint_step_id)
    assert snapshots.calls[1]["checkpoint_kind"] == "after_content_decl_round_terminal"
    assert snapshots.calls[1]["scope_ids"] == ["repo:Repo:node:Main.Core"]
    assert "round_id=round_1" in snapshots.calls[1]["label"]
    assert "round_index=7" in snapshots.calls[1]["label"]
    assert "task_round_count=1" in snapshots.calls[1]["label"]
    assert " round=1" not in snapshots.calls[1]["label"]
    round_step = runtime.flow_service.get_step(round_checkpoint_step_id)
    assert round_step.result.decl_round_count == 1
    assert round_step.result.child_outcome == "completed"
    assert round_step.result.snapshot_id == "content_snapshot_2"


@pytest.mark.parametrize("recon_kind", ["node_dir_dependency", "mathlib", "resource"])
def test_each_preparation_kind_creates_terminal_progress_checkpoint(tmp_path: Path, recon_kind: str) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    lean_runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(
        content_task_progress_enabled=True,
    )
    snapshots = RecordingContentSnapshotRuntime(lean_runtime)
    lean_runtime.app.snapshot_runtime = snapshots
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(_preparation_submission(repo_root, recon_kind))
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=dispatch_step_id,
    )[0]
    if recon_kind == "node_dir_dependency":
        result = NodeDirDependencyReconResult(
            outcome="completed", repo_key="Repo", node_path="Main.Core", summary="Preparation complete."
        )
    elif recon_kind == "mathlib":
        result = MathlibReconResult(
            outcome="completed", repo_key="Repo", node_path="Main.Core", summary="Preparation complete."
        )
    else:
        result = ResourceReconResult(
            outcome="completed", repo_key="Repo", node_path="Main.Core", summary="Preparation complete."
        )
    _complete_child_flow(runtime, child.flow_id, result)

    checkpoint_step_id = runtime.flow_service.advance_flow(flow_id)
    assert checkpoint_step_id is not None
    runtime.run_step(checkpoint_step_id)

    assert snapshots.calls[0]["checkpoint_kind"] == "after_content_preparation_terminal"
    assert f"kind={recon_kind}" in str(snapshots.calls[0]["label"])
    assert f"child={child.flow_id}" in str(snapshots.calls[0]["label"])
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "callback_plan_agent"


def test_failed_preparation_child_outcome_is_recorded_in_progress_checkpoint(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    lean_runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(
        content_task_progress_enabled=True,
    )
    snapshots = RecordingContentSnapshotRuntime(lean_runtime)
    lean_runtime.app.snapshot_runtime = snapshots
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(_node_dir_submission(repo_root))
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=dispatch_step_id,
    )[0]
    _fail_child_flow(runtime, child.flow_id)

    checkpoint_step_id = runtime.flow_service.advance_flow(flow_id)
    assert checkpoint_step_id is not None
    runtime.run_step(checkpoint_step_id)

    step = runtime.flow_service.get_step(checkpoint_step_id)
    assert step.result.child_outcome == "failed"
    assert "outcome=failed" in str(snapshots.calls[0]["label"])


def test_content_task_state_rejects_missing_progress_checkpoint_fields() -> None:
    with pytest.raises(ValidationError):
        ContentNodeTaskState.model_validate(
            {
                "state_type": "content_node_task",
                "position": {"phase": "waiting_child", "round_index": 0},
                "waiting_child_kind": "node_dir_dependency",
            }
        )


def test_content_progress_snapshot_failure_fails_owning_flow(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    lean_runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(
        content_task_progress_enabled=True,
    )
    snapshots = RecordingContentSnapshotRuntime(lean_runtime)
    snapshots.fail = True
    lean_runtime.app.snapshot_runtime = snapshots
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(_node_dir_submission(repo_root))
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=dispatch_step_id,
    )[0]
    _complete_child_flow(
        runtime,
        child.flow_id,
        NodeDirDependencyReconResult(outcome="completed", repo_key="Repo", node_path="Main.Core", summary="Done."),
    )

    checkpoint_step_id = runtime.flow_service.advance_flow(flow_id)
    assert checkpoint_step_id is not None
    runtime.run_step(checkpoint_step_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.FAILED
    assert flow.error.error_type == "content_progress_stable_snapshot_failed"


def test_content_progress_enabled_skips_when_task_parallelism_is_not_one(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    lean_runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(
        content_task_progress_enabled=True,
    )
    snapshots = RecordingContentSnapshotRuntime(lean_runtime)
    lean_runtime.app.snapshot_runtime = snapshots
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = runtime.start_flow(
        "content_node_task",
        {
            "repo_key": repo_root.name,
            "repo_path": str(repo_root),
            "node_path": "Main.Core",
            "contract_version": 1,
            "max_parallel_content_node_tasks": 2,
        },
        scope_id="repo:Repo:node:Main.Core",
    )
    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(_node_dir_submission(repo_root))
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=dispatch_step_id,
    )[0]
    _complete_child_flow(
        runtime,
        child.flow_id,
        NodeDirDependencyReconResult(outcome="completed", repo_key="Repo", node_path="Main.Core", summary="Done."),
    )

    callback_step_id = runtime.flow_service.advance_flow(flow_id)

    assert callback_step_id is not None
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "callback_plan_agent"
    assert "max_parallel_content_node_tasks=2" in flow.state.latest_callback_summary
    assert snapshots.calls == []


def test_direct_resource_curation_does_not_create_preparation_checkpoint(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    lean_runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(
        content_task_progress_enabled=True,
    )
    snapshots = RecordingContentSnapshotRuntime(lean_runtime)
    lean_runtime.app.snapshot_runtime = snapshots
    repo_root = tmp_path / "workspace" / "Repo"
    _prepare_content_repo(lean_runtime, repo_root)
    flow_id = _start_content_task(runtime, repo_root)
    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(
        ContentResourceRequestSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_resource_request",
            tool_name="submit_resource_request",
            repo_key="Repo",
            node_path="Main.Core",
            target_kind="local_file",
            target="notes.md",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo:node:Main.Core",
                    target_kind="local_file",
                    target="notes.md",
                    repo_key="Repo",
                    repo_root=str(repo_root),
                    node_path="Main.Core",
                )
            ],
            summary="Curate notes.",
        )
    )
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=dispatch_step_id,
    )[0]
    _complete_child_flow(
        runtime,
        child.flow_id,
        ResourceCurationResult(
            outcome="rejected",
            repo_key="Repo",
            target_summary="local_file:notes.md",
            summary="Resource rejected.",
        ),
    )

    callback_step_id = runtime.flow_service.advance_flow(flow_id)

    assert callback_step_id is not None
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "callback_plan_agent"
    assert snapshots.calls == []
