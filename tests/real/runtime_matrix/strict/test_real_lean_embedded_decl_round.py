from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus

from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import DeclRoundFixture, RuntimeMatrixWorkspace, create_runtime_matrix_workspace
from tests.real.runtime_matrix.scripted_provider import ScriptedMcpProvider, install_scripted_provider, schedule_until


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_decl_graph_round_real_lake_formal_capture_embedded_in_flow(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    _require_lake_and_lean()
    ws = create_runtime_matrix_workspace(
        tmp_path,
        lake_client=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
    )
    initial_build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert initial_build.ok, initial_build
    round_fixture = ws.create_decl_round(target_state=DeclState.PROVED)
    decl_path = _decl_file_path(ws, round_fixture)
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
                            "nl": "The strict Runtime Matrix theorem states True.",
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement NL completed in strict real Lake flow.",
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
                    ("application", "check_formal_stage_consistency", {"decl_name": round_fixture.decl_name, "stage": "statement"}),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Statement formal completed in strict real Lake flow.",
                        },
                    ),
                ]
            ],
            "StatementFormalReviewerAgent": [_review_actions(round_fixture, "statement_formal", passed=True)],
            "ProofNLWorkerAgent": [
                [
                    (
                        "application",
                        "set_proof_nl",
                        {
                            "decl_name": round_fixture.decl_name,
                            "proof_nl": "Use triviality.",
                        },
                    ),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Proof NL completed in strict real Lake flow.",
                        },
                    ),
                ]
            ],
            "ProofNLReviewerAgent": [_review_actions(round_fixture, "proof_nl", passed=True)],
            "ProofFormalWorkerAgent": [
                [
                    ("application", "prepare_proof_formal_file", {"decl_name": round_fixture.decl_name}),
                    ("application", "capture_proof_formal_file", {"decl_name": round_fixture.decl_name}),
                    ("application", "check_formal_stage_consistency", {"decl_name": round_fixture.decl_name, "stage": "proof"}),
                    (
                        "submit",
                        "submit_stage_worker_completed",
                        {
                            "summary": "Proof formal completed in strict real Lake flow.",
                        },
                    ),
                ]
            ],
            "ProofFormalReviewerAgent": [_review_actions(round_fixture, "proof_formal", passed=True)],
        },
        evidence_recorder=evidence_recorder,
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

    schedule_until(ws.runtime, lambda: ws.runtime.ark.flow_service.get_flow(flow_id).status is FlowStatus.COMPLETED, limit=260)

    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.result.outcome == "completed"
    revision = ws.runtime.decl_graph.get_decl_revision(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        name=round_fixture.decl_name,
        revision=1,
    )
    assert revision.ok and revision.value is not None, revision.issues
    assert revision.value.state is DeclState.PROVED
    assert revision.value.statement_lean_check["status"] == "passed"
    assert revision.value.proof_lean_check["status"] == "passed"
    assert ws.runtime.external.lean_toolchain.run_lake_build(ws.provider_repo, timeout_seconds=120).ok

    evidence_recorder.record_runtime_state(ws.runtime)
    evidence_recorder.add_note("real_lean_embedded_decl_graph_round_completed")
    assert "decl_graph_round" in evidence_recorder.evidence.flow_types
    assert "decl_stage_worker_agent_step" in evidence_recorder.evidence.agent_step_types
    assert "prepare_statement_formal_file" in evidence_recorder.evidence.application_tool_names
    assert "capture_statement_formal_file" in evidence_recorder.evidence.application_tool_names
    assert "prepare_proof_formal_file" in evidence_recorder.evidence.application_tool_names
    assert "capture_proof_formal_file" in evidence_recorder.evidence.application_tool_names
    assert "submit_stage_worker_completed" in evidence_recorder.evidence.submit_tool_names


def _require_lake_and_lean() -> None:
    for command in ("lake", "lean"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for strict real Lean embedded Flow tests.")


def _review_actions(round_fixture: DeclRoundFixture, stage: str, *, passed: bool) -> list[tuple[str, str, dict[str, object]]]:
    if stage == "statement_nl":
        return [
            (
                "application",
                "record_statement_nl_review_passed" if passed else "record_statement_nl_review_rejected",
                {
                    "decl_name": round_fixture.decl_name,
                    "summary": f"{stage} accepted by strict Runtime Matrix.",
                }
                if passed
                else {
                    "decl_name": round_fixture.decl_name,
                    "summary": f"{stage} rejected by strict Runtime Matrix.",
                    "issue_categories": ["runtime_matrix_rejected"],
                    "required_changes": ["Retry with reviewer feedback."],
                },
            ),
            ("submit", "submit_stage_review", {"summary": f"{stage} accepted by strict Runtime Matrix."}),
        ]
    if stage == "statement_formal":
        return [
            (
                "application",
                "record_statement_formal_review_passed" if passed else "record_statement_formal_review_rejected",
                {
                    "decl_name": round_fixture.decl_name,
                    "summary": f"{stage} accepted by strict Runtime Matrix.",
                }
                if passed
                else {
                    "decl_name": round_fixture.decl_name,
                    "summary": f"{stage} rejected by strict Runtime Matrix.",
                    "issue_categories": ["formal_not_equivalent_to_nl"],
                    "required_changes": ["Retry with statement formal reviewer feedback."],
                },
            ),
            ("submit", "submit_stage_review", {"summary": f"{stage} accepted by strict Runtime Matrix."}),
        ]
    if stage == "proof_nl":
        return [
            (
                "application",
                "record_proof_nl_review_passed" if passed else "record_proof_nl_review_rejected",
                {
                    "decl_name": round_fixture.decl_name,
                    "summary": f"{stage} accepted by strict Runtime Matrix.",
                }
                if passed
                else {
                    "decl_name": round_fixture.decl_name,
                    "summary": f"{stage} rejected by strict Runtime Matrix.",
                    "issue_categories": ["proof_route_too_vague"],
                    "required_changes": ["Retry with proof route reviewer feedback."],
                    "recommended_next_action": "worker_repairable",
                },
            ),
            ("submit", "submit_stage_review", {"summary": f"{stage} accepted by strict Runtime Matrix."}),
        ]
    if stage == "proof_formal":
        return [
            (
                "application",
                "record_proof_formal_review_passed" if passed else "record_proof_formal_review_rejected",
                {
                    "decl_name": round_fixture.decl_name,
                    "summary": f"{stage} accepted by strict Runtime Matrix.",
                }
                if passed
                else {
                    "decl_name": round_fixture.decl_name,
                    "summary": f"{stage} rejected by strict Runtime Matrix.",
                    "issue_categories": ["proof_not_aligned_with_proof_nl"],
                    "required_changes": ["Retry with proof formal reviewer feedback."],
                    "recommended_next_action": "worker_repairable",
                },
            ),
            ("submit", "submit_stage_review", {"summary": f"{stage} accepted by strict Runtime Matrix."}),
        ]
    raise AssertionError(f"unsupported decl review stage: {stage}")


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
                "summary": "Strict Runtime Matrix real Lake decl graph round.",
            },
        )
    )
