import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.unit_services_helpers import initialize_native_test_repo, make_runtime

from lean_constellation.services.decl_graph import (
    DeclGraphRound,
    DeclGraphStrategy,
    DeclRevisionChange,
    DeclChangeKind,
    DeclRoundResultKind,
    DeclRoundStatus,
    DeclState,
    DeclStrategyStatus,
)
from lean_constellation.services.foundation import WriteMode


def test_legacy_decl_graph_models_default_missing_execution_constraints_to_none() -> None:
    strategy = DeclGraphStrategy.model_validate(
        {
            "strategy_id": "strategy_legacy",
            "node_path": "Main.Topic.Core",
            "objective": "Legacy strategy.",
        }
    )
    round_record = DeclGraphRound.model_validate(
        {
            "round_id": "round_legacy",
            "node_path": "Main.Topic.Core",
            "strategy_id": "strategy_legacy",
            "round_index": 1,
            "objective": "Legacy round.",
        }
    )
    change = DeclRevisionChange.model_validate(
        {
            "kind": DeclChangeKind.CREATE,
            "objective": "Legacy change.",
        }
    )

    assert strategy.execution_constraints is None
    assert round_record.execution_constraints is None
    assert change.execution_constraints is None


def test_decl_execution_constraints_normalize_optional_text_and_reject_non_text() -> None:
    cases = (
        (
            DeclGraphStrategy,
            {
                "strategy_id": "strategy_text",
                "node_path": "Main.Topic.Core",
                "objective": "Strategy objective.",
            },
        ),
        (
            DeclGraphRound,
            {
                "round_id": "round_text",
                "node_path": "Main.Topic.Core",
                "strategy_id": "strategy_text",
                "round_index": 1,
                "objective": "Round objective.",
            },
        ),
        (
            DeclRevisionChange,
            {
                "kind": DeclChangeKind.CREATE,
                "objective": "Change objective.",
            },
        ),
    )

    for model, payload in cases:
        assert model.model_validate(
            {**payload, "execution_constraints": "  keep this batch small  "}
        ).execution_constraints == "keep this batch small"
        assert model.model_validate(
            {**payload, "execution_constraints": "   "}
        ).execution_constraints is None
        with pytest.raises(ValidationError):
            model.model_validate(
                {**payload, "execution_constraints": {"batch": "small"}}
            )


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


def test_legacy_decl_graph_json_loads_without_execution_constraint_rewrite(
    tmp_path: Path,
) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    strategy = service.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Legacy strategy.",
    )
    assert strategy.ok and strategy.value is not None
    round_record = service.create_round_draft(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
        objective="Legacy round.",
    )
    assert round_record.ok and round_record.value is not None
    created = service.create_decl(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
        name="legacy_result",
        kind="theorem",
        objective="Legacy change.",
        summary="Legacy result.",
    )
    assert created.ok, created.issues

    paths = (
        service.graph_store.strategy_path(
            tmp_path,
            node_path="Main.Topic.Core",
            strategy_id=strategy.value.strategy_id,
        ),
        service.graph_store.round_path(
            tmp_path,
            node_path="Main.Topic.Core",
            round_id=round_record.value.round_id,
        ),
        service.graph_store.revision_path(
            tmp_path,
            node_path="Main.Topic.Core",
            decl_name="legacy_result",
            revision=1,
        ),
    )
    serialized: dict[Path, str] = {}
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if path == paths[2]:
            payload["change"].pop("execution_constraints", None)
        else:
            payload.pop("execution_constraints", None)
        serialized[path] = json.dumps(payload, indent=2) + "\n"
        path.write_text(serialized[path], encoding="utf-8")

    loaded_strategy = service.get_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
    )
    loaded_round = service.get_round(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
    )
    loaded_revision = service.get_decl_revision(
        tmp_path,
        node_path="Main.Topic.Core",
        name="legacy_result",
        revision=1,
    )

    assert loaded_strategy.ok and loaded_strategy.value is not None
    assert loaded_strategy.value.execution_constraints is None
    assert loaded_round.ok and loaded_round.value is not None
    assert loaded_round.value.execution_constraints is None
    assert loaded_revision.ok and loaded_revision.value is not None
    assert loaded_revision.value.change is not None
    assert loaded_revision.value.change.execution_constraints is None
    assert all(path.read_text(encoding="utf-8") == serialized[path] for path in paths)


def test_ensure_open_strategy_creates_and_reuses_open_strategy(tmp_path: Path) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph

    first = service.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Try a bottom-up lemma strategy.",
        rationale="Start from the easiest supporting lemmas.",
        execution_constraints="Plan at most one dependency frontier at a time.",
    )
    second = service.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="This should not replace the open strategy.",
        execution_constraints="This must not replace the open strategy constraints.",
    )

    assert first.ok and first.value is not None
    assert second.ok and second.value is not None
    assert second.value.strategy_id == first.value.strategy_id
    assert second.value.objective == "Try a bottom-up lemma strategy."
    assert second.value.execution_constraints == (
        "Plan at most one dependency frontier at a time."
    )

    view = service.get_strategy_view(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=first.value.strategy_id,
    )
    assert view.ok and view.value is not None
    assert view.value.execution_constraints == (
        "Plan at most one dependency frontier at a time."
    )

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
        execution_constraints="Keep this batch in the statement stage until reviewed.",
    )
    assert round_record.ok and round_record.value is not None
    assert round_record.value.round_index == 1
    assert round_record.value.status == DeclRoundStatus.DRAFT
    assert round_record.value.execution_constraints == (
        "Keep this batch in the statement stage until reviewed."
    )
    round_view = service.get_round_view(
        tmp_path,
        node_path="Main.Topic.Core",
        round_id=round_record.value.round_id,
    )
    assert round_view.ok and round_view.value is not None
    assert round_view.value.execution_constraints == (
        "Keep this batch in the statement stage until reviewed."
    )
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


def test_round_creation_and_completion_closeout_share_strategy_lock(
    tmp_path: Path,
) -> None:
    _create_content_node(tmp_path)
    service = make_runtime().decl_graph
    node_path = "Main.Topic.Core"
    strategy = service.ensure_open_strategy(
        tmp_path,
        node_path=node_path,
        objective="Lock boundary strategy.",
    )
    assert strategy.ok and strategy.value is not None

    with service.strategy_round._strategy_mutation_locked(
        tmp_path, node_path=node_path
    ):
        ensured = service.ensure_open_strategy(
            tmp_path,
            node_path=node_path,
            objective="Must not race closeout.",
        )
        drafted = service.create_round_draft(
            tmp_path,
            node_path=node_path,
            strategy_id=strategy.value.strategy_id,
            objective="Must not race closeout.",
        )
        closed = service.close_strategy_for_content_completion(
            tmp_path,
            node_path=node_path,
            contract_version=1,
            decl_graph_head={},
        )
        manual_close = service.close_strategy(
            tmp_path,
            node_path=node_path,
            strategy_id=strategy.value.strategy_id,
            summary="Must not race automatic closeout.",
        )

    assert not ensured.ok
    assert ensured.issues[0].kind == "strategy_mutation_lock_busy"
    assert not drafted.ok
    assert drafted.issues[0].kind == "strategy_mutation_lock_busy"
    assert not closed.ok
    assert closed.issues[0].kind == "strategy_completion_closeout_lock_busy"
    assert not manual_close.ok
    assert manual_close.issues[0].kind == "strategy_mutation_lock_busy"


@pytest.mark.parametrize("finalized_task_outcome", [None, "blocked", "failed"])
def test_completion_closeout_requires_persisted_ready_outcome(
    tmp_path: Path,
    finalized_task_outcome: str | None,
) -> None:
    _create_content_node(tmp_path)
    runtime = make_runtime()
    node_path = "Main.Topic.Core"
    strategy = runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path=node_path,
        objective="Outcome boundary strategy.",
    )
    assert strategy.ok and strategy.value is not None
    committed = runtime.node.contract._commit_content_contract_with_head(
        tmp_path,
        node_path=node_path,
        summary="Commit without a READY task outcome.",
        decl_graph_head={},
        finalized_task_outcome=finalized_task_outcome,
    )
    assert committed.ok and committed.value is not None

    closed = runtime.decl_graph.close_strategy_for_content_completion(
        tmp_path,
        node_path=node_path,
        contract_version=committed.value.version,
        decl_graph_head={},
    )

    assert not closed.ok
    assert closed.issues[0].kind == "strategy_completion_outcome_not_ready"
    current = runtime.decl_graph.get_strategy(
        tmp_path,
        node_path=node_path,
        strategy_id=strategy.value.strategy_id,
    )
    assert current.ok and current.value is not None
    assert current.value.status == DeclStrategyStatus.OPEN
    assert current.value.completion_closeout is None
