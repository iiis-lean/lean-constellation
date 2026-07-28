from __future__ import annotations

from pathlib import Path

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import AutomaticCheckpointAppConfig, RequirementResumeInput
from tests.unit_services_helpers import publish_adapter_provider_ready
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import CONTENT_NODE_PATH, RuntimeMatrixWorkspace, create_runtime_matrix_workspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until
from tests.real.runtime_matrix.baseline.test_content_node_task_matrix import _start_content_task
from tests.real.runtime_matrix.baseline.test_recon_flow_matrix import _start_recon


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_coordinator_callback_waiting_and_ready_evidence(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    callback_ws = create_runtime_matrix_workspace(tmp_path / "callback")
    callback_ws.prepare_provider_native_repo()
    assert callback_ws.runtime.node.ensure_native_root_main_contract(callback_ws.provider_repo).ok
    created = callback_ws.runtime.node.create_content_node(
        callback_ws.provider_repo,
        path="Main.Core",
        goal="Strict content branch.",
        boundary="Use strict runtime matrix fixtures.",
        objective="Exercise Coordinator content callback.",
        success_criteria="Content callback returns to Coordinator.",
    )
    if not created.ok:
        assert any(issue.kind == "node_path_exists" for issue in created.issues), created.issues
    callback_provider = ScriptedMcpProvider(
        callback_ws.runtime,
        {
            "CoordinatorAgent": [
                (
                    "submit_content_node_tasks",
                    {"summary": "Dispatch Main.Core content task.", "node_paths": ["Main.Core"]},
                ),
                (
                    "submit_resource_request",
                    {
                        "summary": "Curate a web source after content callback.",
                        "target_kind": "web",
                        "target": callback_ws.resources.web_url,
                        "context_summary": "Need deterministic resource callback.",
                    },
                ),
                (
                    "submit_repo_requirement",
                    {
                        "summary": "Wait after resource callback.",
                        "name": "strict_provider_req",
                        "target_repo": "StrictAnalysis",
                        "reason": "Resource callback was observed.",
                    },
                ),
            ],
            "ContentPlanAgent": [
                ("submit_content_node_blocked", {"reason": "Strict content terminal branch."}),
            ],
            "ResourceCuratorAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "Strict resource callback branch.",
                    },
                )
            ],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(callback_ws.runtime, callback_provider)
    callback_ws.create_homes("CoordinatorAgent", "ContentPlanAgent", "ResourceCuratorAgent", provider_type="scripted")
    unwrap(callback_ws.admin.resume_runtime())
    callback_flow_id = _start_coordinator(callback_ws)

    schedule_until(
        callback_ws.runtime,
        lambda: callback_ws.runtime.ark.flow_service.get_flow(callback_flow_id).status is FlowStatus.WAITING
        and callback_ws.runtime.ark.flow_service.get_flow(callback_flow_id).state.position.phase == "waiting_requirement",
        limit=120,
    )

    callback_flow = callback_ws.runtime.ark.flow_service.get_flow(callback_flow_id)
    assert callback_flow.state.waiting_requirement_name == "strict_provider_req"
    assert callback_ws.runtime.ark.flow_service.list_flows(flow_type="content_node_task")[0].result.outcome == "blocked"
    assert callback_ws.runtime.ark.flow_service.list_flows(flow_type="resource_curation")[0].result.outcome == "rejected"
    unwrap(
        callback_ws.runtime.repo_workspace.mark_requirement_waiting_for_provider(
            callback_ws.provider_repo,
            requirement_name="strict_provider_req",
            provider_repo="StrictAnalysis",
            reason="Strict provider handoff is waiting.",
        )
    )
    strict_provider_repo = callback_ws.workspace_root / "StrictAnalysis"
    publish_adapter_provider_ready(
        callback_ws.runtime,
        strict_provider_repo,
        summary="Strict provider result is ready.",
    )
    unwrap(
        callback_ws.runtime.repo_workspace.requirement.mark_requirement_satisfied(
            callback_ws.provider_repo,
            requirement_name="strict_provider_req",
            provider_repo="StrictAnalysis",
            note="Strict provider result is ready.",
        )
    )
    resumed = unwrap(
        callback_ws.admin.resume_requirement(
            RequirementResumeInput(
                consumer_repo_root=callback_ws.provider_repo,
                requirement_name="strict_provider_req",
                provider_repo="StrictAnalysis",
                admin_note="Strict matrix marks requirement observed.",
                enqueue=False,
            )
        )
    )
    assert resumed.observed is True
    schedule_until(
        callback_ws.runtime,
        lambda: any(
            step.status.value == "completed"
            for step in callback_ws.runtime.ark.flow_service.list_steps(
                flow_id=callback_flow_id,
                step_type="coordinator_requirement_resume_gate_step",
            )
        ),
        limit=20,
    )
    evidence_recorder.record_runtime_state(callback_ws.runtime)

    ready_ws = create_runtime_matrix_workspace(tmp_path / "ready")
    ready_ws.prepare_provider_ready_repo()
    ready_gate = ready_ws.runtime.validation_snapshot.check_repo_ready(
        ready_ws.provider_repo,
        summary="Strict repo ready before coordinator submit.",
    )
    assert ready_gate.ok and ready_gate.value is not None, ready_gate.issues
    assert ready_gate.value.passed is True
    ready_provider = ScriptedMcpProvider(
        ready_ws.runtime,
        {
            "CoordinatorAgent": [
                ("submit_repo_ready", {"summary": "Strict provider repo is ready."}),
            ]
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(ready_ws.runtime, ready_provider)
    ready_ws.create_home("CoordinatorAgent", provider_type="scripted")
    unwrap(ready_ws.admin.resume_runtime())
    ready_flow_id = _start_coordinator(ready_ws)
    schedule_until(ready_ws.runtime, lambda: ready_ws.runtime.ark.flow_service.get_flow(ready_flow_id).status is FlowStatus.COMPLETED, limit=80)
    ready_flow = ready_ws.runtime.ark.flow_service.get_flow(ready_flow_id)
    assert ready_flow.result.outcome == "candidate_prepared"
    assert ready_flow.result.prepared_release is not None
    assert ready_flow.result.provider_ready_marked is False
    evidence_recorder.record_runtime_state(ready_ws.runtime)

    assert {
        "native_repo_coordinator",
        "content_node_task",
        "resource_curation",
    }.issubset(evidence_recorder.evidence.flow_types)
    assert {
        "coordinator_content_batch_snapshot_step",
        "mark_coordinator_repo_ready_step",
    }.issubset(evidence_recorder.evidence.logic_step_types)
    assert "coordinator_agent_step" in evidence_recorder.evidence.agent_step_types
    assert {
        "submit_content_node_tasks",
        "submit_resource_request",
        "submit_repo_requirement",
        "submit_repo_ready",
    }.issubset(evidence_recorder.evidence.submit_tool_names)


def test_strict_content_node_task_terminal_and_dispatch_evidence(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    terminal_ws = create_runtime_matrix_workspace(tmp_path / "terminal")
    ready_path = "Main.Topic.Ready"
    blocked_path = "Main.Topic.Blocked"
    failed_path = "Main.Topic.Failed"
    for node_path in (ready_path, blocked_path, failed_path):
        terminal_ws.setup_content_node(node_path=node_path)
    refreshed = terminal_ws.runtime.lean_projection.refresh_node_projection(terminal_ws.provider_repo, node_path=ready_path)
    assert refreshed.ok, refreshed.issues
    terminal_provider = ScriptedMcpProvider(
        terminal_ws.runtime,
        {
            "ContentPlanAgent": [
                ("submit_content_node_ready", {"summary": "Strict content node ready."}),
                ("submit_content_node_blocked", {"reason": "Strict content node blocked."}),
                ("submit_content_node_failed", {"reason": "Strict content node failed."}),
            ]
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(terminal_ws.runtime, terminal_provider)
    terminal_ws.create_home("ContentPlanAgent", provider_type="scripted")
    unwrap(terminal_ws.admin.resume_runtime())
    for node_path, expected in ((ready_path, "ready"), (blocked_path, "blocked"), (failed_path, "failed")):
        flow_id = _start_content_task(terminal_ws, node_path)
        schedule_until(terminal_ws.runtime, lambda flow_id=flow_id: terminal_ws.runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED, limit=160)
        assert terminal_ws.runtime.ark.flow_service.get_flow(flow_id).result.outcome == expected
    evidence_recorder.record_runtime_state(terminal_ws.runtime)

    prep_ws = create_runtime_matrix_workspace(tmp_path / "preparation")
    prep_ws.runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(
        content_task_progress_enabled=True,
    )
    prep_ws.setup_content_node()
    prep_provider = ScriptedMcpProvider(
        prep_ws.runtime,
        {
            "ContentPlanAgent": [
                (
                    "submit_content_preparation_recon",
                    {
                        "summary": "Dispatch node dependency recon.",
                        "recon_kind": "node_dir_dependency",
                        "objective": "Check node dependencies before planning.",
                        "context_summary": "Strict preparation dispatch.",
                    },
                ),
                ("submit_content_node_blocked", {"reason": "Strict preparation callback observed."}),
            ],
            "NodeDirDependencyReconAgent": [
                (
                    "submit_node_dir_dependency_recon_completed",
                    {
                        "summary": "Strict node dependency child recon completed.",
                        "dependency_change_summary": "No node dependency changes.",
                        "checked_boundary_summary": "Checked current visible node boundaries.",
                        "useful_findings": [],
                        "unresolved_within_visible_boundaries": [],
                    },
                )
            ],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(prep_ws.runtime, prep_provider)
    prep_ws.create_homes("ContentPlanAgent", "NodeDirDependencyReconAgent", provider_type="scripted")
    unwrap(prep_ws.admin.resume_runtime())
    prep_flow_id = _start_content_task(prep_ws, CONTENT_NODE_PATH)
    schedule_until(prep_ws.runtime, lambda: prep_ws.runtime.ark.flow_service.get_flow(prep_flow_id).status is FlowStatus.COMPLETED, limit=160)
    assert prep_ws.runtime.ark.flow_service.get_flow(prep_flow_id).result.outcome == "blocked"
    evidence_recorder.record_runtime_state(prep_ws.runtime)

    resource_ws = create_runtime_matrix_workspace(tmp_path / "resource")
    resource_ws.setup_content_node()
    resource_provider = ScriptedMcpProvider(
        resource_ws.runtime,
        {
            "ContentPlanAgent": [
                (
                    "submit_resource_request",
                    {
                        "summary": "Dispatch strict content resource request.",
                        "target_kind": "web",
                        "target": resource_ws.resources.web_url,
                        "context_summary": "Strict content resource branch.",
                    },
                ),
                ("submit_content_node_blocked", {"reason": "Strict resource callback observed."}),
            ],
            "ResourceCuratorAgent": [
                (
                    "submit_resource_rejected",
                    {
                        "reason": "Strict content resource child rejected.",
                    },
                )
            ],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(resource_ws.runtime, resource_provider)
    resource_ws.create_homes("ContentPlanAgent", "ResourceCuratorAgent", provider_type="scripted")
    unwrap(resource_ws.admin.resume_runtime())
    resource_flow_id = _start_content_task(resource_ws, CONTENT_NODE_PATH)
    schedule_until(resource_ws.runtime, lambda: resource_ws.runtime.ark.flow_service.get_flow(resource_flow_id).status is FlowStatus.COMPLETED, limit=160)
    assert resource_ws.runtime.ark.flow_service.get_flow(resource_flow_id).result.outcome == "blocked"
    evidence_recorder.record_runtime_state(resource_ws.runtime)

    decl_ws = create_runtime_matrix_workspace(tmp_path / "decl")
    round_fixture = decl_ws.create_decl_round()
    decl_round = decl_ws.runtime.decl_graph.get_round(
        decl_ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
    )
    assert decl_round.ok and decl_round.value is not None, decl_round.issues
    change_id = decl_round.value.change_ids[0]
    decl_provider = ScriptedMcpProvider(
        decl_ws.runtime,
        {
            "ContentPlanAgent": [
                (
                    "submit_current_decl_round",
                    {
                        "summary": "Dispatch strict current decl round.",
                        "strategy_id": round_fixture.strategy_id,
                        "round_id": round_fixture.round_id,
                        "round_index": round_fixture.round_index,
                    },
                    ),
                        [
                            (
                                "application",
                                "write_decl_change_summary",
                                {
                                    "round_id": round_fixture.round_id,
                                    "change_id": change_id,
                                    "summary": "Strict blocked change reviewed during callback.",
                                },
                            ),
                            (
                                "application",
                                "write_decl_round_summary",
                                {
                                    "round_id": round_fixture.round_id,
                                    "summary": "Strict decl round child blocked.",
                                },
                            ),
                            (
                                "application",
                                "mark_decl_round_terminal",
                            {
                                "round_id": round_fixture.round_id,
                                "result_kind": "blocked",
                                "reason": "Strict decl round child blocked.",
                            },
                        ),
                        (
                            "submit",
                            "submit_content_node_failed",
                            {"reason": "Strict decl round callback observed."},
                        ),
                    ],
            ],
            "StatementNLWorkerAgent": [
                (
                    "submit_stage_worker_blocked",
                    {
                        "reason": "Strict decl round child blocked.",
                        "affected_decl_names": [round_fixture.decl_name],
                    },
                )
            ],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(decl_ws.runtime, decl_provider)
    decl_ws.create_homes(
        "ContentPlanAgent",
        "StatementNLWorkerAgent",
        "StatementNLReviewerAgent",
        "StatementFormalWorkerAgent",
        "StatementFormalReviewerAgent",
        "ProofNLWorkerAgent",
        "ProofNLReviewerAgent",
        "ProofFormalWorkerAgent",
        "ProofFormalReviewerAgent",
        provider_type="scripted",
    )
    unwrap(decl_ws.admin.resume_runtime())
    decl_flow_id = _start_content_task(decl_ws, round_fixture.node_path)
    schedule_until(decl_ws.runtime, lambda: decl_ws.runtime.ark.flow_service.get_flow(decl_flow_id).status is FlowStatus.COMPLETED, limit=160)
    assert decl_ws.runtime.ark.flow_service.get_flow(decl_flow_id).result.outcome == "failed"
    evidence_recorder.record_runtime_state(decl_ws.runtime)

    assert {
        "content_node_task",
        "node_dir_dependency_recon",
        "resource_curation",
        "decl_graph_round",
    }.issubset(evidence_recorder.evidence.flow_types)
    assert {
        "content_task_admission_step",
        "content_progress_checkpoint_step",
        "ensure_decl_stage_agents_step",
    }.issubset(evidence_recorder.evidence.logic_step_types)
    assert {
        "content_plan_agent_step",
        "node_dir_dependency_recon_agent_step",
        "resource_curator_agent_step",
        "decl_stage_worker_agent_step",
    }.issubset(evidence_recorder.evidence.agent_step_types)
    assert {
        "submit_content_node_ready",
        "submit_content_node_blocked",
        "submit_content_node_failed",
        "submit_content_preparation_recon",
        "submit_resource_request",
        "submit_current_decl_round",
        "submit_node_dir_dependency_recon_completed",
        "submit_stage_worker_blocked",
    }.issubset(evidence_recorder.evidence.submit_tool_names)


def test_strict_recon_completed_blocked_and_resource_callback_evidence(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    recon_ws = create_runtime_matrix_workspace(tmp_path / "recon")
    recon_ws.setup_content_node()
    provider = ScriptedMcpProvider(
        recon_ws.runtime,
        {
            "NodeDirDependencyReconAgent": [
                (
                    "submit_node_dir_dependency_recon_completed",
                    {
                        "summary": "Strict node dependencies reconciled.",
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
                        "summary": "Strict Mathlib dependencies reconciled.",
                        "index_update_summary": "Recorded Mathlib.Data.Nat.Basic and Nat.add_comm.",
                        "node_mathlib_hint_summary": "Added current-node Mathlib hints.",
                        "useful_findings": ["Mathlib.Data.Nat.Basic", "Nat.add_comm"],
                        "unresolved_in_mathlib": [],
                    },
                )
            ],
            "ResourceReconAgent": [
                (
                    "submit_resource_recon_completed",
                    {
                        "summary": "Strict resource recon completed.",
                        "material_change_summary": "Attached resource:local_note.",
                        "checked_material_summary": "Checked local material refs.",
                        "useful_findings": ["resource:local_note"],
                        "unresolved_material_needs": [],
                    },
                ),
                (
                    "submit_resource_recon_blocked",
                    {
                        "reason": "Strict resource recon blocked.",
                        "missing_targets": [recon_ws.resources.web_url],
                    },
                ),
                (
                    "submit_resource_request",
                    {
                        "summary": "Request strict missing web resource.",
                        "target_kind": "web",
                        "target": recon_ws.resources.web_url,
                        "context_summary": "Strict resource recon callback.",
                    },
                ),
                (
                    "submit_resource_recon_completed",
                    {
                        "summary": "Strict resource recon completed after callback.",
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
                        "reason": "Strict child resource branch terminal.",
                    },
                )
            ],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(recon_ws.runtime, provider)
    recon_ws.create_homes(
        "NodeDirDependencyReconAgent",
        "MathlibReconAgent",
        "ResourceReconAgent",
        "ResourceCuratorAgent",
        provider_type="scripted",
    )
    unwrap(recon_ws.admin.resume_runtime())

    node_flow_id = _start_recon(recon_ws, "node_dir_dependency_recon")
    mathlib_flow_id = _start_recon(recon_ws, "mathlib_recon")
    resource_completed_flow_id = _start_recon(recon_ws, "resource_recon")
    resource_blocked_flow_id = _start_recon(recon_ws, "resource_recon")
    resource_callback_flow_id = _start_recon(recon_ws, "resource_recon")
    for flow_id in (
        node_flow_id,
        mathlib_flow_id,
        resource_completed_flow_id,
        resource_blocked_flow_id,
        resource_callback_flow_id,
    ):
        schedule_until(recon_ws.runtime, lambda flow_id=flow_id: recon_ws.runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED, limit=160)

    assert recon_ws.runtime.ark.flow_service.get_flow(node_flow_id).result.outcome == "completed"
    assert recon_ws.runtime.ark.flow_service.get_flow(mathlib_flow_id).result.outcome == "completed"
    assert recon_ws.runtime.ark.flow_service.get_flow(resource_completed_flow_id).result.outcome == "completed"
    assert recon_ws.runtime.ark.flow_service.get_flow(resource_blocked_flow_id).result.outcome == "blocked"
    assert recon_ws.runtime.ark.flow_service.get_flow(resource_callback_flow_id).result.outcome == "completed"
    resource_children = recon_ws.runtime.ark.flow_service.list_flows(flow_type="resource_curation")
    assert len(resource_children) == 1
    assert resource_children[0].result.outcome == "rejected"
    evidence_recorder.record_runtime_state(recon_ws.runtime)

    assert {
        "node_dir_dependency_recon",
        "mathlib_recon",
        "resource_recon",
        "resource_curation",
    }.issubset(evidence_recorder.evidence.flow_types)
    assert {
        "node_dir_dependency_recon_agent_step",
        "mathlib_recon_agent_step",
        "resource_recon_agent_step",
        "resource_curator_agent_step",
    }.issubset(evidence_recorder.evidence.agent_step_types)
    assert {
        "submit_node_dir_dependency_recon_completed",
        "submit_mathlib_recon_completed",
        "submit_resource_recon_completed",
        "submit_resource_recon_blocked",
        "submit_resource_request",
        "submit_resource_rejected",
    }.issubset(evidence_recorder.evidence.submit_tool_names)


def _start_coordinator(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_mode": "admin_start",
                "start_reason": "Strict Runtime Matrix coordinator test.",
            },
        )
    )
