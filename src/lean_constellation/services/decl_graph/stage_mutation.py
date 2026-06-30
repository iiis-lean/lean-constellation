"""Stage-specific mutation APIs for declaration revisions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclRevisionRecord,
    DeclRoundStatus,
    DeclState,
)
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
    ) -> ServiceResult[DeclRevisionRecord]:
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
        revision.value.decl_deps = sorted(set(revision.value.statement_deps))
        revision.value.state = DeclState.SPECIFIED
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
    ) -> ServiceResult[DeclRevisionRecord]:
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
        revision.value.decl_deps = sorted(set(revision.value.statement_deps))
        revision.value.state = DeclState.DECLARED
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
    ) -> ServiceResult[DeclRevisionRecord]:
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
        revision.value.decl_deps = sorted(set(revision.value.statement_deps) | set(revision.value.proof_deps))
        revision.value.state = DeclState.DECLARED
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
    ) -> ServiceResult[DeclRevisionRecord]:
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
        revision.value.decl_deps = sorted(set(revision.value.statement_deps) | set(revision.value.proof_deps))
        revision.value.state = DeclState.PROVED
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, revision=revision.value)

    def _revision_for_stage(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
    ) -> ServiceResult[DeclRevisionRecord]:
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
        for change_id in round_record.value.change_ids:
            change = self.decl_catalog.get_decl_change(repo_root, node_path=node_path, change_id=change_id)
            if not change.ok or change.value is None:
                return self.runtime.foundation.fail(change.issues)
            if change.value.decl_name != decl_name:
                continue
            if change.value.kind == DeclChangeKind.DELETE:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("decl_change_is_delete", "Delete changes cannot receive stage mutations.", object_ref=decl_name)
                )
            target_revision = change.value.target_revision
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
        if revision.value.version_status != "open":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_revision_not_open",
                    "Stage mutation requires the target revision to be open.",
                    object_ref=decl_name,
                    current=revision.value.version_status,
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

    def _normalize_check(self, lean_check: dict[str, Any]) -> dict[str, str]:
        return {str(key): str(value) for key, value in lean_check.items()}
