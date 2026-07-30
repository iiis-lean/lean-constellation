from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.flows.common.agent_steps import (
    ContentPlanAgentStep,
    DeclStageReviewerAgentStep,
    DeclStageWorkerAgentStep,
    MathlibReconAgentStep,
    NodeDirDependencyReconAgentStep,
    ResourceReconAgentStep,
    _content_plan_callback_guidance,
)
from lean_constellation.flows.common.flow_requests import (
    build_decl_round_request,
    build_preparation_recon_request,
    build_resource_curation_request,
)
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.decl_round.submissions import DeclRoundDispatchSubmission
from lean_constellation.flows.content_node_task.decl_round.steps import (
    DeclStageReviewerStepState,
    DeclStageWorkerStepState,
)
from lean_constellation.flows.content_node_task.preparation.mathlib_recon.submissions import MathlibReconCompletedSubmission
from lean_constellation.flows.content_node_task.preparation.node_dir_recon.submissions import NodeDirDependencyReconCompletedSubmission
from lean_constellation.flows.content_node_task.preparation.resource_recon.submissions import (
    ResourceReconBlockedSubmission,
    ResourceReconCompletedSubmission,
    ResourceReconRequestResourceSubmission,
)
from lean_constellation.flows.content_node_task.steps import (
    ContentPlanStepResult,
    MathlibReconStepResult,
    NodeDirDependencyReconStepResult,
    ResourceReconStepResult,
)
from lean_constellation.flows.content_node_task.submissions import (
    ContentNodeBlockedSubmission,
    ContentNodeReadySubmission,
    ContentPreparationDispatchSubmission,
    ContentResourceRequestSubmission,
)


def test_decl_round_callback_uses_authoritative_revision_refs_when_terminal_reason_is_empty() -> None:
    round_record = SimpleNamespace(
        revision_refs=[
            SimpleNamespace(decl_name="first_decl"),
            SimpleNamespace(decl_name="second_decl"),
            SimpleNamespace(decl_name="first_decl"),
        ]
    )
    ctx = SimpleNamespace(
        app=SimpleNamespace(
            decl_graph=SimpleNamespace(
                get_round=lambda *_args, **_kwargs: SimpleNamespace(ok=True, value=round_record)
            )
        )
    )
    result = SimpleNamespace(
        outcome="completed",
        node_path="Main.Core",
        round_id="round_1",
        terminal_stage=None,
        terminal_reason=None,
    )
    child = SimpleNamespace(
        flow_type="decl_graph_round",
        status=None,
        result=result,
        input=SimpleNamespace(repo_path="/tmp/Repo", node_path="Main.Core", round_id="round_1"),
    )

    prompt = _content_plan_callback_guidance(
        ctx,
        [child],
        completion_policy_skill="content-plan-completion-policy",
    )

    assert "affected declarations are first_decl, second_decl" in prompt
    assert "$decl-round-closeout" in prompt
    assert "$content-plan-completion-policy" in prompt


def test_blocked_decl_round_callback_preserves_formal_mismatch_without_copying_reason() -> None:
    round_record = SimpleNamespace(revision_refs=[SimpleNamespace(decl_name="consumer_theorem")])
    ctx = SimpleNamespace(
        app=SimpleNamespace(
            decl_graph=SimpleNamespace(
                get_round=lambda *_args, **_kwargs: SimpleNamespace(ok=True, value=round_record)
            )
        )
    )
    result = SimpleNamespace(
        outcome="blocked",
        node_path="Main.Core",
        round_id="round_blocked",
        terminal_stage="proof_formal",
        terminal_reason="SENTINEL PRIVATE BLOCKER BODY",
    )
    child = SimpleNamespace(
        flow_type="decl_graph_round",
        status=None,
        result=result,
        input=SimpleNamespace(repo_path="/tmp/Repo", node_path="Main.Core", round_id="round_blocked"),
    )

    prompt = _content_plan_callback_guidance(
        ctx,
        [child],
        completion_policy_skill="content-plan-completion-policy",
    )

    assert "$decl-round-closeout" in prompt
    assert "consumer-side formal goal" in prompt
    assert "checked declarations" in prompt
    assert "concrete mismatch" in prompt
    assert "re-read every affected revision" in prompt
    assert "SENTINEL PRIVATE BLOCKER BODY" not in prompt


def _start_content_flow(runtime: FakeLeanFlowRuntime, tmp_path: Path) -> str:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir(exist_ok=True)
    return runtime.start_flow(
        "content_node_task",
        {
            "repo_key": "Repo",
            "repo_path": str(repo_root),
            "node_path": "Main.Core",
            "contract_version": 1,
        },
        scope_id="repo:Repo:node:Main.Core",
    )


def _agent_state(role: str, agent_type: str) -> AgentStepState:
    return AgentStepState(
        agent_role=role,
        agent_type=agent_type,
        create_agent_if_missing=True,
        bind_created_agent_to="step",
        max_auto_continue_turns=0,
    )


def _run_step(runtime: FakeLeanFlowRuntime, step, submission=None):
    step_id = runtime.attach_step(step)
    if submission is not None:
        runtime.agent_service.queue_submission(submission)
    else:
        runtime.agent_service.queue_incomplete_turn()
    runtime.run_step(step_id)
    return runtime.flow_service.get_step(step_id)


def test_content_plan_agent_step_dispatch_and_completion_results(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_content_flow(runtime, tmp_path)
    state = _agent_state("content_plan", "ContentPlanAgent")

    prep = _run_step(
        runtime,
        ContentPlanAgentStep(step_id="content_plan_prep", flow_id=flow_id, scope_id="repo:Repo:node:Main.Core", state=state),
        ContentPreparationDispatchSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_preparation_dispatch",
            tool_name="submit_content_preparation_recon",
            repo_key="Repo",
            node_path="Main.Core",
            recon_kind="node_dir_dependency",
            objective="Check visible dependencies.",
            requests=[
                build_preparation_recon_request(
                    recon_kind="node_dir_dependency",
                    repo_key="Repo",
                    node_path="Main.Core",
                    scope_id="repo:Repo:node:Main.Core",
                    objective="Check visible dependencies.",
                )
            ],
            summary="Dispatch node-dir recon.",
        ),
    )
    assert isinstance(prep.result, ContentPlanStepResult)
    assert prep.result.outcome == "preparation_dispatch"
    assert prep.result.preparation.recon_kind == "node_dir_dependency"
    assert (
        runtime.agent_service.start_records[-1]
        .context_maintenance_policy.threshold
        == 0.80
    )

    resource = _run_step(
        runtime,
        ContentPlanAgentStep(step_id="content_plan_resource", flow_id=flow_id, scope_id="repo:Repo:node:Main.Core", state=state),
        ContentResourceRequestSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_resource_request",
            tool_name="submit_resource_request",
            repo_key="Repo",
            node_path="Main.Core",
            target_kind="web",
            target="https://example.com/source",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo:node:Main.Core",
                    repo_key="Repo",
                    repo_root=str(tmp_path / "Repo"),
                    node_path="Main.Core",
                    target_kind="web",
                    target="https://example.com/source",
                    requested_by="content_plan",
                )
            ],
            summary="Request resource.",
        ),
    )
    assert isinstance(resource.result, ContentPlanStepResult)
    assert resource.result.outcome == "resource_request"
    assert resource.result.resource_request.target == "https://example.com/source"

    decl_round = _run_step(
        runtime,
        ContentPlanAgentStep(step_id="content_plan_decl", flow_id=flow_id, scope_id="repo:Repo:node:Main.Core", state=state),
        DeclRoundDispatchSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="decl_round_dispatch",
            tool_name="submit_current_decl_round",
            repo_key="Repo",
            node_path="Main.Core",
            strategy_id="strategy_1",
            round_id="round_1",
            round_index=1,
            requests=[
                build_decl_round_request(
                    repo_key="Repo",
                    node_path="Main.Core",
                    scope_id="repo:Repo:node:Main.Core",
                    strategy_id="strategy_1",
                    round_id="round_1",
                )
            ],
            summary="Dispatch round.",
        ),
    )
    assert isinstance(decl_round.result, ContentPlanStepResult)
    assert decl_round.result.outcome == "decl_round_dispatch"

    ready = _run_step(
        runtime,
        ContentPlanAgentStep(step_id="content_plan_ready", flow_id=flow_id, scope_id="repo:Repo:node:Main.Core", state=state),
        ContentNodeReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_ready",
            tool_name="submit_content_node_ready",
            repo_key="Repo",
            node_path="Main.Core",
            summary="Ready.",
        ),
    )
    assert isinstance(ready.result, ContentPlanStepResult)
    assert ready.result.outcome == "ready"

    blocked_flow_id = _start_content_flow(runtime, tmp_path)
    blocked = _run_step(
        runtime,
        ContentPlanAgentStep(step_id="content_plan_blocked", flow_id=blocked_flow_id, scope_id="repo:Repo:node:Main.Core", state=state),
        ContentNodeBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="content_node_blocked",
            tool_name="submit_content_node_blocked",
            repo_key="Repo",
            node_path="Main.Core",
            reason="Need external dependency.",
            summary="Need external dependency.",
        ),
    )
    assert isinstance(blocked.result, ContentPlanStepResult)
    assert blocked.result.outcome == "blocked"


def test_recon_agent_step_business_results(tmp_path: Path) -> None:
    runtime = create_fake_lean_flow_runtime(tmp_path / "ark")
    flow_id = _start_content_flow(runtime, tmp_path)

    node_dir = _run_step(
        runtime,
        NodeDirDependencyReconAgentStep(
            step_id="node_dir_step",
            flow_id=flow_id,
            scope_id="repo:Repo:node:Main.Core",
            state=_agent_state("node_dir_dependency_recon", "NodeDirDependencyReconAgent"),
        ),
        NodeDirDependencyReconCompletedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="node_dir_dependency_recon_completed",
            tool_name="submit_node_dir_dependency_recon_completed",
            repo_key="Repo",
            node_path="Main.Core",
            dependency_change_summary="Added Main.Base.",
            checked_boundary_summary="Checked same-repo visible node boundaries.",
            useful_findings=["Main.Base"],
            unresolved_within_visible_boundaries=[],
            summary="Deps found.",
        ),
    )
    assert isinstance(node_dir.result, NodeDirDependencyReconStepResult)
    assert node_dir.result.dependency_change_summary == "Added Main.Base."
    assert node_dir.result.useful_findings == ["Main.Base"]

    mathlib = _run_step(
        runtime,
        MathlibReconAgentStep(
            step_id="mathlib_step",
            flow_id=flow_id,
            scope_id="repo:Repo:node:Main.Core",
            state=_agent_state("mathlib_recon", "MathlibReconAgent"),
        ),
        MathlibReconCompletedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="mathlib_recon_completed",
            tool_name="submit_mathlib_recon_completed",
            repo_key="Repo",
            node_path="Main.Core",
            index_update_summary="Recorded Mathlib.Data.Nat.Basic.",
            node_mathlib_hint_summary="Added current-node Mathlib hints.",
            useful_findings=["Mathlib.Data.Nat.Basic"],
            unresolved_in_mathlib=[],
            summary="Mathlib hints added.",
        ),
    )
    assert isinstance(mathlib.result, MathlibReconStepResult)
    assert mathlib.result.index_update_summary == "Recorded Mathlib.Data.Nat.Basic."
    assert mathlib.result.useful_findings == ["Mathlib.Data.Nat.Basic"]

    resource_completed = _run_step(
        runtime,
        ResourceReconAgentStep(
            step_id="resource_completed_step",
            flow_id=flow_id,
            scope_id="repo:Repo:node:Main.Core",
            state=_agent_state("resource_recon", "ResourceReconAgent"),
        ),
        ResourceReconCompletedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_completed",
            tool_name="submit_resource_recon_completed",
            repo_key="Repo",
            node_path="Main.Core",
            material_change_summary="Attached res_1.",
            checked_material_summary="Checked existing material refs.",
            useful_findings=["res_1"],
            unresolved_material_needs=[],
            summary="Resources found.",
        ),
    )
    assert isinstance(resource_completed.result, ResourceReconStepResult)
    assert resource_completed.result.outcome == "completed"

    resource_blocked = _run_step(
        runtime,
        ResourceReconAgentStep(
            step_id="resource_blocked_step",
            flow_id=flow_id,
            scope_id="repo:Repo:node:Main.Core",
            state=_agent_state("resource_recon", "ResourceReconAgent"),
        ),
        ResourceReconBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_blocked",
            tool_name="submit_resource_recon_blocked",
            repo_key="Repo",
            node_path="Main.Core",
            reason="Need paper.",
            missing_targets=["paper"],
            summary="Need paper.",
        ),
    )
    assert isinstance(resource_blocked.result, ResourceReconStepResult)
    assert resource_blocked.result.outcome == "blocked"

    resource_request = _run_step(
        runtime,
        ResourceReconAgentStep(
            step_id="resource_request_step",
            flow_id=flow_id,
            scope_id="repo:Repo:node:Main.Core",
            state=_agent_state("resource_recon", "ResourceReconAgent"),
        ),
        ResourceReconRequestResourceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_request_resource",
            tool_name="submit_resource_request",
            repo_key="Repo",
            node_path="Main.Core",
            target_kind="arxiv",
            target="2501.12345",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo:node:Main.Core",
                    repo_key="Repo",
                    repo_root=str(tmp_path / "Repo"),
                    node_path="Main.Core",
                    target_kind="arxiv",
                    target="2501.12345",
                    requested_by="resource_recon",
                )
            ],
            summary="Curate paper.",
        ),
    )
    assert isinstance(resource_request.result, ResourceReconStepResult)
    assert resource_request.result.outcome == "resource_request"


def test_decl_stage_compact_preflight_only_runs_on_first_stage_attempt() -> None:
    def context(step):
        return SimpleNamespace(load_step=lambda: step)

    first_worker = DeclStageWorkerAgentStep(
        step_id="worker_first",
        flow_id="round_1",
        scope_id="repo:Repo:node:Main.Core",
        state=DeclStageWorkerStepState(
            agent_role="statement_nl_worker",
            retry_attempt_index=0,
        ),
    )
    retry_worker = DeclStageWorkerAgentStep(
        step_id="worker_retry",
        flow_id="round_1",
        scope_id="repo:Repo:node:Main.Core",
        state=DeclStageWorkerStepState(
            agent_role="statement_nl_worker",
            retry_attempt_index=1,
        ),
    )
    first_reviewer = DeclStageReviewerAgentStep(
        step_id="reviewer_first",
        flow_id="round_1",
        scope_id="repo:Repo:node:Main.Core",
        state=DeclStageReviewerStepState(
            agent_role="statement_nl_reviewer",
            review_attempt_index=0,
        ),
    )
    retry_reviewer = DeclStageReviewerAgentStep(
        step_id="reviewer_retry",
        flow_id="round_1",
        scope_id="repo:Repo:node:Main.Core",
        state=DeclStageReviewerStepState(
            agent_role="statement_nl_reviewer",
            review_attempt_index=1,
        ),
    )

    worker_policy = first_worker.prepare_agent_context_before_first_turn(
        context(first_worker),
        "agent_worker",
    )
    reviewer_policy = first_reviewer.prepare_agent_context_before_first_turn(
        context(first_reviewer),
        "agent_reviewer",
    )

    assert worker_policy is not None and worker_policy.threshold == 0.80
    assert reviewer_policy is not None and reviewer_policy.threshold == 0.80
    assert (
        retry_worker.prepare_agent_context_before_first_turn(
            context(retry_worker),
            "agent_worker",
        )
        is None
    )
    assert (
        retry_reviewer.prepare_agent_context_before_first_turn(
            context(retry_reviewer),
            "agent_reviewer",
        )
        is None
    )
