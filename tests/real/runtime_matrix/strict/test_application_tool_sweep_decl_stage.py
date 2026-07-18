from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from agent_runtime_kit.flow.models import FlowRequest
import pytest

from lean_constellation.flows.common.agent_steps import DeclStageReviewerAgentStep
from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.domain.repo_run import SourceScope
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
    active_resource_key = ws.create_active_resource(target_kind="local_file", target=str(ws.resources.local_file))
    _prepare_committed_source_index(ws)
    round_fixture = ws.create_decl_round(target_state=DeclState.PROVED)
    support = ws.runtime.decl_graph.create_decl(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        name="supporting_statement",
        kind="theorem",
        objective="Create supporting_statement.",
        summary="Supporting statement for strict ToolSweep dependency coverage.",
        public=False,
        target_state=DeclState.PROVED,
    )
    assert support.ok, support.issues
    started = ws.runtime.decl_graph.start_round(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
    )
    assert started.ok, started.issues
    support_advance = ws.runtime.decl_graph.advance_stage_state(
        ws.provider_repo,
        node_path=round_fixture.node_path,
        round_id=round_fixture.round_id,
        stage="proof_formal",
        decl_names=["supporting_statement"],
    )
    assert support_advance.ok, support_advance.issues
    refreshed = ws.runtime.lean_projection.refresh_node_projection(ws.provider_repo, node_path=round_fixture.node_path)
    assert refreshed.ok, refreshed.issues
    projection_build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert projection_build.ok, projection_build.summary

    server = unwrap(
        create_mcp_server(
            ws.runtime,
            view_keys=[
                "statement_nl_worker",
                "statement_formal_worker",
                "proof_nl_worker",
                    "proof_formal_worker",
                    "statement_nl_reviewer",
                    "statement_formal_reviewer",
                    "proof_nl_reviewer",
                    "proof_formal_reviewer",
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
        "set_statement_nl",
        {
            "decl_name": round_fixture.decl_name,
            "text": "The strict Runtime Matrix declaration states True.",
        },
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement NL text was written through the typed stage worker view.",
    )
    assert _decl_field(statement_revision.value, "state") == "planned"
    source_origin = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "add_statement_source_origin",
        {"decl_name": round_fixture.decl_name, "source_path": "source.md", "start_line": 1, "end_line": 2, "note": "Source range states the theorem."},
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Typed statement source origin was added.",
    )
    assert _decl_field(source_origin.value, "statement_origin")
    resource_origin = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "add_statement_resource_origin",
        {"decl_name": round_fixture.decl_name, "resource_key": active_resource_key, "start_locator": "normalized/main.md:1", "note": "Resource note supports the theorem."},
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Typed statement resource origin was added.",
    )
    assert len(_decl_field(resource_origin.value, "statement_origin")) == 2
    removed_origin = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "remove_statement_origin",
        {"decl_name": round_fixture.decl_name, "index": 1},
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Typed statement origin removal worked.",
    )
    assert len(_decl_field(removed_origin.value, "statement_origin")) == 1
    cleared_origins = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "clear_statement_origins",
        {"decl_name": round_fixture.decl_name, "reason": "Strict ToolSweep covers origin clearing."},
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Typed statement origin clearing worked.",
    )
    assert _decl_field(cleared_origins.value, "statement_origin") == []
    indexed_true = ws.runtime.mathlib.upsert_mathlib_decl_entry(
        ws.provider_repo,
        name="True",
        module="Init.Prelude",
        kind="inductive",
        summary="Truth proposition used by the strict ToolSweep statement.",
    )
    assert indexed_true.ok, indexed_true.issues
    mathlib_dep = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "add_statement_mathlib_dep",
        {"decl_name": round_fixture.decl_name, "mathlib_decl_name": "True", "module": "Init.Prelude", "reason": "The statement mentions True."},
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Typed statement Mathlib dependency was added.",
    )
    assert "True" in _decl_field(mathlib_dep.value, "statement_deps")
    removed_dep = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "remove_statement_dep",
        {"decl_name": round_fixture.decl_name, "index": 0},
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Typed statement dependency removal worked.",
    )
    assert _decl_field(removed_dep.value, "statement_deps") == []
    decl_dep = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "add_statement_decl_dep",
        {"decl_name": round_fixture.decl_name, "dep_name": "supporting_statement", "reason": "Support declaration used by statement."},
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Typed statement declaration dependency was added.",
    )
    assert "supporting_statement" in _decl_field(decl_dep.value, "statement_deps")
    cleared_deps = call_tool_with_evidence(
        server,
        "statement_nl_worker",
        "clear_statement_deps",
        {"decl_name": round_fixture.decl_name, "reason": "Strict ToolSweep leaves the sample statement dependency-free."},
        runtime_context=statement_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Typed statement dependency clearing worked.",
    )
    assert _decl_field(cleared_deps.value, "statement_deps") == []

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
    decl_path.write_text(
        decl_path.read_text(encoding="utf-8")
        + f"\ntheorem {round_fixture.decl_name} : True := by\n  sorry\n",
        encoding="utf-8",
    )

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
        assertion_summary="Sorry/axiom scan detected the ToolSweep statement fixture sorry.",
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
    formal_mathlib_dep = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "add_statement_mathlib_dep",
        {"decl_name": round_fixture.decl_name, "mathlib_decl_name": "True", "module": "Init.Prelude", "reason": "The formal statement mentions True."},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal worker added a typed Mathlib statement dependency.",
    )
    assert "True" in _decl_field(formal_mathlib_dep.value, "statement_deps")
    formal_removed_dep = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "remove_statement_dep",
        {"decl_name": round_fixture.decl_name, "index": 0},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal worker removed a typed statement dependency.",
    )
    assert _decl_field(formal_removed_dep.value, "statement_deps") == []
    formal_decl_dep = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "add_statement_decl_dep",
        {"decl_name": round_fixture.decl_name, "dep_name": "supporting_statement", "reason": "Support declaration checked during formalization."},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal worker added a typed project statement dependency.",
    )
    assert "supporting_statement" in _decl_field(formal_decl_dep.value, "statement_deps")
    formal_cleared_deps = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "clear_statement_deps",
        {"decl_name": round_fixture.decl_name, "reason": "Strict ToolSweep leaves formal statement dependency-free."},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal worker cleared typed statement dependencies.",
    )
    assert _decl_field(formal_cleared_deps.value, "statement_deps") == []
    statement_sync = call_tool_with_evidence(
        server,
        "statement_formal_worker",
        "check_decl_file_snapshot_sync",
        {"decl_name": round_fixture.decl_name, "stage": "statement"},
        runtime_context=statement_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement file capture sync gate passed.",
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

    statement_formal_reviewer_ctx = _ctx(
        ws,
        round_fixture,
        view="statement_formal_reviewer",
        agent_type="StatementFormalReviewerAgent",
        role="reviewer",
        stage="statement_formal",
    )
    statement_formal_reviewer_ctx = _attach_reviewer_step(ws, statement_formal_reviewer_ctx, round_fixture)
    formal_rejected_mark = call_tool_with_evidence(
        server,
        "statement_formal_reviewer",
        "record_statement_formal_review_rejected",
        {
            "decl_name": round_fixture.decl_name,
            "summary": "Strict Runtime Matrix statement formal rejected before replacement.",
            "issue_categories": ["formal_not_equivalent_to_nl"],
            "required_changes": ["Replace this probe rejection with the accepted mark."],
        },
        runtime_context=statement_formal_reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal reviewer rejected mark was recorded.",
    )
    assert _field(formal_rejected_mark.value, "passed") is False
    formal_review_mark = call_tool_with_evidence(
        server,
        "statement_formal_reviewer",
        "record_statement_formal_review_passed",
        {
            "decl_name": round_fixture.decl_name,
            "summary": "Strict Runtime Matrix statement formal accepted.",
        },
        runtime_context=statement_formal_reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement formal reviewer passed mark was recorded.",
    )
    assert _field(formal_review_mark.value, "passed") is True

    reviewer_ctx = _ctx(
        ws,
        round_fixture,
        view="statement_nl_reviewer",
        agent_type="StatementNLReviewerAgent",
        role="reviewer",
        stage="statement_nl",
    )
    reviewer_ctx = _attach_reviewer_step(ws, reviewer_ctx, round_fixture)
    rejected_mark = call_tool_with_evidence(
        server,
        "statement_nl_reviewer",
        "record_statement_nl_review_rejected",
        {
            "decl_name": round_fixture.decl_name,
            "summary": "Strict Runtime Matrix statement NL rejected before replacement.",
            "issue_categories": ["runtime_matrix_probe"],
            "required_changes": ["Replace this probe rejection with the accepted mark."],
        },
        runtime_context=reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement NL reviewer rejected mark was recorded.",
    )
    assert _field(rejected_mark.value, "passed") is False
    review_mark = call_tool_with_evidence(
        server,
        "statement_nl_reviewer",
        "record_statement_nl_review_passed",
        {
            "decl_name": round_fixture.decl_name,
            "summary": "Strict Runtime Matrix statement NL accepted.",
        },
        runtime_context=reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement NL reviewer mark was recorded.",
    )
    assert _field(review_mark.value, "passed") is True
    review_status = call_tool_with_evidence(
        server,
        "statement_nl_reviewer",
        "inspect_current_stage_review_status",
        {},
        runtime_context=reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Statement NL reviewer status reported full mark coverage.",
    )
    assert _field(review_status.value, "ready_to_submit") is True

    proof_nl_ctx = _ctx(ws, round_fixture, view="proof_nl_worker", agent_type="ProofNLWorkerAgent", stage="proof_nl")
    proof_revision = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "set_proof_nl",
        {
            "decl_name": round_fixture.decl_name,
            "text": "Use triviality.",
        },
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL was written through the stage worker view.",
    )
    assert _decl_field(proof_revision.value, "proof_nl") == "Use triviality."
    proof_source_origin = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "add_proof_source_origin",
        {"decl_name": round_fixture.decl_name, "source_path": "source.md", "start_line": 1, "end_line": 1, "note": "Strict proof source-origin probe."},
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL worker added a typed source proof origin.",
    )
    assert len(_decl_field(proof_source_origin.value, "proof_origin")) == 1
    proof_resource_origin = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "add_proof_resource_origin",
        {"decl_name": round_fixture.decl_name, "resource_key": active_resource_key, "start_locator": "proof", "note": "Strict proof resource-origin probe."},
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL worker added a typed resource proof origin.",
    )
    assert len(_decl_field(proof_resource_origin.value, "proof_origin")) == 2
    proof_removed_origin = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "remove_proof_origin",
        {"decl_name": round_fixture.decl_name, "index": 0},
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL worker removed a typed proof origin.",
    )
    assert len(_decl_field(proof_removed_origin.value, "proof_origin")) == 1
    proof_cleared_origins = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "clear_proof_origins",
        {"decl_name": round_fixture.decl_name, "reason": "Strict ToolSweep leaves generated proof route without origins."},
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL worker cleared typed proof origins.",
    )
    assert _decl_field(proof_cleared_origins.value, "proof_origin") == []
    proof_mathlib_dep = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "add_proof_mathlib_dep",
        {"decl_name": round_fixture.decl_name, "mathlib_decl_name": "True", "module": "Init.Prelude", "reason": "Proof closes True."},
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL worker added a typed Mathlib proof dependency.",
    )
    assert "True" in _decl_field(proof_mathlib_dep.value, "proof_deps")
    proof_removed_dep = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "remove_proof_dep",
        {"decl_name": round_fixture.decl_name, "index": 0},
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL worker removed a typed proof dependency.",
    )
    assert _decl_field(proof_removed_dep.value, "proof_deps") == []
    proof_decl_dep = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "add_proof_decl_dep",
        {"decl_name": round_fixture.decl_name, "dep_name": "supporting_statement", "reason": "Strict proof dependency probe."},
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL worker added a typed project proof dependency.",
    )
    assert "supporting_statement" in _decl_field(proof_decl_dep.value, "proof_deps")
    proof_cleared_deps = call_tool_with_evidence(
        server,
        "proof_nl_worker",
        "clear_proof_deps",
        {"decl_name": round_fixture.decl_name, "reason": "Strict ToolSweep leaves generated proof route dependency-free."},
        runtime_context=proof_nl_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL worker cleared typed proof dependencies.",
    )
    assert _decl_field(proof_cleared_deps.value, "proof_deps") == []

    proof_nl_reviewer_ctx = _ctx(ws, round_fixture, view="proof_nl_reviewer", agent_type="ProofNLReviewerAgent", role="reviewer", stage="proof_nl")
    proof_nl_reviewer_ctx = _attach_reviewer_step(ws, proof_nl_reviewer_ctx, round_fixture)
    proof_rejected_mark = call_tool_with_evidence(
        server,
        "proof_nl_reviewer",
        "record_proof_nl_review_rejected",
        {
            "decl_name": round_fixture.decl_name,
            "summary": "Strict Runtime Matrix proof NL rejected before replacement.",
            "issue_categories": ["proof_route_too_vague"],
            "required_changes": ["Replace this probe rejection with the accepted mark."],
            "recommended_next_action": "worker_repairable",
        },
        runtime_context=proof_nl_reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL reviewer rejected mark was recorded.",
    )
    assert _field(proof_rejected_mark.value, "passed") is False
    proof_review_mark = call_tool_with_evidence(
        server,
        "proof_nl_reviewer",
        "record_proof_nl_review_passed",
        {
            "decl_name": round_fixture.decl_name,
            "summary": "Strict Runtime Matrix proof NL accepted.",
        },
        runtime_context=proof_nl_reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof NL reviewer passed mark was recorded.",
    )
    assert _field(proof_review_mark.value, "passed") is True

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
        assertion_summary="Proof file capture sync gate passed.",
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

    proof_formal_mathlib_dep = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "add_proof_mathlib_dep",
        {"decl_name": round_fixture.decl_name, "mathlib_decl_name": "True", "module": "Init.Prelude", "reason": "Proof formal closes True."},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof Formal worker added a typed Mathlib proof dependency.",
    )
    assert "True" in _decl_field(proof_formal_mathlib_dep.value, "proof_deps")
    proof_formal_removed_dep = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "remove_proof_dep",
        {"decl_name": round_fixture.decl_name, "index": 0},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof Formal worker removed a typed proof dependency.",
    )
    assert _decl_field(proof_formal_removed_dep.value, "proof_deps") == []
    proof_formal_decl_dep = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "add_proof_decl_dep",
        {"decl_name": round_fixture.decl_name, "dep_name": "supporting_statement", "reason": "Proof formal dependency probe."},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof Formal worker added a typed project proof dependency.",
    )
    assert "supporting_statement" in _decl_field(proof_formal_decl_dep.value, "proof_deps")
    proof_formal_cleared_deps = call_tool_with_evidence(
        server,
        "proof_formal_worker",
        "clear_proof_deps",
        {"decl_name": round_fixture.decl_name, "reason": "Strict ToolSweep leaves generated formal proof dependency-free."},
        runtime_context=proof_formal_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof Formal worker cleared typed proof dependencies.",
    )
    assert _decl_field(proof_formal_cleared_deps.value, "proof_deps") == []

    proof_formal_reviewer_ctx = _ctx(ws, round_fixture, view="proof_formal_reviewer", agent_type="ProofFormalReviewerAgent", role="reviewer", stage="proof_formal")
    proof_formal_reviewer_ctx = _attach_reviewer_step(ws, proof_formal_reviewer_ctx, round_fixture)
    proof_formal_rejected_mark = call_tool_with_evidence(
        server,
        "proof_formal_reviewer",
        "record_proof_formal_review_rejected",
        {
            "decl_name": round_fixture.decl_name,
            "summary": "Strict Runtime Matrix proof formal rejected before replacement.",
            "issue_categories": ["proof_not_aligned_with_proof_nl"],
            "required_changes": ["Replace this probe rejection with the accepted mark."],
            "recommended_next_action": "worker_repairable",
        },
        runtime_context=proof_formal_reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof Formal reviewer rejected mark was recorded.",
    )
    assert _field(proof_formal_rejected_mark.value, "passed") is False
    assert _field(proof_formal_rejected_mark.value, "recommended_next_action") == "worker_repairable"
    proof_formal_review_mark = call_tool_with_evidence(
        server,
        "proof_formal_reviewer",
        "record_proof_formal_review_passed",
        {
            "decl_name": round_fixture.decl_name,
            "summary": "Strict Runtime Matrix proof formal accepted.",
        },
        runtime_context=proof_formal_reviewer_ctx,
        recorder=evidence_recorder,
        assertion_summary="Proof Formal reviewer passed mark was recorded.",
    )
    assert _field(proof_formal_review_mark.value, "passed") is True

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


def _prepare_committed_source_index(ws: RuntimeMatrixWorkspace) -> None:
    source_root = ws.provider_repo / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "source.md").write_text(
        "# Strict decl stage proof source\n\n"
        "Source provenance: local strict DeclStage fixture.\n"
        "Reading order: use this main source entry for the strict Runtime Matrix declaration.\n"
        "The strict Runtime Matrix declaration states True.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    material = ws.runtime.material
    resolved = material.resolve_source_scope(ws.provider_repo, source_scope=SourceScope(mode="all"))
    assert resolved.ok and resolved.value is not None, resolved.issues
    opened = material.open_source_index_update(
        ws.provider_repo,
        resolved_scope=resolved.value,
        index_policy="auto",
    )
    assert opened.ok and opened.value is not None, opened.issues
    assert material.set_source_index_overview(
        ws.provider_repo,
        overview="Strict decl stage proof source index.",
    ).ok
    block = material.create_source_block(
        ws.provider_repo,
        parent_id="root",
        kind="proof",
        title="Strict proof source",
        summary="Strict proof source summary.",
    )
    assert block.ok and block.value is not None, block.issues
    ref = ws.runtime.material.add_source_block_ref(
        ws.provider_repo,
        block_id=block.value.block_id,
        path="source.md",
        start_line=1,
        end_line=1,
        role="main",
    )
    assert ref.ok, ref.issues
    assert material.mark_block_refs_done(
        ws.provider_repo, block_id=block.value.block_id
    ).ok
    assert material.mark_block_links_done(
        ws.provider_repo, block_id=block.value.block_id
    ).ok
    assert material.mark_block_completed(
        ws.provider_repo, block_id=block.value.block_id
    ).ok
    assert material.set_file_survey_status(
        ws.provider_repo,
        path="source.md",
        status="surveyed",
        summary="Read in full.",
    ).ok
    assert material.set_file_indexing_status(
        ws.provider_repo, path="source.md", status="indexed"
    ).ok
    validated = material.validate_source_index_update(
        ws.provider_repo,
        baseline_index=None,
        expected_baseline_digest=opened.value.baseline_digest,
        resolved_scope=resolved.value.resolved_file_paths,
        require_completed=True,
    )
    assert validated.ok and validated.value is not None, validated.issues
    assert validated.value.gate.passed, validated.value.gate.issues
    committed = material.commit_source_index_update(
        ws.provider_repo, validated=validated.value
    )
    assert committed.ok and committed.value is not None, committed.issues


def _attach_reviewer_step(
    ws: RuntimeMatrixWorkspace,
    ctx: RuntimeToolContext,
    round_fixture: DeclRoundFixture,
) -> RuntimeToolContext:
    scope_id = f"repo:Provider:node:{round_fixture.node_path}"
    flow_id = ws.runtime.ark.flow_service.start_flow(
        FlowRequest(
            flow_type="content_node_task",
            scope_id=scope_id,
            params={
                "repo_key": "Provider",
                "repo_path": str(ws.provider_repo),
                "node_path": round_fixture.node_path,
                "contract_version": 1,
                "task_mode": "run",
            },
        ),
        enqueue=False,
    )
    step_id = f"strict_runtime_matrix_{ctx.expected_view_key}_step"
    step = DeclStageReviewerAgentStep(
        step_id=step_id,
        flow_id=flow_id,
        scope_id=scope_id,
        state=DeclStageReviewerStepState(
            agent_role=ctx.expected_view_key or "decl_stage_reviewer",
            agent_type=ctx.agent_type or "DeclStageReviewerAgent",
        ),
    )
    ws.runtime.ark.step_service.create_step(step, enqueue=False)
    return ctx.model_copy(update={"flow_id": flow_id, "step_id": step_id, "scope_id": scope_id})


def _field(value: Any, *path: str) -> Any:
    current = value
    for item in path:
        if isinstance(current, dict):
            current = current[item]
        else:
            current = getattr(current, item)
    return current


def _decl_field(value: Any, *path: str) -> Any:
    return _field(value, "decl", *path)
