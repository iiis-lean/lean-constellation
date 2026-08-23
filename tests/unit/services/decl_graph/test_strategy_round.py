import json
from pathlib import Path

from tests.unit_services_helpers import initialize_native_test_repo, make_runtime

from lean_constellation.services.decl_graph import (
    DeclGraphRound,
    DeclGraphStrategy,
    DeclRoundResultKind,
    DeclRoundStatus,
    DeclState,
    DeclStrategyStatus,
)
from lean_constellation.services.foundation import WriteMode


def _create_content_node(tmp_path: Path, *, node_path: str = "Main.Topic.Core") -> None:
    initialize_native_test_repo(tmp_path)
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
    )
    assert round_record.ok and round_record.value is not None
    assert round_record.value.round_index == 1
    assert round_record.value.status == DeclRoundStatus.DRAFT
    change_a = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="supporting_lemma",
        kind="lemma",
        objective="Create the supporting lemma.",
        summary="Supporting lemma.",
        target_state=DeclState.DECLARED,
    )
    assert change_a.ok and change_a.value is not None, change_a.issues
    change_b = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="main_result",
        kind="theorem",
        objective="Create the target theorem.",
        summary="Target theorem.",
        target_state=DeclState.DECLARED,
    )
    assert change_b.ok and change_b.value is not None, change_b.issues
    round_record = service.get_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id)
    assert round_record.ok and round_record.value is not None
    assert round_record.value.change_ids == [change_a.value.change_id, change_b.value.change_id]

    started = service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=round_record.value.round_id)
    assert started.ok and started.value is not None
    assert started.value.status == DeclRoundStatus.RUNNING

    one_summary = service.write_decl_change_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        change_id=change_a.value.change_id,
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
        change_id=change_b.value.change_id,
        summary="Updated the target theorem.",
    ).ok
    assert service.write_round_summary(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        summary="Both declarations were completed.",
    ).ok
    recorded = service.strategy_round.record_round_execution_result(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    )
    assert recorded.ok, recorded.issues

    terminal = service.mark_round_terminal(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    )

    assert terminal.ok and terminal.value is not None
    assert terminal.value.status == DeclRoundStatus.COMMITTED
    assert terminal.value.result_kind == DeclRoundResultKind.SUCCESS
    assert terminal.value.committed_at is not None
    raw_round = json.loads(
        service.graph_store.round_path(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
        ).read_text(encoding="utf-8")
    )
    assert raw_round["status"] == "committed"
    assert raw_round["result_kind"] == "success"
    assert raw_round["committed_at"] is not None
    assert raw_round["execution_result_kind"] == "success"
    assert raw_round["execution_completed_at"] is not None
    assert raw_round["plan_closeout_acknowledged_at"] is not None

    reloaded = make_runtime().decl_graph.get_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
    )
    assert reloaded.ok and reloaded.value is not None
    assert reloaded.value.summary == "Both declarations were completed."


def test_failed_agent_step_marker_can_be_reopened_without_reverting_round_truth(
    tmp_path: Path,
) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Resume a failed worker without rebuilding the round.",
    )
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Keep the current declaration revisions.",
    )
    assert round_record.ok and round_record.value is not None
    created = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="restart_target",
        kind="theorem",
        objective="Create the target declaration.",
        summary="Restart target.",
        target_state=DeclState.DECLARED,
    )
    assert created.ok and created.value is not None
    started = service.start_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
    )
    assert started.ok and started.value is not None
    revision_refs = list(started.value.revision_refs)
    failed_step_id = "decl_stage_worker_failed"
    failed = service.strategy_round.record_round_execution_result(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        result_kind=DeclRoundResultKind.FAILED,
        reason=(
            f"Step {failed_step_id} failed before DeclGraph round completion: "
            "stream disconnected before completion"
        ),
    )
    assert failed.ok, failed.issues

    wrong_step = service.reopen_failed_round_execution(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        failed_step_id="different_step",
    )
    assert not wrong_step.ok
    assert wrong_step.issues[0].kind == "round_failed_step_marker_mismatch"

    reopened = service.reopen_failed_round_execution(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        failed_step_id=failed_step_id,
    )

    assert reopened.ok and reopened.value is not None
    assert reopened.value.status is DeclRoundStatus.RUNNING
    assert reopened.value.execution_result_kind is None
    assert reopened.value.execution_reason is None
    assert reopened.value.execution_completed_at is None
    assert reopened.value.revision_refs == revision_refs


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
    assert blocked.value.status == DeclRoundStatus.COMMITTED
    assert blocked.value.result_kind == DeclRoundResultKind.BLOCKED
    assert blocked.value.result_reason == "Need a provider repo."


def test_round_draft_rejects_second_unfinished_round(tmp_path: Path) -> None:
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
    assert not second.ok
    assert second.issues[0].kind == "round_closeout_pending"
    assert service.start_round(tmp_path, node_path="Main.Topic.Core", round_id=first.value.round_id).ok


def test_current_strategy_resolver_distinguishes_missing_unique_and_ambiguous(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    node_path = "Main.Topic.Core"

    missing = service.require_current_open_strategy(tmp_path, node_path=node_path)
    assert not missing.ok
    assert missing.issues[0].kind == "current_open_strategy_missing"

    first = service.ensure_open_strategy(tmp_path, node_path=node_path, objective="First strategy.")
    assert first.ok and first.value is not None
    unique = service.require_current_open_strategy(tmp_path, node_path=node_path)
    assert unique.ok and unique.value is not None
    assert unique.value.strategy_id == first.value.strategy_id

    second = DeclGraphStrategy(
        strategy_id="strategy_corrupt_second",
        node_path=node_path,
        objective="Corrupt second open strategy.",
    )
    written = runtime.foundation.store.write_json_atomic(
        service.graph_store.strategy_path(tmp_path, node_path=node_path, strategy_id=second.strategy_id),
        second,
        mode=WriteMode.CREATE_ONLY,
    )
    assert written.ok

    ambiguous = service.require_current_open_strategy(tmp_path, node_path=node_path)
    assert not ambiguous.ok
    assert ambiguous.issues[0].kind == "current_open_strategy_ambiguous"


def test_current_round_resolvers_filter_lifecycle_and_propagate_read_errors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _create_content_node(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    node_path = "Main.Topic.Core"
    strategy = service.ensure_open_strategy(tmp_path, node_path=node_path, objective="Current strategy.")
    assert strategy.ok and strategy.value is not None
    draft = service.create_round_draft(
        tmp_path,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        objective="Draft round.",
    )
    assert draft.ok and draft.value is not None

    resolved_draft = service.require_current_draft_round(tmp_path, node_path=node_path)
    resolved_unfinished = service.require_current_unfinished_round(tmp_path, node_path=node_path)
    assert resolved_draft.ok and resolved_draft.value is not None
    assert resolved_unfinished.ok and resolved_unfinished.value is not None
    assert resolved_draft.value.round_id == draft.value.round_id
    assert resolved_unfinished.value.round_id == draft.value.round_id

    assert service.start_round(tmp_path, node_path=node_path, round_id=draft.value.round_id).ok
    no_draft = service.require_current_draft_round(tmp_path, node_path=node_path)
    running = service.require_current_unfinished_round(tmp_path, node_path=node_path)
    assert not no_draft.ok
    assert no_draft.issues[0].kind == "current_draft_round_missing"
    assert running.ok and running.value is not None
    assert running.value.status is DeclRoundStatus.RUNNING

    awaiting = service.strategy_round.record_round_execution_result(
        tmp_path,
        node_path=node_path,
        round_id=draft.value.round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    )
    assert awaiting.ok
    resolved_closeout = service.require_current_awaiting_closeout_round(tmp_path, node_path=node_path)
    assert resolved_closeout.ok and resolved_closeout.value is not None
    assert resolved_closeout.value.round_id == draft.value.round_id

    expected = runtime.foundation.fail(
        runtime.foundation.issue("round_list_read_failed_for_test", "Synthetic list failure.")
    )
    monkeypatch.setattr(service.strategy_round, "list_rounds", lambda *_args, **_kwargs: expected)
    read_failure = service.require_current_unfinished_round(tmp_path, node_path=node_path)
    assert not read_failure.ok
    assert read_failure.issues[0].kind == "round_list_read_failed_for_test"


def test_multiple_current_rounds_are_ambiguous_and_mutation_gate_does_not_write(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    node_path = "Main.Topic.Core"
    strategy = service.ensure_open_strategy(tmp_path, node_path=node_path, objective="Current strategy.")
    assert strategy.ok and strategy.value is not None
    first = service.create_round_draft(
        tmp_path,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        objective="First draft.",
    )
    assert first.ok and first.value is not None
    second = DeclGraphRound(
        round_id="round_corrupt_second",
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        round_index=2,
        objective="Corrupt second draft.",
    )
    written = runtime.foundation.store.write_json_atomic(
        service.graph_store.round_path(tmp_path, node_path=node_path, round_id=second.round_id),
        second,
        mode=WriteMode.CREATE_ONLY,
    )
    assert written.ok

    draft = service.require_current_draft_round(tmp_path, node_path=node_path)
    unfinished = service.require_current_unfinished_round(tmp_path, node_path=node_path)
    assert not draft.ok and draft.issues[0].kind == "current_draft_round_ambiguous"
    assert not unfinished.ok and unfinished.issues[0].kind == "current_unfinished_round_ambiguous"

    close_attempt = service.close_strategy(
        tmp_path,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        summary="Must not close against corrupt round truth.",
    )
    assert not close_attempt.ok
    assert close_attempt.issues[0].kind == "current_unfinished_round_ambiguous"
    unchanged = service.get_strategy(
        tmp_path,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
    )
    assert unchanged.ok and unchanged.value is not None
    assert unchanged.value.status is DeclStrategyStatus.OPEN
    assert unchanged.value.summary is None


def test_mutation_gate_propagates_round_read_failure_without_writing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _create_content_node(tmp_path)
    runtime = make_runtime()
    service = runtime.decl_graph
    node_path = "Main.Topic.Core"
    strategy = service.ensure_open_strategy(tmp_path, node_path=node_path, objective="Current strategy.")
    assert strategy.ok and strategy.value is not None
    expected = runtime.foundation.fail(
        runtime.foundation.issue("round_list_read_failed_for_test", "Synthetic list failure.")
    )
    monkeypatch.setattr(service.strategy_round, "list_rounds", lambda *_args, **_kwargs: expected)

    close_attempt = service.close_strategy(
        tmp_path,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        summary="Must not close after a failed round read.",
    )
    assert not close_attempt.ok
    assert close_attempt.issues[0].kind == "round_list_read_failed_for_test"

    unchanged = service.get_strategy(
        tmp_path,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
    )
    assert unchanged.ok and unchanged.value is not None
    assert unchanged.value.status is DeclStrategyStatus.OPEN
    assert unchanged.value.summary is None


def test_strategy_and_round_sequence_resolution_is_stable_and_fail_closed(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    node_path = "Main.Topic.Core"
    first_strategy = service.ensure_open_strategy(tmp_path, node_path=node_path, objective="First strategy.")
    assert first_strategy.ok and first_strategy.value is not None
    assert service.close_strategy(
        tmp_path,
        node_path=node_path,
        strategy_id=first_strategy.value.strategy_id,
        summary="First strategy closed.",
    ).ok
    second_strategy = service.ensure_open_strategy(tmp_path, node_path=node_path, objective="Second strategy.")
    assert second_strategy.ok and second_strategy.value is not None

    sequence_one = service.get_strategy_by_sequence(tmp_path, node_path=node_path, strategy_sequence=1)
    sequence_two = service.get_strategy_by_sequence(tmp_path, node_path=node_path, strategy_sequence=2)
    assert sequence_one.ok and sequence_one.value is not None
    assert sequence_two.ok and sequence_two.value is not None
    expected_order = sorted(
        [first_strategy.value, second_strategy.value],
        key=lambda item: (item.created_at, item.strategy_id),
    )
    assert sequence_one.value.strategy_id == expected_order[0].strategy_id
    assert sequence_two.value.strategy_id == expected_order[1].strategy_id
    first_reverse = service.strategy_sequence(
        tmp_path,
        node_path=node_path,
        strategy_id=sequence_one.value.strategy_id,
    )
    second_reverse = service.strategy_sequence(
        tmp_path,
        node_path=node_path,
        strategy_id=sequence_two.value.strategy_id,
    )
    assert first_reverse.ok and first_reverse.value == 1
    assert second_reverse.ok and second_reverse.value == 2
    missing_strategy = service.get_strategy_by_sequence(tmp_path, node_path=node_path, strategy_sequence=3)
    assert not missing_strategy.ok
    assert missing_strategy.issues[0].kind == "strategy_sequence_not_found"

    round_record = service.create_round_draft(
        tmp_path,
        node_path=node_path,
        strategy_id=second_strategy.value.strategy_id,
        objective="First round in history.",
    )
    assert round_record.ok and round_record.value is not None
    by_round_sequence = service.get_round_by_sequence(tmp_path, node_path=node_path, round_sequence=1)
    assert by_round_sequence.ok and by_round_sequence.value is not None
    assert by_round_sequence.value.round_id == round_record.value.round_id
    round_reverse = service.round_sequence(
        tmp_path,
        node_path=node_path,
        round_id=round_record.value.round_id,
    )
    assert round_reverse.ok and round_reverse.value == 1
    missing_round = service.get_round_by_sequence(tmp_path, node_path=node_path, round_sequence=2)
    assert not missing_round.ok
    assert missing_round.issues[0].kind == "round_sequence_not_found"


def test_closeout_round_resolution_prefers_exact_context_and_validates_lifecycle(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    node_path = "Main.Topic.Core"
    strategy = service.ensure_open_strategy(tmp_path, node_path=node_path, objective="Closeout strategy.")
    assert strategy.ok and strategy.value is not None
    draft = service.create_round_draft(
        tmp_path,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        objective="Closeout round.",
    )
    assert draft.ok and draft.value is not None

    stale = service.resolve_round_for_closeout(
        tmp_path,
        node_path=node_path,
        exact_round_id=draft.value.round_id,
    )
    assert not stale.ok
    assert stale.issues[0].kind == "round_context_lifecycle_mismatch"
    wrong_context = service.resolve_round_for_closeout(
        tmp_path,
        node_path=node_path,
        exact_round_id="round_from_another_node",
    )
    assert not wrong_context.ok
    assert wrong_context.issues[0].kind == "round_context_mismatch"

    assert service.start_round(tmp_path, node_path=node_path, round_id=draft.value.round_id).ok
    awaiting = service.strategy_round.record_round_execution_result(
        tmp_path,
        node_path=node_path,
        round_id=draft.value.round_id,
        result_kind=DeclRoundResultKind.SUCCESS,
    )
    assert awaiting.ok
    exact = service.resolve_round_for_closeout(
        tmp_path,
        node_path=node_path,
        exact_round_id=draft.value.round_id,
    )
    fallback = service.resolve_round_for_closeout(
        tmp_path,
        node_path=node_path,
        exact_round_id=None,
    )
    assert exact.ok and exact.value is not None
    assert fallback.ok and fallback.value is not None
    assert exact.value.round_id == draft.value.round_id
    assert fallback.value.round_id == draft.value.round_id

    second = DeclGraphRound(
        round_id="round_corrupt_awaiting",
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
        round_index=2,
        objective="Corrupt awaiting round.",
        status=DeclRoundStatus.AWAITING_CLOSEOUT,
        execution_result_kind=DeclRoundResultKind.SUCCESS,
        execution_completed_at="2026-08-24T00:00:00+00:00",
    )
    assert service.runtime.foundation.store.write_json_atomic(
        service.graph_store.round_path(tmp_path, node_path=node_path, round_id=second.round_id),
        second,
        mode=WriteMode.CREATE_ONLY,
    ).ok
    exact_with_ambiguous_fallback = service.resolve_round_for_closeout(
        tmp_path,
        node_path=node_path,
        exact_round_id=draft.value.round_id,
    )
    ambiguous_fallback = service.resolve_round_for_closeout(
        tmp_path,
        node_path=node_path,
        exact_round_id=None,
    )
    assert exact_with_ambiguous_fallback.ok
    assert not ambiguous_fallback.ok
    assert ambiguous_fallback.issues[0].kind == "current_awaiting_closeout_round_ambiguous"
