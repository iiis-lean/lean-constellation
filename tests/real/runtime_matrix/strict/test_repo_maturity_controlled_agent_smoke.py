from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.app import (
    AdminFlowAdvanceInput,
    AdminStepStartInput,
    ExternalTakeoverCompleteInput,
    ExternalTakeoverToolCallInput,
    ExternalTakeoverToolListInput,
)
from lean_constellation.domain.repo import ProofAvailability, RepoWorkMode
from lean_constellation.services.decl_graph import DeclState
from tests.real.runtime_matrix.admin_helpers import (
    read_handoff_json,
    run_until_step_created,
    set_external_takeover_override,
    unwrap,
    wait_for_pending_handoff,
)
from tests.real.runtime_matrix.baseline.test_decl_graph_round_matrix import _review_actions
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import CONTENT_NODE_PATH, DeclRoundFixture, RuntimeMatrixWorkspace, create_runtime_matrix_workspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_declared_interface_content_plan_controlled_agent_smoke(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = create_runtime_matrix_workspace(tmp_path)
    ws.setup_content_node(node_path=CONTENT_NODE_PATH)
    updated = ws.runtime.repo_workspace.metadata.update_repo_config(
        ws.provider_repo,
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
    )
    assert updated.ok, updated.issues
    ws.create_home("ContentPlanControlledTestAgent")
    flow_id = _start_content_task(ws)
    admission_step_id = _advance_and_complete(ws, flow_id)
    assert ws.runtime.ark.step_service.store.get_step(admission_step_id).result.outcome == "accepted"

    plan_step_id = run_until_step_created(ws.admin, flow_id, "content_plan_agent_step")
    set_external_takeover_override(
        ws.admin,
        plan_step_id,
        agent_type="ContentPlanControlledTestAgent",
        prompt_overlay="Strict RepoMaturity smoke: create a declared-interface public theorem round.",
    )
    plan_payload = _start_external_step(ws, plan_step_id)
    env = plan_payload["env"]
    assert env["LEAN_CONSTELLATION_AGENT_TYPE"] == "ContentPlanControlledTestAgent"
    assert env["LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"] == "content_plan"
    assert env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"] == "content_plan_submit"

    config = _call_external_tool(ws, plan_payload, "application", "get_current_repo_work_config", {}, evidence_recorder)
    assert config.value is not None
    assert config.value["target_proof_availability"] == "declared"
    assert config.value["work_mode"] == "declared_interface"
    strategy = _call_external_tool(
        ws,
        plan_payload,
        "application",
        "ensure_open_decl_strategy",
        {
            "objective": "Create the smallest declared public interface for the smoke node.",
            "rationale": "declared_interface smoke keeps only the public theorem statement.",
        },
        evidence_recorder,
    )
    assert strategy.value is not None
    strategy_id = strategy.value["strategy_id"]
    round_view = _call_external_tool(
        ws,
        plan_payload,
        "application",
        "create_decl_round_draft",
        {"strategy_id": strategy_id, "objective": "Declare public theorem main_result."},
        evidence_recorder,
    )
    assert round_view.value is not None
    round_id = round_view.value["round_id"]
    round_index = round_view.value["round_index"]
    created = _call_external_tool(
        ws,
        plan_payload,
        "application",
        "plan_create_decl",
        {
            "round_id": round_id,
            "name": "main_result",
            "kind": "theorem",
            "objective": "Expose the public theorem statement for this declared interface node.",
            "summary": "The public theorem statement is the declared interface.",
            "public": True,
            "target_state": "declared",
            "require_target_state_satisfied": True,
        },
        evidence_recorder,
    )
    assert created.value is not None
    _call_external_tool(ws, plan_payload, "application", "validate_decl_round_draft", {"round_id": round_id}, evidence_recorder)
    _call_external_tool(
        ws,
        plan_payload,
        "submit",
        "submit_current_decl_round",
        {
            "summary": "Dispatch the declared interface round.",
            "strategy_id": strategy_id,
            "round_id": round_id,
            "round_index": round_index,
        },
        evidence_recorder,
    )
    _complete_external_step(ws, plan_payload, plan_step_id, "ContentPlan declared-interface round dispatched.")

    decl_path_view = ws.runtime.lean_projection.decl_file.derive_decl_file_path(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        decl_name="main_result",
        kind="theorem",
    )
    assert decl_path_view.ok and decl_path_view.value is not None, decl_path_view.issues
    decl_path = Path(decl_path_view.value.path)

    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "StatementNLWorkerAgent": [
                [
                    (
                        "application",
                        "set_statement_nl",
                        {
                            "decl_name": "main_result",
                            "text": "The smoke interface theorem states True.",
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {"summary": "Statement NL completed."},
                    ),
                ]
            ],
            "StatementNLReviewerAgent": [_review_actions(_round_fixture(round_id, round_index), "statement_nl", passed=True)],
            "StatementFormalWorkerAgent": [
                [
                    ("application", "prepare_statement_formal_file", {"decl_name": "main_result"}),
                    (
                        "file_replace",
                        "append_main_result_statement",
                        {
                            "repo_root": str(ws.provider_repo),
                            "path": str(decl_path),
                            "old": "-/\n",
                            "new": "-/\n\ntheorem main_result : True := by\n  trivial\n",
                        },
                    ),
                    ("application", "capture_statement_formal_file", {"decl_name": "main_result"}),
                    ("application", "check_formal_stage_consistency", {"decl_name": "main_result", "stage": "statement"}),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {"summary": "Statement formal completed."},
                    ),
                ]
            ],
            "StatementFormalReviewerAgent": [_review_actions(_round_fixture(round_id, round_index), "statement_formal", passed=True)],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(ws.runtime, provider, cli_type="codex")
    ws.create_homes(
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
    _advance_and_complete(ws, flow_id)
    dispatch_step_id = _advance_and_complete(ws, flow_id)
    assert ws.runtime.ark.step_service.store.get_step(dispatch_step_id).result.outcome == "dispatched"
    child_flow = _single_child_flow(ws, flow_type="decl_graph_round")
    schedule_until(ws.runtime, lambda: ws.runtime.ark.flow_service.get_flow(child_flow.flow_id).status is FlowStatus.COMPLETED, limit=220)
    child_flow = ws.runtime.ark.flow_service.get_flow(child_flow.flow_id)
    assert child_flow.result.outcome == "completed"
    assert child_flow.result.completed_stages == ["statement_nl", "statement_formal"]
    assert not any(call["agent_type"].startswith("Proof") for call in provider.calls)

    callback_step_id = run_until_step_created(ws.admin, flow_id, "content_plan_agent_step", max_advances=20)
    set_external_takeover_override(
        ws.admin,
        callback_step_id,
        agent_type="ContentPlanControlledTestAgent",
        prompt_overlay="Strict RepoMaturity smoke: verify completion gate and submit ready.",
    )
    ready_payload = _start_external_step(ws, callback_step_id)
    completion = _call_external_tool(
        ws,
        ready_payload,
        "application",
        "check_current_content_node_completion",
        {},
        evidence_recorder,
    )
    assert completion.value is not None
    assert completion.value["target_proof_availability"] == "declared"
    assert completion.value["ready_to_submit"] is True
    _call_external_tool(
        ws,
        ready_payload,
        "submit",
        "submit_content_node_ready",
        {"summary": "Declared-interface content node is complete."},
        evidence_recorder,
    )
    _complete_external_step(ws, ready_payload, callback_step_id, "ContentPlan declared-interface node submitted ready.")

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "ready"
    revision = ws.runtime.decl_graph.get_decl_revision(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None, revision.issues
    assert revision.value.state is DeclState.DECLARED
    satisfied = ws.runtime.decl_graph.check_decl_proof_policy_satisfied(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        decl_name="main_result",
    )
    assert satisfied.ok and satisfied.value is not None, satisfied.issues
    assert satisfied.value.proof_policy_satisfied is True
    evidence_recorder.record_runtime_state(ws.runtime)


def _start_content_task(ws: RuntimeMatrixWorkspace) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id=f"repo:Provider:node:{CONTENT_NODE_PATH}",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": CONTENT_NODE_PATH,
                "contract_version": 1,
                "task_mode": "run",
            },
        )
    )


def _advance_and_complete(ws: RuntimeMatrixWorkspace, flow_id: str) -> str:
    advanced = unwrap(ws.admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id)))
    assert advanced.created_step_id is not None, advanced
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=advanced.created_step_id, wait=True, timeout_s=20)))
    assert started.status == "completed", started
    return advanced.created_step_id


def _start_external_step(ws: RuntimeMatrixWorkspace, step_id: str) -> dict[str, Any]:
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=False)))
    assert started.status in {"created", "running"}, started
    handoff = wait_for_pending_handoff(ws.admin)
    payload = read_handoff_json(handoff.handoff_path)
    payload["handoff_id"] = handoff.handoff_id
    return payload


def _call_external_tool(
    ws: RuntimeMatrixWorkspace,
    payload: dict[str, Any],
    view_kind: str,
    tool_name: str,
    arguments: dict[str, Any],
    recorder: EvidenceRecorder,
):
    handoff_id = payload["handoff_id"]
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
    recorder.record_tool_call(
        tool_name=tool_name,
        view_key=env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW" if view_kind == "submit" else "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW"],
        view_kind=view_kind,
        agent_type=env["LEAN_CONSTELLATION_AGENT_TYPE"],
        step_id=env.get("ARK_STEP_ID"),
        ok=True,
        assertion_summary="RepoMaturity controlled Agent smoke external takeover call succeeded.",
    )
    return called


def _complete_external_step(ws: RuntimeMatrixWorkspace, payload: dict[str, Any], step_id: str, final_response: str) -> None:
    completed = unwrap(
        ws.admin.complete_external_takeover(
            ExternalTakeoverCompleteInput(
                handoff_id=payload["handoff_id"],
                final_response=final_response,
                thread_id=f"repo-maturity-smoke-{payload['handoff_id']}",
            )
        )
    )
    assert completed.status == "completed"
    waited = unwrap(ws.admin.wait_step(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=20)))
    assert waited.status == "completed", waited


def _single_child_flow(ws: RuntimeMatrixWorkspace, *, flow_type: str):
    flows = ws.runtime.ark.flow_service.list_flows(flow_type=flow_type)
    assert len(flows) == 1
    return flows[0]


def _round_fixture(round_id: str, round_index: int | None):
    return DeclRoundFixture(
        node_path=CONTENT_NODE_PATH,
        decl_name="main_result",
        strategy_id="strategy",
        round_id=round_id,
        round_index=round_index,
    )
