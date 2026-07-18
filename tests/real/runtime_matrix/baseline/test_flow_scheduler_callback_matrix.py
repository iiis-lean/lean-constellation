from __future__ import annotations

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import RequirementResumeInput
from tests.unit_services_helpers import publish_adapter_provider_ready
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_coordinator_content_resource_requirement_callback_matrix(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.prepare_provider_native_repo()
    assert ws.runtime.node.ensure_native_root_main_contract(ws.provider_repo).ok
    created = ws.runtime.node.create_content_node(
        ws.provider_repo,
        path="Main.Core",
        goal="Runtime Matrix content branch.",
        boundary="Use deterministic runtime matrix fixtures.",
        objective="Exercise Coordinator content callback.",
        success_criteria="Content callback returns to Coordinator.",
    )
    if not created.ok:
        assert any(issue.kind == "node_path_exists" for issue in created.issues), created.issues
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "CoordinatorAgent": [
                (
                    "submit_content_node_tasks",
                    {"summary": "Dispatch Main.Core content task.", "node_paths": ["Main.Core"], "task_mode": "run"},
                ),
                (
                    "submit_resource_request",
                    {
                        "summary": "Curate a web source after content callback.",
                        "target_kind": "web",
                        "target": ws.resources.web_url,
                        "context_summary": "Need deterministic resource callback.",
                    },
                ),
                (
                    "submit_repo_requirement",
                    {
                        "summary": "Wait after resource callback.",
                        "name": "runtime_matrix_provider_req",
                        "target_repo": "RuntimeMatrixAnalysis",
                        "reason": "Resource callback was observed.",
                    },
                ),
            ],
            "ContentPlanAgent": [
                (
                    "submit_content_node_blocked",
                    {"reason": "Intentional Runtime Matrix content terminal branch."},
                )
            ],
            "ResourceCuratorAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "Intentional Runtime Matrix resource callback branch.",
                    },
                )
            ],
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("CoordinatorAgent", "ContentPlanAgent", "ResourceCuratorAgent", cli_type="codex")
    unwrap(ws.admin.resume_runtime())
    flow_id = ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_mode": "admin_start",
                "start_reason": "Runtime Matrix callback test.",
            },
        )
    )

    schedule_until(
        ws.runtime,
        lambda: ws.runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.WAITING
        and ws.runtime.ark.flow_service.get_flow(flow_id).state.position.phase == "waiting_requirement",
        limit=120,
    )

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.state.waiting_requirement_name == "runtime_matrix_provider_req"
    content_flows = ws.runtime.ark.flow_service.list_flows(flow_type="content_node_task")
    assert len(content_flows) == 1
    assert content_flows[0].status is FlowStatus.COMPLETED
    assert content_flows[0].result.outcome == "blocked"
    resource_flows = ws.runtime.ark.flow_service.list_flows(flow_type="resource_curation")
    assert len(resource_flows) == 1
    assert resource_flows[0].status is FlowStatus.COMPLETED
    assert resource_flows[0].result.outcome == "rejected"
    coordinator_calls = [call for call in provider.calls if call["agent_type"] == "CoordinatorAgent"]
    assert len(coordinator_calls) == 3
    assert "Intentional Runtime Matrix content terminal branch." in coordinator_calls[1]["prompt"]
    assert "Intentional Runtime Matrix resource callback branch." in coordinator_calls[2]["prompt"]
    requirement = unwrap(
        ws.runtime.repo_workspace.requirement.get_requirement(
            ws.provider_repo,
            name="runtime_matrix_provider_req",
        )
    )
    assert requirement.requirement.target_repo == "RuntimeMatrixAnalysis"
    unwrap(
        ws.runtime.repo_workspace.mark_requirement_waiting_for_provider(
            ws.provider_repo,
            requirement_name="runtime_matrix_provider_req",
            provider_repo="RuntimeMatrixAnalysis",
            reason="Runtime Matrix provider handoff is waiting.",
        )
    )
    provider_repo = ws.workspace_root / "RuntimeMatrixAnalysis"
    publish_adapter_provider_ready(
        ws.runtime,
        provider_repo,
        summary="Runtime Matrix provider result is ready.",
    )
    unwrap(
        ws.runtime.repo_workspace.requirement.mark_requirement_satisfied(
            ws.provider_repo,
            requirement_name="runtime_matrix_provider_req",
            provider_repo="RuntimeMatrixAnalysis",
            note="Runtime Matrix provider result is ready.",
        )
    )
    resumed = unwrap(
        ws.admin.resume_requirement(
            RequirementResumeInput(
                consumer_repo_root=ws.provider_repo,
                requirement_name="runtime_matrix_provider_req",
                provider_repo="RuntimeMatrixAnalysis",
                admin_note="Runtime Matrix marks requirement observed.",
                enqueue=False,
            )
        )
    )
    assert resumed.observed is True
    assert resumed.resume_flow.flow_type == "native_repo_coordinator"
    assert resumed.resume_flow.scope_id == "repo:Provider"


def test_coordinator_repo_ready_branch_marks_provider_ready(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    ws.prepare_provider_ready_repo()
    ready_gate = ws.runtime.validation_snapshot.check_repo_ready(
        ws.provider_repo,
        summary="Runtime Matrix repo ready before coordinator submit.",
    )
    assert ready_gate.ok and ready_gate.value is not None, ready_gate.issues
    assert ready_gate.value.passed is True
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "CoordinatorAgent": [
                (
                    "submit_repo_ready",
                    {"summary": "Runtime Matrix provider repo is ready."},
                )
            ]
        },
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_home("CoordinatorAgent", cli_type="codex")
    unwrap(ws.admin.resume_runtime())
    flow_id = ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_mode": "admin_start",
                "start_reason": "Runtime Matrix ready branch test.",
            },
        )
    )

    schedule_until(
        ws.runtime,
        lambda: ws.runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED,
        limit=80,
    )

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "candidate_prepared"
    assert flow.result.prepared_release is not None
    assert flow.result.provider_ready_marked is False
    ready = unwrap(ws.runtime.repo_workspace.metadata.get_repo_publication(ws.provider_repo))
    assert ready.publication.status.value == "stable"
    model = unwrap(ws.runtime.repo_workspace.metadata.get_repo_model(ws.provider_repo))
    assert model.summary == "Runtime Matrix provider repo is ready."
    mark_steps = ws.runtime.ark.flow_service.list_steps(flow_id=flow_id, step_type="mark_coordinator_repo_ready_step")
    assert len(mark_steps) == 1
    assert mark_steps[0].result.outcome == "candidate_prepared"
    assert [(call["agent_type"], call["tool_name"]) for call in provider.calls] == [
        ("CoordinatorAgent", "submit_repo_ready")
    ]
