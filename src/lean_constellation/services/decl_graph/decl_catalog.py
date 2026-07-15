"""Declaration catalog, revision, and round change planning."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclChangeView,
    DeclDeleteClosureView,
    DeclLifecycle,
    Decl,
    DeclRevisionChange,
    DeclRevisionRef,
    DeclRevision,
    DeclRevisionStatus,
    DeclGraphRound,
    DeclRoundStatus,
    DeclStatement,
    DeclState,
)
from lean_constellation.services.decl_graph.strategy_round import StrategyRoundComponent
from lean_constellation.services.foundation import GateReport, ServiceResult, WriteMode
from lean_constellation.services.foundation.module_layout import NativeModuleLayoutError, native_decl_module

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices
    from lean_constellation.services.decl_graph.release_guard import DeclReleaseGuard


class DeclCatalogComponent:
    """Manage Decl catalog records, revisions, and planned round changes."""

    _ALLOWED_END_STATES = {DeclState.DECLARED, DeclState.PROVED}

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        graph_store: GraphStoreComponent,
        strategy_round: StrategyRoundComponent,
        release_guard: "DeclReleaseGuard | None" = None,
    ) -> None:
        self.runtime = runtime
        self.graph_store = graph_store
        self.strategy_round = strategy_round
        self.release_guard = release_guard

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
        require_target_state_satisfied: bool = True,
    ) -> ServiceResult[DeclChangeView]:
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
        try:
            module = native_decl_module(repo_root, node_path=node_path, kind=kind, decl_name=name)
        except NativeModuleLayoutError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "native_decl_module_invalid",
                    str(exc),
                    object_ref=f"{node_path}:{name}",
                    field="name",
                )
            )

        decl = Decl(
            name=name,
            node_path=node_path,
            kind=kind,
            public=public,
            current_revision=1,
            revision_ids=[1],
            module=module,
            summary=summary,
        )
        revision = DeclRevision(
            revision=1,
            state=DeclState.PLANNED,
            status=DeclRevisionStatus.OPEN,
            change=DeclRevisionChange(
                kind=DeclChangeKind.CREATE,
                end_after_state=end_state,
                require_target_state_satisfied=require_target_state_satisfied,
                objective=objective,
            ),
        )
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
        attached = self._attach_revision_to_round(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=name,
            revision=revision,
        )
        if not attached.ok:
            return self.runtime.foundation.fail(attached.issues)
        rebuilt = self.graph_store.rebuild_index(repo_root, node_path=node_path)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(
            self._change_view_from_revision(node_path=node_path, round_id=round_id, decl_name=name, revision=revision)
        )

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
        require_target_state_satisfied: bool = True,
    ) -> ServiceResult[DeclChangeView]:
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
        if latest.value.status == "open":
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
        next_revision.status = DeclRevisionStatus.OPEN
        next_revision.change = DeclRevisionChange(
            kind=DeclChangeKind.UPDATE,
            start_before_state=start_state,
            end_after_state=end_state,
            require_target_state_satisfied=require_target_state_satisfied,
            objective=objective,
        )
        next_revision.updated_at = utc_now_iso()
        self._reset_revision_to_state(next_revision, start_state)

        if self.release_guard is not None:
            guarded = self.release_guard.check_update_candidate(
                repo_root,
                node_path=node_path,
                decl=decl.value,
                candidate=next_revision,
            )
            if not guarded.ok:
                return self.runtime.foundation.fail(guarded.issues)

        decl.value.current_revision = next_revision_id
        decl.value.revision_ids.append(next_revision_id)
        decl.value.updated_at = utc_now_iso()
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        ref = self._revision_ref(name, next_revision)
        if ref.change_id not in round_record.value.change_ids:
            round_record.value.revision_refs.append(ref)
        with self.runtime.foundation.store.mutation("open_decl_update") as mutation:
            mutation.stage_json(
                self.graph_store.revision_path(repo_root, node_path=node_path, decl_name=name, revision=next_revision_id),
                next_revision,
                mode=WriteMode.CREATE_ONLY,
            )
            mutation.stage_json(
                self.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=name),
                decl.value,
                mode=WriteMode.UPDATE_EXISTING,
            )
            mutation.stage_json(
                self.graph_store.round_path(repo_root, node_path=node_path, round_id=round_id),
                round_record.value,
                mode=WriteMode.UPDATE_EXISTING,
            )
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        rebuilt = self.graph_store.rebuild_index(repo_root, node_path=node_path)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(
            self._change_view_from_revision(node_path=node_path, round_id=round_id, decl_name=name, revision=next_revision)
        )

    def mark_decl_delete(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        name: str,
        objective: str,
    ) -> ServiceResult[DeclChangeView]:
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
        if latest.value.status == "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_already_open",
                    "Current declaration revision is already open.",
                    object_ref=name,
                    current=str(latest.value.revision),
                )
            )
        if self.release_guard is not None:
            guarded = self.release_guard.check_delete(repo_root, node_path=node_path, decl_name=name)
            if not guarded.ok:
                return self.runtime.foundation.fail(guarded.issues)
        next_revision_id = max(decl.value.revision_ids) + 1
        next_revision = latest.value.model_copy(deep=True)
        next_revision.revision = next_revision_id
        next_revision.status = DeclRevisionStatus.OPEN
        next_revision.state = DeclState.OBSOLETE
        next_revision.change = DeclRevisionChange(
            kind=DeclChangeKind.DELETE,
            start_before_state=latest.value.state,
            end_after_state=DeclState.OBSOLETE,
            objective=objective,
        )
        next_revision.updated_at = utc_now_iso()
        decl.value.current_revision = next_revision_id
        decl.value.revision_ids.append(next_revision_id)
        decl.value.updated_at = utc_now_iso()
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        ref = self._revision_ref(name, next_revision)
        if ref.change_id not in round_record.value.change_ids:
            round_record.value.revision_refs.append(ref)
        with self.runtime.foundation.store.mutation("mark_decl_delete") as mutation:
            mutation.stage_json(
                self.graph_store.revision_path(repo_root, node_path=node_path, decl_name=name, revision=next_revision_id),
                next_revision,
                mode=WriteMode.CREATE_ONLY,
            )
            mutation.stage_json(
                self.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=name),
                decl.value,
                mode=WriteMode.UPDATE_EXISTING,
            )
            mutation.stage_json(
                self.graph_store.round_path(repo_root, node_path=node_path, round_id=round_id),
                round_record.value,
                mode=WriteMode.UPDATE_EXISTING,
            )
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        rebuilt = self.graph_store.rebuild_index(repo_root, node_path=node_path)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(
            self._change_view_from_revision(node_path=node_path, round_id=round_id, decl_name=name, revision=next_revision)
        )

    def commit_decl_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int | None = None,
        state: DeclState | str | None = None,
    ) -> ServiceResult[DeclRevision]:
        decl = self.get_decl(repo_root, node_path=node_path, name=name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        target_revision = revision or decl.value.current_revision
        record = self.get_decl_revision(repo_root, node_path=node_path, name=name, revision=target_revision)
        if not record.ok or record.value is None:
            return self.runtime.foundation.fail(record.issues)
        if record.value.status != "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_open",
                    "Only an open revision can be committed.",
                    object_ref=name,
                    current=record.value.status.value,
                )
            )
        if target_revision != decl.value.current_revision:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_current",
                    "Only the declaration's current open revision can be committed.",
                    object_ref=name,
                    current=str(target_revision),
                    expected=str(decl.value.current_revision),
                )
            )
        if state is not None:
            record.value.state = DeclState(state)
        if self.release_guard is not None:
            guarded = self.release_guard.check_update_candidate(
                repo_root,
                node_path=node_path,
                decl=decl.value,
                candidate=record.value,
            )
            if not guarded.ok:
                return self.runtime.foundation.fail(guarded.issues)
        record.value.status = DeclRevisionStatus.COMMITTED
        record.value.updated_at = utc_now_iso()
        if record.value.state == DeclState.OBSOLETE:
            decl.value.lifecycle = DeclLifecycle.DELETED
            decl.value.updated_at = utc_now_iso()
        with self.runtime.foundation.store.mutation("commit_decl_revision") as mutation:
            mutation.stage_json(
                self.graph_store.revision_path(
                    repo_root, node_path=node_path, decl_name=name, revision=target_revision
                ),
                record.value,
                mode=WriteMode.UPDATE_EXISTING,
            )
            if record.value.state == DeclState.OBSOLETE:
                mutation.stage_json(
                    self.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=name),
                    decl.value,
                    mode=WriteMode.UPDATE_EXISTING,
                )
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        rebuilt = self.graph_store.rebuild_index(repo_root, node_path=node_path)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(record.value)

    def get_decl(self, repo_root: Path, *, node_path: str, name: str) -> ServiceResult[Decl]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        return self.runtime.foundation.store.read_json(
            self.graph_store.decl_record_path(repo_root, node_path=node_path, decl_name=name),
            Decl,
        )

    def list_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[Decl]]:
        ensured = self.graph_store.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        graph_root = self.graph_store.graph_root(repo_root, node_path=node_path)
        decls: list[Decl] = []
        issues = []
        for decl_json in sorted((graph_root / "decls").glob("*/decl.json")):
            loaded = self.runtime.foundation.store.read_json(decl_json, Decl)
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
    ) -> ServiceResult[DeclRevision]:
        return self.runtime.foundation.store.read_json(
            self.graph_store.revision_path(repo_root, node_path=node_path, decl_name=name, revision=revision),
            DeclRevision,
        )

    def get_decl_change(
        self,
        repo_root: Path,
        *,
        node_path: str,
        change_id: str,
    ) -> ServiceResult[DeclChangeView]:
        located = self._locate_change_revision(repo_root, node_path=node_path, change_id=change_id)
        if not located.ok or located.value is None:
            return self.runtime.foundation.fail(located.issues)
        round_id, decl_name, revision = located.value
        return self.runtime.foundation.ok(
            self._change_view_from_revision(
                node_path=node_path,
                round_id=round_id,
                decl_name=decl_name,
                revision=revision,
            )
        )

    def list_round_changes(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[list[DeclChangeView]]:
        entries = self.list_round_revisions(repo_root, node_path=node_path, round_id=round_id)
        if not entries.ok or entries.value is None:
            return self.runtime.foundation.fail(entries.issues)
        return self.runtime.foundation.ok(
            [
                self._change_view_from_revision(
                    node_path=node_path,
                    round_id=round_id,
                    decl_name=decl_name,
                    revision=revision,
                )
                for decl_name, revision in entries.value
            ]
        )

    def list_round_revisions(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
    ) -> ServiceResult[list[tuple[str, DeclRevision]]]:
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        revisions: list[tuple[str, DeclRevision]] = []
        issues = []
        for ref in round_record.value.revision_refs:
            revision = self.get_decl_revision(repo_root, node_path=node_path, name=ref.decl_name, revision=ref.revision)
            if not revision.ok or revision.value is None:
                issues.extend(revision.issues)
                continue
            if revision.value.change is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "round_revision_missing_change",
                        "Round revision ref points to a revision without embedded change metadata.",
                        object_ref=ref.change_id,
                    )
                )
            revisions.append((ref.decl_name, revision.value))
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(revisions)

    def write_decl_change_summary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        change_id: str,
        summary: str,
    ) -> ServiceResult[DeclGraphRound]:
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("change_summary_required", "Decl change summary is required.", field="summary")
            )
        located = self._locate_change_revision(repo_root, node_path=node_path, change_id=change_id, round_id=round_id)
        if not located.ok or located.value is None:
            return self.runtime.foundation.fail(located.issues)
        _round_id, decl_name, revision = located.value
        if revision.change is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("round_revision_missing_change", "Decl revision has no embedded change metadata.", object_ref=change_id)
            )
        revision.change.summary = summary.strip()
        revision.updated_at = utc_now_iso()
        written = self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        return self.runtime.foundation.ok(round_record.value)

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
        changes = self.list_round_changes(repo_root, node_path=node_path, round_id=round_id)
        if not changes.ok or changes.value is None:
            return self.runtime.foundation.fail(changes.issues)
        missing = [change.change_id for change in changes.value if not change.summary]
        if missing:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_change_summary_missing",
                    "Every round change must have its own summary before writing the round summary.",
                    object_ref=round_id,
                    current=", ".join(missing),
                )
            )
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        round_record.value.summary = summary.strip()
        return self.strategy_round._write_round(repo_root, node_path=node_path, round_record=round_record.value)

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
        changes = self.list_round_changes(repo_root, node_path=node_path, round_id=round_id)
        if not changes.ok or changes.value is None:
            issues.extend(changes.issues)
            changes_value: list[DeclChangeView] = []
        else:
            changes_value = changes.value
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("decl_round_draft", issues, summary="Round change records are invalid.")
            )

        create_names = [change.decl_name for change in changes_value if change.kind == DeclChangeKind.CREATE]
        if len(set(create_names)) != len(create_names):
            issues.append(self.runtime.foundation.issue("duplicate_create_change", "Round has duplicate create changes."))
        changed_names = {change.decl_name for change in changes_value}
        delete_names = [change.decl_name for change in changes_value if change.kind == DeclChangeKind.DELETE]
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
        change_kind_by_decl = {change.decl_name: change.kind for change in changes_value}
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
            revision_deps = set(revision.value.statement_deps) | set(revision.value.proof_deps)
            for dep_name in sorted(revision_deps & changed_names):
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

    def _round_for_planning(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[DeclGraphRound]:
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

    def _attach_revision_to_round(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        revision: DeclRevision,
    ) -> ServiceResult[DeclRevisionRef]:
        if revision.change is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_change_required",
                    "Round revisions must include embedded change metadata.",
                    object_ref=decl_name,
                )
            )
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        ref = self._revision_ref(decl_name, revision)
        if ref.change_id not in round_record.value.change_ids:
            round_record.value.revision_refs.append(ref)
        round_write = self.strategy_round._write_round(repo_root, node_path=node_path, round_record=round_record.value)
        if not round_write.ok:
            return self.runtime.foundation.fail(round_write.issues)
        return self.runtime.foundation.ok(ref)

    def _locate_change_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        change_id: str,
        round_id: str | None = None,
    ) -> ServiceResult[tuple[str, str, DeclRevision]]:
        if not change_id or not change_id.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("change_id_required", "Decl change id is required.", field="change_id")
            )
        if round_id:
            round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
            if not round_record.ok or round_record.value is None:
                return self.runtime.foundation.fail(round_record.issues)
            rounds = [round_record.value]
        else:
            rounds = None
        if rounds is None:
            loaded_rounds = self.strategy_round.list_rounds(repo_root, node_path=node_path)
            if not loaded_rounds.ok or loaded_rounds.value is None:
                return self.runtime.foundation.fail(loaded_rounds.issues)
            rounds = loaded_rounds.value
        for round_record in rounds:
            if round_record is None:
                continue
            for ref in round_record.revision_refs:
                if ref.change_id != change_id:
                    continue
                revision = self.get_decl_revision(repo_root, node_path=node_path, name=ref.decl_name, revision=ref.revision)
                if not revision.ok or revision.value is None:
                    return self.runtime.foundation.fail(revision.issues)
                return self.runtime.foundation.ok((round_record.round_id, ref.decl_name, revision.value))
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue("decl_change_not_found", "Decl change was not found in round revision refs.", object_ref=change_id)
        )

    def _revision_ref(self, decl_name: str, revision: DeclRevision) -> DeclRevisionRef:
        return DeclRevisionRef(
            change_id=self._change_id_for_revision(decl_name, revision.revision),
            decl_name=decl_name,
            revision=revision.revision,
        )

    def _change_id_for_revision(self, decl_name: str, revision: int) -> str:
        return f"{decl_name}@rev:{revision}"

    def _change_view_from_revision(
        self,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        revision: DeclRevision,
    ) -> DeclChangeView:
        if revision.change is None:
            raise ValueError(f"Revision {decl_name}@{revision.revision} has no embedded change metadata.")
        return DeclChangeView(
            change_id=self._change_id_for_revision(decl_name, revision.revision),
            node_path=node_path,
            round_id=round_id,
            kind=revision.change.kind,
            decl_name=decl_name,
            start_before_state=revision.change.start_before_state,
            end_after_state=revision.change.end_after_state,
            require_target_state_satisfied=revision.change.require_target_state_satisfied,
            objective=revision.change.objective or "",
            summary=revision.change.summary,
            target_revision=revision.revision,
            created_at=revision.updated_at,
            updated_at=revision.updated_at,
        )

    def _write_decl(self, repo_root: Path, *, node_path: str, decl: Decl) -> ServiceResult[Decl]:
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
        decl_name: str,
        revision: DeclRevision,
    ) -> ServiceResult[DeclRevision]:
        written = self.runtime.foundation.store.write_json_atomic(
            self.graph_store.revision_path(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                revision=revision.revision,
            ),
            revision,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(revision)

    def _reset_revision_to_state(self, revision: DeclRevision, state: DeclState) -> None:
        revision.state = state
        if state == DeclState.PLANNED:
            revision.lean_decl_name = None
            revision.statement = DeclStatement()
            revision.proof = None
        elif state == DeclState.SPECIFIED:
            revision.lean_decl_name = None
            revision.statement.formal = None
            revision.proof = None
        elif state == DeclState.DECLARED:
            revision.proof = None
        elif state == DeclState.PROOF_PLANNED:
            if revision.proof is not None:
                revision.proof.formal = None
                if revision.proof.nl is None and not revision.proof.deps:
                    revision.proof = None

    def _reverse_dependency_map(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decls: dict[str, Decl],
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
            for dep_name in sorted(set(revision.value.statement_deps) | set(revision.value.proof_deps)):
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

    def _unsupported_end_state(self, value: str) -> ServiceResult[DeclChangeView]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "unsupported_end_after_state",
                "Decl change end_after_state must be declared or proved.",
                current=value,
                expected="declared, proved",
            )
        )
