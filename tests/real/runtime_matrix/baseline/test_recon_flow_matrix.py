from __future__ import annotations

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.fixtures import CONTENT_NODE_PATH, RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_node_dir_and_mathlib_recon_completed_branches(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "NodeDirDependencyReconAgent": [
                (
                    "submit_node_dir_dependency_recon_completed",
                    {
                        "summary": "Node dependencies reconciled.",
                        "dependency_change_summary": "Added Main.Topic.Helper.",
                        "checked_boundary_summary": "Checked same-repo visible node boundaries.",
                        "useful_findings": ["Main.Topic.Helper"],
                        "unresolved_within_visible_boundaries": [],
                    },
                )
            ],
            "MathlibReconAgent": [
                (
                    "submit_mathlib_recon_completed",
                    {
                        "summary": "Mathlib dependencies reconciled.",
                        "index_update_summary": "Recorded Mathlib.Data.Nat.Basic and Nat.add_comm.",
                        "node_mathlib_hint_summary": "Added current-node Mathlib hints.",
                        "useful_findings": ["Mathlib.Data.Nat.Basic", "Nat.add_comm"],
                        "unresolved_in_mathlib": [],
                    },
                )
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("NodeDirDependencyReconAgent", "MathlibReconAgent", provider_type="scripted")
    unwrap(ws.admin.resume_runtime())

    node_flow_id = _start_recon(ws, "node_dir_dependency_recon")
    mathlib_flow_id = _start_recon(ws, "mathlib_recon")
    _wait_completed(ws, node_flow_id)
    _wait_completed(ws, mathlib_flow_id)

    node_flow = ws.runtime.ark.flow_service.get_flow(node_flow_id)
    mathlib_flow = ws.runtime.ark.flow_service.get_flow(mathlib_flow_id)
    assert node_flow.result.outcome == "completed"
    assert node_flow.result.dependency_change_summary == "Added Main.Topic.Helper."
    assert mathlib_flow.result.outcome == "completed"
    assert mathlib_flow.result.index_update_summary == "Recorded Mathlib.Data.Nat.Basic and Nat.add_comm."


def test_resource_recon_completed_and_blocked_branches(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "ResourceReconAgent": [
                (
                    "submit_resource_recon_completed",
                    {
                        "summary": "Resource recon completed.",
                        "material_change_summary": "Attached resource:local_note.",
                        "checked_material_summary": "Checked local material refs.",
                        "useful_findings": ["resource:local_note"],
                        "unresolved_material_needs": [],
                    },
                ),
                (
                    "submit_resource_recon_blocked",
                    {
                        "reason": "Runtime Matrix resource recon blocked.",
                        "missing_targets": [ws.resources.web_url],
                    },
                ),
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_home("ResourceReconAgent", provider_type="scripted")
    unwrap(ws.admin.resume_runtime())

    completed_flow_id = _start_recon(ws, "resource_recon")
    blocked_flow_id = _start_recon(ws, "resource_recon")
    _wait_completed(ws, completed_flow_id)
    _wait_completed(ws, blocked_flow_id)

    completed = ws.runtime.ark.flow_service.get_flow(completed_flow_id)
    blocked = ws.runtime.ark.flow_service.get_flow(blocked_flow_id)
    assert completed.result.outcome == "completed"
    assert completed.result.material_change_summary == "Attached resource:local_note."
    assert blocked.result.outcome == "blocked"
    assert blocked.result.reason == "Runtime Matrix resource recon blocked."


def test_resource_recon_request_resource_callback_branch(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node()
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "ResourceReconAgent": [
                (
                    "submit_resource_request",
                    {
                        "summary": "Request missing web resource.",
                        "target_kind": "web",
                        "target": ws.resources.web_url,
                        "context_summary": "Runtime Matrix resource recon callback.",
                    },
                ),
                (
                    "submit_resource_recon_completed",
                    {
                        "summary": "Resource recon completed after callback.",
                        "material_change_summary": "Attached web:runtime-matrix-resource.",
                        "checked_material_summary": "Checked resource callback result.",
                        "useful_findings": ["web:runtime-matrix-resource"],
                        "unresolved_material_needs": [],
                    },
                ),
            ],
            "ResourceCuratorAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "Runtime Matrix child resource branch terminal.",
                    },
                )
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("ResourceReconAgent", "ResourceCuratorAgent", provider_type="scripted")
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_recon(ws, "resource_recon")

    _wait_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "completed"
    resource_children = ws.runtime.ark.flow_service.list_flows(flow_type="resource_curation")
    assert len(resource_children) == 1
    assert resource_children[0].status is FlowStatus.COMPLETED
    assert resource_children[0].result.outcome == "rejected"
    recon_calls = [call for call in provider.calls if call["agent_type"] == "ResourceReconAgent"]
    assert len(recon_calls) == 2
    assert "Runtime Matrix child resource branch terminal." in recon_calls[1]["prompt"]


def _start_recon(ws: RuntimeMatrixWorkspace, flow_type: str) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type=flow_type,
            scope_id=f"repo:Provider:node:{CONTENT_NODE_PATH}",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": CONTENT_NODE_PATH,
                "contract_version": 1,
                "objective": "Runtime Matrix recon objective.",
                "context_summary": "Runtime Matrix recon context.",
            },
        )
    )


def _wait_completed(ws: RuntimeMatrixWorkspace, flow_id: str) -> None:
    schedule_until(ws.runtime, lambda: ws.runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED, limit=120)
