"""Stage-specific mutation APIs for declaration revisions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.domain.lean_check import LeanCheck
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.models import DeclChangeKind, DeclDep, DeclOriginRef, DeclRevision, DeclRoundStatus, DeclState
from lean_constellation.services.decl_graph.strategy_round import StrategyRoundComponent
from lean_constellation.services.foundation import ServiceResult, WriteMode

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class StageMutationComponent:
    """Write fixed stage-owned fields to the current open revision."""

    _THEOREM_LIKE_KINDS = {"theorem", "lemma"}

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        graph_store: GraphStoreComponent,
        strategy_round: StrategyRoundComponent,
        decl_catalog: DeclCatalogComponent,
    ) -> None:
        self.runtime = runtime
        self.graph_store = graph_store
        self.strategy_round = strategy_round
        self.decl_catalog = decl_catalog

    def write_statement_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
        origin: list[dict[str, Any]] | None = None,
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevision]:
        if not nl or not nl.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_nl_required", "Statement NL text is required.", field="nl")
            )
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        revision.value.statement_nl = nl.strip()
        revision.value.statement_origin = self._normalize_origin(origin)
        revision.value.statement_deps = self._normalize_deps(deps)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def write_statement_nl_typed(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
        origin: list[DeclOriginRef],
        deps: list[DeclDep],
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if not nl.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("statement_nl_required", "Statement NL text is required.", field="nl"))
        revision.value.statement.nl = revision.value.statement.nl.model_copy(update={"text": nl.strip(), "origin": origin}) if revision.value.statement.nl is not None else None
        if revision.value.statement.nl is None:
            revision.value.statement_nl = nl.strip()
            revision.value.statement.nl.origin = origin
        revision.value.statement.deps = deps
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def set_statement_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
    ) -> ServiceResult[DeclRevision]:
        if not nl or not nl.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_nl_required", "Statement NL text is required.", field="nl")
            )
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        revision.value.statement_nl = nl.strip()
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def add_statement_origin(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        origin: DeclOriginRef,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        current = revision.value.statement.nl.origin if revision.value.statement.nl is not None else []
        revision.value.statement_origin = [item.model_dump(mode="json", exclude_none=True) for item in [*current, origin]]
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def remove_statement_origin(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        index: int,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        origins = list(revision.value.statement.nl.origin if revision.value.statement.nl is not None else [])
        if index < 0 or index >= len(origins):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_origin_index_invalid", "Statement origin index is out of range.", object_ref=decl_name, field="index")
            )
        del origins[index]
        revision.value.statement_origin = [item.model_dump(mode="json", exclude_none=True) for item in origins]
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def clear_statement_origins(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        revision.value.statement_origin = []
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def add_statement_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        dep: DeclDep,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        try:
            revision.value.statement.deps = [*revision.value.statement.deps, dep]
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_dep_invalid", str(exc), object_ref=decl_name)
            )
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def remove_statement_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        index: int,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        deps = list(revision.value.statement.deps)
        if index < 0 or index >= len(deps):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_dep_index_invalid", "Statement dependency index is out of range.", object_ref=decl_name, field="index")
            )
        del deps[index]
        revision.value.statement.deps = deps
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def clear_statement_deps(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        revision.value.statement.deps = []
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def write_statement_formal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        lean_code: str,
        lean_check: dict[str, Any],
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevision]:
        if not lean_code or not lean_code.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_formal_code_required", "Statement formal Lean code is required.", field="lean_code")
            )
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if not revision.value.statement_nl:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_nl_missing", "Statement NL must be written before statement formalization.", object_ref=decl_name)
            )
        revision.value.statement_lean_code = lean_code.strip()
        revision.value.statement_lean_check = self._normalize_check(lean_check)
        revision.value.statement_deps = self._normalize_deps(deps if deps is not None else revision.value.statement_deps)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def write_statement_deps(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if not revision.value.statement_nl:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_nl_missing", "Statement NL must be accepted before statement dependency refinement.", object_ref=decl_name)
            )
        revision.value.statement_deps = self._normalize_deps(deps)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def write_proof_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
        origin: list[dict[str, Any]] | None = None,
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevision]:
        if not nl or not nl.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("proof_nl_required", "Proof NL text is required.", field="nl")
            )
        theorem_like = self._require_theorem_like(repo_root, node_path=node_path, decl_name=decl_name)
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if not revision.value.statement_lean_code:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_formal_missing", "Statement formal code must be written before proof planning.", object_ref=decl_name)
            )
        revision.value.proof_nl = nl.strip()
        revision.value.proof_origin = self._normalize_origin(origin)
        revision.value.proof_deps = self._normalize_deps(deps)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def write_proof_nl_typed(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
        origin: list[DeclOriginRef],
        deps: list[DeclDep],
    ) -> ServiceResult[DeclRevision]:
        theorem_like = self._require_theorem_like(repo_root, node_path=node_path, decl_name=decl_name)
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if not nl.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("proof_nl_required", "Proof NL text is required.", field="nl"))
        if not revision.value.statement_lean_code:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("statement_formal_missing", "Statement formal code must be written before proof planning.", object_ref=decl_name))
        revision.value.proof_nl = nl.strip()
        proof = revision.value._ensure_proof()
        assert proof.nl is not None
        proof.nl.origin = origin
        proof.deps = deps
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def set_proof_nl(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        nl: str,
    ) -> ServiceResult[DeclRevision]:
        if not nl or not nl.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("proof_nl_required", "Proof NL text is required.", field="proof_nl")
            )
        theorem_like = self._require_theorem_like(repo_root, node_path=node_path, decl_name=decl_name)
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if not revision.value.statement_lean_code:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_formal_missing", "Accepted statement formal code must exist before proof planning.", object_ref=decl_name)
            )
        revision.value.proof_nl = nl.strip()
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def add_proof_origin(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        origin: DeclOriginRef,
    ) -> ServiceResult[DeclRevision]:
        theorem_like = self._require_theorem_like(repo_root, node_path=node_path, decl_name=decl_name)
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        proof = revision.value._ensure_proof()
        current = proof.nl.origin if proof.nl is not None else []
        revision.value.proof_origin = [item.model_dump(mode="json", exclude_none=True) for item in [*current, origin]]
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def remove_proof_origin(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        index: int,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        origins = list(revision.value.proof.nl.origin if revision.value.proof is not None and revision.value.proof.nl is not None else [])
        if index < 0 or index >= len(origins):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("proof_origin_index_invalid", "Proof origin index is out of range.", object_ref=decl_name, field="index")
            )
        del origins[index]
        revision.value.proof_origin = [item.model_dump(mode="json", exclude_none=True) for item in origins]
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def clear_proof_origins(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        revision.value.proof_origin = []
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def add_proof_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        dep: DeclDep,
    ) -> ServiceResult[DeclRevision]:
        theorem_like = self._require_theorem_like(repo_root, node_path=node_path, decl_name=decl_name)
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        proof = revision.value._ensure_proof()
        try:
            proof.deps = [*proof.deps, dep]
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("proof_dep_invalid", str(exc), object_ref=decl_name)
            )
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def remove_proof_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        index: int,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        deps = list(revision.value.proof.deps if revision.value.proof is not None else [])
        if index < 0 or index >= len(deps):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("proof_dep_index_invalid", "Proof dependency index is out of range.", object_ref=decl_name, field="index")
            )
        del deps[index]
        proof = revision.value._ensure_proof()
        proof.deps = deps
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def clear_proof_deps(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        proof = revision.value._ensure_proof()
        proof.deps = []
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def write_proof_formal(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        lean_code: str,
        lean_check: dict[str, Any],
        deps: list[str] | None = None,
    ) -> ServiceResult[DeclRevision]:
        if not lean_code or not lean_code.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("proof_formal_code_required", "Proof formal Lean code is required.", field="lean_code")
            )
        theorem_like = self._require_theorem_like(repo_root, node_path=node_path, decl_name=decl_name)
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if not revision.value.proof_nl:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("proof_nl_missing", "Proof NL must be written before proof formalization.", object_ref=decl_name)
            )
        revision.value.proof_lean_code = lean_code.strip()
        revision.value.proof_lean_check = self._normalize_check(lean_check)
        revision.value.proof_deps = self._normalize_deps(deps if deps is not None else revision.value.proof_deps)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def advance_stage_state(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        stage: str,
        decl_names: list[str],
    ) -> ServiceResult[list[str]]:
        target_state = self._target_state_for_stage(stage)
        if target_state is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_stage_unknown", "Cannot advance accepted state for an unknown decl stage.", current=stage)
            )
        revisions: list[DeclRevision] = []
        for decl_name in decl_names:
            revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            revisions.append(revision.value)

        advanced: list[str] = []
        with self.runtime.foundation.mutation(f"advance {stage} state") as mutation:
            for revision in revisions:
                if self._state_rank(revision.state) < self._state_rank(target_state):
                    revision.state = target_state
                    revision.updated_at = utc_now_iso()
                    mutation.stage_json(
                        self.graph_store.revision_path(
                            repo_root,
                            node_path=node_path,
                            decl_name=revision.decl_name,
                            revision=revision.revision,
                        ),
                        revision,
                        mode=WriteMode.UPDATE_EXISTING,
                    )
                advanced.append(revision.decl_name)
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        return self.runtime.foundation.ok(sorted(advanced))

    def _revision_for_stage(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
    ) -> ServiceResult[DeclRevision]:
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        if round_record.value.status != DeclRoundStatus.RUNNING:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_not_running",
                    "Stage mutation requires a running decl round.",
                    object_ref=round_id,
                    current=round_record.value.status.value,
                    expected=DeclRoundStatus.RUNNING.value,
                )
            )
        target_revision: int | None = None
        for ref in round_record.value.revision_refs:
            if ref.decl_name != decl_name:
                continue
            revision = self.decl_catalog.get_decl_revision(repo_root, node_path=node_path, name=ref.decl_name, revision=ref.revision)
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            if revision.value.change is not None and revision.value.change.kind == DeclChangeKind.DELETE:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("decl_change_is_delete", "Delete changes cannot receive stage mutations.", object_ref=decl_name)
                )
            target_revision = ref.revision
            break
        if target_revision is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_not_in_round", "Declaration is not part of this round.", object_ref=decl_name)
            )
        revision = self.decl_catalog.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=decl_name,
            revision=target_revision,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        if revision.value.status != "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_open",
                    "Stage mutation requires the target revision to be open.",
                    object_ref=decl_name,
                    current=revision.value.status.value,
                )
            )
        return self.runtime.foundation.ok(revision.value)

    def _require_theorem_like(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[None]:
        decl = self.decl_catalog.get_decl(repo_root, node_path=node_path, name=decl_name)
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        if decl.value.kind not in self._THEOREM_LIKE_KINDS:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_not_theorem_like",
                    "Proof stages are only valid for theorem-like declarations.",
                    object_ref=decl_name,
                    current=decl.value.kind,
                    expected=", ".join(sorted(self._THEOREM_LIKE_KINDS)),
                )
            )
        return self.runtime.foundation.ok(None)

    def _write_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        revision: DeclRevision,
    ) -> ServiceResult[DeclRevision]:
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

    def _normalize_origin(self, origin: list[dict[str, Any]] | None) -> list[dict[str, str]]:
        normalized = []
        for item in origin or []:
            normalized.append({str(key): str(value) for key, value in item.items()})
        return normalized

    def _normalize_deps(self, deps: list[str] | None) -> list[str]:
        if deps is None:
            return []
        stripped = [dep.strip() for dep in deps]
        return sorted({dep for dep in stripped if dep})

    def _normalize_check(self, lean_check: dict[str, Any]) -> LeanCheck:
        return LeanCheck.model_validate(lean_check)

    @staticmethod
    def _target_state_for_stage(stage: str) -> DeclState | None:
        return {
            "statement_nl": DeclState.SPECIFIED,
            "statement_formal": DeclState.DECLARED,
            "proof_nl": DeclState.PROOF_PLANNED,
            "proof_formal": DeclState.PROVED,
        }.get(stage)

    @staticmethod
    def _state_rank(state: DeclState) -> int:
        return {
            DeclState.OBSOLETE: -1,
            DeclState.PLANNED: 0,
            DeclState.SPECIFIED: 1,
            DeclState.DECLARED: 2,
            DeclState.PROOF_PLANNED: 3,
            DeclState.PROVED: 4,
        }[state]
