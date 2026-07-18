from __future__ import annotations

from pathlib import Path

from lean_constellation.services.decl_graph import DeclDraftSpec, DeclState
from tests.unit.flows.decl_round._helpers import (
    NODE_PATH,
    create_round_with_decl,
    make_decl_round_runtime,
    seed_committed_theorem,
)
from tests.unit_services_helpers import lean_check_payload, write_statement_formal_for_test


def test_round_with_decl_drafts_rolls_back_complete_graph_on_mid_batch_failure(tmp_path: Path, monkeypatch) -> None:
    _flow_runtime, runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Transactional strategy.")
    assert strategy.ok and strategy.value is not None
    real_create = runtime.decl_graph.create_decl_revision_view
    calls = 0

    def injected_create(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal calls
        calls += 1
        if calls == 2:
            return runtime.foundation.fail(runtime.foundation.issue("injected_decl_failure", "injected"))
        return real_create(*args, **kwargs)

    monkeypatch.setattr(runtime.decl_graph, "create_decl_revision_view", injected_create)
    result = runtime.decl_graph.create_round_with_decl_drafts(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Atomic round.",
        declarations=[
            DeclDraftSpec(name="first_decl", kind="definition", objective="First.", summary="First."),
            DeclDraftSpec(name="second_decl", kind="definition", objective="Second.", summary="Second."),
        ],
    )

    assert not result.ok
    assert result.issues[0].kind == "round_decl_transaction_failed"
    rounds = runtime.decl_graph.list_rounds(repo_root, node_path=NODE_PATH)
    decls = runtime.decl_graph.list_decls(repo_root, node_path=NODE_PATH)
    assert rounds.ok and rounds.value == []
    assert decls.ok and decls.value == []


def test_round_with_decl_drafts_returns_structured_batch(tmp_path: Path) -> None:
    _flow_runtime, runtime, repo_root = make_decl_round_runtime(tmp_path)
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Transactional strategy.")
    assert strategy.ok and strategy.value is not None

    result = runtime.decl_graph.create_round_with_decl_drafts(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Atomic round.",
        declarations=[DeclDraftSpec(name="main_def", kind="definition", objective="Create it.", summary="A definition.")],
    )

    assert result.ok, result.issues
    assert result.value is not None
    assert [item.decl_name for item in result.value.declarations] == ["main_def"]


def test_blocked_business_terminal_commits_partial_revision_and_allows_follow_up_update(tmp_path: Path) -> None:
    _flow_runtime, runtime, repo_root = make_decl_round_runtime(tmp_path)
    _strategy_id, round_id, _round_index = create_round_with_decl(
        runtime,
        repo_root,
        decl_name="main_result",
        target_state=DeclState.PROVED,
    )
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok

    closed = runtime.decl_graph.closeout_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        outcome="blocked",
    )

    assert closed.ok, closed.issues
    assert closed.value is not None
    assert closed.value.committed_decl_names == ["main_result"]
    revision = runtime.decl_graph.get_decl_revision(repo_root, node_path=NODE_PATH, name="main_result", revision=1)
    assert revision.ok and revision.value is not None
    assert revision.value.status == "committed"
    assert revision.value.state == DeclState.PLANNED
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Continue.")
    assert strategy.ok and strategy.value is not None
    follow_up = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Continue the partial declaration.",
    )
    assert follow_up.ok and follow_up.value is not None
    update = runtime.decl_graph.open_decl_update(
        repo_root,
        node_path=NODE_PATH,
        round_id=follow_up.value.round_id,
        name="main_result",
        objective="Resume from the achieved state.",
        target_state=DeclState.PROVED,
    )
    assert update.ok, update.issues
    assert update.value is not None
    assert update.value.base_revision == 1
    assert update.value.reset_to_state == DeclState.PLANNED


def test_failed_business_terminal_commits_partial_revision(tmp_path: Path) -> None:
    _flow_runtime, runtime, repo_root = make_decl_round_runtime(tmp_path)
    _strategy_id, round_id, _round_index = create_round_with_decl(
        runtime,
        repo_root,
        decl_name="main_result",
        target_state=DeclState.PROVED,
    )
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok

    closed = runtime.decl_graph.closeout_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        outcome="failed",
    )

    assert closed.ok, closed.issues
    assert closed.value is not None
    assert closed.value.committed_decl_names == ["main_result"]
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name="main_result",
        revision=1,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.status == "committed"
    assert revision.value.state == DeclState.PLANNED


def test_blocked_delete_commits_obsolete_revision_without_deleting_decl_lifecycle(tmp_path: Path) -> None:
    _flow_runtime, runtime, repo_root = make_decl_round_runtime(tmp_path)
    seed_committed_theorem(runtime, repo_root, decl_name="old_result")
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Delete it.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Delete old_result.",
    )
    assert round_record.ok and round_record.value is not None
    deleted = runtime.decl_graph.mark_decl_delete(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name="old_result",
        objective="Delete old_result.",
    )
    assert deleted.ok, deleted.issues
    assert runtime.decl_graph.start_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
    ).ok

    closed = runtime.decl_graph.closeout_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        outcome="blocked",
    )

    assert closed.ok, closed.issues
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name="old_result",
        revision=2,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.status == "committed"
    assert revision.value.state == DeclState.OBSOLETE
    decl = runtime.decl_graph.get_decl(repo_root, node_path=NODE_PATH, name="old_result")
    assert decl.ok and decl.value is not None
    assert decl.value.lifecycle == "active"


def test_completed_delete_commits_obsolete_revision_and_deletes_decl_lifecycle(tmp_path: Path) -> None:
    _flow_runtime, runtime, repo_root = make_decl_round_runtime(tmp_path)
    seed_committed_theorem(runtime, repo_root, decl_name="old_result")
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Delete it.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Delete old_result.",
    )
    assert round_record.ok and round_record.value is not None
    deleted = runtime.decl_graph.mark_decl_delete(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name="old_result",
        objective="Delete old_result.",
    )
    assert deleted.ok, deleted.issues
    assert runtime.decl_graph.start_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
    ).ok

    closed = runtime.decl_graph.closeout_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        outcome="completed",
    )

    assert closed.ok, closed.issues
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name="old_result",
        revision=2,
    )
    assert revision.ok and revision.value is not None
    assert revision.value.status == "committed"
    assert revision.value.state == DeclState.OBSOLETE
    decl = runtime.decl_graph.get_decl(repo_root, node_path=NODE_PATH, name="old_result")
    assert decl.ok and decl.value is not None
    assert decl.value.lifecycle == "deleted"


def test_final_audit_accepts_same_node_dependency_from_earlier_committed_round(tmp_path: Path) -> None:
    _flow_runtime, runtime, repo_root = make_decl_round_runtime(tmp_path)
    seed_committed_theorem(runtime, repo_root, decl_name="supporting_result")
    _strategy_id, round_id, _round_index = create_round_with_decl(
        runtime,
        repo_root,
        decl_name="dependent_result",
        target_state=DeclState.DECLARED,
    )
    started = runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id)
    assert started.ok, started.issues
    statement_nl = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="dependent_result",
        nl="The dependent result uses the earlier committed declaration.",
        deps=["supporting_result"],
    )
    assert statement_nl.ok, statement_nl.issues
    advanced_nl = runtime.decl_graph.advance_stage_state(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage="statement_nl",
        decl_names=["dependent_result"],
    )
    assert advanced_nl.ok, advanced_nl.issues
    statement_formal = write_statement_formal_for_test(runtime,
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name="dependent_result",
        lean_code="theorem dependent_result : True := by trivial",
        lean_check=lean_check_payload(),
    )
    assert statement_formal.ok, statement_formal.issues
    advanced_formal = runtime.decl_graph.advance_stage_state(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        stage="statement_formal",
        decl_names=["dependent_result"],
    )
    assert advanced_formal.ok, advanced_formal.issues

    audit = runtime.decl_graph.audit_round_final(repo_root, node_path=NODE_PATH, round_id=round_id)

    assert audit.ok, audit.issues
    assert audit.value is not None
    assert audit.value.passed is True
    closed = runtime.decl_graph.closeout_round(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        outcome="completed",
    )
    assert closed.ok, closed.issues
