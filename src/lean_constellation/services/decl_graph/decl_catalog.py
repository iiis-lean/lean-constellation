"""Declaration catalog, revision, and round change planning."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.services.decl_graph.availability_policy import required_state_for_availability
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
    DeclRoundDraftDiscardReceipt,
    DeclRoundStatus,
    RepoDeclDep,
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
        target_state: DeclState | str = DeclState.DECLARED,
        require_target_state_satisfied: bool = True,
        anticipated_statement_dep_names: list[str] | None = None,
        anticipated_proof_dep_names: list[str] | None = None,
    ) -> ServiceResult[DeclChangeView]:
        end_state = self._coerce_end_state(target_state)
        if end_state is None:
            return self._unsupported_end_state(str(target_state))
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
                target_state=end_state,
                require_target_state_satisfied=require_target_state_satisfied,
                objective=objective,
                anticipated_statement_dep_names=anticipated_statement_dep_names or [],
                anticipated_proof_dep_names=anticipated_proof_dep_names or [],
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
        target_state: DeclState | str,
        base_revision: int | None = None,
        reset_to_state: DeclState | str | None = None,
        require_target_state_satisfied: bool = True,
        anticipated_statement_dep_names: list[str] | None = None,
        anticipated_proof_dep_names: list[str] | None = None,
    ) -> ServiceResult[DeclChangeView]:
        end_state = self._coerce_end_state(target_state)
        if end_state is None:
            return self._unsupported_end_state(str(target_state))
        preflight = self._round_for_planning(repo_root, node_path=node_path, round_id=round_id)
        if not preflight.ok:
            return self.runtime.foundation.fail(preflight.issues)
        decl = self.get_decl(repo_root, node_path=node_path, name=name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        current = self.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=name,
            revision=decl.value.current_revision,
        )
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        if current.value.status == "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_already_open",
                    "Current declaration revision is already open.",
                    object_ref=name,
                    current=str(current.value.revision),
                )
            )
        source_revision_id = base_revision if base_revision is not None else decl.value.current_revision
        if source_revision_id not in decl.value.revision_ids:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_base_revision_not_found",
                    "Update base_revision must identify an existing revision of this declaration.",
                    object_ref=name,
                    current=str(source_revision_id),
                )
            )
        source = self.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=name,
            revision=source_revision_id,
        )
        if not source.ok or source.value is None:
            return self.runtime.foundation.fail(source.issues)
        if source.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_base_revision_not_committed",
                    "Update base_revision must be committed.",
                    object_ref=name,
                    current=f"{source_revision_id}:{source.value.status.value}",
                )
            )
        if reset_to_state is None:
            if self._state_reaches(source.value.state, end_state):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_update_reset_required",
                        "The selected base revision already reaches target_state; specify reset_to_state for an explicit redo range.",
                        object_ref=name,
                        current=source.value.state.value,
                        expected=end_state.value,
                    )
                )
            start_state = source.value.state
        else:
            try:
                start_state = DeclState(reset_to_state)
            except ValueError:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "unsupported_reset_to_state",
                        "reset_to_state must be a declaration pipeline state.",
                        object_ref=name,
                        current=str(reset_to_state),
                    )
                )
            if start_state in {DeclState.OBSOLETE} or not self._state_reaches(source.value.state, start_state):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_update_reset_above_base",
                        "reset_to_state cannot be later than the selected base revision state.",
                        object_ref=name,
                        current=start_state.value,
                        expected=source.value.state.value,
                    )
                )
        if self._state_reaches(start_state, end_state):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_update_range_empty_or_reversed",
                    "reset_to_state must be earlier than target_state so the update runs at least one stage.",
                    object_ref=name,
                    current=start_state.value,
                    expected=end_state.value,
                )
            )
        next_revision_id = max(decl.value.revision_ids) + 1
        next_revision = source.value.model_copy(deep=True)
        next_revision.revision = next_revision_id
        next_revision.status = DeclRevisionStatus.OPEN
        next_revision.change = DeclRevisionChange(
            kind=DeclChangeKind.UPDATE,
            base_revision=source_revision_id,
            reset_to_state=start_state,
            target_state=end_state,
            require_target_state_satisfied=require_target_state_satisfied,
            objective=objective,
            anticipated_statement_dep_names=anticipated_statement_dep_names or [],
            anticipated_proof_dep_names=anticipated_proof_dep_names or [],
        )
        next_revision.updated_at = utc_now_iso()
        self._reset_revision_to_state(next_revision, start_state)
        self._rebind_same_node_dependencies_to_current(
            repo_root,
            node_path=node_path,
            decl_name=name,
            revision=next_revision,
        )

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
            base_revision=latest.value.revision,
            reset_to_state=latest.value.state,
            target_state=DeclState.OBSOLETE,
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

    def discard_round_draft(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        reason: str,
        discarded_by: str,
    ) -> ServiceResult[DeclRoundDraftDiscardReceipt]:
        """Atomically roll back every planned revision in an unsubmitted draft round."""

        normalized_reason = reason.strip()
        normalized_actor = discarded_by.strip()
        if not normalized_reason:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_discard_reason_required",
                    "Discarding a draft declaration round requires a concrete reason.",
                    object_ref=round_id,
                    field="reason",
                )
            )
        if not normalized_actor:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_discard_actor_required",
                    "Discarding a draft declaration round requires an actor.",
                    object_ref=round_id,
                    field="discarded_by",
                )
            )

        loaded_round = self.strategy_round.get_round(
            repo_root,
            node_path=node_path,
            round_id=round_id,
        )
        if not loaded_round.ok or loaded_round.value is None:
            return self.runtime.foundation.fail(loaded_round.issues)
        round_record = loaded_round.value
        if round_record.status == DeclRoundStatus.DISCARDED:
            if round_record.discard_reason != normalized_reason:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "round_discard_conflict",
                        "The draft round was already discarded for a different reason.",
                        object_ref=round_id,
                        current=round_record.discard_reason,
                        expected=normalized_reason,
                    )
                )
            return self.runtime.foundation.ok(
                self._discard_receipt(round_record, changed=False)
            )
        if round_record.status != DeclRoundStatus.DRAFT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_not_discardable",
                    "Only an unsubmitted draft declaration round can be discarded.",
                    object_ref=round_id,
                    current=round_record.status.value,
                    expected=DeclRoundStatus.DRAFT.value,
                )
            )
        if (
            round_record.started_at is not None
            or round_record.execution_result_kind is not None
            or round_record.execution_reason is not None
            or round_record.execution_completed_at is not None
            or round_record.result_kind is not None
            or round_record.result_reason is not None
            or round_record.plan_closeout_acknowledged_at is not None
            or round_record.plan_closeout_acknowledged_by is not None
            or round_record.committed_at is not None
            or round_record.change_summaries
            or round_record.summary is not None
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_discard_started_truth_present",
                    "A draft with execution or closeout truth cannot be discarded.",
                    object_ref=round_id,
                )
            )

        created_decl_names: list[str] = []
        restored_decl_revisions: dict[str, int] = {}
        updated_decls: dict[str, Decl] = {}
        revisions_to_delete: list[tuple[str, int]] = []
        seen_decl_names: set[str] = set()
        for ref in round_record.revision_refs:
            if ref.decl_name in seen_decl_names:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "round_discard_duplicate_decl",
                        "A discardable draft can contain at most one planned revision per declaration.",
                        object_ref=ref.decl_name,
                    )
                )
            seen_decl_names.add(ref.decl_name)
            loaded_decl = self.get_decl(
                repo_root,
                node_path=node_path,
                name=ref.decl_name,
            )
            if not loaded_decl.ok or loaded_decl.value is None:
                return self.runtime.foundation.fail(loaded_decl.issues)
            loaded_revision = self.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=ref.decl_name,
                revision=ref.revision,
            )
            if not loaded_revision.ok or loaded_revision.value is None:
                return self.runtime.foundation.fail(loaded_revision.issues)
            decl = loaded_decl.value
            revision = loaded_revision.value
            if decl.current_revision != ref.revision or revision.status != DeclRevisionStatus.OPEN:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "round_discard_revision_not_open_head",
                        "Every discarded revision must be the current open declaration head.",
                        object_ref=ref.decl_name,
                        current=f"{decl.current_revision}:{revision.status.value}",
                        expected=f"{ref.revision}:{DeclRevisionStatus.OPEN.value}",
                    )
                )
            expected_change_id = self._change_id_for_revision(
                ref.decl_name,
                ref.revision,
            )
            if ref.change_id != expected_change_id or revision.change is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "round_discard_revision_mismatch",
                        "Draft round revision truth does not match its change reference.",
                        object_ref=ref.change_id,
                        current=expected_change_id,
                    )
                )

            revisions_to_delete.append((ref.decl_name, ref.revision))
            if revision.change.kind == DeclChangeKind.CREATE:
                if ref.revision != 1 or decl.revision_ids != [1]:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "round_discard_create_history_conflict",
                            "A discarded create must be the declaration's only revision.",
                            object_ref=ref.decl_name,
                            current=", ".join(str(item) for item in decl.revision_ids),
                            expected="1",
                        )
                    )
                created_decl_names.append(ref.decl_name)
                continue

            remaining_revision_ids = [
                item for item in decl.revision_ids if item != ref.revision
            ]
            if not remaining_revision_ids:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "round_discard_restore_missing",
                        "A discarded update or delete requires a prior committed revision.",
                        object_ref=ref.decl_name,
                    )
                )
            restored_revision = max(remaining_revision_ids)
            loaded_restored = self.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=ref.decl_name,
                revision=restored_revision,
            )
            if (
                not loaded_restored.ok
                or loaded_restored.value is None
                or loaded_restored.value.status != DeclRevisionStatus.COMMITTED
            ):
                return self.runtime.foundation.fail(
                    loaded_restored.issues
                    or [
                        self.runtime.foundation.issue(
                            "round_discard_restore_not_committed",
                            "The declaration head restored by discard must be committed.",
                            object_ref=ref.decl_name,
                            current=str(restored_revision),
                        )
                    ]
                )
            restored_decl = decl.model_copy(deep=True)
            restored_decl.current_revision = restored_revision
            restored_decl.revision_ids = remaining_revision_ids
            restored_decl.updated_at = utc_now_iso()
            updated_decls[ref.decl_name] = restored_decl
            restored_decl_revisions[ref.decl_name] = restored_revision

        discarded_at = utc_now_iso()
        discarded_round = round_record.model_copy(deep=True)
        discarded_round.status = DeclRoundStatus.DISCARDED
        discarded_round.discarded_revision_refs = list(round_record.revision_refs)
        discarded_round.discarded_created_decl_names = sorted(created_decl_names)
        discarded_round.discarded_restored_decl_revisions = dict(
            sorted(restored_decl_revisions.items())
        )
        discarded_round.discard_reason = normalized_reason
        discarded_round.discarded_by = normalized_actor
        discarded_round.discarded_at = discarded_at
        discarded_round.revision_refs = []

        index = self.graph_store.get_index(repo_root, node_path=node_path)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        updated_index = index.value.model_copy(deep=True)
        updated_index.decl_names = [
            name
            for name in updated_index.decl_names
            if name not in set(created_decl_names)
        ]
        updated_index.updated_at = discarded_at
        updated_index.summary = (
            f"DeclGraph index updated after discarding {round_id}: "
            f"{len(updated_index.decl_names)} decls, "
            f"{len(updated_index.round_ids)} rounds, "
            f"{len(updated_index.strategy_ids)} strategies."
        )

        with self.runtime.foundation.store.mutation(
            "discard_decl_round_draft"
        ) as mutation:
            for decl_name, revision_id in revisions_to_delete:
                mutation.stage_delete(
                    self.graph_store.revision_path(
                        repo_root,
                        node_path=node_path,
                        decl_name=decl_name,
                        revision=revision_id,
                    )
                )
                if decl_name in created_decl_names:
                    mutation.stage_delete(
                        self.graph_store.decl_record_path(
                            repo_root,
                            node_path=node_path,
                            decl_name=decl_name,
                        )
                    )
                else:
                    mutation.stage_json(
                        self.graph_store.decl_record_path(
                            repo_root,
                            node_path=node_path,
                            decl_name=decl_name,
                        ),
                        updated_decls[decl_name],
                        mode=WriteMode.UPDATE_EXISTING,
                    )
            mutation.stage_json(
                self.graph_store.round_path(
                    repo_root,
                    node_path=node_path,
                    round_id=round_id,
                ),
                discarded_round,
                mode=WriteMode.UPDATE_EXISTING,
            )
            mutation.stage_json(
                self.graph_store.index_path(repo_root, node_path=node_path),
                updated_index,
                mode=WriteMode.UPDATE_EXISTING,
            )
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        return self.runtime.foundation.ok(
            self._discard_receipt(discarded_round, changed=True)
        )

    def commit_decl_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int | None = None,
        state: DeclState | str | None = None,
        apply_delete_lifecycle: bool = True,
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
        if record.value.state == DeclState.OBSOLETE and apply_delete_lifecycle:
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
            if record.value.state == DeclState.OBSOLETE and apply_delete_lifecycle:
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
        decl_by_name: dict[str, Decl] = {}
        revision_by_decl: dict[str, DeclRevision] = {}
        for decl_name in sorted(changed_names):
            decl = self.get_decl(repo_root, node_path=node_path, name=decl_name)
            if not decl.ok or decl.value is None:
                continue
            decl_by_name[decl_name] = decl.value
            revision = self.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=decl_name,
                revision=decl.value.current_revision,
            )
            if not revision.ok or revision.value is None:
                continue
            revision_by_decl[decl_name] = revision.value
            actual_statement = {
                dep.ref.name
                for dep in revision.value.statement.deps
                if isinstance(dep, RepoDeclDep) and dep.ref.repo is None and dep.ref.node == node_path
            }
            actual_proof = {
                dep.ref.name
                for dep in (revision.value.proof.deps if revision.value.proof is not None else [])
                if isinstance(dep, RepoDeclDep) and dep.ref.repo is None and dep.ref.node == node_path
            }
            change = revision.value.change
            anticipated_statement = set(change.anticipated_statement_dep_names if change is not None else [])
            anticipated_proof = set(change.anticipated_proof_dep_names if change is not None else [])
            for dependency_stage, required_availability, dependency_names in (
                ("statement", ProofAvailability.DECLARED, actual_statement | anticipated_statement),
                ("proof", ProofAvailability.PROVED, actual_proof | anticipated_proof),
            ):
                for dep_name in sorted(dependency_names):
                    required_state = required_state_for_availability("theorem", required_availability)
                    if dep_name == decl_name:
                        issues.append(
                            self.runtime.foundation.issue(
                                "round_dependency_cycle",
                                "A declaration cannot depend on itself.",
                                object_ref=decl_name,
                                details={
                                    "consumer": decl_name,
                                    "provider": dep_name,
                                    "dependency_stage": dependency_stage,
                                    "required_state": required_state.value,
                                },
                                suggested_action="Remove the self dependency or plan the missing prerequisite as a separate declaration.",
                            )
                        )
                        continue
                    if dep_name not in changed_names:
                        provider = self.get_decl(repo_root, node_path=node_path, name=dep_name)
                        if not provider.ok or provider.value is None:
                            issues.append(
                                self.runtime.foundation.issue(
                                    "round_dependency_provider_missing",
                                    "A planned dependency provider does not exist in the current node.",
                                    object_ref=decl_name,
                                    details={
                                        "consumer": decl_name,
                                        "provider": dep_name,
                                        "dependency_stage": dependency_stage,
                                        "required_state": required_state.value,
                                    },
                                )
                            )
                            continue
                        required_state = required_state_for_availability(
                            provider.value.kind,
                            required_availability,
                        )
                        provider_revision = self.get_decl_revision(
                            repo_root,
                            node_path=node_path,
                            name=dep_name,
                            revision=provider.value.current_revision,
                        )
                        if (
                            not provider_revision.ok
                            or provider_revision.value is None
                            or not self._state_reaches(provider_revision.value.state, required_state)
                        ):
                            issues.append(
                                self.runtime.foundation.issue(
                                    "round_dependency_provider_not_ready",
                                    "A planned dependency provider has not reached the required state.",
                                    object_ref=decl_name,
                                    current=provider_revision.value.state.value if provider_revision.ok and provider_revision.value is not None else "missing",
                                    expected=required_state.value,
                                    details={
                                        "consumer": decl_name,
                                        "provider": dep_name,
                                        "dependency_stage": dependency_stage,
                                        "required_state": required_state.value,
                                    },
                                    suggested_action=f"Complete {dep_name} through {required_state.value} before this round.",
                                )
                            )
                        continue
                    if (
                        change_kind_by_decl.get(decl_name) == DeclChangeKind.DELETE
                        and change_kind_by_decl.get(dep_name) == DeclChangeKind.DELETE
                    ):
                        continue
                    provider_record = decl_by_name.get(dep_name)
                    if provider_record is None:
                        loaded_decl = self.get_decl(repo_root, node_path=node_path, name=dep_name)
                        provider_record = loaded_decl.value if loaded_decl.ok else None
                    required_state = required_state_for_availability(
                        provider_record.kind if provider_record is not None else "theorem",
                        required_availability,
                    )
                    provider_revision = revision_by_decl.get(dep_name)
                    if provider_revision is None and provider_record is not None:
                        loaded = self.get_decl_revision(
                            repo_root,
                            node_path=node_path,
                            name=dep_name,
                            revision=provider_record.current_revision,
                        )
                        provider_revision = loaded.value if loaded.ok else None
                    provider_state = provider_revision.state if provider_revision is not None else DeclState.PLANNED
                    if not self._state_reaches(provider_state, required_state):
                        issues.append(
                            self.runtime.foundation.issue(
                                "round_internal_dependency",
                                "The consumer requires a provider that will only be produced by the same round.",
                                object_ref=decl_name,
                                current=provider_state.value,
                                expected=required_state.value,
                                details={
                                    "consumer": decl_name,
                                    "provider": dep_name,
                                    "dependency_stage": dependency_stage,
                                    "required_state": required_state.value,
                                    "provider_state_at_round_start": provider_state.value,
                                    "provider_change_kind": change_kind_by_decl[dep_name].value,
                                },
                                suggested_action=f"Split the round: complete {dep_name} first, then run {decl_name}.",
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

    def _discard_receipt(
        self,
        round_record: DeclGraphRound,
        *,
        changed: bool,
    ) -> DeclRoundDraftDiscardReceipt:
        return DeclRoundDraftDiscardReceipt(
            round_id=round_record.round_id,
            strategy_id=round_record.strategy_id,
            changed=changed,
            discarded_change_ids=[
                item.change_id for item in round_record.discarded_revision_refs
            ],
            deleted_created_decl_names=list(
                round_record.discarded_created_decl_names
            ),
            restored_decl_revisions=(
                dict(round_record.discarded_restored_decl_revisions) or None
            ),
            reason=round_record.discard_reason or "",
            discarded_by=round_record.discarded_by or "",
            discarded_at=round_record.discarded_at or "",
            summary=(
                f"Discarded unsubmitted draft {round_record.round_id}; "
                f"rolled back {len(round_record.discarded_revision_refs)} planned changes."
            ),
        )

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
            base_revision=revision.change.base_revision,
            reset_to_state=revision.change.reset_to_state,
            target_state=revision.change.target_state,
            require_target_state_satisfied=revision.change.require_target_state_satisfied,
            objective=revision.change.objective or "",
            summary=revision.change.summary,
            anticipated_statement_dep_names=list(revision.change.anticipated_statement_dep_names),
            anticipated_proof_dep_names=list(revision.change.anticipated_proof_dep_names),
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

    def _rebind_same_node_dependencies_to_current(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        revision: DeclRevision,
    ) -> None:
        proof_deps = revision.proof.deps if revision.proof is not None else []
        for dep in [*revision.statement.deps, *proof_deps]:
            if (
                not isinstance(dep, RepoDeclDep)
                or dep.ref.repo is not None
                or dep.ref.node not in {node_path, "Main"}
                or dep.ref.name == decl_name
            ):
                continue
            provider = self.get_decl(repo_root, node_path=node_path, name=dep.ref.name)
            if provider.ok and provider.value is not None:
                dep.ref.revision = provider.value.current_revision

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
            proof_deps = revision.value.proof.deps if revision.value.proof is not None else []
            for dep_name in sorted({dep.ref.name for dep in [*revision.value.statement.deps, *proof_deps]}):
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

    def _state_reaches(self, current: DeclState, target: DeclState) -> bool:
        order = {
            DeclState.PLANNED: 0,
            DeclState.SPECIFIED: 1,
            DeclState.DECLARED: 2,
            DeclState.PROOF_PLANNED: 3,
            DeclState.PROVED: 4,
        }
        return current in order and target in order and order[current] >= order[target]

    def _unsupported_end_state(self, value: str) -> ServiceResult[DeclChangeView]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "unsupported_target_state",
                "Decl change target_state must be declared or proved.",
                current=value,
                expected="declared, proved",
            )
        )
