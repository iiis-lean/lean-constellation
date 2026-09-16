from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_runtime_kit.agent.provider_contracts import ProviderHomeSpec
from agent_runtime_kit.flow import (
    AgentStep,
    BaseStepError,
    FlowStatus,
    SchedulerRunDecision,
    SchedulerSemanticRunPolicy,
    StepStatus,
)
from agent_runtime_kit.flow.models import FlowRequest
from agent_runtime_kit.flow.standard_steps import AgentStepState
from pydantic import ValidationError

from lean_constellation.app import (
    LeanAdminApi,
    RuntimeSemanticAdvanceInput,
    StartFlowInput,
    create_app_runtime_services,
)
from lean_constellation.app.semantic_scheduler import (
    SemanticAdvancePolicyError,
    _reserve_independent_content_batch,
    _unresolved_suspended_agent_steps,
    build_semantic_run_policy,
    register_semantic_lease_observation,
)
from lean_constellation.services.concurrency import RepoActivityRecoveryRequiredError
from lean_constellation.flows.common.agent_steps import ContentPlanAgentStep, RepoFormatDiscoveryAgentStep
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskState
from lean_constellation.flows.content_node_task.decl_round.flow import DeclGraphRoundResult


def _start_coordinator(admin: LeanAdminApi, repo_root: Path) -> str:
    result = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="native_repo_coordinator",
            scope_id="repo:Repo",
            params={"repo_key": "Repo", "repo_root": str(repo_root), "start_mode": "admin_start"},
        )
    )
    assert result.ok and result.value is not None
    return result.value.flow_id


@pytest.mark.parametrize("target", [
    {"granularity": "step", "action": "logic", "scope_id": "repo:Repo"},
    {"granularity": "content_batch", "repo_key": "Repo", "coordinator_flow_id": "coordinator",
     "expected_source_submission_id": "submission", "expected_dispatch_step_id": "dispatch"},
])
def test_semantic_safety_defaults_and_explicit_override(target) -> None:
    default = RuntimeSemanticAdvanceInput.model_validate(target)
    assert default.safety.model_dump() == {"max_flow_advances": 500, "max_step_starts": 500}
    override = RuntimeSemanticAdvanceInput.model_validate({
        **target, "safety": {"max_flow_advances": 50, "max_step_starts": 50},
    })
    assert override.safety.model_dump() == {"max_flow_advances": 50, "max_step_starts": 50}


def test_semantic_advance_input_has_strict_discriminated_shapes() -> None:
    assert RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo").action == "logic"
    assert RuntimeSemanticAdvanceInput(granularity="step", action="agent", step_id="s_1").action == "agent"
    assert RuntimeSemanticAdvanceInput(
        granularity="content_phase", action="plan", content_task_flow_id="f_1"
    ).action == "plan"
    assert RuntimeSemanticAdvanceInput(granularity="content_task", content_task_flow_id="f_1").action is None
    content_batch = RuntimeSemanticAdvanceInput(
        granularity="content_batch",
        repo_key="Repo",
        coordinator_flow_id="f_coordinator",
        expected_source_submission_id="sub_batch",
    )
    assert content_batch.coordinator_flow_id == "f_coordinator"
    assert content_batch.expected_source_submission_id == "sub_batch"
    assert content_batch.progress_epoch_decl_rounds is None

    with pytest.raises(ValidationError, match="step.logic requires scope_id"):
        RuntimeSemanticAdvanceInput(granularity="step", action="logic")
    with pytest.raises(ValidationError, match="content_task semantic advance does not accept action"):
        RuntimeSemanticAdvanceInput(granularity="content_task", action="plan", content_task_flow_id="f_1")
    with pytest.raises(ValidationError, match="content_batch semantic advance requires coordinator_flow_id"):
        RuntimeSemanticAdvanceInput(
            granularity="content_batch",
            repo_key="Repo",
            expected_source_submission_id="sub_batch",
        )


def test_semantic_batch_admission_translates_activity_recovery_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()

    def fail_frontier(_repo_root: Path):
        raise RepoActivityRecoveryRequiredError("frontier recovery required")

    monkeypatch.setattr(runtime.repo_activity, "_persisted_content_batches", fail_frontier)

    with pytest.raises(SemanticAdvancePolicyError, match="frontier recovery required"):
        _reserve_independent_content_batch(
            runtime,
            repo_root,
            batch_id="content_batch_test",
            node_paths=("Main.A", "Main.B"),
        )


def test_production_step_logic_runs_to_agent_boundary_and_auto_pauses(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_coordinator(admin, repo_root)
    assert admin.pause_runtime().ok

    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert started.ok and started.value is not None
    assert started.value.run_control is not None
    assert started.value.run_control.mode == "semantic"
    assert started.value.run_control.semantic_policy == "step.logic"

    tick = runtime.ark.schedule_service.schedule_ready()

    assert tick.auto_paused is True
    assert tick.advanced_flow_ids == [flow_id]
    assert tick.started_step_ids == []
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = runtime.ark.step_service.store.get_step(flow.current_step_id)
    assert isinstance(step, AgentStep)
    assert step.status is StepStatus.CREATED
    assert tick.run_control is not None
    assert tick.run_control.pause_reason == f"agent_step_created:{step.step_id}"


def test_step_logic_does_not_let_reopened_child_callback_block_child_progress() -> None:
    scope_id = "repo:Repo:node:Main.Core"
    parent_flow_id = "parent_flow"
    child_flow_id = "reopened_child"
    dispatch_step_id = "dispatch_child"
    callback_step = ContentPlanAgentStep(
        step_id="stale_callback",
        flow_id=parent_flow_id,
        scope_id=scope_id,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            prompt_mode="callback",
            callback_dispatch_step_id=dispatch_step_id,
        ),
    )
    parent = SimpleNamespace(
        flow_id=parent_flow_id,
        flow_type="content_node_task",
        scope_id=scope_id,
        status=FlowStatus.RUNNING,
        state=ContentNodeTaskState(
            position={"phase": "callback_plan_agent", "round_index": 1},
            waiting_dispatch_step_id=dispatch_step_id,
            waiting_child_kind="decl_graph_round",
            completed_child_flow_id=child_flow_id,
            completed_child_outcome="failed",
            progress_checkpoint_repo_scope_captured=False,
        ),
    )
    child = SimpleNamespace(
        flow_id=child_flow_id,
        flow_type="decl_graph_round",
        scope_id=scope_id,
        status=FlowStatus.RUNNING,
        parent_flow_id=parent_flow_id,
        parent_dispatch_step_id=dispatch_step_id,
        result=None,
        error=None,
    )

    class Store:
        def get_step(self, step_id: str):
            assert step_id == callback_step.step_id
            return callback_step

        def update_flow_record(self, flow_id: str, mutator) -> None:  # noqa: ANN001
            assert flow_id == parent_flow_id
            mutator(parent)

    store = Store()

    class FlowService:
        def __init__(self) -> None:
            self.store = store

        def list_non_terminal_flows(self, *, scope_id: str):
            assert scope_id == "repo:Repo:node:Main.Core"
            return [parent, child]

        def get_flow(self, flow_id: str):
            return {parent_flow_id: parent, child_flow_id: child}[flow_id]

    class StepService:
        def __init__(self) -> None:
            self.store = store

        def list_created_steps(self, *, scope_id: str):
            assert scope_id == "repo:Repo:node:Main.Core"
            return [callback_step.step_id]

    runtime = SimpleNamespace(
        ark=SimpleNamespace(flow_service=FlowService(), step_service=StepService())
    )
    policy = build_semantic_run_policy(
        runtime,
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id=scope_id),
    )

    assert policy.allow_flow_advance(child) is True
    assert policy.decide(None).action == "continue"

    child.status = FlowStatus.COMPLETED
    child.result = SimpleNamespace(summary="Reopened child completed.", outcome="completed")
    decision = policy.decide(None)

    assert decision.action == "pause"
    assert decision.reason == f"agent_step_created:{callback_step.step_id}"
    assert parent.state.completed_child_outcome == "completed"
    assert parent.state.latest_callback_summary == "Reopened child completed."


def test_production_step_logic_reports_flow_terminal_before_idle_fallback(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_coordinator(admin, repo_root)
    assert admin.pause_runtime().ok

    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert started.ok and started.value is not None
    policy = runtime.ark.schedule_service._semantic_policy  # noqa: SLF001 - semantic policy fixture.
    assert policy is not None
    with runtime.ark.flow_service.store.edit_session("repo:Repo") as tx:
        flow = tx.load_flow_for_update(flow_id)
        flow.status = FlowStatus.COMPLETED
        flow.current_step_id = None

    decision = policy.decide(runtime.ark.schedule_service)

    assert decision.action == "pause"
    assert decision.reason == f"flow_terminal:{flow_id}"


def test_runtime_lease_monitor_classifies_no_runnable_completed_flow_as_handoff(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_coordinator(admin, repo_root)
    assert admin.pause_runtime().ok
    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert started.ok and started.value is not None and started.value.lease_id is not None
    with runtime.ark.flow_service.store.edit_session("repo:Repo") as tx:
        flow = tx.load_flow_for_update(flow_id)
        flow.status = FlowStatus.COMPLETED
        flow.current_step_id = None
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - scheduler lease fixture.
            status="terminal",
            terminal_reason="no_runnable_candidate",
            advanced_flow_ids=[flow_id],
        )

    view = admin.get_runtime_lease(started.value.lease_id)

    assert view.ok and view.value is not None
    assert view.value.terminal_disposition == "cross_flow_handoff"
    assert view.value.requires_review is False
    assert view.value.suggested_next_action == "inspect_flow_result_and_start_next_lifecycle_entry"


def test_runtime_lease_monitor_keeps_unexplained_no_runnable_reviewable(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    _start_coordinator(admin, repo_root)
    assert admin.pause_runtime().ok
    started = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert started.ok and started.value is not None and started.value.lease_id is not None
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - scheduler lease fixture.
            status="terminal",
            terminal_reason="no_runnable_candidate",
        )

    view = admin.get_runtime_lease(started.value.lease_id)

    assert view.ok and view.value is not None
    assert view.value.terminal_disposition == "review_required"
    assert view.value.requires_review is True
    assert view.value.suggested_next_action == "audit_candidates_before_next_admission"


@pytest.mark.parametrize(
    ("reason", "disposition", "requires_review", "next_action"),
    [
        ("content_batch_checkpointed:batch-1", "normal_boundary", False, "inspect_boundary_and_continue"),
        ("content_batch_progress_epoch:batch-1", "normal_boundary", False, "inspect_boundary_and_continue"),
        ("content_batch_recovery_required:step-1", "review_required", True, "inspect_agent_step_recovery"),
    ],
)
def test_runtime_lease_monitor_classifies_content_batch_terminal_reasons(
    tmp_path: Path,
    reason: str,
    disposition: str,
    requires_review: bool,
    next_action: str,
) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    control = runtime.ark.schedule_service.configure_semantic_run(
        SchedulerSemanticRunPolicy(
            name="content_batch_reason",
            allow_flow_advance=lambda _flow: False,
            allow_step_start=lambda _step: False,
            decide=lambda _scheduler: SchedulerRunDecision(action="pause", reason=reason),
            max_flow_advances=1,
            max_step_starts=1,
        )
    )
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001
            status="terminal",
            terminal_reason=reason,
        )

    view = admin.get_runtime_lease(control.lease_id or "")

    assert view.ok and view.value is not None
    assert view.value.terminal_disposition == disposition
    assert view.value.requires_review is requires_review
    assert view.value.suggested_next_action == next_action


def test_runtime_lease_monitor_classifies_suspended_step_as_recovery_required(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_coordinator(admin, repo_root)
    flow = runtime.ark.flow_service.get_flow(flow_id)
    step = ContentPlanAgentStep(
        step_id="suspended-plan-step",
        flow_id=flow_id,
        scope_id=flow.scope_id,
        status=StepStatus.SUSPENDED,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            provider_type="codex",
        ),
        error=BaseStepError(
            error_type="agent_provider_turn_failed",
            message="Agent provider execution suspended before business completion.",
            details={
                "provider_type": "codex",
                "provider_error_type": "provider_rate_limit",
                "retryable": True,
                "operator_action_required": False,
            },
        ),
    )
    runtime.ark.step_service.create_step(step, enqueue=False)
    runtime.ark.flow_service.store.update_flow_record(
        flow_id,
        lambda target: (
            setattr(target, "status", FlowStatus.RUNNING),
            target.step_ids.append(step.step_id),
            setattr(target, "current_step_id", step.step_id),
        ),
    )
    assert admin.pause_runtime().ok
    control = runtime.ark.schedule_service.configure_semantic_run(
        SchedulerSemanticRunPolicy(
            name="suspended_monitor",
            allow_flow_advance=lambda _flow: False,
            allow_step_start=lambda _step: False,
            decide=lambda _scheduler: SchedulerRunDecision(action="pause", reason="no_runnable_candidate"),
            max_flow_advances=1,
            max_step_starts=0,
        )
    )
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - lease truth fixture.
            status="terminal",
            terminal_reason="no_runnable_candidate",
            started_step_ids=[step.step_id],
        )

    view = admin.get_runtime_lease(control.lease_id or "")

    assert view.ok and view.value is not None
    assert view.value.started_steps[0].status == "suspended"
    assert view.value.started_steps[0].provider_error_type == "provider_rate_limit"
    assert view.value.started_steps[0].available_recovery_actions == ["resume_suspended"]
    assert view.value.terminal_disposition == "review_required"
    assert view.value.requires_review is True
    assert view.value.suggested_next_action == "inspect_agent_step_recovery"


def test_content_batch_recovery_ignores_superseded_suspended_step() -> None:
    source = ContentPlanAgentStep(
        step_id="suspended-source",
        flow_id="member-flow",
        scope_id="repo:Repo:node:Main.Member",
        status=StepStatus.SUSPENDED,
        state=AgentStepState(agent_role="content_plan", agent_type="ContentPlanAgent"),
    )
    replacement = ContentPlanAgentStep(
        step_id="completed-replacement",
        flow_id=source.flow_id,
        scope_id=source.scope_id,
        status=StepStatus.COMPLETED,
        state=AgentStepState(
            agent_role="content_plan",
            agent_type="ContentPlanAgent",
            restart_of_step_id=source.step_id,
        ),
    )

    assert _unresolved_suspended_agent_steps([source, replacement]) == ()

    replacement.status = StepStatus.SUSPENDED
    assert _unresolved_suspended_agent_steps([source, replacement]) == (replacement,)


def test_semantic_advance_requires_global_pause_and_valid_target(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    _start_coordinator(admin, repo_root)

    unpaused = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Repo")
    )
    assert not unpaused.ok
    assert unpaused.issues[0].kind == "semantic_advance_requires_global_pause"

    assert admin.pause_runtime().ok
    invalid = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="content_task", content_task_flow_id="missing")
    )
    assert not invalid.ok
    assert invalid.issues[0].kind == "semantic_advance_failed"
    assert runtime.ark.pause_controller.is_paused(None)


def test_runtime_lease_monitor_keeps_its_semantic_content_target(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)

    flow_ids = []
    for node_path in ("Main.First", "Main.Second"):
        started = admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="content_node_task",
                scope_id=f"repo:Repo:node:{node_path}",
                params={
                    "repo_key": "Repo",
                    "repo_path": str(repo_root),
                    "node_path": node_path,
                    "contract_version": 1,
                },
            )
        )
        assert started.ok and started.value is not None
        flow_ids.append(started.value.flow_id)

    assert admin.pause_runtime().ok
    first = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_phase",
            action="plan",
            content_task_flow_id=flow_ids[0],
        )
    )
    assert first.ok and first.value is not None and first.value.lease_id is not None
    runtime.ark.schedule_service.clear_run_budget(reason="test_terminal")
    runtime.ark.pause_controller.pause(None)
    second = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(
            granularity="content_phase",
            action="plan",
            content_task_flow_id=flow_ids[1],
        )
    )
    assert second.ok and second.value is not None

    first_lease = admin.get_runtime_lease(first.value.lease_id)

    assert first_lease.ok and first_lease.value is not None
    assert first_lease.value.current_content_task_flow_id == flow_ids[0]
    assert first_lease.value.current_content_task_phase == "admission"


def test_runtime_lease_monitor_derives_content_batch_bookmark_from_current_truth(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    coordinator_id = _start_coordinator(admin, repo_root)
    dispatch_step_id = "dispatch-content-batch"

    def mark_batch(flow) -> None:  # noqa: ANN001
        flow.state.position = flow.state.position.model_copy(update={"phase": "waiting_content_tasks"})
        flow.state.waiting_dispatch_step_id = dispatch_step_id
        flow.state.pending_dispatch_source_submission_id = "sub-batch"
        flow.state.pending_content_node_paths = ["Main.A", "Main.B"]

    runtime.ark.flow_service.store.update_flow_record(coordinator_id, mark_batch)
    child_ids = []
    for node_path in ("Main.A", "Main.B"):
        child_ids.append(
            runtime.ark.flow_service.start_flow(
                FlowRequest(
                    flow_type="content_node_task",
                    scope_id=f"repo:Repo:node:{node_path}",
                    params={
                        "repo_key": "Repo",
                        "repo_path": str(repo_root),
                        "node_path": node_path,
                        "contract_version": 1,
                    },
                ),
                parent_flow_id=coordinator_id,
                parent_dispatch_step_id=dispatch_step_id,
                enqueue=False,
            )
        )
    old_round_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="decl_graph_round",
            scope_id="repo:Repo:node:Main.A",
            params={
                "repo_key": "Repo",
                "repo_path": str(repo_root),
                "node_path": "Main.A",
                "contract_version": 1,
                "strategy_id": "strategy-1",
                "round_id": "round-old",
                "round_index": 1,
            },
        ),
        parent_flow_id=child_ids[0],
        parent_dispatch_step_id="old-dispatch",
        enqueue=False,
    )
    runtime.ark.flow_service.store.update_flow_record(
        old_round_id,
        lambda flow: (
            setattr(flow, "status", FlowStatus.COMPLETED),
            setattr(flow, "current_step_id", None),
            setattr(
                flow,
                "result",
                DeclGraphRoundResult(
                    outcome="completed",
                    repo_key="Repo",
                    node_path="Main.A",
                    round_id="round-old",
                    strategy_id="strategy-1",
                    round_index=1,
                    summary="Old round completed.",
                ),
            ),
        ),
    )
    current_round_id = runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="decl_graph_round",
            scope_id="repo:Repo:node:Main.A",
            params={
                "repo_key": "Repo",
                "repo_path": str(repo_root),
                "node_path": "Main.A",
                "contract_version": 1,
                "strategy_id": "strategy-1",
                "round_id": "round-current",
                "round_index": 2,
            },
        ),
        parent_flow_id=child_ids[0],
        parent_dispatch_step_id="current-dispatch",
        enqueue=False,
    )

    def mark_current_child(flow) -> None:  # noqa: ANN001
        flow.state.waiting_dispatch_step_id = "current-dispatch"
        flow.state.waiting_child_kind = "decl_graph_round"
        flow.state.completed_child_flow_id = old_round_id
        flow.state.completed_child_outcome = "completed"

    runtime.ark.flow_service.store.update_flow_record(child_ids[0], mark_current_child)
    policy = SchedulerSemanticRunPolicy(
        name="content_batch",
        allow_flow_advance=lambda _flow: False,
        allow_step_start=lambda _step: False,
        decide=lambda _scheduler: SchedulerRunDecision(action="pause", reason="content_batch_progress_epoch:test"),
        max_flow_advances=1,
        max_step_starts=1,
    )
    control = runtime.ark.schedule_service.configure_semantic_run(policy)
    assert control.lease_id is not None
    register_semantic_lease_observation(
        runtime.ark.schedule_service,
        control.lease_id,
        RuntimeSemanticAdvanceInput(
            granularity="content_batch",
            repo_key="Repo",
            coordinator_flow_id=coordinator_id,
            expected_source_submission_id="sub-batch",
            expected_dispatch_step_id=dispatch_step_id,
            progress_epoch_decl_rounds=1,
        ),
    )

    view = admin.get_runtime_lease(control.lease_id)

    assert view.ok and view.value is not None
    bookmark = view.value.content_batch_bookmark
    assert bookmark is not None
    assert bookmark.coordinator_flow_id == coordinator_id
    assert bookmark.dispatch_step_id == dispatch_step_id
    assert bookmark.snapshot_eligible is False
    assert bookmark.snapshot_ineligible_reason == "active_content_batch_not_at_repo_consistent_boundary"
    assert [child.content_task_flow_id for child in bookmark.children] == child_ids
    assert [child.node_path for child in bookmark.children] == ["Main.A", "Main.B"]
    assert bookmark.children[0].active_or_latest_child_flow_id == current_round_id
    assert bookmark.children[0].round_id == "round-current"
    assert bookmark.children[0].latest_terminal_round_id == "round-old"


def test_runtime_lease_monitor_does_not_borrow_running_agent_from_newer_lease(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir()
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".runtime")
    admin = LeanAdminApi(runtime)
    flow_ids = []
    for node_path in ("Main.Old", "Main.New"):
        started = admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="content_node_task",
                scope_id=f"repo:Repo:node:{node_path}",
                params={
                    "repo_key": "Repo",
                    "repo_path": str(repo_root),
                    "node_path": node_path,
                    "contract_version": 1,
                },
            )
        )
        assert started.ok and started.value is not None
        flow_ids.append(started.value.flow_id)

    agent_service = runtime.ark.agent_service
    agent_service.home_service.create_home(
        ProviderHomeSpec(provider_type="codex", home_id="RepoFormatDiscoveryAgent")
    )
    agents = [
        agent_service.create_agent(
            f"repo:Repo:node:{node_path}",
            "RepoFormatDiscoveryAgent",
            home_id="RepoFormatDiscoveryAgent",
        )
        for node_path in ("Main.Old", "Main.New")
    ]
    step_ids = []
    for index, (flow_id, agent) in enumerate(zip(flow_ids, agents, strict=True)):
        step = RepoFormatDiscoveryAgentStep(
            step_id=f"lease-agent-step-{index}",
            flow_id=flow_id,
            scope_id=agent.scope_id,
            state=AgentStepState(
                agent_role="repo_format_discovery",
                agent_type="RepoFormatDiscoveryAgent",
                home_id="RepoFormatDiscoveryAgent",
                create_agent_if_missing=False,
            ),
        )
        step.agent_bindings.by_role["repo_format_discovery"] = agent.agent_id
        runtime.ark.step_service.create_step(step, enqueue=False)
        step_ids.append(step.step_id)

    assert admin.pause_runtime().ok
    first = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="agent", step_id=step_ids[0])
    )
    assert first.ok and first.value is not None and first.value.lease_id is not None
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - scheduler lease fixture.
            started_step_ids=[step_ids[0]]
        )
    runtime.ark.schedule_service.clear_run_budget(reason="first_lease_terminal")

    runtime.ark.pause_controller.pause(None)
    second = admin.semantic_advance(
        RuntimeSemanticAdvanceInput(granularity="step", action="agent", step_id=step_ids[1])
    )
    assert second.ok and second.value is not None and second.value.lease_id is not None
    with runtime.ark.schedule_service.lock:
        runtime.ark.schedule_service._update_semantic_lease_locked(  # noqa: SLF001 - scheduler lease fixture.
            started_step_ids=[step_ids[1]]
        )
    agent_service.store.patch_agent(agents[1].agent_id, status="running")

    old_view = admin.get_runtime_lease(first.value.lease_id)
    new_view = admin.get_runtime_lease(second.value.lease_id)

    assert old_view.ok and old_view.value is not None
    assert old_view.value.current_agent_id is None
    assert [step.step_id for step in old_view.value.started_steps] == [step_ids[0]]
    assert new_view.ok and new_view.value is not None
    assert new_view.value.current_agent_id == agents[1].agent_id
    assert [step.step_id for step in new_view.value.started_steps] == [step_ids[1]]
