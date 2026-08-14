from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lean_constellation.mcp import create_mcp_server
from lean_constellation.services.decl_graph import DeclRoundResultKind, DeclStage, DeclState
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.runtime_matrix.admin_helpers import unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import CONTENT_NODE_PATH, RuntimeMatrixWorkspace
from tests.real.runtime_matrix.strict.tool_sweep_partitions import decl_graph_tool_sweep_names
from tests.real.runtime_matrix.strict_helpers import call_tool_with_evidence, checkpoint_with_evidence, restore_with_evidence
from tests.unit_services_helpers import lean_check_payload, write_proof_formal_for_test, write_statement_formal_for_test


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_decl_graph_strategy_round_readiness_tool_cases_execute(
    runtime_matrix_workspace: RuntimeMatrixWorkspace,
    evidence_recorder: EvidenceRecorder,
    tmp_path: Path,
) -> None:
    ws = runtime_matrix_workspace
    ws.setup_content_node(repo_root=ws.provider_repo, node_path=CONTENT_NODE_PATH)
    _seed_ready_decl(ws, "ready_public_result", public=True)
    _seed_ready_decl(ws, "existing_result", public=False)
    _seed_ready_decl(ws, "delete_result", public=False)
    assert ws.runtime.node.interface.add_interface(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        name="strict_public_result",
        kind="theorem",
        summary="Strict ToolSweep current Content interface.",
        actor="coordinator",
    ).ok

    server = unwrap(create_mcp_server(ws.runtime, view_keys=["content_plan"]))
    plan_ctx = _ctx(ws)
    checkpoint = checkpoint_with_evidence(
        ws.admin,
        ws.provider_repo,
        scope_ids=["repo:Provider"],
        label="strict_decl_graph_tool_sweep",
        recorder=evidence_recorder,
    )

    store = call_tool_with_evidence(
        server,
        "content_plan",
        "ensure_current_decl_graph",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="DeclGraph store exists for the current content node.",
    )
    assert _field(store.value, "node_path") == CONTENT_NODE_PATH

    index = call_tool_with_evidence(
        server,
        "content_plan",
        "get_current_decl_graph_index",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="DeclGraph index was read.",
    )
    assert "ready_public_result" in _field(index.value, "decl_names")

    store_view = call_tool_with_evidence(
        server,
        "content_plan",
        "get_current_decl_graph_store",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="DeclGraph store view was read.",
    )
    assert _field(store_view.value, "decl_count") >= 3

    rebuilt_index = call_tool_with_evidence(
        server,
        "content_plan",
        "rebuild_current_decl_graph_index",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="DeclGraph index was rebuilt.",
    )
    assert "existing_result" in _field(rebuilt_index.value, "decl_names")

    strategy = call_tool_with_evidence(
        server,
        "content_plan",
        "ensure_open_decl_strategy",
        {"objective": "Strict Runtime Matrix DeclGraph ToolSweep strategy.", "rationale": "Cover strategy and round tools."},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Open declaration strategy was ensured.",
    )
    strategy_id = _field(strategy.value, "strategy_id")
    assert strategy_id

    strategies = call_tool_with_evidence(
        server,
        "content_plan",
        "list_decl_strategies",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Declaration strategies were listed.",
    )
    assert any(_field(item, "strategy_id") == strategy_id for item in _as_items(strategies.value))

    strategy_lookup = call_tool_with_evidence(
        server,
        "content_plan",
        "get_decl_strategy",
        {"strategy_id": strategy_id},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Declaration strategy was loaded.",
    )
    assert _field(strategy_lookup.value, "strategy_id") == strategy_id

    round_record = call_tool_with_evidence(
        server,
        "content_plan",
        "create_decl_round_draft",
        {"strategy_id": strategy_id, "objective": "Plan create, update, and delete changes."},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Declaration round draft was created.",
    )
    round_id = _field(round_record.value, "round_id")
    assert _field(round_record.value, "status") == "draft"

    create_change = call_tool_with_evidence(
        server,
        "content_plan",
        "plan_create_decl",
        {
            "round_id": round_id,
            "decl_name": "created_result",
            "kind": "theorem",
            "objective": "Create a new strict ToolSweep theorem.",
            "summary": "Strict ToolSweep created theorem.",
            "public": False,
            "target_state": "declared",
        },
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Declaration create change was planned.",
    )
    create_change_id = _field(create_change.value, "change_id")

    update_change = call_tool_with_evidence(
        server,
        "content_plan",
        "plan_update_decl",
        {
            "round_id": round_id,
            "decl_name": "existing_result",
            "objective": "Open a strict ToolSweep update revision.",
            "target_state": "proved",
            "start_stage": "proof_nl",
        },
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Declaration update change was planned.",
    )
    update_change_id = _field(update_change.value, "change_id")

    rounds = call_tool_with_evidence(
        server,
        "content_plan",
        "list_decl_rounds",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Declaration rounds were listed.",
    )
    assert any(_field(item, "round_id") == round_id for item in _as_items(rounds.value))

    round_lookup = call_tool_with_evidence(
        server,
        "content_plan",
        "get_decl_round",
        {"round_id": round_id},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Declaration round was loaded.",
    )
    assert {item["change_id"] for item in _field(round_lookup.value, "revision_refs")} == {
        create_change_id,
        update_change_id,
    }

    decls = call_tool_with_evidence(
        server,
        "content_plan",
        "list_current_node_decls",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current declarations were listed.",
    )
    assert any(_field(item, "name") == "created_result" for item in _as_items(decls.value))

    created_decl = call_tool_with_evidence(
        server,
        "content_plan",
        "inspect_current_node_decl",
        {"decl_name": "created_result"},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Created declaration was loaded.",
    )
    assert _field(created_decl.value, "decl_name") == "created_result"

    revision = call_tool_with_evidence(
        server,
        "content_plan",
        "inspect_current_node_decl",
        {"decl_name": "existing_result", "revision": 2},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Update declaration revision was loaded.",
    )
    assert _field(revision.value, "revision") == 2

    delete_closure = call_tool_with_evidence(
        server,
        "content_plan",
        "preview_decl_delete_closure",
        {"decl_names": ["delete_result"]},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Delete closure was computed.",
    )
    assert "delete_result" in _field(delete_closure.value, "closure_decl_names")
    deleted = call_tool_with_evidence(
        server,
        "content_plan",
        "delete_decls",
        {"decl_names": ["delete_result"]},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Exact declaration closure was deleted synchronously.",
    )
    assert _field(deleted.value, "deleted_decl_names") == ["delete_result"]

    draft_gate = call_tool_with_evidence(
        server,
        "content_plan",
        "validate_decl_round_draft",
        {"round_id": round_id},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Draft round gate passed for planned changes.",
    )
    assert _field(draft_gate.value, "passed") is True

    dependency_closure = call_tool_with_evidence(
        server,
        "content_plan",
        "compute_current_node_decl_dependency_closure",
        {"decl_names": ["ready_public_result"]},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Dependency closure was computed for a ready public decl.",
    )
    assert "ready_public_result" in str(dependency_closure.value)

    active_names = call_tool_with_evidence(
        server,
        "content_plan",
        "list_active_decl_names",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Active declaration names were listed.",
    )
    assert "ready_public_result" in _as_items(active_names.value)

    interface_binding = call_tool_with_evidence(
        server,
        "content_plan",
        "bind_current_node_interface",
        {"interface_name": "strict_public_result", "decl_name": "ready_public_result"},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current Content interface was bound to a public ready declaration on the same node.",
    )
    assert _field(interface_binding.value, "changed") is True
    assert _field(_field(interface_binding.value, "bound_decl"), "node") == CONTENT_NODE_PATH
    assert _field(_field(interface_binding.value, "bound_decl"), "name") == "ready_public_result"

    content_ready = call_tool_with_evidence(
        server,
        "content_plan",
        "check_current_content_node_completion",
        {},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Current content-node completion view was checked.",
    )
    assert _field(content_ready.value, "checked_decl_count") >= 1
    assert "blocking_issue_kinds" in content_ready.value

    for change_id in (create_change_id, update_change_id):
        summarized = call_tool_with_evidence(
            server,
            "content_plan",
            "write_decl_change_summary",
            {"round_id": round_id, "change_id": change_id, "summary": f"Strict ToolSweep summarized {change_id}."},
            runtime_context=plan_ctx,
            recorder=evidence_recorder,
            assertion_summary=f"Decl change summary was written for {change_id}.",
        )
        assert _field(summarized.value, "round_id") == round_id

    round_summary = call_tool_with_evidence(
        server,
        "content_plan",
        "write_decl_round_summary",
        {"round_id": round_id, "summary": "Strict ToolSweep round summary."},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Decl round summary was written.",
    )
    assert _field(round_summary.value, "summary") == "Strict ToolSweep round summary."

    started = ws.runtime.decl_graph.start_round(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_id,
    )
    assert started.ok, started.issues
    recorded = ws.runtime.decl_graph.record_round_execution_result(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_id,
        outcome="blocked",
        reason="Strict ToolSweep intentionally leaves the planned revisions unexecuted.",
    )
    assert recorded.ok, recorded.issues
    terminal = call_tool_with_evidence(
        server,
        "content_plan",
        "mark_decl_round_terminal",
        {
            "round_id": round_id,
            "result_kind": "blocked",
            "reason": "Strict ToolSweep intentionally leaves the planned revisions unexecuted.",
        },
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Unexecuted ToolSweep changes were closed as a terminal blocked round.",
    )
    assert _field(terminal.value, "closeout_complete") is True
    assert _field(terminal.value, "result_kind") == "blocked"

    restored = call_tool_with_evidence(
        server,
        "content_plan",
        "restore_decl_revision",
        {"decl_name": "existing_result", "source_revision": 1},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Historical accepted content was restored as a new monotonic revision.",
    )
    assert _field(restored.value, "source_revision") == 1

    discard_round = call_tool_with_evidence(
        server,
        "content_plan",
        "create_decl_round_draft",
        {"strategy_id": strategy_id, "objective": "Exercise atomic rejected-draft rollback."},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="An isolated declaration round draft was created for discard coverage.",
    )
    discard_round_id = _field(discard_round.value, "round_id")
    discarded_change = call_tool_with_evidence(
        server,
        "content_plan",
        "plan_create_decl",
        {
            "round_id": discard_round_id,
            "decl_name": "discarded_result",
            "kind": "theorem",
            "objective": "Create a declaration that must disappear with the rejected draft.",
            "summary": "Strict ToolSweep discarded theorem.",
            "public": False,
            "target_state": "declared",
        },
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="A planned create revision was attached to the isolated discard draft.",
    )
    discarded_change_id = _field(discarded_change.value, "change_id")
    discard_receipt = call_tool_with_evidence(
        server,
        "content_plan",
        "discard_decl_round_draft",
        {"round_id": discard_round_id},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="The unsubmitted draft and its planned declaration revision were atomically discarded.",
    )
    assert _field(discard_receipt.value, "changed") is True
    assert _field(discard_receipt.value, "discarded_change_ids") == [discarded_change_id]
    assert _field(discard_receipt.value, "deleted_created_decl_names") == ["discarded_result"]
    assert not ws.runtime.decl_graph.get_decl(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        name="discarded_result",
    ).ok
    discarded_round = unwrap(
        ws.runtime.decl_graph.get_round(
            ws.provider_repo,
            node_path=CONTENT_NODE_PATH,
            round_id=discard_round_id,
        )
    )
    assert discarded_round.status.value == "discarded"
    assert discarded_round.revision_refs == []

    closed_strategy = call_tool_with_evidence(
        server,
        "content_plan",
        "close_decl_strategy",
        {"strategy_id": strategy_id, "summary": "Strict ToolSweep strategy closed.", "reason": "ToolSweep complete.", "failed": True},
        runtime_context=plan_ctx,
        recorder=evidence_recorder,
        assertion_summary="Decl strategy was closed.",
    )
    assert _field(closed_strategy.value, "status") == "failed"

    restore_with_evidence(
        ws.admin,
        ws.provider_repo,
        checkpoint.snapshot_id,
        scope_ids=["repo:Provider"],
        label="strict_decl_graph_tool_sweep",
        recorder=evidence_recorder,
    )
    assert not ws.runtime.decl_graph.get_decl(ws.provider_repo, node_path=CONTENT_NODE_PATH, name="created_result").ok
    restored_existing = unwrap(ws.runtime.decl_graph.get_decl(ws.provider_repo, node_path=CONTENT_NODE_PATH, name="existing_result"))
    assert restored_existing.revision_ids == [1]

    assert decl_graph_tool_sweep_names() <= evidence_recorder.evidence.application_tool_names
    evidence_recorder.add_note("strict_decl_graph_tool_sweep_completed")
    evidence_recorder.export_json(tmp_path / "runtime_matrix_evidence" / "decl_graph_tool_sweep.json")
    evidence_recorder.export_markdown_summary(tmp_path / "runtime_matrix_evidence" / "decl_graph_tool_sweep.md")


def _ctx(ws: RuntimeMatrixWorkspace) -> RuntimeToolContext:
    return RuntimeToolContext(
        flow_id="strict_runtime_matrix_decl_graph",
        step_id="strict_runtime_matrix_decl_graph_step",
        agent_id="strict_runtime_matrix_decl_graph_agent",
        agent_type="ContentPlanAgent",
        agent_role="plan",
        expected_view_key="content_plan",
        repo_root=ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        node_kind="content",
        contract_version=1,
    )


def _seed_ready_decl(ws: RuntimeMatrixWorkspace, name: str, *, public: bool) -> None:
    strategy = ws.runtime.decl_graph.ensure_open_strategy(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        objective="Seed committed declaration for strict DeclGraph ToolSweep.",
    )
    assert strategy.ok and strategy.value is not None, strategy.issues
    round_record = ws.runtime.decl_graph.create_round_draft(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective=f"Seed {name}.",
    )
    assert round_record.ok and round_record.value is not None, round_record.issues
    created = ws.runtime.decl_graph.create_decl(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        name=name,
        kind="theorem",
        objective=f"Create {name}.",
            summary=f"{name} summary.",
            public=public,
            target_state=DeclState.PROVED,
            require_target_state_satisfied=False,
        )
    assert created.ok and created.value is not None, created.issues
    assert ws.runtime.decl_graph.start_round(ws.provider_repo, node_path=CONTENT_NODE_PATH, round_id=round_record.value.round_id).ok
    assert ws.runtime.decl_graph.write_statement_nl(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=name,
        nl=f"{name} states True.",
        origin=[{"kind": "runtime_matrix_strict", "ref": name}],
        deps=[],
    ).ok
    assert write_statement_formal_for_test(ws.runtime,
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  sorry",
        lean_check=_passed_statement_check(),
        deps=[],
    ).ok
    assert ws.runtime.decl_graph.write_proof_nl(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=name,
        nl="Use triviality.",
        origin=[{"kind": "runtime_matrix_strict", "ref": f"{name}:proof"}],
        deps=[],
    ).ok
    assert write_proof_formal_for_test(ws.runtime,
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=name,
        lean_code=f"theorem {name} : True := by\n  trivial",
        lean_check=_passed_proof_check(),
        deps=[],
    ).ok
    reviewed = ws.runtime.decl_graph.record_decl_review(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        stage=DeclStage.PROOF_FORMAL,
        decl_name=name,
        passed=True,
        summary=f"{name} proof accepted.",
    )
    assert reviewed.ok, reviewed.issues
    assert reviewed.value is not None
    submitted = ws.runtime.decl_graph.aggregate_stage_review_marks(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        stage=DeclStage.PROOF_FORMAL,
        summary=f"{name} proof stage accepted.",
        marks=[reviewed.value],
    )
    assert submitted.ok, submitted.issues
    advanced = ws.runtime.decl_graph.advance_stage_state(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        stage=DeclStage.PROOF_FORMAL,
        decl_names=[name],
    )
    assert advanced.ok and advanced.value is not None, advanced.issues
    assert advanced.value == [name]
    assert ws.runtime.decl_graph.write_decl_change_summary(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        change_id=created.value.change_id,
        summary=f"{name} seeded.",
    ).ok
    assert ws.runtime.decl_graph.write_round_summary(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        summary=f"{name} seed round complete.",
    ).ok
    assert ws.runtime.decl_graph.record_round_execution_result(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        outcome="completed",
    ).ok
    terminal = ws.runtime.decl_graph.mark_round_terminal(
        ws.provider_repo,
        node_path=CONTENT_NODE_PATH,
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    )
    assert terminal.ok, terminal.issues
    assert ws.runtime.decl_graph.rebuild_decl_graph_index(ws.provider_repo, node_path=CONTENT_NODE_PATH).ok


def _passed_statement_check() -> dict[str, object]:
    return lean_check_payload(contains_sorry=True, allow_sorry=True)


def _passed_proof_check() -> dict[str, object]:
    return lean_check_payload()


def _field(value: Any, *path: str) -> Any:
    current = value
    for item in path:
        if isinstance(current, dict):
            current = current[item]
        else:
            current = getattr(current, item)
    return current


def _as_items(value: Any) -> list[Any]:
    if isinstance(value, dict) and "items" in value:
        return list(value["items"])
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return list(value)
