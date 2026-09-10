from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceIssue, ServiceResult
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.decl_graph import DeclGraphStrategy
from lean_constellation.services.node import ContentTaskOutcome, ContentTaskResultView, NodeService
from lean_constellation.services.validation_snapshot.readiness_gate import ContentNodeCompletionGateView


class FakeContentReadyGate:
    def __init__(self, *, passed: bool = True) -> None:
        self.passed = passed
        self.calls: list[tuple[Path, str]] = []

    def check_content_node_completion(self, repo_root: Path, *, node_path: str) -> ServiceResult[ContentNodeCompletionGateView]:
        self.calls.append((Path(repo_root), node_path))
        gate = (
            GateReport(
                gate_name="content_node_completion",
                passed=True,
                summary="Fake content completion passed.",
            )
            if self.passed
            else GateReport(
                gate_name="content_node_completion",
                passed=False,
                summary="Fake content completion failed.",
                issues=[
                    ServiceIssue(
                        kind="fake_content_not_ready",
                        message="Fake content completion failed.",
                        object_ref=node_path,
                        suggested_action="Inspect the content task result before committing.",
                    )
                ],
            )
        )
        if self.passed:
            return ServiceResult(
                ok=True,
                value=ContentNodeCompletionGateView(
                    node_path=node_path,
                    task_completion_mode=RepoCompletionMode.GRAPH_PROVED,
                    repo_completion_mode=RepoCompletionMode.GRAPH_PROVED,
                    remaining_repo_gap=False,
                    target_proof_availability=ProofAvailability.PROVED,
                    gate=gate,
                    ready_to_submit=True,
                    summary="Fake content completion passed.",
                ),
            )
        return ServiceResult(
            ok=True,
            value=ContentNodeCompletionGateView(
                node_path=node_path,
                task_completion_mode=RepoCompletionMode.GRAPH_PROVED,
                repo_completion_mode=RepoCompletionMode.GRAPH_PROVED,
                remaining_repo_gap=False,
                target_proof_availability=ProofAvailability.PROVED,
                gate=gate,
                ready_to_submit=False,
                blocking_issue_kinds=["fake_content_not_ready"],
                summary="Fake content completion failed.",
            ),
        )

    def check_content_node_ready(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        raise AssertionError("finalize_content_task_result should use check_content_node_completion when available")


def _write_preparation_input(tmp_path: Path) -> None:
    runtime = make_runtime()
    prep = RepoPreparationInput(
        goal="Formalize the requested source material.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
        source_corpus_relpath=".lean_constellation/source",
        source_description="A source description.",
        interface_inputs=[],
    )
    path = runtime.foundation.layout.preparation_input_path(FoundationContext(repo_root=tmp_path))
    assert runtime.foundation.store.write_json_atomic(path, prep).ok


def _make_service_with_content_node(tmp_path: Path, *, ready_passed: bool = True) -> tuple[NodeService, FakeContentReadyGate]:
    _write_preparation_input(tmp_path)
    runtime = make_runtime()
    ready_gate = FakeContentReadyGate(passed=ready_passed)
    service = NodeService(runtime, content_ready_gate=ready_gate)
    assert service.ensure_native_root_main_contract(tmp_path).ok
    assert service.create_scope_node(tmp_path, path="Main.Topic", goal="Topic goal", boundary="Topic boundary.").ok
    content = service.create_content_node(
        tmp_path,
        path="Main.Topic.Core",
        goal="Core goal.",
        boundary="Core boundary.",
        objective="Build core content.",
        success_criteria="Core content is ready.",
    )
    assert content.ok
    return service, ready_gate


def test_finalize_ready_content_task_commits_contract(tmp_path: Path) -> None:
    service, ready_gate = _make_service_with_content_node(tmp_path)

    finalized = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(
            outcome=ContentTaskOutcome.READY,
            task_completion_mode=RepoCompletionMode.GRAPH_DECLARED,
            repo_completion_mode=RepoCompletionMode.GRAPH_PROVED,
            remaining_repo_gap=True,
            summary="Task submitted ready.",
        ),
        coordinator_summary="Coordinator verified the ready content task.",
    )

    assert finalized.ok
    assert finalized.value is not None
    assert finalized.value.finalized is True
    assert finalized.value.contract_summary_written is True
    assert finalized.value.contract_committed is True
    assert finalized.value.contract_version_status == "committed"
    assert finalized.value.task_completion_mode == RepoCompletionMode.GRAPH_DECLARED
    assert finalized.value.repo_completion_mode == RepoCompletionMode.GRAPH_PROVED
    assert finalized.value.remaining_repo_gap is True
    assert ready_gate.calls == [(tmp_path, "Main.Topic.Core")]

    current = service.contract.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert current.ok
    assert current.value is not None
    assert current.value.status == "committed"
    assert current.value.contract.summary == "Coordinator verified the ready content task."
    assert current.value.contract.committed_at is not None


def test_finalize_ready_closes_exact_strategy_and_retry_reuses_receipt(
    tmp_path: Path,
) -> None:
    service, _ready_gate = _make_service_with_content_node(tmp_path)
    strategy = service.runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Complete the Content node.",
    )
    assert strategy.ok and strategy.value is not None
    task_result = ContentTaskResultView(
        outcome=ContentTaskOutcome.READY,
        contract_version=1,
        summary="Task submitted ready.",
    )

    first = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=task_result,
        coordinator_summary="Coordinator verified the ready content task.",
    )
    second = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=task_result,
        coordinator_summary="Coordinator verified the ready content task.",
    )

    assert first.ok and first.value is not None, first.issues
    assert first.value.strategy_closeout is not None
    assert first.value.strategy_closeout.status == "closed"
    assert second.ok and second.value is not None, second.issues
    assert second.value.strategy_closeout is not None
    assert second.value.strategy_closeout.status == "already_closed"
    loaded = service.runtime.decl_graph.get_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
    )
    assert loaded.ok and loaded.value is not None
    assert loaded.value.status == "closed"
    receipt = loaded.value.completion_closeout
    assert receipt is not None
    assert receipt.contract_version == 1
    assert receipt.strategy_id == strategy.value.strategy_id
    assert receipt.round_statuses == {}
    assert receipt.completion_identity == (
        first.value.strategy_closeout.completion_identity
    )
    replacement = service.runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Start follow-up declaration work.",
    )
    assert replacement.ok and replacement.value is not None
    third = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=task_result,
        coordinator_summary="Coordinator verified the ready content task.",
    )
    assert third.ok and third.value is not None
    assert third.value.strategy_closeout is not None
    assert third.value.strategy_closeout.status == "already_closed"
    replacement_after_retry = service.runtime.decl_graph.get_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=replacement.value.strategy_id,
    )
    assert replacement_after_retry.ok and replacement_after_retry.value is not None
    assert replacement_after_retry.value.status == "open"


def test_ready_content_commit_survives_strategy_closeout_failure(
    tmp_path: Path, monkeypatch
) -> None:
    service, _ready_gate = _make_service_with_content_node(tmp_path)
    strategy = service.runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Complete the Content node.",
    )
    assert strategy.ok and strategy.value is not None
    monkeypatch.setattr(
        service.runtime.decl_graph,
        "close_strategy_for_content_completion",
        lambda *args, **kwargs: service.runtime.foundation.fail(
            service.runtime.foundation.issue(
                "strategy_completion_injected_failure",
                "Injected closeout failure.",
            )
        ),
    )

    finalized = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(outcome=ContentTaskOutcome.READY),
        coordinator_summary="Coordinator committed Content truth.",
    )

    assert finalized.ok and finalized.value is not None
    assert finalized.value.contract_committed is True
    assert finalized.value.strategy_closeout is not None
    assert finalized.value.strategy_closeout.status == "pending"
    assert finalized.value.strategy_closeout.issues == [
        "strategy_completion_injected_failure"
    ]
    loaded = service.runtime.decl_graph.get_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
    )
    assert loaded.ok and loaded.value is not None
    assert loaded.value.status == "open"


def test_ready_content_commit_does_not_guess_between_multiple_open_strategies(
    tmp_path: Path,
) -> None:
    service, _ready_gate = _make_service_with_content_node(tmp_path)
    first = service.runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="First strategy.",
    )
    assert first.ok and first.value is not None
    second = DeclGraphStrategy(
        strategy_id="strategy_ambiguous_second",
        node_path="Main.Topic.Core",
        objective="Second strategy.",
    )
    assert service.runtime.foundation.store.write_json_atomic(
        service.runtime.decl_graph.graph_store.strategy_path(
            tmp_path,
            node_path="Main.Topic.Core",
            strategy_id=second.strategy_id,
        ),
        second,
        mode=WriteMode.CREATE_ONLY,
    ).ok

    finalized = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(outcome=ContentTaskOutcome.READY),
        coordinator_summary="Coordinator committed exact Content truth.",
    )

    assert finalized.ok and finalized.value is not None
    assert finalized.value.contract_committed is True
    assert finalized.value.strategy_closeout is not None
    assert finalized.value.strategy_closeout.status == "pending"
    assert finalized.value.strategy_closeout.issues == [
        "strategy_completion_open_ambiguous"
    ]
    strategies = service.runtime.decl_graph.list_strategies(
        tmp_path, node_path="Main.Topic.Core"
    )
    assert strategies.ok and strategies.value is not None
    assert {item.status.value for item in strategies.value} == {"open"}


def test_finalize_blocked_content_task_commits_contract(tmp_path: Path) -> None:
    service, ready_gate = _make_service_with_content_node(tmp_path)

    finalized = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result={
            "outcome": "blocked",
            "summary": "Task needs an external dependency.",
            "reason": "A required provider repo is missing.",
        },
        coordinator_summary="Coordinator recorded a blocked content task.",
    )

    assert finalized.ok
    assert finalized.value is not None
    assert finalized.value.finalized is True
    assert finalized.value.contract_summary_written is True
    assert finalized.value.contract_committed is True
    assert finalized.value.contract_version_status == "committed"
    assert ready_gate.calls == []

    current = service.contract.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert current.ok
    assert current.value is not None
    assert current.value.status == "committed"
    assert current.value.contract.summary == "Coordinator recorded a blocked content task."
    assert current.value.contract.committed_at is not None
    assert finalized.value.strategy_closeout is None


def test_finalize_failed_content_task_commits_contract(tmp_path: Path) -> None:
    service, _ready_gate = _make_service_with_content_node(tmp_path)

    finalized = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(
            outcome=ContentTaskOutcome.FAILED,
            summary="Task exhausted retry budget.",
            reason="Formal proof stage failed after reviewer retry budget.",
        ),
        coordinator_summary="Coordinator recorded a failed content task.",
    )

    assert finalized.ok
    assert finalized.value is not None
    assert finalized.value.task_outcome == ContentTaskOutcome.FAILED
    assert finalized.value.contract_committed is True
    assert finalized.value.contract_version_status == "committed"

    current = service.contract.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert current.ok
    assert current.value is not None
    assert current.value.status == "committed"
    assert current.value.contract.summary == "Coordinator recorded a failed content task."
    assert current.value.contract.committed_at is not None


def test_finalize_ready_gate_failure_returns_failure_view_without_summary_write(tmp_path: Path) -> None:
    service, _ready_gate = _make_service_with_content_node(tmp_path, ready_passed=False)

    finalized = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(outcome=ContentTaskOutcome.READY, summary="Task submitted ready."),
        coordinator_summary="Coordinator would commit if ready gate passed.",
    )

    assert not finalized.ok
    assert finalized.value is not None
    assert finalized.value.finalized is False
    assert finalized.value.contract_summary_written is False
    assert finalized.value.contract_committed is False
    assert finalized.value.gate is not None
    assert finalized.value.gate.passed is False
    assert finalized.value.follow_up_hints == ["Inspect the content task result before committing."]

    current = service.contract.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert current.ok
    assert current.value is not None
    assert current.value.status == "open"
    assert current.value.contract.summary is None


def test_finalize_content_task_requires_summary_and_blocked_reason(tmp_path: Path) -> None:
    service, _ready_gate = _make_service_with_content_node(tmp_path)

    missing_summary = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(outcome=ContentTaskOutcome.READY, summary="Task submitted ready."),
        coordinator_summary=" ",
    )
    assert not missing_summary.ok
    assert missing_summary.issues[0].kind == "contract_summary_required"

    missing_reason = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(outcome=ContentTaskOutcome.BLOCKED, summary="Blocked.", reason=" "),
        coordinator_summary="Coordinator summary.",
    )
    assert not missing_reason.ok
    assert missing_reason.issues[0].kind == "content_task_reason_required"

    missing_failed_reason = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(outcome=ContentTaskOutcome.FAILED, summary="Failed.", reason=" "),
        coordinator_summary="Coordinator summary.",
    )
    assert not missing_failed_reason.ok
    assert missing_failed_reason.issues[0].kind == "content_task_reason_required"


def test_finalize_rejects_contract_version_mismatch(tmp_path: Path) -> None:
    service, ready_gate = _make_service_with_content_node(tmp_path)

    finalized = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(
            outcome=ContentTaskOutcome.BLOCKED,
            contract_version=99,
            summary="Blocked.",
            reason="Wrong contract version.",
        ),
        coordinator_summary="Coordinator summary.",
    )

    assert not finalized.ok
    assert finalized.issues[0].kind == "content_task_contract_version_mismatch"
    assert ready_gate.calls == []

    current = service.contract.get_current_contract(tmp_path, node_path="Main.Topic.Core")
    assert current.ok
    assert current.value is not None
    assert current.value.status == "open"
    assert current.value.contract.summary is None


def test_finalize_duplicate_commit_is_idempotent_but_cannot_replace_summary(tmp_path: Path) -> None:
    service, _ready_gate = _make_service_with_content_node(tmp_path)
    task_result = ContentTaskResultView(
        outcome=ContentTaskOutcome.BLOCKED,
        contract_version=1,
        summary="Blocked.",
        reason="Need provider.",
    )

    first = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=task_result,
        coordinator_summary="Coordinator recorded blocked task.",
    )
    second = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=task_result,
        coordinator_summary="Coordinator recorded blocked task.",
    )
    conflicting = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=task_result,
        coordinator_summary="Coordinator tries to replace the committed summary.",
    )

    assert first.ok
    assert second.ok and second.value is not None
    assert second.value.finalized is True
    assert second.value.contract_committed is True
    assert second.value.coordinator_summary == "Coordinator recorded blocked task."
    assert "already finalized" in second.value.summary
    assert not conflicting.ok
    assert conflicting.issues[0].kind == "content_task_already_finalized"


def test_blocked_finalize_cannot_be_replayed_as_ready_to_close_strategy(
    tmp_path: Path,
) -> None:
    service, _ready_gate = _make_service_with_content_node(tmp_path)
    strategy = service.runtime.decl_graph.ensure_open_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        objective="Remain open after a blocked task.",
    )
    assert strategy.ok and strategy.value is not None
    summary = "Coordinator recorded blocked task."
    blocked = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(
            outcome=ContentTaskOutcome.BLOCKED,
            reason="Provider missing.",
        ),
        coordinator_summary=summary,
    )

    replayed = service.finalize_content_task_result(
        tmp_path,
        node_path="Main.Topic.Core",
        task_result=ContentTaskResultView(outcome=ContentTaskOutcome.READY),
        coordinator_summary=summary,
    )

    assert blocked.ok
    assert not replayed.ok
    assert replayed.issues[0].kind == "content_task_outcome_mismatch"
    loaded = service.runtime.decl_graph.get_strategy(
        tmp_path,
        node_path="Main.Topic.Core",
        strategy_id=strategy.value.strategy_id,
    )
    assert loaded.ok and loaded.value is not None
    assert loaded.value.status == "open"
