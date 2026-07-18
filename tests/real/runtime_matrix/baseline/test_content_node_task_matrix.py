from __future__ import annotations

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.fixtures import CONTENT_NODE_PATH, RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_content_node_task_ready_blocked_and_failed_terminal_branches(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ready_path = "Main.Topic.Ready"
    blocked_path = "Main.Topic.Blocked"
    failed_path = "Main.Topic.Failed"
    for node_path in (ready_path, blocked_path, failed_path):
        ws.setup_content_node(node_path=node_path)
    refreshed = ws.runtime.lean_projection.refresh_node_projection(ws.provider_repo, node_path=ready_path)
    assert refreshed.ok, refreshed.issues
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "ContentPlanAgent": [
                ("submit_content_node_ready", {"summary": "Runtime Matrix content node ready."}),
                ("submit_content_node_blocked", {"reason": "Runtime Matrix content node blocked."}),
                ("submit_content_node_failed", {"reason": "Runtime Matrix content node failed."}),
            ]
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_home("ContentPlanAgent", cli_type="codex")
    unwrap(ws.admin.resume_runtime())

    ready_flow_id = _start_content_task(ws, ready_path)
    _wait_completed(ws, ready_flow_id)
    blocked_flow_id = _start_content_task(ws, blocked_path)
    _wait_completed(ws, blocked_flow_id)
    failed_flow_id = _start_content_task(ws, failed_path)
    _wait_completed(ws, failed_flow_id)

    assert ws.runtime.ark.flow_service.get_flow(ready_flow_id).result.outcome == "ready"
    assert ws.runtime.ark.flow_service.get_flow(blocked_flow_id).result.outcome == "blocked"
    assert ws.runtime.ark.flow_service.get_flow(failed_flow_id).result.outcome == "failed"
    assert [call["tool_name"] for call in provider.calls] == [
        "submit_content_node_ready",
        "submit_content_node_blocked",
        "submit_content_node_failed",
    ]


def test_content_node_task_preparation_dispatch_callback_branch(
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
                        "summary": "Dispatch node dependency recon.",
                        "recon_kind": "node_dir_dependency",
                        "objective": "Check node dependencies before planning.",
                        "context_summary": "Runtime Matrix preparation dispatch.",
                    },
                ),
                ("submit_content_node_blocked", {"reason": "Runtime Matrix preparation callback observed."}),
            ],
            "NodeDirDependencyReconAgent": [
                (
                    "submit_node_dir_dependency_recon_completed",
                    {
                        "summary": "Node dependency child recon completed.",
                        "dependency_change_summary": "No node dependency changes.",
                        "checked_boundary_summary": "Checked current visible node boundaries.",
                        "useful_findings": [],
                        "unresolved_within_visible_boundaries": [],
                    },
                )
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("ContentPlanAgent", "NodeDirDependencyReconAgent", cli_type="codex")
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_content_task(ws, CONTENT_NODE_PATH)

    _wait_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "blocked"
    children = ws.runtime.ark.flow_service.list_flows(flow_type="node_dir_dependency_recon")
    assert len(children) == 1
    assert children[0].result.outcome == "completed"
    plan_calls = [call for call in provider.calls if call["agent_type"] == "ContentPlanAgent"]
    assert "Node dependency child recon completed." in plan_calls[1]["prompt"]


def test_content_node_task_resource_dispatch_callback_branch(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "ContentPlanAgent": [
                (
                    "submit_resource_request",
                    {
                        "summary": "Dispatch content resource request.",
                        "target_kind": "web",
                        "target": ws.resources.web_url,
                        "context_summary": "Runtime Matrix content resource branch.",
                    },
                ),
                ("submit_content_node_blocked", {"reason": "Runtime Matrix resource callback observed."}),
            ],
            "ResourceCuratorAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "Runtime Matrix content resource child rejected.",
                    },
                )
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("ContentPlanAgent", "ResourceCuratorAgent", cli_type="codex")
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_content_task(ws, CONTENT_NODE_PATH)

    _wait_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "blocked"
    children = ws.runtime.ark.flow_service.list_flows(flow_type="resource_curation")
    assert len(children) == 1
    assert children[0].status is FlowStatus.COMPLETED
    assert children[0].result.outcome == "rejected"


def test_content_node_task_decl_round_dispatch_callback_branch(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    round_fixture = ws.create_decl_round()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "ContentPlanAgent": [
                (
                    "submit_current_decl_round",
                    {
                        "summary": "Dispatch current decl round.",
                        "strategy_id": round_fixture.strategy_id,
                        "round_id": round_fixture.round_id,
                        "round_index": round_fixture.round_index,
                    },
                ),
                ("submit_content_node_failed", {"reason": "Runtime Matrix decl round callback observed."}),
            ],
            "StatementNLWorkerAgent": [
                (
                    "submit_stage_worker_blocked",
                    {
                        "reason": "Runtime Matrix decl round child blocked.",
                        "affected_decl_names": [round_fixture.decl_name],
                    },
                )
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes(
        "ContentPlanAgent",
        "StatementNLWorkerAgent",
        "StatementNLReviewerAgent",
        "StatementFormalWorkerAgent",
        "StatementFormalReviewerAgent",
        "ProofNLWorkerAgent",
        "ProofNLReviewerAgent",
        "ProofFormalWorkerAgent",
        "ProofFormalReviewerAgent",
        cli_type="codex",
    )
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_content_task(ws, round_fixture.node_path)

    _wait_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "failed"
    children = ws.runtime.ark.flow_service.list_flows(flow_type="decl_graph_round")
    assert len(children) == 1
    assert children[0].result.outcome == "blocked"
    plan_calls = [call for call in provider.calls if call["agent_type"] == "ContentPlanAgent"]
    assert "Runtime Matrix decl round child blocked." in plan_calls[1]["prompt"]


def _start_content_task(ws: RuntimeMatrixWorkspace, node_path: str) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id=f"repo:Provider:node:{node_path}",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": node_path,
                "contract_version": 1,
                "task_mode": "run",
            },
        )
    )


def _wait_completed(ws: RuntimeMatrixWorkspace, flow_id: str) -> None:
    schedule_until(ws.runtime, lambda: ws.runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED, limit=160)
