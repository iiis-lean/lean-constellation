from __future__ import annotations

from pathlib import Path

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.services.decl_graph import DeclState
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.fixtures import DeclRoundFixture, RuntimeMatrixWorkspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_decl_graph_round_four_stage_completed_matrix(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
) -> None:
    ws = runtime_matrix_workspace
    round_fixture = ws.create_decl_round(end_after_state=DeclState.PROVED)
    decl_path = _decl_file_path(ws, round_fixture)
    provider = ScriptedMcpProvider(
        ws.runtime,
        {
            "StatementNLWorkerAgent": [
                [
                    (
                        "application",
                        "write_statement_nl",
                        {
                            "decl_name": round_fixture.decl_name,
                            "nl": "The Runtime Matrix main result states True.",
                            "deps": [],
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement NL completed.",
                        },
                    ),
                ]
            ],
            "StatementNLReviewerAgent": [_review_actions(round_fixture, "statement_nl", passed=True)],
            "StatementFormalWorkerAgent": [
                [
                    ("application", "prepare_statement_formal_file", {"decl_name": round_fixture.decl_name}),
                    (
                        "file_replace",
                        "replace_statement_sorry_with_trivial",
                        {
                            "repo_root": str(ws.provider_repo),
                            "path": str(decl_path),
                            "old": "  sorry",
                            "new": "  trivial",
                        },
                    ),
                    ("application", "capture_statement_formal_file", {"decl_name": round_fixture.decl_name}),
                    (
                        "application",
                        "check_formal_stage_consistency",
                        {"decl_name": round_fixture.decl_name, "stage": "statement"},
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement formal completed.",
                        },
                    ),
                ]
            ],
            "StatementFormalReviewerAgent": [_review_actions(round_fixture, "statement_formal", passed=True)],
            "ProofNLWorkerAgent": [
                [
                    (
                        "application",
                        "write_proof_nl",
                        {
                            "decl_name": round_fixture.decl_name,
                            "nl": "The proof closes by triviality.",
                            "deps": [],
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Proof NL completed.",
                        },
                    ),
                ]
            ],
            "ProofNLReviewerAgent": [_review_actions(round_fixture, "proof_nl", passed=True)],
            "ProofFormalWorkerAgent": [
                [
                    ("application", "prepare_proof_formal_file", {"decl_name": round_fixture.decl_name}),
                    ("application", "capture_proof_formal_file", {"decl_name": round_fixture.decl_name}),
                    (
                        "application",
                        "check_formal_stage_consistency",
                        {"decl_name": round_fixture.decl_name, "stage": "proof"},
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Proof formal completed.",
                        },
                    ),
                ]
            ],
            "ProofFormalReviewerAgent": [_review_actions(round_fixture, "proof_formal", passed=True)],
        },
    )
    install_scripted_provider(ws.runtime, provider)
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
    flow_id = _start_decl_round(ws, round_fixture)

    _wait_round_completed(ws, flow_id)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "completed"
    assert flow.result.completed_stages == ["statement_nl", "statement_formal", "proof_nl", "proof_formal"]
    revision = ws.runtime.decl_graph.get_decl_revision(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        name=round_fixture.decl_name,
        revision=1,
    )
    assert revision.ok and revision.value is not None, revision.issues
    assert revision.value.state is DeclState.PROVED
    call_keys = [(call["agent_type"], call["view_kind"], call["tool_name"]) for call in provider.calls]
    assert ("StatementFormalWorkerAgent", "application", "capture_statement_formal_file") in call_keys
    assert ("ProofFormalWorkerAgent", "application", "capture_proof_formal_file") in call_keys
    assert ("ProofFormalReviewerAgent", "submit", "submit_stage_review") in call_keys


def test_decl_graph_round_review_rejected_then_worker_blocked_matrix(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
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
                        "write_statement_nl",
                        {
                            "decl_name": round_fixture.decl_name,
                            "nl": "The rejected branch statement is intentionally sparse.",
                            "deps": [],
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement NL completed before reviewer rejection.",
                        },
                    ),
                ],
                (
                    "submit",
                    "submit_stage_worker_blocked",
                    {
                        "reason": "Statement NL retry is blocked after reviewer rejection.",
                        "affected_decl_names": [round_fixture.decl_name],
                    },
                ),
            ],
            "StatementNLReviewerAgent": [_review_actions(round_fixture, "statement_nl", passed=False)],
        },
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
    assert [call["tool_name"] for call in provider.calls] == [
        "write_statement_nl",
        "submit_stage_worker_completed",
        "record_statement_nl_review_rejected",
        "submit_stage_review",
        "submit_stage_worker_blocked",
    ]


def _review_actions(round_fixture: DeclRoundFixture, stage: str, *, passed: bool) -> list[tuple[str, str, dict[str, object]]]:
    if stage == "statement_nl":
        tool_name = "record_statement_nl_review_passed" if passed else "record_statement_nl_review_rejected"
        args: dict[str, object] = {
            "decl_name": round_fixture.decl_name,
            "summary": f"{stage} {'accepted' if passed else 'rejected'} by Runtime Matrix.",
        }
        if not passed:
            args["issue_categories"] = ["runtime_matrix_rejected"]
            args["required_changes"] = ["Retry with reviewer feedback."]
        return [
            ("application", tool_name, args),
            (
                "submit",
                "submit_stage_review",
                {"summary": f"{stage} {'accepted' if passed else 'rejected'} by Runtime Matrix."},
            ),
        ]
    return [
        (
            "application",
            "record_decl_review",
            {
                "round_id": round_fixture.round_id,
                "stage": stage,
                "decl_name": round_fixture.decl_name,
                "passed": passed,
                "summary": f"{stage} {'accepted' if passed else 'rejected'} by Runtime Matrix.",
                "issue_kind": None if passed else "runtime_matrix_rejected",
                "suggested_fix": None if passed else "Retry with reviewer feedback.",
            },
        ),
        (
            "submit",
            "submit_stage_review",
            {"summary": f"{stage} {'accepted' if passed else 'rejected'} by Runtime Matrix."},
        ),
    ]


def _decl_file_path(ws: RuntimeMatrixWorkspace, round_fixture: DeclRoundFixture) -> Path:
    path_view = ws.runtime.lean_projection.decl_file.derive_decl_file_path(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        decl_name=round_fixture.decl_name,
        kind="theorem",
    )
    assert path_view.ok and path_view.value is not None, path_view.issues
    return Path(path_view.value.path)


def _start_decl_round(ws: RuntimeMatrixWorkspace, round_fixture: DeclRoundFixture) -> str:
    return ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="decl_graph_round",
            scope_id=f"repo:Provider:node:{round_fixture.node_path}",
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": round_fixture.node_path,
                "contract_version": 1,
                "strategy_id": round_fixture.strategy_id,
                "round_id": round_fixture.round_id,
                "round_index": round_fixture.round_index,
                "summary": "Runtime Matrix decl graph round.",
            },
        )
    )


def _wait_round_completed(ws: RuntimeMatrixWorkspace, flow_id: str) -> None:
    schedule_until(
        ws.runtime,
        lambda: ws.runtime.ark.flow_service.get_flow(flow_id).status in {FlowStatus.COMPLETED, FlowStatus.FAILED},
        limit=220,
    )
