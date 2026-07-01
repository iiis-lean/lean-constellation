from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

import pytest

from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import DeclRoundFixture, RuntimeMatrixWorkspace, create_runtime_matrix_workspace
from tests.real.runtime_matrix.strict.tool_sweep_partitions import decl_stage_formal_tool_sweep_names
from tests.real.runtime_matrix.strict_helpers import call_tool_with_evidence, checkpoint_with_evidence, restore_with_evidence


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_decl_stage_formal_tool_cases_execute_with_real_lake(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    _require_lake_and_lean()
    ws = create_runtime_matrix_workspace(
        tmp_path,
        lake_client=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
    )
    initial_build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert initial_build.ok, initial_build.summary
    round_fixture = ws.create_decl_round(end_after_state=DeclState.PROVED)
    started = ws.runtime.decl_graph.start_round(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
    )
    assert started.ok, started.issues
    refreshed = ws.runtime.lean_projection.refresh_node_projection(ws.provider_repo, node_path=round_fixture.node_path)
    assert refreshed.ok, refreshed.issues

    server = unwrap(
        create_mcp_server(
            ws.runtime,
            view_keys=[
                "statement_nl_worker",
                "statement_formal_worker",
                "proof_nl_worker",
                "proof_formal_worker",
                "statement_nl_reviewer",
            ],
        )
    )
    checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_decl_stage_formal_tool_sweep",
        recorder=evidence_recorder,
    )

    statement_nl_ctx = _ctx(ws, round_fixture, view="statement_nl_worker", agent_type="StatementNLWorkerAgent", stage="statement_nl")
    statement_revision = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "write_statement_nl",
        {
            "decl_name": round_fixture.decl_name,
            "nl": "The strict Runtime Matrix declaration states True.",
            "origin": [{"kind": "runtime_matrix_strict", "ref": "decl_stage_tool_sweep"}],
            "deps": [],
        },
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement NL was written through the stage worker view.",
    )
    assert _field(statement_revision.value, "state") == "specified"

    statement_formal_ctx = _ctx(
        ws,
        round_fixture,
        view="statement_formal_worker",
        agent_type="StatementFormalWorkerAgent",
        stage="statement_formal",
    )
    prepared_statement = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "prepare_statement_formal_file",
        {"decl_name": round_fixture.decl_name},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal Decl-owned file was prepared.",
    )
    decl_path = Path(_field(prepared_statement.value, "path"))
    assert decl_path.exists()
    rel_decl_path = decl_path.relative_to(ws.provider_repo).as_posix()

    diagnostics = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "run_lean_file_diagnostics",
        {"file_path": rel_decl_path},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Real Lake diagnostics ran on the prepared statement file.",
    )
    assert _field(diagnostics.value, "passed") is True

    sorry_scan = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "scan_lean_sorry_axiom",
        {"file_path": rel_decl_path},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Sorry/axiom scan detected the initial statement skeleton sorry.",
    )
    assert _field(sorry_scan.value, "contains_sorry") is True

    statement_policy = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "check_statement_formal_policy",
        {"file_path": rel_decl_path, "decl_kind": "theorem"},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal policy accepted theorem statement skeleton policy.",
    )
    assert _field(statement_policy.value, "status") == "passed"

    decl_path.write_text(decl_path.read_text(encoding="utf-8").replace("  sorry", "  trivial"), encoding="utf-8")
    statement_capture = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "capture_statement_formal_file",
        {"decl_name": round_fixture.decl_name},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal file was captured with real Lake diagnostics.",
    )
    assert _field(statement_capture.value, "check", "status") == "passed"

    statement_sync = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "check_decl_file_snapshot_sync",
        {"decl_name": round_fixture.decl_name, "stage": "statement"},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement file snapshot sync gate passed.",
    )
    assert _field(statement_sync.value, "passed") is True

    statement_consistency = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "check_formal_stage_consistency",
        {"decl_name": round_fixture.decl_name, "stage": "statement"},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal consistency gate passed.",
    )
    assert _field(statement_consistency.value, "passed") is True

    reviewer_ctx = _ctx(
        ws,
        round_fixture,
        view="statement_nl_reviewer",
        agent_type="StatementNLReviewerAgent",
        role="reviewer",
        stage="statement_nl_review",
    )
    review_mark = call_tool_with_evidence(
        server,
        "statement_nl_reviewer",
        "record_decl_review",
        {
            "round_id": round_fixture.round_id,
            "stage": "statement_nl",
            "decl_name": round_fixture.decl_name,
            "passed": True,
            "summary": "Strict Runtime Matrix statement NL accepted.",
        },
        runtime_context=reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement NL reviewer mark was recorded.",
    )
    assert _field(review_mark.value, "passed") is True

    proof_nl_ctx = _ctx(ws, round_fixture, view="proof_nl_worker", agent_type="ProofNLWorkerAgent", stage="proof_nl")
    proof_revision = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "write_proof_nl",
        {
            "decl_name": round_fixture.decl_name,
            "nl": "Use triviality.",
            "origin": [{"kind": "runtime_matrix_strict", "ref": "decl_stage_tool_sweep_proof"}],
            "deps": [],
        },
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL was written through the stage worker view.",
    )
    assert _field(proof_revision.value, "proof_nl") == "Use triviality."

    proof_formal_ctx = _ctx(ws, round_fixture, view="proof_formal_worker", agent_type="ProofFormalWorkerAgent", stage="proof_formal")
    prepared_proof = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "prepare_proof_formal_file",
        {"decl_name": round_fixture.decl_name},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof formal Decl-owned file was prepared.",
    )
    proof_path = Path(_field(prepared_proof.value, "path"))
    assert proof_path == decl_path
    proof_path.write_text(proof_path.read_text(encoding="utf-8").replace("  trivial", "  sorry"), encoding="utf-8")
    assert "sorry" in proof_path.read_text(encoding="utf-8")

    failing_proof_policy = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "check_proof_formal_policy",
        {"file_path": rel_decl_path},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof formal policy rejected the prepared proof skeleton with sorry.",
    )
    assert _field(failing_proof_policy.value, "status") == "failed"
    assert _field(failing_proof_policy.value, "contains_sorry") is True

    proof_path.write_text(proof_path.read_text(encoding="utf-8").replace("  sorry", "  trivial"), encoding="utf-8")
    proof_capture = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "capture_proof_formal_file",
        {"decl_name": round_fixture.decl_name},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof formal file was captured with real Lake diagnostics.",
    )
    assert _field(proof_capture.value, "check", "status") == "passed"

    passing_proof_policy = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "check_proof_formal_policy",
        {"file_path": rel_decl_path},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof formal policy accepted the completed proof file.",
    )
    assert _field(passing_proof_policy.value, "status") == "passed"

    proof_sync = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "check_decl_file_snapshot_sync",
        {"decl_name": round_fixture.decl_name, "stage": "proof"},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof file snapshot sync gate passed.",
    )
    assert _field(proof_sync.value, "passed") is True

    proof_consistency = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "check_formal_stage_consistency",
        {"decl_name": round_fixture.decl_name, "stage": "proof"},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof formal consistency gate passed.",
    )
    assert _field(proof_consistency.value, "passed") is True

    audit = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "run_decl_round_local_audit",
        {},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current decl round local audit passed after formal captures.",
    )
    assert _field(audit.value, "passed") is True

    proof_path.write_text(proof_path.read_text(encoding="utf-8") + "\n-- local drift before sync\n", encoding="utf-8")
    synced = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "sync_decl_file_after_revision_reset",
        {"decl_name": round_fixture.decl_name},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Decl-owned file was synchronized back to the captured proof snapshot.",
    )
    assert _field(synced.value, "changed") is True
    assert "-- local drift before sync" not in proof_path.read_text(encoding="utf-8")

    removed = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "remove_decl_file_for_delete",
        {"decl_name": round_fixture.decl_name},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Decl-owned file removal deleted the projected Lean file.",
    )
    assert _field(removed.value, "changed") is True
    assert not proof_path.exists()

    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_decl_stage_formal_tool_sweep",
        recorder=evidence_recorder,
    )
    assert not proof_path.exists()

    assert decl_stage_formal_tool_sweep_names() <= evidence_recorder.evidence.application_tool_names
    evidence_recorder.add_note("strict_decl_stage_formal_tool_sweep_real_lake_completed")
    evidence_recorder.export_json(tmp_path / "runtime_matrix_evidence" / "decl_stage_formal_tool_sweep.json")
    evidence_recorder.export_markdown_summary(tmp_path / "runtime_matrix_evidence" / "decl_stage_formal_tool_sweep.md")


def _require_lake_and_lean() -> None:
    for command in ("lake", "lean"):
        if shutil.which(command) is None:
            pytest.skip(f"`{command}` is required for strict DeclStage ToolSweep tests.")


def _ctx(
    ws: RuntimeMatrixWorkspace,
    round_fixture: DeclRoundFixture,
    *,
    view: str,
    agent_type: str,
    stage: str,
    role: str = "worker",
) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id=f"strict_runtime_matrix_{view}",
        step_id=f"strict_runtime_matrix_{view}_step",
        agent_id=f"strict_runtime_matrix_{view}_agent",
        agent_type=agent_type,
        agent_role=role,  # type: ignore[arg-type]
        expected_view_key=view,
        repo_root=ws.provider_repo,
        node_path=round_fixture.node_path,
        node_kind="content",
        contract_version=1,
        stage=stage,
        round_id=round_fixture.round_id,
        batch_decls=[round_fixture.decl_name],
        current_decl=round_fixture.decl_name,
        decl_kind="theorem",
    )


def _field(value: Any, *path: str) -> Any:
    current = value
    for item in path:
        if isinstance(current, dict):
            current = current[item]
        else:
            current = getattr(current, item)
    return current
