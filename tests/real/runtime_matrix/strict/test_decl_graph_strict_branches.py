from __future__ import annotations

import pytest
from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.services.decl_graph import DeclState
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider
from tests.real.runtime_matrix.baseline.test_decl_graph_round_matrix import (
    _review_actions,
    _start_decl_round,
    _wait_round_completed,
)


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_decl_graph_review_rejected_then_worker_blocked_evidence(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = runtime_matrix_workspace
    round_fixture = ws.create_decl_round(target_state=DeclState.DECLARED)
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "StatementNLWorkerAgent": [
                [
                    (
                        "application",
                        "set_statement_nl",
                        {
                            "decl_name": round_fixture.decl_name,
                            "text": "The strict rejected branch statement is intentionally sparse.",
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement NL completed before strict reviewer rejection.",
                        },
                    ),
                ],
                (
                    "submit",
                    "submit_stage_worker_blocked",
                    {
                        "reason": "Statement NL retry is blocked after strict reviewer rejection.",
                        "affected_decl_names": [round_fixture.decl_name],
                    },
                ),
            ],
            "StatementNLReviewerAgent": [_review_actions(round_fixture, "statement_nl", passed=False)],
        },
        evidence_recorder=evidence_recorder,
    )
    install_scripted_provider(ws.runtime, provider)
    ws.create_homes("StatementNLWorkerAgent", "StatementNLReviewerAgent", provider_type="scripted")
    unwrap(ws.admin.resume_runtime())
    flow_id = _start_decl_round(ws, round_fixture)

    _wait_round_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "blocked"
    assert flow.result.terminal_reason.stage == "statement_nl"
    evidence_recorder.record_runtime_state(ws.runtime)
    assert "decl_graph_round" in evidence_recorder.evidence.flow_types
    assert {
        "decl_round_stage_gate_audit_step",
        "decl_round_build_result_step",
    }.issubset(evidence_recorder.evidence.logic_step_types)
    assert {
        "decl_stage_worker_agent_step",
        "decl_stage_reviewer_agent_step",
    }.issubset(evidence_recorder.evidence.agent_step_types)
    assert {
        "submit_stage_worker_completed",
        "submit_stage_worker_blocked",
        "submit_stage_review",
    }.issubset(evidence_recorder.evidence.submit_tool_names)
