from __future__ import annotations

import pytest
from agent_runtime_kit.flow import FlowRequest, FlowStatus, StepStatus

from lean_constellation.app import RuntimeSemanticAdvanceInput
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.fixtures import CONTENT_NODE_PATH, RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_content_phase_semantic_advance_runs_initial_admission_plan_and_child(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "ContentPlanAgent": [
                (
                    "submit_content_preparation_recon",
                    {
                        "summary": "Dispatch semantic node dependency recon.",
                        "recon_kind": "node_dir_dependency",
                        "objective": "Check dependencies under semantic scheduling.",
                        "context_summary": "Semantic Runtime Matrix preparation.",
                    },
                ),
                ("submit_content_node_blocked", {"reason": "Semantic callback boundary observed."}),
            ],
            "NodeDirDependencyReconAgent": [
                (
                    "submit_node_dir_dependency_recon_completed",
                    {
                        "summary": "Semantic child recon completed.",
                        "dependency_change_summary": "No dependency changes.",
                        "checked_boundary_summary": "Checked the visible boundary.",
                        "useful_findings": [],
                        "unresolved_within_visible_boundaries": [],
                    },
                )
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("ContentPlanAgent", "NodeDirDependencyReconAgent", provider_type="scripted")
    flow_id = ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id=f"repo:Provider:node:{CONTENT_NODE_PATH}",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": CONTENT_NODE_PATH,
                "contract_version": 1,
                "task_mode": "run",
                "max_parallel_content_node_tasks": 1,
            },
        ),
        enqueue=True,
    )

    unwrap(
        ws.admin.semantic_advance(
            RuntimeSemanticAdvanceInput(
                granularity="content_phase",
                action="plan",
                content_task_flow_id=flow_id,
            )
        )
    )
    schedule_until(ws.runtime, lambda: ws.runtime.ark.pause_controller.is_paused(None), limit=20)
    task = ws.runtime.ark.flow_service.get_flow(flow_id)
    first_plan_step = next(
        step
        for step in ws.runtime.ark.flow_service.list_steps(
            flow_id=flow_id,
            step_type="content_plan_agent_step",
        )
    )
    admission_step = next(
        step
        for step in ws.runtime.ark.flow_service.list_steps(
            flow_id=flow_id,
            step_type="content_task_admission_step",
        )
    )
    assert admission_step.status is StepStatus.COMPLETED
    assert first_plan_step.status is StepStatus.COMPLETED
    assert task.state.position.phase == "dispatch_child"
    assert first_plan_step.step_id == task.step_ids[-1]

    unwrap(
        ws.admin.semantic_advance(
            RuntimeSemanticAdvanceInput(
                granularity="content_phase",
                action="child",
                content_task_flow_id=flow_id,
            )
        )
    )
    schedule_until(ws.runtime, lambda: ws.runtime.ark.pause_controller.is_paused(None), limit=80)
    task = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert task.state.position.phase == "callback_plan_agent"
    children = ws.runtime.ark.flow_service.list_flows(flow_type="node_dir_dependency_recon")
    assert len(children) == 1
    assert children[0].status is FlowStatus.COMPLETED

    unwrap(
        ws.admin.semantic_advance(
            RuntimeSemanticAdvanceInput(
                granularity="content_phase",
                action="plan",
                content_task_flow_id=flow_id,
            )
        )
    )
    schedule_until(ws.runtime, lambda: ws.runtime.ark.pause_controller.is_paused(None), limit=20)
    task = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert task.status is FlowStatus.COMPLETED
    assert task.result.outcome == "blocked"


def test_content_task_semantic_advance_includes_coordinator_checkpoint_closeout(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "CoordinatorAgent": [
                (
                    "submit_content_node_tasks",
                    {
                        "summary": "Dispatch one semantic content task.",
                        "node_paths": [CONTENT_NODE_PATH],
                        "task_mode": "run",
                    },
                )
            ],
            "ContentPlanAgent": [
                ("submit_content_node_blocked", {"reason": "Terminal semantic content-task branch."})
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("CoordinatorAgent", "ContentPlanAgent", provider_type="scripted")
    coordinator_id = ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_mode": "admin_start",
                "start_reason": "Semantic content task Runtime Matrix.",
            },
        ),
        enqueue=True,
    )

    unwrap(
        ws.admin.semantic_advance(
            RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Provider")
        )
    )
    schedule_until(ws.runtime, lambda: ws.runtime.ark.pause_controller.is_paused(None), limit=20)
    coordinator = ws.runtime.ark.flow_service.get_flow(coordinator_id)
    coordinator_agent_step_id = coordinator.current_step_id
    assert coordinator_agent_step_id is not None

    unwrap(
        ws.admin.semantic_advance(
            RuntimeSemanticAdvanceInput(
                granularity="step", action="agent", step_id=coordinator_agent_step_id
            )
        )
    )
    schedule_until(ws.runtime, lambda: ws.runtime.ark.pause_controller.is_paused(None), limit=20)

    unwrap(
        ws.admin.semantic_advance(
            RuntimeSemanticAdvanceInput(granularity="step", action="logic", scope_id="repo:Provider")
        )
    )
    schedule_until(ws.runtime, lambda: ws.runtime.ark.pause_controller.is_paused(None), limit=40)
    content_tasks = ws.runtime.ark.flow_service.list_flows(flow_type="content_node_task")
    assert len(content_tasks) == 1
    content_task_id = content_tasks[0].flow_id

    unwrap(
        ws.admin.semantic_advance(
            RuntimeSemanticAdvanceInput(granularity="content_task", content_task_flow_id=content_task_id)
        )
    )
    schedule_until(ws.runtime, lambda: ws.runtime.ark.pause_controller.is_paused(None), limit=80)

    content_task = ws.runtime.ark.flow_service.get_flow(content_task_id)
    coordinator = ws.runtime.ark.flow_service.get_flow(coordinator_id)
    assert content_task.status is FlowStatus.COMPLETED
    assert content_task.result.outcome == "blocked"
    assert coordinator.state.position.phase == "coordinator_callback"
    snapshot_steps = ws.runtime.ark.flow_service.list_steps(
        flow_id=coordinator_id,
        step_type="coordinator_content_batch_snapshot_step",
    )
    assert snapshot_steps[-1].result.checkpoint_kind == "after_content_task_batch_terminal"
    created_coordinator_agents = [
        step
        for step in ws.runtime.ark.flow_service.list_steps(
            flow_id=coordinator_id,
            step_type="coordinator_agent_step",
        )
        if step.status is StepStatus.CREATED
    ]
    assert created_coordinator_agents == []
