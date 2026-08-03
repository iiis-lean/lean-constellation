from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.flows.common.flow_requests import build_resource_curation_request
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.preparation.mathlib_recon.submissions import MathlibReconCompletedSubmission
from lean_constellation.flows.content_node_task.preparation.node_dir_recon.submissions import NodeDirDependencyReconCompletedSubmission
from lean_constellation.flows.content_node_task.preparation.resource_recon.submissions import (
    ResourceReconBlockedSubmission,
    ResourceReconCompletedSubmission,
    ResourceReconRequestResourceSubmission,
)
from lean_constellation.flows.resource_request.flows import ResourceCurationResult


def _runtime(tmp_path: Path) -> FakeLeanFlowRuntime:
    return create_fake_lean_flow_runtime(tmp_path / "ark")


def _start_recon(runtime: FakeLeanFlowRuntime, flow_type: str, tmp_path: Path) -> str:
    repo_root = tmp_path / "Repo"
    repo_root.mkdir(exist_ok=True)
    return runtime.start_flow(
        flow_type,
        {
            "repo_key": "Repo",
            "repo_path": str(repo_root),
            "node_path": "Main.Core",
            "contract_version": 1,
            "objective": "Find useful support.",
            "context_summary": "Initial task.",
        },
        scope_id="repo:Repo:node:Main.Core",
    )


def _expected_node_workdir(tmp_path: Path) -> str:
    return str(tmp_path / "Repo" / "Main" / "Core")


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


def test_node_dir_dependency_recon_flow_completed_result(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    flow_id = _start_recon(runtime, "node_dir_dependency_recon", tmp_path)

    runtime.agent_service.queue_submission(
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
            summary="Node deps completed.",
        )
    )
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "completed"
    assert flow.result.dependency_change_summary == "Added Main.Base."
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(tmp_path)
    assert runtime.agent_service.start_records[-1].context_maintenance_policy is None


def test_mathlib_recon_flow_completed_result(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    flow_id = _start_recon(runtime, "mathlib_recon", tmp_path)

    runtime.agent_service.queue_submission(
        MathlibReconCompletedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="mathlib_recon_completed",
            tool_name="submit_mathlib_recon_completed",
            repo_key="Repo",
            node_path="Main.Core",
            index_update_summary="Recorded Mathlib.Data.Nat.Basic and Nat.succ_ne_zero.",
            node_mathlib_hint_summary="Added current-node hints.",
            useful_findings=["Mathlib.Data.Nat.Basic", "Nat.succ_ne_zero"],
            unresolved_in_mathlib=[],
            summary="Mathlib completed.",
        )
    )
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "completed"
    assert flow.result.index_update_summary == "Recorded Mathlib.Data.Nat.Basic and Nat.succ_ne_zero."
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(tmp_path)
    assert runtime.agent_service.start_records[-1].context_maintenance_policy is None


def test_resource_recon_flow_blocked_and_completed_results(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    blocked_flow_id = _start_recon(runtime, "resource_recon", tmp_path)
    runtime.agent_service.queue_submission(
        ResourceReconBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_blocked",
            tool_name="submit_resource_recon_blocked",
            repo_key="Repo",
            node_path="Main.Core",
            reason="Need inaccessible source.",
            missing_targets=["source"],
            summary="Need source.",
        )
    )
    _advance_and_run(runtime, blocked_flow_id)
    blocked = runtime.flow_service.get_flow(blocked_flow_id)
    assert blocked.status is FlowStatus.COMPLETED
    assert blocked.result.outcome == "blocked"
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(tmp_path)

    completed_flow_id = _start_recon(runtime, "resource_recon", tmp_path)
    runtime.agent_service.queue_submission(
        ResourceReconCompletedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_completed",
            tool_name="submit_resource_recon_completed",
            repo_key="Repo",
            node_path="Main.Core",
            material_change_summary="Attached source:README.md.",
            checked_material_summary="Checked local source refs.",
            useful_findings=["source:README.md"],
            unresolved_material_needs=[],
            summary="Resources completed.",
        )
    )
    _advance_and_run(runtime, completed_flow_id)
    completed = runtime.flow_service.get_flow(completed_flow_id)
    assert completed.status is FlowStatus.COMPLETED
    assert completed.result.outcome == "completed"
    assert completed.result.material_change_summary == "Attached source:README.md."
    assert runtime.agent_service.start_records[-1].workdir == _expected_node_workdir(tmp_path)


def test_resource_recon_flow_supports_multiple_resource_request_callbacks(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    flow_id = _start_recon(runtime, "resource_recon", tmp_path)
    repo_root = tmp_path / "Repo"

    runtime.agent_service.queue_submission(
        ResourceReconRequestResourceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_request_resource",
            tool_name="submit_resource_request",
            repo_key="Repo",
            node_path="Main.Core",
            target_kind="arxiv",
            target="2501.12345",
            requested_use="supporting_material",
            consumer_need="Need the supporting theorem.",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo:node:Main.Core",
                    repo_key="Repo",
                    repo_root=str(repo_root),
                    node_path="Main.Core",
                    target_kind="arxiv",
                    target="2501.12345",
                    requested_use="supporting_material",
                    consumer_need="Need the supporting theorem.",
                    requested_by="resource_recon",
                )
            ],
            summary="Curate paper.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "dispatch_resource_request"

    dispatch_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    child_flow = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)[0]
    _complete_child_flow(
        runtime,
        child_flow.flow_id,
        ResourceCurationResult(
            outcome="duplicate",
            repo_key="Repo",
            target_summary="arxiv:2501.12345",
            existing_resource_key="res_existing",
            summary="Duplicate resource.",
        ),
    )

    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    runtime.agent_service.queue_submission(
        ResourceReconRequestResourceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_request_resource",
            tool_name="submit_resource_request",
            repo_key="Repo",
            node_path="Main.Core",
            target_kind="web",
            target="https://example.com/supporting-note",
            requested_use="supporting_material",
            consumer_need="Need the explanatory note.",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo:node:Main.Core",
                    repo_key="Repo",
                    repo_root=str(repo_root),
                    node_path="Main.Core",
                    target_kind="web",
                    target="https://example.com/supporting-note",
                    requested_use="supporting_material",
                    consumer_need="Need the explanatory note.",
                    requested_by="resource_recon",
                )
            ],
            summary="Curate supporting note.",
        )
    )
    runtime.run_step(callback_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "dispatch_resource_request"
    assert flow.state.resource_request_count == 2

    second_dispatch_step_id = _advance_and_run(runtime, flow_id)
    second_child = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=second_dispatch_step_id,
    )[0]
    _complete_child_flow(
        runtime,
        second_child.flow_id,
        ResourceCurationResult(
            outcome="rejected",
            repo_key="Repo",
            target_summary="web:https://example.com/supporting-note",
            reason="The note is not authoritative.",
            summary="Rejected supporting note.",
        ),
    )

    second_callback_step_id = runtime.flow_service.advance_flow(flow_id)
    runtime.agent_service.queue_submission(
        ResourceReconCompletedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_completed",
            tool_name="submit_resource_recon_completed",
            repo_key="Repo",
            node_path="Main.Core",
            material_change_summary="Attached duplicate resource res_existing.",
            checked_material_summary="Checked duplicate resource callback.",
            useful_findings=["res_existing"],
            unresolved_material_needs=[],
            summary="Resource recon completed after curation.",
        )
    )
    runtime.run_step(second_callback_step_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "completed"
    assert flow.result.material_change_summary == "Attached duplicate resource res_existing."
    assert "Duplicate resource." in (runtime.agent_service.start_records[1].prompt or "")
    assert "Rejected supporting note." in (
        runtime.agent_service.start_records[2].prompt or ""
    )
    assert runtime.agent_service.start_records[0].workdir == _expected_node_workdir(tmp_path)
    assert runtime.agent_service.start_records[1].workdir == _expected_node_workdir(tmp_path)
    assert runtime.agent_service.start_records[2].workdir == _expected_node_workdir(tmp_path)
    assert len(
        {record.agent_id for record in runtime.agent_service.start_records}
    ) == 1


def test_resource_recon_flow_rejects_repeated_resource_request(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    flow_id = _start_recon(runtime, "resource_recon", tmp_path)
    repo_root = tmp_path / "Repo"

    def request_submission():
        return ResourceReconRequestResourceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="resource_recon_request_resource",
            tool_name="submit_resource_request",
            repo_key="Repo",
            node_path="Main.Core",
            target_kind="arxiv",
            target="2501.12345",
            requested_use="supporting_material",
            consumer_need="Need the supporting theorem.",
            requests=[
                build_resource_curation_request(
                    scope_id="repo:Repo:node:Main.Core",
                    repo_key="Repo",
                    repo_root=str(repo_root),
                    node_path="Main.Core",
                    target_kind="arxiv",
                    target="2501.12345",
                    requested_use="supporting_material",
                    consumer_need="Need the supporting theorem.",
                    requested_by="resource_recon",
                )
            ],
            summary="Curate paper.",
        )

    runtime.agent_service.queue_submission(request_submission())
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
            target_summary="arxiv:2501.12345",
            reason="Not useful.",
            summary="Rejected paper.",
        ),
    )

    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    runtime.agent_service.queue_submission(request_submission())
    runtime.run_step(callback_step_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.FAILED
    assert flow.error.error_type == "resource_recon_duplicate_request"
