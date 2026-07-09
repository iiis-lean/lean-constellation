from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agent_runtime_kit.flow.models import FlowRequest

from lean_constellation.app import (
    AdminFlowAdvanceInput,
    AdminStepStartInput,
    ExternalTakeoverCompleteInput,
    ExternalTakeoverToolCallInput,
    ExternalTakeoverToolListInput,
    StartFlowInput,
)
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig
from tests.real.runtime_matrix.admin_helpers import (
    read_handoff_json,
    run_next_created_step,
    run_until_step_created,
    set_external_takeover_override,
    unwrap,
    wait_for_pending_handoff,
)
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace, create_runtime_matrix_workspace
from tests.real.runtime_matrix.strict.test_real_codex_agent_resource_matrix import (
    _assert_decl_stage_step,
    _complete_proof_nl_stage_for_real_codex,
    _complete_statement_formal_stage_for_real_codex,
    _complete_statement_nl_stage_for_real_codex,
    _require_lake_and_lean,
    _start_decl_round,
)


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_external_takeover_handoff_payload_for_repo_coordinator_resource_and_content(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    repo_ws = create_runtime_matrix_workspace(tmp_path / "repo_format")
    repo_ws.create_home("RepoFormatDiscoveryControlledTestAgent")
    repo_ws.write_bootstrap_preparation(repo_ws.provider_repo)
    repo_flow_id = _start_repo_format_bootstrap(repo_ws)
    run_next_created_step(repo_ws.admin, repo_flow_id, timeout_s=20)
    repo_step_id = unwrap(repo_ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=repo_flow_id))).created_step_id
    assert repo_step_id is not None
    _assert_handoff_payload_and_tools(
        repo_ws,
        repo_step_id,
        agent_type="RepoFormatDiscoveryControlledTestAgent",
        marker="strict-handoff-repo-format",
        expected_workdir=repo_ws.provider_repo,
        app_view="repo_format_discovery",
        submit_view="repo_format_discovery_submit",
        app_call=("get_preparation_input", {}),
        submit_call=("submit_native_repo_choice", {"summary": "Strict handoff chooses native.", "searched_targets": ["strict handoff"], "rejected_candidates": []}),
        recorder=evidence_recorder,
    )

    coordinator_ws = create_runtime_matrix_workspace(tmp_path / "coordinator")
    coordinator_ws.create_home("CoordinatorControlledTestAgent")
    coordinator_ws.prepare_provider_ready_repo()
    coordinator_flow_id = _start_coordinator(coordinator_ws)
    coordinator_step_id = run_until_step_created(coordinator_ws.admin, coordinator_flow_id, "coordinator_agent_step")
    _assert_handoff_payload_and_tools(
        coordinator_ws,
        coordinator_step_id,
        agent_type="CoordinatorControlledTestAgent",
        marker="strict-handoff-coordinator",
        expected_workdir=coordinator_ws.provider_repo,
        app_view="native_repo_coordinator",
        submit_view="native_repo_coordinator_submit",
        app_call=("inspect_workspace_for_coordinator", {}),
        submit_call=("submit_repo_ready", {"summary": "Strict handoff coordinator marks repo ready."}),
        recorder=evidence_recorder,
    )

    resource_ws = create_runtime_matrix_workspace(tmp_path / "resource_curator")
    resource_ws.create_home("ResourceCuratorControlledTestAgent")
    resource_ws.prepare_provider_native_repo()
    resource_flow_id = _start_resource_curation(resource_ws, target_kind="web", target=resource_ws.resources.web_url)
    run_next_created_step(resource_ws.admin, resource_flow_id, timeout_s=20)
    resource_step_id = run_until_step_created(resource_ws.admin, resource_flow_id, "resource_curator_agent_step")
    _assert_handoff_payload_and_tools(
        resource_ws,
        resource_step_id,
        agent_type="ResourceCuratorControlledTestAgent",
        marker="strict-handoff-resource-curator",
        expected_workdir=resource_ws.provider_repo / ".lean_constellation" / "resources" / ".drafts",
        app_view="resource_curator",
        submit_view="resource_curator_submit",
        app_call=("normalize_resource_target", {"target": resource_ws.resources.web_url}),
        submit_call=(
            "submit_resource_rejected",
            {"reason": "Strict handoff resource branch stops after payload inspection.", "target_kind": "web", "target": resource_ws.resources.web_url},
        ),
        recorder=evidence_recorder,
    )

    content_ws = create_runtime_matrix_workspace(tmp_path / "content_plan")
    content_ws.create_home("ContentPlanControlledTestAgent")
    content_ws.setup_content_node()
    content_flow_id = _start_content_task(content_ws)
    admission_step_id = run_next_created_step(content_ws.admin, content_flow_id, timeout_s=20)
    admission_step = content_ws.runtime.ark.flow_service.get_step(admission_step_id)
    assert admission_step.result is not None
    assert admission_step.result.outcome == "accepted"
    content_step_id = run_until_step_created(content_ws.admin, content_flow_id, "content_plan_agent_step")
    _assert_handoff_payload_and_tools(
        content_ws,
        content_step_id,
        agent_type="ContentPlanControlledTestAgent",
        marker="strict-handoff-content-plan",
        expected_workdir=content_ws.provider_repo / "Main" / "Topic" / "Core",
        app_view="content_plan",
        submit_view="content_plan_submit",
        app_call=("get_current_node_contract", {}),
        submit_call=("submit_content_node_blocked", {"reason": "Strict handoff content plan stops after payload inspection."}),
        recorder=evidence_recorder,
    )


def test_strict_external_takeover_handoff_payload_for_adapter_and_mathlib(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    adapter_ws = create_runtime_matrix_workspace(tmp_path / "adapter")
    adapter_ws.create_home("AdapterDeclCatalogControlledTestAgent")
    adapter_ws.prepare_adapter_truth()
    adapter_flow_id = _start_adapter_preparation(adapter_ws)
    validate_step_id = run_next_created_step(adapter_ws.admin, adapter_flow_id, timeout_s=20)
    assert adapter_ws.runtime.ark.flow_service.get_step(validate_step_id).result.outcome == "passed"
    ensure_step_id = run_next_created_step(adapter_ws.admin, adapter_flow_id, timeout_s=20)
    assert adapter_ws.runtime.ark.flow_service.get_step(ensure_step_id).result.outcome == "ready"
    adapter_step_id = run_until_step_created(adapter_ws.admin, adapter_flow_id, "adapter_decl_catalog_agent_step")
    _assert_handoff_payload_and_tools(
        adapter_ws,
        adapter_step_id,
        agent_type="AdapterDeclCatalogControlledTestAgent",
        marker="strict-handoff-adapter-decl-catalog",
        expected_workdir=adapter_ws.adapter_repo,
        app_view="adapter_repo_import",
        submit_view="adapter_repo_import_submit",
        app_call=("inspect_adapter_input", {}),
        submit_call=("submit_adapter_catalog_blocked", {"reason": "Strict handoff adapter branch stops after payload inspection."}),
        recorder=evidence_recorder,
    )

    mathlib_ws = create_runtime_matrix_workspace(tmp_path / "mathlib")
    mathlib_ws.create_home("MathlibReconControlledTestAgent")
    mathlib_ws.setup_content_node()
    mathlib_flow_id = _start_mathlib_recon(mathlib_ws)
    mathlib_step_id = run_until_step_created(mathlib_ws.admin, mathlib_flow_id, "mathlib_recon_agent_step")
    _assert_handoff_payload_and_tools(
        mathlib_ws,
        mathlib_step_id,
        agent_type="MathlibReconControlledTestAgent",
        marker="strict-handoff-mathlib-recon",
        expected_workdir=mathlib_ws.provider_repo / "Main" / "Topic" / "Core",
        app_view="mathlib_recon",
        submit_view="mathlib_recon_submit",
        app_call=("get_current_node_mathlib_hints", {}),
        submit_call=(
            "submit_mathlib_recon_completed",
            {
                "summary": "Strict handoff Mathlib recon completed.",
                "index_update_summary": "No Mathlib index changes.",
                "node_mathlib_hint_summary": "Inspected current-node hints.",
                "useful_findings": [],
                "unresolved_in_mathlib": [],
            },
        ),
        recorder=evidence_recorder,
    )


def test_strict_external_takeover_handoff_payload_for_formal_workers(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    _require_lake_and_lean()

    statement_ws = _formal_workspace(tmp_path / "statement_formal")
    statement_round = statement_ws.create_decl_round(end_after_state=DeclState.PROVED)
    statement_flow_id = _start_decl_round(statement_ws, statement_round)
    _complete_statement_nl_stage_for_real_codex(statement_ws, statement_flow_id, statement_round, evidence_recorder)
    run_next_created_step(statement_ws.admin, statement_flow_id, timeout_s=20)
    run_next_created_step(statement_ws.admin, statement_flow_id, timeout_s=20)
    statement_step_id = run_until_step_created(statement_ws.admin, statement_flow_id, "decl_stage_worker_agent_step")
    _assert_decl_stage_step(statement_ws, statement_step_id, stage="statement_formal")
    _assert_handoff_payload_and_tools(
        statement_ws,
        statement_step_id,
        agent_type="StatementFormalWorkerControlledTestAgent",
        marker="strict-handoff-statement-formal-worker",
        expected_workdir=statement_ws.provider_repo / "Main" / "Topic" / "Core",
        app_view="statement_formal_worker",
        submit_view="decl_stage_worker_submit",
        app_call=("prepare_statement_formal_file", {"decl_name": statement_round.decl_name}),
        submit_call=(
            "submit_stage_worker_blocked",
            {"reason": "Strict handoff statement formal branch stops after payload inspection.", "affected_decl_names": [statement_round.decl_name]},
        ),
        recorder=evidence_recorder,
        env_overrides={
            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "statement_formal_worker",
            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "decl_stage_worker_submit",
        },
    )

    proof_ws = _formal_workspace(tmp_path / "proof_formal")
    proof_round = proof_ws.create_decl_round(end_after_state=DeclState.PROVED)
    proof_flow_id = _start_decl_round(proof_ws, proof_round)
    _complete_statement_nl_stage_for_real_codex(proof_ws, proof_flow_id, proof_round, evidence_recorder)
    _complete_statement_formal_stage_for_real_codex(proof_ws, proof_flow_id, proof_round, evidence_recorder)
    _complete_proof_nl_stage_for_real_codex(proof_ws, proof_flow_id, proof_round, evidence_recorder)
    run_next_created_step(proof_ws.admin, proof_flow_id, timeout_s=20)
    run_next_created_step(proof_ws.admin, proof_flow_id, timeout_s=20)
    proof_step_id = run_until_step_created(proof_ws.admin, proof_flow_id, "decl_stage_worker_agent_step")
    _assert_decl_stage_step(proof_ws, proof_step_id, stage="proof_formal")
    _assert_handoff_payload_and_tools(
        proof_ws,
        proof_step_id,
        agent_type="ProofFormalWorkerControlledTestAgent",
        marker="strict-handoff-proof-formal-worker",
        expected_workdir=proof_ws.provider_repo / "Main" / "Topic" / "Core",
        app_view="proof_formal_worker",
        submit_view="decl_stage_worker_submit",
        app_call=("prepare_proof_formal_file", {"decl_name": proof_round.decl_name}),
        submit_call=(
            "submit_stage_worker_blocked",
            {"reason": "Strict handoff proof formal branch stops after payload inspection.", "affected_decl_names": [proof_round.decl_name]},
        ),
        recorder=evidence_recorder,
        env_overrides={
            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "proof_formal_worker",
            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "decl_stage_worker_submit",
        },
    )


def _assert_handoff_payload_and_tools(
    ws: RuntimeMatrixWorkspace,
    step_id: str,
    *,
    agent_type: str,
    marker: str,
    expected_workdir: Path,
    app_view: str,
    submit_view: str,
    app_call: tuple[str, dict[str, Any]],
    submit_call: tuple[str, dict[str, Any]],
    recorder: EvidenceRecorder,
    env_overrides: dict[str, str] | None = None,
) -> None:
    set_external_takeover_override(
        ws.admin,
        step_id,
        agent_type=agent_type,
        prompt_overlay=f"Strict Runtime Matrix external handoff marker: {marker}.",
        env_overrides=env_overrides,
    )
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=False)))
    assert started.status in {"created", "running"}, started
    handoff = wait_for_pending_handoff(ws.admin)
    payload = read_handoff_json(handoff.handoff_path)
    env = payload["env"]
    assert isinstance(env, dict)
    assert marker in payload["prompt"]
    assert payload["developer_instructions"].strip()
    assert env["LEAN_CONSTELLATION_AGENT_TYPE"] == agent_type
    assert env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == app_view
    assert env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"] == submit_view
    assert payload["workdir"] == str(expected_workdir)
    assert payload["agent_id"]
    assert payload["home_id"] == agent_type

    _call_handoff_tool(ws, handoff.handoff_id, payload, "application", app_call[0], app_call[1], recorder)
    _call_handoff_tool(ws, handoff.handoff_id, payload, "submit", submit_call[0], submit_call[1], recorder)
    completed = unwrap(
        ws.admin.complete_external_takeover(
            ExternalTakeoverCompleteInput(
                handoff_id=handoff.handoff_id,
                final_response=f"Strict Runtime Matrix external handoff completed for {agent_type}.",
                thread_id=f"runtime-matrix-strict-{handoff.handoff_id}",
            )
        )
    )
    assert completed.status == "completed"
    waited = unwrap(ws.admin.wait_step(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=20)))
    assert waited.status == "completed", waited
    recorder.record_runtime_state(ws.runtime)


def _call_handoff_tool(
    ws: RuntimeMatrixWorkspace,
    handoff_id: str,
    payload: dict[str, object],
    view_kind: str,
    tool_name: str,
    arguments: dict[str, Any],
    recorder: EvidenceRecorder,
) -> Any:
    listed = unwrap(
        ws.admin.list_external_takeover_tools(
            ExternalTakeoverToolListInput(handoff_id=handoff_id, view_kind=view_kind)
        )
    )
    assert tool_name in {tool.name for tool in listed}
    called = unwrap(
        ws.admin.call_external_takeover_tool(
            ExternalTakeoverToolCallInput(
                handoff_id=handoff_id,
                view_kind=view_kind,
                tool_name=tool_name,
                arguments=arguments,
            )
        )
    )
    assert called.ok is True, called
    env = payload["env"]
    assert isinstance(env, dict)
    recorder.record_tool_call(
        tool_name=tool_name,
        view_key=str(env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW" if view_kind == "submit" else "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"]),
        view_kind=view_kind,
        agent_type=str(env["LEAN_CONSTELLATION_AGENT_TYPE"]),
        step_id=str(env.get("ARK_STEP_ID") or ""),
        ok=True,
        assertion_summary=f"Strict external handoff {agent_type_for_summary(env)} {view_kind} tool call.",
    )
    return called


def agent_type_for_summary(env: dict[str, object]) -> str:
    return str(env.get("LEAN_CONSTELLATION_AGENT_TYPE") or "unknown-agent")


def _formal_workspace(path: Path) -> RuntimeMatrixWorkspace:
    ws = create_runtime_matrix_workspace(
        path,
        lake_client=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
    )
    initial_build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert initial_build.ok, initial_build
    ws.create_homes(
        "StatementNLWorkerControlledTestAgent",
        "StatementNLReviewerControlledTestAgent",
        "StatementFormalWorkerControlledTestAgent",
        "StatementFormalReviewerControlledTestAgent",
        "ProofNLWorkerControlledTestAgent",
        "ProofNLReviewerControlledTestAgent",
        "ProofFormalWorkerControlledTestAgent",
    )
    return ws


def _start_repo_format_bootstrap(ws: RuntimeMatrixWorkspace) -> str:
    started = unwrap(
        ws.admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="requirement_group_repo_bootstrap",
                scope_id="repo:Provider",
                enqueue=False,
                params={
                    "target_repo": "Provider",
                    "repo_root": str(ws.provider_repo),
                    "workspace_root": str(ws.workspace_root),
                    "requirement_refs": ["Consumer:need_provider"],
                },
            ),
            repo_root=str(ws.provider_repo),
        )
    )
    return started.flow_id


def _start_coordinator(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="native_repo_coordinator",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "start_mode": "admin_start",
                "start_reason": "Strict external handoff Coordinator test.",
            },
        )
    )


def _start_resource_curation(ws: RuntimeMatrixWorkspace, *, target_kind: str, target: str) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="resource_curation",
            scope_id="repo:Provider",
            params={
                "repo_key": "Provider",
                "repo_root": str(ws.provider_repo),
                "target_kind": target_kind,
                "target": target,
                "requested_by": "strict_external_handoff",
                "context_summary": "Strict external handoff ResourceCurator test.",
                "node_path": "Main.Core",
            },
        )
    )


def _start_content_task(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id="repo:Provider:node:Main.Topic.Core",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": "Main.Topic.Core",
                "contract_version": 1,
                "task_mode": "run",
            },
        )
    )


def _start_adapter_preparation(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="adapter_repo_preparation",
            scope_id="repo:Adapter",
            params={
                "repo_key": "Adapter",
                "repo_root": str(ws.adapter_repo),
                "start_reason": "bootstrap",
                "admin_notes": "Strict external handoff AdapterDeclCatalog test.",
            },
        )
    )


def _start_mathlib_recon(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="mathlib_recon",
            scope_id="repo:Provider:node:Main.Topic.Core",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": "Main.Topic.Core",
                "contract_version": 1,
                "objective": "Strict external handoff MathlibRecon test.",
                "context_summary": "Inspect handoff payload and tool views.",
            },
        )
    )
