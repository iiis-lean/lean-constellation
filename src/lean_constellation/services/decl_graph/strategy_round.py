"""Strategy and round lifecycle management for DeclGraphService."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Collection
from contextlib import contextmanager
import fcntl
import hashlib
import json
from typing import TYPE_CHECKING, Iterator

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.models import (
    DeclRevisionRef,
    DeclGraphRound,
    DeclRoundResultKind,
    DeclRoundStatus,
    DeclGraphStrategy,
    DeclStrategyStatus,
    StrategyCompletionCloseoutReceipt,
    StrategyCompletionCloseoutView,
)
from lean_constellation.services.foundation import (
    FoundationContext,
    ServiceResult,
    WriteMode,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class StrategyMutationLockBusyError(RuntimeError):
    pass


class StrategyRoundComponent:
    """Create, inspect, and close Strategy / Round truth records."""

    def __init__(self, runtime: LeanRuntimeServices, graph_store: GraphStoreComponent) -> None:
        self.runtime = runtime
        self.graph_store = graph_store

    def ensure_open_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        objective: str,
        rationale: str | None = None,
        execution_constraints: str | None = None,
    ) -> ServiceResult[DeclGraphStrategy]:
        try:
            with self._strategy_mutation_locked(repo_root, node_path=node_path):
                return self._ensure_open_strategy_locked(
                    repo_root,
                    node_path=node_path,
                    objective=objective,
                    rationale=rationale,
                    execution_constraints=execution_constraints,
                )
        except StrategyMutationLockBusyError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_mutation_lock_busy",
                    str(exc),
                    object_ref=node_path,
                )
            )

    def _ensure_open_strategy_locked(
        self,
        repo_root: Path,
        *,
        node_path: str,
        objective: str,
        rationale: str | None = None,
        execution_constraints: str | None = None,
    ) -> ServiceResult[DeclGraphStrategy]:
        if not objective or not objective.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("strategy_objective_required", "Strategy objective is required.", field="objective")
            )
        strategies = self.list_strategies(repo_root, node_path=node_path)
        if not strategies.ok or strategies.value is None:
            return self.runtime.foundation.fail(strategies.issues)
        open_strategies = [item for item in strategies.value if item.status == DeclStrategyStatus.OPEN]
        if len(open_strategies) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "multiple_open_strategies",
                    "A Content node DeclGraph must not have multiple open strategies.",
                    object_ref=node_path,
                    current=", ".join(strategy.strategy_id for strategy in open_strategies),
                )
            )
        if open_strategies:
            return self.runtime.foundation.ok(open_strategies[0])

        contract = self.runtime.node.contract.get_current_contract(
            repo_root, node_path=node_path
        )
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        contract_status = getattr(
            contract.value.version_status,
            "value",
            str(contract.value.version_status),
        )
        if contract_status == "committed":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_content_contract_committed",
                    "A new Strategy requires an open Content contract version.",
                    object_ref=node_path,
                    current=str(contract.value.version),
                )
            )

        allocated = self.runtime.foundation.store.allocate_uuid(
            lambda candidate: self.graph_store.strategy_path(
                repo_root,
                node_path=node_path,
                strategy_id=candidate,
            ).exists(),
            prefix="strategy",
        )
        if not allocated.ok or allocated.value is None:
            return self.runtime.foundation.fail(allocated.issues)

        strategy = DeclGraphStrategy(
            strategy_id=allocated.value,
            node_path=node_path,
            objective=objective,
            rationale=rationale.strip() if rationale else None,
            execution_constraints=execution_constraints,
        )
        path = self.graph_store.strategy_path(repo_root, node_path=node_path, strategy_id=strategy.strategy_id)
        written = self.runtime.foundation.store.write_json_atomic(path, strategy, mode=WriteMode.CREATE_ONLY)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        rebuilt = self.graph_store.rebuild_index(repo_root, node_path=node_path)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(strategy)

    def close_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        summary: str,
        reason: str | None = None,
        failed: bool = False,
    ) -> ServiceResult[DeclGraphStrategy]:
        try:
            with self._strategy_mutation_locked(repo_root, node_path=node_path):
                return self._close_strategy_locked(
                    repo_root,
                    node_path=node_path,
                    strategy_id=strategy_id,
                    summary=summary,
                    reason=reason,
                    failed=failed,
                )
        except StrategyMutationLockBusyError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_mutation_lock_busy",
                    str(exc),
                    object_ref=node_path,
                )
            )

    def _close_strategy_locked(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        summary: str,
        reason: str | None = None,
        failed: bool = False,
    ) -> ServiceResult[DeclGraphStrategy]:
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("strategy_summary_required", "Strategy close summary is required.", field="summary")
            )
        strategy = self.get_strategy(repo_root, node_path=node_path, strategy_id=strategy_id)
        if not strategy.ok or strategy.value is None:
            return self.runtime.foundation.fail(strategy.issues)
        if strategy.value.status != DeclStrategyStatus.OPEN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_not_open",
                    "Only an open strategy can be closed.",
                    object_ref=strategy_id,
                    current=strategy.value.status.value,
                    expected=DeclStrategyStatus.OPEN.value,
                )
            )
        pending = self._unfinished_round(repo_root, node_path=node_path)
        if not pending.ok:
            return self.runtime.foundation.fail(pending.issues)
        if pending.value is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_round_closeout_pending",
                    "A declaration round must be closed before its strategy can be closed.",
                    object_ref=strategy_id,
                    current=f"{pending.value.round_id}:{pending.value.status.value}",
                )
            )
        strategy.value.status = DeclStrategyStatus.FAILED if failed else DeclStrategyStatus.CLOSED
        strategy.value.summary = summary.strip()
        strategy.value.closed_reason = reason.strip() if reason else None
        strategy.value.closed_at = utc_now_iso()
        return self._write_strategy(repo_root, node_path=node_path, strategy=strategy.value)

    def create_round_draft(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        objective: str,
        execution_constraints: str | None = None,
        revision_refs: list[DeclRevisionRef] | None = None,
    ) -> ServiceResult[DeclGraphRound]:
        try:
            with self._strategy_mutation_locked(repo_root, node_path=node_path):
                return self._create_round_draft_locked(
                    repo_root,
                    node_path=node_path,
                    strategy_id=strategy_id,
                    objective=objective,
                    execution_constraints=execution_constraints,
                    revision_refs=revision_refs,
                )
        except StrategyMutationLockBusyError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_mutation_lock_busy",
                    str(exc),
                    object_ref=node_path,
                )
            )

    def _create_round_draft_locked(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
        objective: str,
        execution_constraints: str | None = None,
        revision_refs: list[DeclRevisionRef] | None = None,
    ) -> ServiceResult[DeclGraphRound]:
        if not objective or not objective.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("round_objective_required", "Round objective is required.", field="objective")
            )
        strategy = self.get_strategy(repo_root, node_path=node_path, strategy_id=strategy_id)
        if not strategy.ok or strategy.value is None:
            return self.runtime.foundation.fail(strategy.issues)
        if strategy.value.status != DeclStrategyStatus.OPEN:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_not_open",
                    "Cannot create a round under a non-open strategy.",
                    object_ref=strategy_id,
                    current=strategy.value.status.value,
                    expected=DeclStrategyStatus.OPEN.value,
                )
            )
        pending = self._unfinished_round(repo_root, node_path=node_path)
        if not pending.ok:
            return self.runtime.foundation.fail(pending.issues)
        if pending.value is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_closeout_pending",
                    "A Content node already has an unfinished declaration round.",
                    object_ref=node_path,
                    current=f"{pending.value.round_id}:{pending.value.status.value}",
                )
            )
        allocated = self.runtime.foundation.store.allocate_uuid(
            lambda candidate: self.graph_store.round_path(repo_root, node_path=node_path, round_id=candidate).exists(),
            prefix="round",
        )
        if not allocated.ok or allocated.value is None:
            return self.runtime.foundation.fail(allocated.issues)
        next_round_index = self._next_round_index(repo_root, node_path=node_path)
        if not next_round_index.ok or next_round_index.value is None:
            return self.runtime.foundation.fail(next_round_index.issues)
        round_record = DeclGraphRound(
            round_id=allocated.value,
            node_path=node_path,
            strategy_id=strategy_id,
            round_index=next_round_index.value,
            objective=objective,
            execution_constraints=execution_constraints,
            revision_refs=revision_refs or [],
        )
        round_path = self.graph_store.round_path(repo_root, node_path=node_path, round_id=round_record.round_id)
        written = self.runtime.foundation.store.write_json_atomic(round_path, round_record, mode=WriteMode.CREATE_ONLY)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)

        strategy.value.created_round_ids.append(round_record.round_id)
        strategy_write = self._write_strategy(repo_root, node_path=node_path, strategy=strategy.value)
        if not strategy_write.ok:
            return self.runtime.foundation.fail(strategy_write.issues)
        rebuilt = self.graph_store.rebuild_index(repo_root, node_path=node_path)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(round_record)

    def close_strategy_for_content_completion(
        self,
        repo_root: Path,
        *,
        node_path: str,
        contract_version: int,
        decl_graph_head: dict[str, int],
    ) -> ServiceResult[StrategyCompletionCloseoutView]:
        head_digest = hashlib.sha256(
            json.dumps(
                dict(sorted(decl_graph_head.items())),
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        completion_identity = hashlib.sha256(
            json.dumps(
                {
                    "node_path": node_path,
                    "contract_version": contract_version,
                    "head_digest": head_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        try:
            with self._strategy_mutation_locked(repo_root, node_path=node_path):
                return self._close_strategy_for_content_completion_locked(
                    repo_root,
                    node_path=node_path,
                    contract_version=contract_version,
                    decl_graph_head=decl_graph_head,
                    head_digest=head_digest,
                    completion_identity=completion_identity,
                )
        except StrategyMutationLockBusyError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_completion_closeout_lock_busy",
                    str(exc),
                    object_ref=node_path,
                    details={"completion_identity": completion_identity},
                )
            )

    def _close_strategy_for_content_completion_locked(
        self,
        repo_root: Path,
        *,
        node_path: str,
        contract_version: int,
        decl_graph_head: dict[str, int],
        head_digest: str,
        completion_identity: str,
    ) -> ServiceResult[StrategyCompletionCloseoutView]:
        strategies = self.list_strategies(repo_root, node_path=node_path)
        if not strategies.ok or strategies.value is None:
            return self.runtime.foundation.fail(strategies.issues)
        matching = [
            item
            for item in strategies.value
            if item.completion_closeout is not None
            and item.completion_closeout.completion_identity == completion_identity
        ]
        if len(matching) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_completion_closeout_ambiguous",
                    "Multiple Strategy receipts claim the same Content completion identity.",
                    object_ref=node_path,
                    details={"completion_identity": completion_identity},
                )
            )
        if matching:
            return self.runtime.foundation.ok(
                StrategyCompletionCloseoutView(
                    status="already_closed",
                    completion_identity=completion_identity,
                    receipt=matching[0].completion_closeout,
                    summary="Strategy was already closed for this exact Content completion.",
                )
            )
        open_strategies = [
            item for item in strategies.value if item.status == DeclStrategyStatus.OPEN
        ]
        if not open_strategies:
            return self.runtime.foundation.ok(
                StrategyCompletionCloseoutView(
                    status="not_applicable",
                    completion_identity=completion_identity,
                    summary="Content completion has no open Strategy to close.",
                )
            )
        if len(open_strategies) != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_completion_open_ambiguous",
                    "Content completion requires exactly one current open Strategy.",
                    object_ref=node_path,
                    current=", ".join(
                        sorted(item.strategy_id for item in open_strategies)
                    ),
                )
            )
        current = self.runtime.node.contract.get_current_contract(
            repo_root, node_path=node_path
        )
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        current_status = getattr(
            current.value.version_status,
            "value",
            str(current.value.version_status),
        )
        if (
            current_status != "committed"
            or current.value.version != contract_version
            or current.value.contract.decl_graph_head != decl_graph_head
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_completion_identity_stale",
                    "Content completion identity no longer matches the committed contract head.",
                    object_ref=node_path,
                    current=(
                        f"version={current.value.version};head="
                        + json.dumps(current.value.contract.decl_graph_head, sort_keys=True)
                    ),
                    expected=(
                        f"version={contract_version};head="
                        + json.dumps(decl_graph_head, sort_keys=True)
                    ),
                )
            )
        if current.value.contract.finalized_task_outcome != "ready":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_completion_outcome_not_ready",
                    "Strategy completion closeout requires a persisted READY Content outcome.",
                    object_ref=node_path,
                    current=current.value.contract.finalized_task_outcome or "absent",
                    expected="ready",
                )
            )
        strategy = open_strategies[0]
        rounds = self.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        strategy_rounds = [
            item for item in rounds.value if item.strategy_id == strategy.strategy_id
        ]
        actual_ids = {item.round_id for item in strategy_rounds}
        expected_ids = set(strategy.created_round_ids)
        if actual_ids != expected_ids:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_completion_round_set_mismatch",
                    "Strategy round inventory is incomplete or inconsistent.",
                    object_ref=strategy.strategy_id,
                    current=", ".join(sorted(actual_ids)),
                    expected=", ".join(sorted(expected_ids)),
                )
            )
        unfinished = [
            item
            for item in strategy_rounds
            if item.status not in {DeclRoundStatus.COMMITTED, DeclRoundStatus.DISCARDED}
        ]
        if unfinished:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_completion_round_pending",
                    "Strategy has a non-terminal declaration round.",
                    object_ref=strategy.strategy_id,
                    current=", ".join(
                        sorted(f"{item.round_id}:{item.status.value}" for item in unfinished)
                    ),
                )
            )
        closed_at = utc_now_iso()
        reason = "Closed automatically because the exact Content contract completed READY."
        receipt = StrategyCompletionCloseoutReceipt(
            node_path=node_path,
            contract_version=contract_version,
            head_digest=head_digest,
            strategy_id=strategy.strategy_id,
            round_statuses={
                item.round_id: item.status for item in strategy_rounds
            },
            completion_identity=completion_identity,
            closed_at=closed_at,
            reason=reason,
        )
        strategy.status = DeclStrategyStatus.CLOSED
        strategy.summary = "Content completion automatically closed this Strategy."
        strategy.closed_reason = reason
        strategy.closed_at = closed_at
        strategy.completion_closeout = receipt
        written = self._write_strategy(
            repo_root, node_path=node_path, strategy=strategy
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(
            StrategyCompletionCloseoutView(
                status="closed",
                completion_identity=completion_identity,
                receipt=receipt,
                summary="Closed the exact open Strategy after Content READY completion.",
            )
        )

    @contextmanager
    def _strategy_mutation_locked(
        self,
        repo_root: Path,
        *,
        node_path: str,
    ) -> Iterator[Path]:
        lifecycle_path = self.runtime.foundation.layout.repo_lifecycle_lock_path(
            FoundationContext(repo_root=Path(repo_root))
        )
        node_key = hashlib.sha256(node_path.encode("utf-8")).hexdigest()[:16]
        path = lifecycle_path.parent / f"strategy-{node_key}.lock"
        self.runtime.foundation.store.ensure_dir(path.parent)
        handle = path.open("a+", encoding="utf-8")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise StrategyMutationLockBusyError(
                    f"Strategy mutation lock is busy: {path}"
                ) from exc
            yield path
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def start_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
    ) -> ServiceResult[DeclGraphRound]:
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        if round_record.value.status != DeclRoundStatus.DRAFT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_not_draft",
                    "Only a draft decl round can be started.",
                    object_ref=round_id,
                    current=round_record.value.status.value,
                    expected=DeclRoundStatus.DRAFT.value,
                )
            )
        pending = self._unfinished_round(
            repo_root,
            node_path=node_path,
            exclude_round_id=round_id,
        )
        if not pending.ok:
            return self.runtime.foundation.fail(pending.issues)
        if pending.value is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_closeout_pending",
                    "A Content node already has another unfinished declaration round.",
                    object_ref=node_path,
                    current=f"{pending.value.round_id}:{pending.value.status.value}",
                )
            )
        round_record.value.status = DeclRoundStatus.RUNNING
        round_record.value.started_at = utc_now_iso()
        return self._write_round(repo_root, node_path=node_path, round_record=round_record.value)

    def write_round_summary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        summary: str,
    ) -> ServiceResult[DeclGraphRound]:
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("round_summary_required", "Round summary is required.", field="summary")
            )
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        normalized_summary = summary.strip()
        if round_record.value.summary is not None:
            if round_record.value.summary == normalized_summary:
                return self.runtime.foundation.ok(round_record.value)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_summary_conflict",
                    "A round summary cannot be replaced after it has been recorded.",
                    object_ref=round_id,
                    current=round_record.value.summary,
                    expected=normalized_summary,
                )
            )
        if round_record.value.status == DeclRoundStatus.COMMITTED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_summary_committed",
                    "A committed round cannot accept a new round summary.",
                    object_ref=round_id,
                )
            )
        round_record.value.summary = normalized_summary
        return self._write_round(repo_root, node_path=node_path, round_record=round_record.value)

    def record_round_execution_result(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        result_kind: DeclRoundResultKind | str,
        reason: str | None = None,
    ) -> ServiceResult[DeclGraphRound]:
        result_kind = DeclRoundResultKind(result_kind)
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        normalized_reason = reason.strip() if reason and reason.strip() else None
        if round_record.value.status == DeclRoundStatus.AWAITING_CLOSEOUT:
            if (
                round_record.value.execution_result_kind == result_kind
                and round_record.value.execution_reason == normalized_reason
            ):
                return self.runtime.foundation.ok(round_record.value)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_execution_result_conflict",
                    "Declaration round execution result has already been recorded with different truth.",
                    object_ref=round_id,
                    current=round_record.value.execution_result_kind.value
                    if round_record.value.execution_result_kind is not None
                    else None,
                    expected=result_kind.value,
                )
            )
        if (
            round_record.value.status == DeclRoundStatus.DRAFT
            and result_kind == DeclRoundResultKind.FAILED
        ):
            pass
        elif round_record.value.status != DeclRoundStatus.RUNNING:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_not_running",
                    "Only a running declaration round, or a failed child start for a draft round, can record an execution result.",
                    object_ref=round_id,
                    current=round_record.value.status.value,
                    expected=DeclRoundStatus.RUNNING.value,
                )
            )
        if result_kind in {DeclRoundResultKind.BLOCKED, DeclRoundResultKind.FAILED} and not (reason and reason.strip()):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_terminal_reason_required",
                    "Blocked or failed round result requires a reason.",
                    object_ref=round_id,
                    field="reason",
                )
            )
        round_record.value.execution_result_kind = result_kind
        round_record.value.execution_reason = normalized_reason
        round_record.value.execution_completed_at = utc_now_iso()
        round_record.value.status = DeclRoundStatus.AWAITING_CLOSEOUT
        return self._write_round(repo_root, node_path=node_path, round_record=round_record.value)

    def reopen_failed_round_execution(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        failed_step_id: str,
    ) -> ServiceResult[DeclGraphRound]:
        """Clear only the failure marker written by DeclGraphRoundFlow."""

        round_record = self.validate_failed_round_execution_restart(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            failed_step_id=failed_step_id,
        )
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        round_record.value.status = DeclRoundStatus.RUNNING
        round_record.value.execution_result_kind = None
        round_record.value.execution_reason = None
        round_record.value.execution_completed_at = None
        return self._write_round(
            repo_root,
            node_path=node_path,
            round_record=round_record.value,
        )

    def rollback_failed_round_execution_reopen(
        self,
        repo_root: Path,
        *,
        node_path: str,
        failed_step_id: str,
        previous_round: DeclGraphRound,
    ) -> ServiceResult[DeclGraphRound]:
        """Compensate a recovery-boundary reopen after the ARK transaction fails."""

        expected_reason_prefix = (
            f"Step {failed_step_id} failed before DeclGraph round completion:"
        )
        if (
            previous_round.node_path != node_path
            or previous_round.status is not DeclRoundStatus.AWAITING_CLOSEOUT
            or previous_round.execution_result_kind is not DeclRoundResultKind.FAILED
            or not (previous_round.execution_reason or "").startswith(expected_reason_prefix)
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_reopen_compensation_source_invalid",
                    "Declaration round compensation source is not the expected failed Step marker.",
                    object_ref=previous_round.round_id,
                )
            )
        current = self.get_round(
            repo_root,
            node_path=node_path,
            round_id=previous_round.round_id,
        )
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        if current.value == previous_round:
            return self.runtime.foundation.ok(current.value)
        expected_reopened = previous_round.model_copy(deep=True)
        expected_reopened.status = DeclRoundStatus.RUNNING
        expected_reopened.execution_result_kind = None
        expected_reopened.execution_reason = None
        expected_reopened.execution_completed_at = None
        if current.value != expected_reopened:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_reopen_compensation_cas_mismatch",
                    "Declaration round changed after recovery-boundary reopen.",
                    object_ref=previous_round.round_id,
                )
            )
        return self._write_round(
            repo_root,
            node_path=node_path,
            round_record=previous_round,
        )

    def validate_failed_round_execution_restart(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        failed_step_id: str,
    ) -> ServiceResult[DeclGraphRound]:
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        expected_reason_prefix = (
            f"Step {failed_step_id} failed before DeclGraph round completion:"
        )
        if (
            round_record.value.status is not DeclRoundStatus.AWAITING_CLOSEOUT
            or round_record.value.execution_result_kind is not DeclRoundResultKind.FAILED
            or not (round_record.value.execution_reason or "").startswith(expected_reason_prefix)
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_failed_step_marker_mismatch",
                    "Declaration round does not carry the expected failed AgentStep marker.",
                    object_ref=round_id,
                    expected=expected_reason_prefix,
                    current=round_record.value.execution_reason,
                )
            )
        if (
            round_record.value.result_kind is not None
            or round_record.value.plan_closeout_acknowledged_at is not None
            or round_record.value.plan_closeout_acknowledged_by is not None
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_failed_step_already_closed_out",
                    "Declaration round failure has already been consumed by ContentPlan closeout.",
                    object_ref=round_id,
                )
            )
        return self.runtime.foundation.ok(round_record.value)

    def persist_round_closeout(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        result_kind: DeclRoundResultKind | str,
        reason: str | None,
        acknowledged_by: str,
    ) -> ServiceResult[tuple[DeclGraphRound, bool]]:
        """Persist the final Plan-owned round closeout after revision commit."""

        result_kind = DeclRoundResultKind(result_kind)
        normalized_reason = reason.strip() if reason and reason.strip() else None
        normalized_actor = acknowledged_by.strip()
        if not normalized_actor:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_closeout_actor_required",
                    "Round closeout requires the acknowledging Agent id.",
                    field="acknowledged_by",
                )
            )
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        if round_record.value.status == DeclRoundStatus.COMMITTED:
            if (
                round_record.value.result_kind == result_kind
                and round_record.value.result_reason == normalized_reason
            ):
                return self.runtime.foundation.ok((round_record.value, False))
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_closeout_conflict",
                    "Committed declaration round closeout truth conflicts with this request.",
                    object_ref=round_id,
                    current=(
                        round_record.value.result_kind.value
                        if round_record.value.result_kind is not None
                        else None
                    ),
                    expected=result_kind.value,
                )
            )
        if round_record.value.status not in {
            DeclRoundStatus.DRAFT,
            DeclRoundStatus.AWAITING_CLOSEOUT,
        }:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_not_ready_for_closeout",
                    "Declaration round must be draft or awaiting ContentPlan closeout.",
                    object_ref=round_id,
                    current=round_record.value.status.value,
                )
            )
        round_record.value.result_kind = result_kind
        round_record.value.result_reason = normalized_reason
        round_record.value.committed_at = utc_now_iso()
        round_record.value.plan_closeout_acknowledged_at = round_record.value.committed_at
        round_record.value.plan_closeout_acknowledged_by = normalized_actor
        round_record.value.status = DeclRoundStatus.COMMITTED
        written = self._write_round(
            repo_root,
            node_path=node_path,
            round_record=round_record.value,
        )
        if not written.ok or written.value is None:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok((written.value, True))

    def get_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
    ) -> ServiceResult[DeclGraphStrategy]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        path = self.graph_store.strategy_path(repo_root, node_path=node_path, strategy_id=strategy_id)
        return self.runtime.foundation.store.read_json(path, DeclGraphStrategy)

    def list_strategies(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclGraphStrategy]]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        graph_root = self.graph_store.graph_root(repo_root, node_path=node_path)
        return self.runtime.foundation.store.list_json(graph_root / "strategies", DeclGraphStrategy)

    def get_round(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclGraphRound]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        path = self.graph_store.round_path(repo_root, node_path=node_path, round_id=round_id)
        return self.runtime.foundation.store.read_json(path, DeclGraphRound)

    def list_rounds(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclGraphRound]]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        graph_root = self.graph_store.graph_root(repo_root, node_path=node_path)
        rounds = self.runtime.foundation.store.list_json(graph_root / "rounds", DeclGraphRound)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        return self.runtime.foundation.ok(sorted(rounds.value, key=lambda item: (item.round_index, item.round_id)))

    def require_current_open_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
    ) -> ServiceResult[DeclGraphStrategy]:
        strategies = self.list_strategies(repo_root, node_path=node_path)
        if not strategies.ok or strategies.value is None:
            return self.runtime.foundation.fail(strategies.issues)
        candidates = [item for item in strategies.value if item.status is DeclStrategyStatus.OPEN]
        return self._require_unique_strategy(
            candidates,
            node_path=node_path,
            missing_kind="current_open_strategy_missing",
            missing_message="The current Content node has no open declaration strategy.",
            ambiguous_kind="current_open_strategy_ambiguous",
            ambiguous_message="The current Content node has multiple open declaration strategies.",
        )

    def require_current_draft_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
    ) -> ServiceResult[DeclGraphRound]:
        return self._require_current_round(
            repo_root,
            node_path=node_path,
            statuses={DeclRoundStatus.DRAFT},
            missing_kind="current_draft_round_missing",
            missing_message="The current Content node has no draft declaration round.",
            ambiguous_kind="current_draft_round_ambiguous",
            ambiguous_message="The current Content node has multiple draft declaration rounds.",
        )

    def require_current_unfinished_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
    ) -> ServiceResult[DeclGraphRound]:
        return self._require_current_round(
            repo_root,
            node_path=node_path,
            statuses={
                DeclRoundStatus.DRAFT,
                DeclRoundStatus.RUNNING,
                DeclRoundStatus.AWAITING_CLOSEOUT,
            },
            missing_kind="current_unfinished_round_missing",
            missing_message="The current Content node has no unfinished declaration round.",
            ambiguous_kind="current_unfinished_round_ambiguous",
            ambiguous_message="The current Content node has multiple unfinished declaration rounds.",
        )

    def require_current_awaiting_closeout_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
    ) -> ServiceResult[DeclGraphRound]:
        return self._require_current_round(
            repo_root,
            node_path=node_path,
            statuses={DeclRoundStatus.AWAITING_CLOSEOUT},
            missing_kind="current_awaiting_closeout_round_missing",
            missing_message="The current Content node has no declaration round awaiting closeout.",
            ambiguous_kind="current_awaiting_closeout_round_ambiguous",
            ambiguous_message="The current Content node has multiple declaration rounds awaiting closeout.",
        )

    def resolve_round_for_closeout(
        self,
        repo_root: Path,
        *,
        node_path: str,
        exact_round_id: str | None,
    ) -> ServiceResult[DeclGraphRound]:
        if exact_round_id is None:
            return self.require_current_awaiting_closeout_round(repo_root, node_path=node_path)
        round_record = self.get_round(
            repo_root,
            node_path=node_path,
            round_id=exact_round_id,
        )
        if not round_record.ok or round_record.value is None:
            if round_record.issues and all(issue.kind == "missing_file" for issue in round_record.issues):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "round_context_mismatch",
                        "The exact callback Round does not belong to the current Content node.",
                        object_ref=exact_round_id,
                        expected=node_path,
                    )
                )
            return self.runtime.foundation.fail(round_record.issues)
        if round_record.value.status is not DeclRoundStatus.AWAITING_CLOSEOUT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_context_lifecycle_mismatch",
                    "The exact callback Round is not awaiting ContentPlan closeout.",
                    object_ref=exact_round_id,
                    current=round_record.value.status.value,
                    expected=DeclRoundStatus.AWAITING_CLOSEOUT.value,
                )
            )
        return self.runtime.foundation.ok(round_record.value)

    def get_strategy_by_sequence(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_sequence: int,
    ) -> ServiceResult[DeclGraphStrategy]:
        strategies = self.list_strategies(repo_root, node_path=node_path)
        if not strategies.ok or strategies.value is None:
            return self.runtime.foundation.fail(strategies.issues)
        ordered = self._ordered_strategies(strategies.value)
        if strategy_sequence < 1 or strategy_sequence > len(ordered):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "strategy_sequence_not_found",
                    "No declaration strategy exists at the requested sequence.",
                    object_ref=node_path,
                    current=str(strategy_sequence),
                )
            )
        return self.runtime.foundation.ok(ordered[strategy_sequence - 1])

    def get_round_by_sequence(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_sequence: int,
    ) -> ServiceResult[DeclGraphRound]:
        rounds = self.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        candidates = [item for item in rounds.value if item.round_index == round_sequence]
        if len(candidates) != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_sequence_not_found" if not candidates else "round_sequence_ambiguous",
                    (
                        "No declaration round exists at the requested sequence."
                        if not candidates
                        else "Multiple declaration rounds use the requested sequence."
                    ),
                    object_ref=node_path,
                    current=str(round_sequence),
                )
            )
        return self.runtime.foundation.ok(candidates[0])

    def strategy_sequence(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
    ) -> ServiceResult[int]:
        strategies = self.list_strategies(repo_root, node_path=node_path)
        if not strategies.ok or strategies.value is None:
            return self.runtime.foundation.fail(strategies.issues)
        for sequence, strategy in enumerate(self._ordered_strategies(strategies.value), start=1):
            if strategy.strategy_id == strategy_id:
                return self.runtime.foundation.ok(sequence)
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "strategy_sequence_not_found",
                "The declaration strategy has no sequence in the current Content node.",
                object_ref=strategy_id,
            )
        )

    def round_sequence(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
    ) -> ServiceResult[int]:
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        return self.runtime.foundation.ok(round_record.value.round_index)

    def _write_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy: DeclGraphStrategy,
    ) -> ServiceResult[DeclGraphStrategy]:
        path = self.graph_store.strategy_path(repo_root, node_path=node_path, strategy_id=strategy.strategy_id)
        written = self.runtime.foundation.store.write_json_atomic(path, strategy, mode=WriteMode.UPDATE_EXISTING)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(strategy)

    def _write_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_record: DeclGraphRound,
    ) -> ServiceResult[DeclGraphRound]:
        path = self.graph_store.round_path(repo_root, node_path=node_path, round_id=round_record.round_id)
        written = self.runtime.foundation.store.write_json_atomic(path, round_record, mode=WriteMode.UPDATE_EXISTING)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(round_record)

    def _next_round_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[int]:
        rounds = self.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        if not rounds.value:
            return self.runtime.foundation.ok(1)
        return self.runtime.foundation.ok(max(round_record.round_index for round_record in rounds.value) + 1)

    def _unfinished_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
        exclude_round_id: str | None = None,
    ) -> ServiceResult[DeclGraphRound | None]:
        rounds = self.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        candidates = [
            round_record
            for round_record in rounds.value
            if round_record.round_id != exclude_round_id
            and round_record.status
            in {
                DeclRoundStatus.DRAFT,
                DeclRoundStatus.RUNNING,
                DeclRoundStatus.AWAITING_CLOSEOUT,
            }
        ]
        if len(candidates) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "current_unfinished_round_ambiguous",
                    "The current Content node has multiple unfinished declaration rounds.",
                    object_ref=node_path,
                    current=", ".join(item.round_id for item in candidates),
                )
            )
        return self.runtime.foundation.ok(candidates[0] if candidates else None)

    def _require_current_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
        statuses: Collection[DeclRoundStatus],
        missing_kind: str,
        missing_message: str,
        ambiguous_kind: str,
        ambiguous_message: str,
    ) -> ServiceResult[DeclGraphRound]:
        rounds = self.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        candidates = [item for item in rounds.value if item.status in statuses]
        if not candidates:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(missing_kind, missing_message, object_ref=node_path)
            )
        if len(candidates) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    ambiguous_kind,
                    ambiguous_message,
                    object_ref=node_path,
                    current=", ".join(item.round_id for item in candidates),
                )
            )
        return self.runtime.foundation.ok(candidates[0])

    def _require_unique_strategy(
        self,
        candidates: list[DeclGraphStrategy],
        *,
        node_path: str,
        missing_kind: str,
        missing_message: str,
        ambiguous_kind: str,
        ambiguous_message: str,
    ) -> ServiceResult[DeclGraphStrategy]:
        if not candidates:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(missing_kind, missing_message, object_ref=node_path)
            )
        if len(candidates) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    ambiguous_kind,
                    ambiguous_message,
                    object_ref=node_path,
                    current=", ".join(item.strategy_id for item in candidates),
                )
            )
        return self.runtime.foundation.ok(candidates[0])

    @staticmethod
    def _ordered_strategies(strategies: list[DeclGraphStrategy]) -> list[DeclGraphStrategy]:
        return sorted(strategies, key=lambda item: (item.created_at, item.strategy_id))
