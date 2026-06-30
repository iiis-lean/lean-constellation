from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclRoundResultKind, DeclRoundStatus, DeclStrategyStatus


def _create_content_node(tmp_path: Path, *, node_path: str = "Main.Topic.Core") -> None:
    runtime = make_runtime()
    assert runtime.node.node_tree.ensure_root_scope_node(tmp_path).ok
    assert runtime.node.create_scope_node(
        tmp_path,
        path="Main.Topic",
        goal="Topic goal",
        boundary="Topic boundary",
    ).ok
    assert runtime.node.create_content_node(
        tmp_path,
        path=node_path,
        goal="Core goal",
        boundary="Core boundary",
        objective="Build the core declarations.",
        success_criteria="The core declarations are ready.",
    ).ok


def test_ensure_open_strategy_creates_and_reuses_open_strategy(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph

    first = service.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Try a bottom-up lemma strategy.",
        rationale="Start from the easiest supporting lemmas.",
    )
    second = service.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="This should not replace the open strategy.",
    )

    assert first.ok and first.value is not None
    assert second.ok and second.value is not None
    assert second.value.strategy_id == first.value.strategy_id
    assert second.value.objective == "Try a bottom-up lemma strategy."

    index = service.get_decl_graph_index(tmp_path, node_path="Main.Topic.Core")
    assert index.ok and index.value is not None
    assert index.value.strategy_ids == [first.value.strategy_id]


def test_closing_strategy_allows_new_open_strategy(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    first = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="First strategy.")
    assert first.ok and first.value is not None

    closed = service.close_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=first.value.strategy_id,
        summary="The first strategy reached its intended checkpoint.",
        reason="checkpoint reached",
    )
    assert closed.ok and closed.value is not None
    assert closed.value.status == DeclStrategyStatus.CLOSED

    second = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Second strategy.")
    assert second.ok and second.value is not None
    assert second.value.strategy_id != first.value.strategy_id

    strategies = service.list_strategies(tmp_path, node_path="Main.Topic.Core")
    assert strategies.ok and strategies.value is not None
    assert [item.status for item in strategies.value].count(DeclStrategyStatus.OPEN) == 1


def test_round_draft_start_summary_and_success_terminal(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Prove core theorem.")
    assert strategy.ok and strategy.value is not None

    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Create and prove two declarations.",
        change_ids=["change_a", "change_b"],
    )
    assert round_record.ok and round_record.value is not None
    assert round_record.value.round_index == 1
    assert round_record.value.status == DeclRoundStatus.DRAFT

    started = service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id)
    assert started.ok and started.value is not None
    assert started.value.status == DeclRoundStatus.RUNNING

    one_summary = service.write_decl_change_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        change_id="change_a",
        summary="Created the supporting lemma.",
    )
    assert one_summary.ok

    missing = service.write_round_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        summary="Round summary should wait for all change summaries.",
    )
    assert not missing.ok
    assert missing.issues[0].kind == "decl_change_summary_missing"

    assert service.write_decl_change_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        change_id="change_b",
        summary="Updated the target theorem.",
    ).ok
    assert service.write_round_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        summary="Both declarations were completed.",
    ).ok

    terminal = service.mark_round_terminal(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    )

    assert terminal.ok and terminal.value is not None
    assert terminal.value.status == DeclRoundStatus.COMPLETED
    assert terminal.value.result_kind == DeclRoundResultKind.SUCCESS

    reloaded = make_runtime().decl_graph.get_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
    )
    assert reloaded.ok and reloaded.value is not None
    assert reloaded.value.summary == "Both declarations were completed."


def test_round_terminal_requires_summary_and_blocked_reason(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Investigate proof.")
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Try one update.",
    )
    assert round_record.ok and round_record.value is not None

    no_summary = service.mark_round_terminal(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.BLOCKED,
        reason="Need a provider repo.",
    )
    assert not no_summary.ok
    assert no_summary.issues[0].kind == "round_summary_missing"

    assert service.write_round_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        summary="No changes were executed.",
    ).ok

    no_reason = service.mark_round_terminal(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.BLOCKED,
    )
    assert not no_reason.ok
    assert no_reason.issues[0].kind == "round_terminal_reason_required"

    blocked = service.mark_round_terminal(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.BLOCKED,
        reason="Need a provider repo.",
    )
    assert blocked.ok and blocked.value is not None
    assert blocked.value.status == DeclRoundStatus.BLOCKED
    assert blocked.value.result_reason == "Need a provider repo."


def test_round_start_rejects_second_running_round(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(tmp_path, node_path="Main.Topic.Core", objective="Parallel attempt.")
    assert strategy.ok and strategy.value is not None
    first = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="First round.",
    )
    second = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Second round.",
    )
    assert first.ok and first.value is not None
    assert second.ok and second.value is not None
    assert service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=first.value.round_id).ok

    blocked = service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=second.value.round_id)

    assert not blocked.ok
    assert blocked.issues[0].kind == "round_already_running"
