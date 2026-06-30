"""Declaration catalog, revision, and round change planning."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclChangeRecord,
    DeclDeleteClosureView,
    DeclLifecycle,
    DeclRecord,
    DeclRevisionRecord,
    DeclRoundRecord,
    DeclRoundStatus,
    DeclState,
)
from lean_constellation.services.decl_graph.strategy_round import StrategyRoundComponent
from lean_constellation.services.foundation import GateReport, ServiceResult, WriteMode

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclCatalogComponent:
    """Manage Decl catalog records, revisions, and planned round changes."""

    _ALLOWED_END_STATES = {DeclState.DECLARED, DeclState.PROVED}

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        graph_store: GraphStoreComponent,
        strategy_round: StrategyRoundComponent,
    ) -> None:
        self.runtime = runtime
        self.graph_store = graph_store
        self.strategy_round = strategy_round

    def create_decl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        kind: str,
        objective: str,
        summary: str,
        public: bool = False,
        end_after_state: DeclState | str = DeclState.DECLARED,
        module: str | None = None,
    ) -> ServiceResult[DeclChangeRecord]:
        end_state = self._coerce_end_state(end_after_state)
        if end_state is None:
            return self._unsupported_end_state(str(end_after_state))
        preflight = self._round_for_planning(repo_root, node_path=node_path, round_id=round_id)
        if not preflight.ok or preflight.value is None:
            return self.runtime.foundation.fail(preflight.issues)
        if not all([name.strip() if name else "", kind.strip() if kind else "", objective.strip() if objective else "", summary.strip() if summary else ""]):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_create_fields_required", "Decl create requires name, kind, objective, and summary.")
            )
        if self.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=name).exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("duplicate_decl", "Declaration already exists.", object_ref=name)
            )

        decl = DeclRecord(
            name=name,
            node_path=node_path,
            kind=kind,
            public=public,
            current_revision=1,
            revision_ids=[1],
            module=module.strip() if module else None,
            summary=summary,
        )
        revision = DeclRevisionRecord(
            decl_name=name,
            revision=1,
            state=DeclState.PLANNED,
            version_status="open",
            change_kind=DeclChangeKind.CREATE,
            module=module.strip() if module else None,
        )
        change = self._new_change(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            kind=DeclChangeKind.CREATE,
            decl_name=name,
            start_before_state=None,
            end_after_state=end_state,
            objective=objective,
            target_revision=1,
        )
        if not change.ok or change.value is None:
            return self.runtime.foundation.fail(change.issues)

        ensured = self.runtime.foundation.store.ensure_dir(
            self.graph_store.decl_revisions_dir(repo_root, node_path=node_path, decl_name=name)
        )
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        written_decl = self.runtime.foundation.store.write_json_atomic(
            self.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=name),
            decl,
            mode=WriteMode.CREATE_ONLY,
        )
        if not written_decl.ok:
            return self.runtime.foundation.fail(written_decl.issues)
        written_revision = self.runtime.foundation.store.write_json_atomic(
            self.graph_store.revision_path(repo_root, node_path=node_path, decl_name=name, revision=1),
            revision,
            mode=WriteMode.CREATE_ONLY,
        )
        if not written_revision.ok:
            return self.runtime.foundation.fail(written_revision.issues)
        attached = self._attach_change_to_round(repo_root, node_path=node_path, round_id=round_id, change=change.value)
        if not attached.ok:
            return self.runtime.foundation.fail(attached.issues)
        rebuilt = self.graph_store.rebuild_index(repo_root, node_path=node_path)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(change.value)

    def open_decl_update(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        objective: str,
        end_after_state: DeclState | str,
        start_before_state: DeclState | str | None = None,
    ) -> ServiceResult[DeclChangeRecord]:
        end_state = self._coerce_end_state(end_after_state)
        if end_state is None:
            return self._unsupported_end_state(str(end_after_state))
        preflight = self._round_for_planning(repo_root, node_path=node_path, round_id=round_id)
        if not preflight.ok:
            return self.runtime.foundation.fail(preflight.issues)
        decl = self.get_decl(repo_root, node_path=node_path, name=name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        latest = self.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=name,
            revision=decl.value.current_revision,
        )
        if not latest.ok or latest.value is None:
            return self.runtime.foundation.fail(latest.issues)
        if latest.value.version_status == "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_already_open",
                    "Current declaration revision is already open.",
                    object_ref=name,
                    current=str(latest.value.revision),
                )
            )
        start_state = DeclState(start_before_state) if start_before_state is not None else DeclState.PLANNED
        next_revision_id = max(decl.value.revision_ids) + 1
        next_revision = latest.value.model_copy(deep=True)
        next_revision.revision = next_revision_id
        next_revision.version_status = "open"
        next_revision.change_kind = DeclChangeKind.UPDATE
        next_revision.updated_at = utc_now_iso()
        self._reset_revision_to_state(next_revision, start_state)

        decl.value.current_revision = next_revision_id
        decl.value.revision_ids.append(next_revision_id)
        decl.value.updated_at = utc_now_iso()
        change = self._new_change(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            kind=DeclChangeKind.UPDATE,
            decl_name=name,
            start_before_state=start_state,
            end_after_state=end_state,
            objective=objective,
            target_revision=next_revision_id,
        )
        if not change.ok or change.value is None:
            return self.runtime.foundation.fail(change.issues)
        written_revision = self.runtime.foundation.store.write_json_atomic(
            self.graph_store.revision_path(repo_root, node_path=node_path, decl_name=name, revision=next_revision_id),
            next_revision,
            mode=WriteMode.CREATE_ONLY,
        )
        if not written_revision.ok:
            return self.runtime.foundation.fail(written_revision.issues)
        decl_write = self._write_decl(repo_root, node_path=node_path, decl=decl.value)
        if not decl_write.ok:
            return self.runtime.foundation.fail(decl_write.issues)
        attached = self._attach_change_to_round(repo_root, node_path=node_path, round_id=round_id, change=change.value)
        if not attached.ok:
            return self.runtime.foundation.fail(attached.issues)
        return self.runtime.foundation.ok(change.value)

    def mark_decl_delete(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        objective: str,
    ) -> ServiceResult[DeclChangeRecord]:
        preflight = self._round_for_planning(repo_root, node_path=node_path, round_id=round_id)
        if not preflight.ok:
            return self.runtime.foundation.fail(preflight.issues)
        decl = self.get_decl(repo_root, node_path=node_path, name=name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        latest = self.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=name,
            revision=decl.value.current_revision,
        )
        if not latest.ok or latest.value is None:
            return self.runtime.foundation.fail(latest.issues)
        change = self._new_change(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            kind=DeclChangeKind.DELETE,
            decl_name=name,
            start_before_state=latest.value.state,
            end_after_state=None,
            objective=objective,
            target_revision=latest.value.revision,
        )
        if not change.ok or change.value is None:
            return self.runtime.foundation.fail(change.issues)
        attached = self._attach_change_to_round(repo_root, node_path=node_path, round_id=round_id, change=change.value)
        if not attached.ok:
            return self.runtime.foundation.fail(attached.issues)
        return self.runtime.foundation.ok(change.value)

    def commit_decl_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int | None = None,
        state: DeclState | str | None = None,
    ) -> ServiceResult[DeclRevisionRecord]:
        decl = self.get_decl(repo_root, node_path=node_path, name=name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        target_revision = revision or decl.value.current_revision
        record = self.get_decl_revision(repo_root, node_path=node_path, name=name, revision=target_revision)
        if not record.ok or record.value is None:
            return self.runtime.foundation.fail(record.issues)
        if record.value.version_status != "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_open",
                    "Only an open revision can be committed.",
                    object_ref=name,
                    current=record.value.version_status,
                )
            )
        if state is not None:
            record.value.state = DeclState(state)
        record.value.version_status = "committed"
        record.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=record.value)

    def get_decl(self, repo_root: Path, *, node_path: str, name: str) -> ServiceResult[DeclRecord]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        return self.runtime.foundation.store.read_json(
            self.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=name),
            DeclRecord,
        )

    def list_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclRecord]]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        graph_root = self.graph_store.graph_root(repo_root, node_path=node_path)
        decls: list[DeclRecord] = []
        issues = []
        for decl_json in sorted((graph_root / "decls").glob("*/decl.json")):
            loaded = self.runtime.foundation.store.read_json(decl_json, DeclRecord)
            if loaded.ok and loaded.value is not None:
                decls.append(loaded.value)
            else:
                issues.extend(loaded.issues)
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(sorted(decls, key=lambda item: item.name))

    def get_decl_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int,
    ) -> ServiceResult[DeclRevisionRecord]:
        return self.runtime.foundation.store.read_json(
            self.graph_store.revision_path(repo_root, node_path=node_path, decl_name=name, revision=revision),
            DeclRevisionRecord,
        )

    def get_decl_change(
        self,
        repo_root: Path,
        *,
        node_path: str,
        change_id: str,
    ) -> ServiceResult[DeclChangeRecord]:
        return self.runtime.foundation.store.read_json(
            self.graph_store.change_path(repo_root, node_path=node_path, change_id=change_id),
            DeclChangeRecord,
        )

    def compute_delete_closure(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[DeclDeleteClosureView]:
        requested = sorted({name.strip() for name in decl_names if name and name.strip()})
        if not requested:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("delete_closure_empty", "At least one declaration name is required.")
            )
        decls = self.list_decls(repo_root, node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        active = {decl.name: decl for decl in decls.value if decl.lifecycle == DeclLifecycle.ACTIVE}
        missing = sorted(name for name in requested if name not in active)
        reverse_deps = self._reverse_dependency_map(repo_root, node_path=node_path, decls=active)
        closure = set(requested)
        queue = list(requested)
        while queue:
            current = queue.pop(0)
            for dependent in reverse_deps.get(current, set()):
                if dependent not in closure:
                    closure.add(dependent)
                    queue.append(dependent)
        return self.runtime.foundation.ok(
            DeclDeleteClosureView(
                requested_decl_names=requested,
                closure_decl_names=sorted(closure),
                missing_decl_names=missing,
                summary=f"Delete closure includes {len(closure)} declarations.",
            )
        )

    def validate_round_draft(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[GateReport]:
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        issues = []
        changes: list[DeclChangeRecord] = []
        for change_id in round_record.value.change_ids:
            change = self.get_decl_change(repo_root, node_path=node_path, change_id=change_id)
            if not change.ok or change.value is None:
                issues.extend(change.issues)
            else:
                changes.append(change.value)
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("decl_round_draft", issues, summary="Round change records are invalid.")
            )

        create_names = [change.decl_name for change in changes if change.kind == DeclChangeKind.CREATE]
        if len(set(create_names)) != len(create_names):
            issues.append(self.runtime.foundation.issue("duplicate_create_change", "Round has duplicate create changes."))
        changed_names = {change.decl_name for change in changes}
        delete_names = [change.decl_name for change in changes if change.kind == DeclChangeKind.DELETE]
        if delete_names:
            closure = self.compute_delete_closure(repo_root, node_path=node_path, decl_names=delete_names)
            if not closure.ok or closure.value is None:
                issues.extend(closure.issues)
            else:
                missing_from_round = sorted(set(closure.value.closure_decl_names) - set(delete_names))
                if missing_from_round:
                    issues.append(
                        self.runtime.foundation.issue(
                            "delete_closure_incomplete",
                            "Round delete changes do not cover the downstream dependency closure.",
                            object_ref=round_id,
                            current=", ".join(missing_from_round),
                        )
                    )
        change_kind_by_decl = {change.decl_name: change.kind for change in changes}
        for decl_name in sorted(changed_names):
            decl = self.get_decl(repo_root, node_path=node_path, name=decl_name)
            if not decl.ok or decl.value is None:
                continue
            revision = self.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=decl_name,
                revision=decl.value.current_revision,
            )
            if not revision.ok or revision.value is None:
                continue
            internal_deps = []
            for dep_name in sorted(set(revision.value.decl_deps) & changed_names):
                if (
                    change_kind_by_decl.get(decl_name) == DeclChangeKind.DELETE
                    and change_kind_by_decl.get(dep_name) == DeclChangeKind.DELETE
                ):
                    continue
                internal_deps.append(dep_name)
            if internal_deps:
                issues.append(
                    self.runtime.foundation.issue(
                        "round_internal_dependency",
                        "Round changes must not depend on each other.",
                        object_ref=decl_name,
                        current=", ".join(internal_deps),
                    )
                )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("decl_round_draft", issues, summary=f"{len(issues)} round draft checks failed.")
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("decl_round_draft", summary="Round draft is valid.")
        )

    def _round_for_planning(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclRoundRecord]:
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        if round_record.value.status != DeclRoundStatus.DRAFT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_not_draft",
                    "Decl planning changes can only be added to a draft round.",
                    object_ref=round_id,
                    current=round_record.value.status.value,
                    expected=DeclRoundStatus.DRAFT.value,
                )
            )
        return self.runtime.foundation.ok(round_record.value)

    def _new_change(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        kind: DeclChangeKind,
        decl_name: str,
        start_before_state: DeclState | None,
        end_after_state: DeclState | None,
        objective: str,
        target_revision: int | None,
    ) -> ServiceResult[DeclChangeRecord]:
        if not objective or not objective.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_change_objective_required", "Decl change objective is required.", field="objective")
            )
        allocated = self.runtime.foundation.store.allocate_uuid(
            lambda candidate: self.graph_store.change_path(repo_root, node_path=node_path, change_id=candidate).exists(),
            prefix="change",
        )
        if not allocated.ok or allocated.value is None:
            return self.runtime.foundation.fail(allocated.issues)
        return self.runtime.foundation.ok(
            DeclChangeRecord(
                change_id=allocated.value,
                node_path=node_path,
                round_id=round_id,
                kind=kind,
                decl_name=decl_name,
                start_before_state=start_before_state,
                end_after_state=end_after_state,
                objective=objective,
                target_revision=target_revision,
            )
        )

    def _attach_change_to_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        change: DeclChangeRecord,
    ) -> ServiceResult[DeclChangeRecord]:
        written = self.runtime.foundation.store.write_json_atomic(
            self.graph_store.change_path(repo_root, node_path=node_path, change_id=change.change_id),
            change,
            mode=WriteMode.CREATE_ONLY,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        if change.change_id not in round_record.value.change_ids:
            round_record.value.change_ids.append(change.change_id)
        round_write = self.strategy_round._write_round(repo_root, node_path=node_path, round_record=round_record.value)
        if not round_write.ok:
            return self.runtime.foundation.fail(round_write.issues)
        return self.runtime.foundation.ok(change)

    def _write_decl(self, repo_root: Path, *, node_path: str, decl: DeclRecord) -> ServiceResult[DeclRecord]:
        written = self.runtime.foundation.store.write_json_atomic(
            self.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=decl.name),
            decl,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(decl)

    def _write_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        revision: DeclRevisionRecord,
    ) -> ServiceResult[DeclRevisionRecord]:
        written = self.runtime.foundation.store.write_json_atomic(
            self.graph_store.revision_path(
                repo_root,
                node_path=node_path,
                decl_name=revision.decl_name,
                revision=revision.revision,
            ),
            revision,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(revision)

    def _reset_revision_to_state(self, revision: DeclRevisionRecord, state: DeclState) -> None:
        revision.state = state
        if state == DeclState.PLANNED:
            revision.statement_nl = None
            revision.statement_origin = []
            revision.statement_deps = []
            revision.statement_lean_code = None
            revision.statement_lean_check = None
            revision.proof_nl = None
            revision.proof_origin = []
            revision.proof_deps = []
            revision.proof_lean_code = None
            revision.proof_lean_check = None
            revision.decl_deps = []
        elif state == DeclState.SPECIFIED:
            revision.statement_lean_code = None
            revision.statement_lean_check = None
            revision.proof_nl = None
            revision.proof_origin = []
            revision.proof_deps = []
            revision.proof_lean_code = None
            revision.proof_lean_check = None
            revision.decl_deps = sorted(set(revision.statement_deps))
        elif state == DeclState.DECLARED:
            revision.proof_nl = None
            revision.proof_origin = []
            revision.proof_deps = []
            revision.proof_lean_code = None
            revision.proof_lean_check = None
            revision.decl_deps = sorted(set(revision.statement_deps))
        else:
            revision.decl_deps = sorted(set(revision.statement_deps) | set(revision.proof_deps))

    def _reverse_dependency_map(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decls: dict[str, DeclRecord],
    ) -> dict[str, set[str]]:
        reverse: dict[str, set[str]] = {}
        for decl_name, decl in decls.items():
            revision = self.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=decl_name,
                revision=decl.current_revision,
            )
            if not revision.ok or revision.value is None:
                continue
            for dep_name in revision.value.decl_deps:
                reverse.setdefault(dep_name, set()).add(decl_name)
        return reverse

    def _coerce_end_state(self, value: DeclState | str) -> DeclState | None:
        try:
            state = DeclState(value)
        except ValueError:
            return None
        if state not in self._ALLOWED_END_STATES:
            return None
        return state

    def _unsupported_end_state(self, value: str) -> ServiceResult[DeclChangeRecord]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "unsupported_end_after_state",
                "Decl change end_after_state must be declared or proved.",
                current=value,
                expected="declared, proved",
            )
        )
