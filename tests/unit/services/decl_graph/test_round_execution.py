from __future__ import annotations

from pathlib import Path

from lean_constellation.services.decl_graph import DeclDraftSpec
from tests.unit.flows.decl_round._helpers import NODE_PATH, make_decl_round_runtime


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
