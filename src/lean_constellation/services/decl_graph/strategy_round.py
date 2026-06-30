"""Strategy and round lifecycle management for DeclGraphService."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.models import (
    DeclRoundRecord,
    DeclRoundResultKind,
    DeclRoundStatus,
    DeclStrategyRecord,
    DeclStrategyStatus,
)
from lean_constellation.services.foundation import ServiceResult, WriteMode

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


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
    ) -> ServiceResult[DeclStrategyRecord]:
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

        strategy = DeclStrategyRecord(
            strategy_id=allocated.value,
            node_path=node_path,
            objective=objective,
            rationale=rationale.strip() if rationale else None,
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
    ) -> ServiceResult[DeclStrategyRecord]:
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
        change_ids: list[str] | None = None,
    ) -> ServiceResult[DeclRoundRecord]:
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
        running = self._running_round(repo_root, node_path=node_path)
        if running is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_already_running",
                    "A Content node already has a running decl round.",
                    object_ref=node_path,
                    current=running.round_id,
                )
            )
        allocated = self.runtime.foundation.store.allocate_uuid(
            lambda candidate: self.graph_store.round_path(repo_root, node_path=node_path, round_id=candidate).exists(),
            prefix="round",
        )
        if not allocated.ok or allocated.value is None:
            return self.runtime.foundation.fail(allocated.issues)
        round_index = self._next_round_index(repo_root, node_path=node_path)
        round_record = DeclRoundRecord(
            round_id=allocated.value,
            node_path=node_path,
            strategy_id=strategy_id,
            round_index=round_index,
            objective=objective,
            change_ids=change_ids or [],
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

    def start_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
    ) -> ServiceResult[DeclRoundRecord]:
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
        running = self._running_round(repo_root, node_path=node_path)
        if running is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_already_running",
                    "A Content node already has a running decl round.",
                    object_ref=node_path,
                    current=running.round_id,
                )
            )
        round_record.value.status = DeclRoundStatus.RUNNING
        round_record.value.started_at = utc_now_iso()
        return self._write_round(repo_root, node_path=node_path, round_record=round_record.value)

    def write_decl_change_summary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        change_id: str,
        summary: str,
    ) -> ServiceResult[DeclRoundRecord]:
        if not change_id or not change_id.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("change_id_required", "Decl change id is required.", field="change_id")
            )
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("change_summary_required", "Decl change summary is required.", field="summary")
            )
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        if round_record.value.change_ids and change_id not in round_record.value.change_ids:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "unknown_decl_change",
                    "Decl change is not part of this round.",
                    object_ref=change_id,
                    expected=", ".join(round_record.value.change_ids),
                )
            )
        if change_id not in round_record.value.change_ids:
            round_record.value.change_ids.append(change_id)
        round_record.value.change_summaries[change_id] = summary.strip()
        return self._write_round(repo_root, node_path=node_path, round_record=round_record.value)

    def write_round_summary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        summary: str,
    ) -> ServiceResult[DeclRoundRecord]:
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("round_summary_required", "Round summary is required.", field="summary")
            )
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        missing = [
            change_id
            for change_id in round_record.value.change_ids
            if change_id not in round_record.value.change_summaries
        ]
        if missing:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_change_summary_missing",
                    "Every round change must have its own summary before writing the round summary.",
                    object_ref=round_id,
                    current=", ".join(missing),
                )
            )
        round_record.value.summary = summary.strip()
        return self._write_round(repo_root, node_path=node_path, round_record=round_record.value)

    def mark_round_terminal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        result_kind: DeclRoundResultKind | str,
        reason: str | None = None,
    ) -> ServiceResult[DeclRoundRecord]:
        result_kind = DeclRoundResultKind(result_kind)
        round_record = self.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        if round_record.value.status not in {DeclRoundStatus.DRAFT, DeclRoundStatus.RUNNING}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_already_terminal",
                    "Decl round is already terminal.",
                    object_ref=round_id,
                    current=round_record.value.status.value,
                )
            )
        if not round_record.value.summary:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_summary_missing",
                    "Round summary is required before marking the round terminal.",
                    object_ref=round_id,
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
        round_record.value.result_kind = result_kind
        round_record.value.result_reason = reason.strip() if reason else None
        round_record.value.completed_at = utc_now_iso()
        if result_kind == DeclRoundResultKind.SUCCESS:
            round_record.value.status = DeclRoundStatus.COMPLETED
        elif result_kind == DeclRoundResultKind.BLOCKED:
            round_record.value.status = DeclRoundStatus.BLOCKED
        else:
            round_record.value.status = DeclRoundStatus.FAILED
        return self._write_round(repo_root, node_path=node_path, round_record=round_record.value)

    def get_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy_id: str,
    ) -> ServiceResult[DeclStrategyRecord]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        path = self.graph_store.strategy_path(repo_root, node_path=node_path, strategy_id=strategy_id)
        return self.runtime.foundation.store.read_json(path, DeclStrategyRecord)

    def list_strategies(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclStrategyRecord]]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        graph_root = self.graph_store.graph_root(repo_root, node_path=node_path)
        return self.runtime.foundation.store.list_json(graph_root / "strategies", DeclStrategyRecord)

    def get_round(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclRoundRecord]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        path = self.graph_store.round_path(repo_root, node_path=node_path, round_id=round_id)
        return self.runtime.foundation.store.read_json(path, DeclRoundRecord)

    def list_rounds(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclRoundRecord]]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        graph_root = self.graph_store.graph_root(repo_root, node_path=node_path)
        rounds = self.runtime.foundation.store.list_json(graph_root / "rounds", DeclRoundRecord)
        if not rounds.ok or rounds.value is None:
            return self.runtime.foundation.fail(rounds.issues)
        return self.runtime.foundation.ok(sorted(rounds.value, key=lambda item: (item.round_index, item.round_id)))

    def _write_strategy(
        self,
        repo_root: Path,
        *,
        node_path: str,
        strategy: DeclStrategyRecord,
    ) -> ServiceResult[DeclStrategyRecord]:
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
        round_record: DeclRoundRecord,
    ) -> ServiceResult[DeclRoundRecord]:
        path = self.graph_store.round_path(repo_root, node_path=node_path, round_id=round_record.round_id)
        written = self.runtime.foundation.store.write_json_atomic(path, round_record, mode=WriteMode.UPDATE_EXISTING)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(round_record)

    def _next_round_index(self, repo_root: Path, *, node_path: str) -> int:
        rounds = self.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return 1
        if not rounds.value:
            return 1
        return max(round_record.round_index for round_record in rounds.value) + 1

    def _running_round(self, repo_root: Path, *, node_path: str) -> DeclRoundRecord | None:
        rounds = self.list_rounds(repo_root, node_path=node_path)
        if not rounds.ok or rounds.value is None:
            return None
        for round_record in rounds.value:
            if round_record.status == DeclRoundStatus.RUNNING:
                return round_record
        return None
