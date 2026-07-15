from __future__ import annotations

import pytest
from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.services.decl_graph import DeclState
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import DeclRoundFixture, RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider
from tests.real.runtime_matrix.baseline.test_decl_graph_round_matrix import (
    _review_actions,
    _start_decl_round,
    _wait_round_completed,
)
from tests.unit_services_helpers import write_proof_formal_for_test, write_statement_formal_for_test


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_decl_graph_review_rejected_then_worker_blocked_evidence(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = runtime_matrix_workspace
    round_fixture = ws.create_decl_round(end_after_state=DeclState.DECLARED)
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
                            "nl": "The strict rejected branch statement is intentionally sparse.",
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
    ws.create_homes("StatementNLWorkerAgent", "StatementNLReviewerAgent", cli_type="codex")
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


def test_strict_decl_graph_delete_normalize_path_executes_with_skipped_stages(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node(repo_root=ws.provider_repo, node_path="Main.Topic.Core")
    _seed_committed_decl(ws, name="public_keep_result", public=True)
    _seed_committed_decl(ws, name="delete_only_result", public=False)
    round_fixture = _create_delete_round(ws, name="delete_only_result")

    unwrap(ws.admin.resume_runtime())
    flow_id = _start_decl_round(ws, round_fixture)
    _wait_round_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "completed"
    assert flow.result.skipped_stages == ["statement_nl", "statement_formal", "proof_nl", "proof_formal"]
    assert flow.result.completed_stages == []
    evidence_recorder.record_runtime_state(ws.runtime)
    assert "decl_round_delete_normalize_step" in evidence_recorder.evidence.logic_step_types
    assert "decl_round_final_audit_step" in evidence_recorder.evidence.logic_step_types
    assert "decl_stage_worker_agent_step" not in evidence_recorder.evidence.agent_step_types


def _seed_committed_decl(ws: RuntimeMatrixWorkspace, *, name: str, public: bool) -> None:
    strategy = ws.runtime.decl_graph.ensure_open_strategy(
        ws.provider_repo,
        node_path="Main.Topic.Core",
        objective="Strict Runtime Matrix seed committed declaration.",
    )
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = ws.runtime.decl_graph.create_round_draft(
        ws.provider_repo,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective=f"Seed {name}.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    created = ws.runtime.decl_graph.create_decl(
        ws.provider_repo,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name=name,
        kind="theorem",
        objective=f"Create {name}.",
        summary=f"{name} summary.",
        public=public,
        end_after_state=DeclState.PROVED,
    )
    assert created.ok and created.value is not None, created.issues
    round_fixture = DeclRoundFixture(
        node_path="Main.Topic.Core",
        decl_name=name,
        strategy_id=strategy.value.strategy_id,
        round_id=round_record.value.round_id,
        round_index=round_record.value.round_index,
    )
    assert ws.runtime.decl_graph.start_round(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
    ).ok
    assert ws.runtime.decl_graph.write_statement_nl(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        decl_name=name,
        nl=f"{name} states True.",
        origin=[{"kind": "runtime_matrix_strict", "ref": name}],
        deps=[],
    ).ok
    assert write_statement_formal_for_test(ws.runtime,
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  sorry",
        lean_check=_passed_statement_check(),
        deps=[],
    ).ok
    assert ws.runtime.decl_graph.write_proof_nl(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        decl_name=name,
        nl="Use triviality.",
        origin=[{"kind": "runtime_matrix_strict", "ref": f"{name}:proof"}],
        deps=[],
    ).ok
    assert write_proof_formal_for_test(ws.runtime,
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  trivial",
        lean_check=_passed_proof_check(),
        deps=[],
    ).ok
    committed = ws.runtime.decl_graph.commit_decl_revision(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        name=name,
        state=DeclState.PROVED,
    )
    assert committed.ok, committed.issues
    assert ws.runtime.decl_graph.write_decl_change_summary(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        change_id=created.value.change_id,
        summary=f"{name} seeded for strict delete/normalize branch.",
    ).ok
    assert ws.runtime.decl_graph.write_round_summary(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        summary=f"{name} seed round complete.",
    ).ok
    terminal = ws.runtime.decl_graph.mark_round_terminal(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        result_kind="success",
    )
    assert terminal.ok, terminal.issues
    assert ws.runtime.decl_graph.rebuild_decl_graph_index(ws.provider_repo, node_path=round_fixture.node_path).ok


def _create_delete_round(ws: RuntimeMatrixWorkspace, *, name: str) -> DeclRoundFixture:
    strategy = ws.runtime.decl_graph.ensure_open_strategy(
        ws.provider_repo,
        node_path="Main.Topic.Core",
        objective="Strict Runtime Matrix delete-only decl round.",
    )
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = ws.runtime.decl_graph.create_round_draft(
        ws.provider_repo,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective=f"Delete {name}.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    deleted = ws.runtime.decl_graph.mark_decl_delete(
        ws.provider_repo,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name=name,
        objective=f"Delete {name} in strict delete/normalize branch.",
    )
    assert deleted.ok and deleted.value is not None, deleted.issues
    return DeclRoundFixture(
        node_path="Main.Topic.Core",
        decl_name=name,
        strategy_id=strategy.value.strategy_id,
        round_id=round_record.value.round_id,
        round_index=round_record.value.round_index,
    )


def _passed_statement_check() -> dict[str, object]:
    return {
        "status": "passed",
        "policy": "statement_formal",
        "allow_sorry": True,
        "contains_sorry": True,
        "contains_axiom": False,
        "message": "Strict delete/normalize statement check passed.",
    }


def _passed_proof_check() -> dict[str, object]:
    return {
        "status": "passed",
        "policy": "proof_formal",
        "allow_sorry": False,
        "contains_sorry": False,
        "contains_axiom": False,
        "message": "Strict delete/normalize proof check passed.",
    }
