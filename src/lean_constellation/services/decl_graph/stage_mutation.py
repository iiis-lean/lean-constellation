"""Stage-specific mutation APIs for declaration revisions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.common import utc_now_iso
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.graph_store import GraphStoreComponent
from lean_constellation.services.decl_graph.models import (
    DeclChangeKind,
    DeclDep,
    DeclNaturalLanguageSection,
    DeclOriginRef,
    DeclRevision,
    DeclRoundStatus,
    DeclState,
    RepoDeclDep,
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
    ) -> ServiceResult[DeclRevision]:
        if not nl or not nl.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_nl_required", "Statement NL text is required.", field="nl")
            )
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        revision.value.statement.nl = DeclNaturalLanguageSection(
            text=nl.strip(),
            origin=self._normalize_origin(origin),
        )
        revision.value.statement.deps = self._normalize_deps(deps)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
            revision.value.statement.nl = DeclNaturalLanguageSection(text=nl.strip(), origin=origin)
        revision.value.statement.deps = deps
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        origins = revision.value.statement.nl.origin if revision.value.statement.nl is not None else []
        revision.value.statement.nl = DeclNaturalLanguageSection(text=nl.strip(), origin=origins)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        text = revision.value.statement.nl.text if revision.value.statement.nl is not None else None
        revision.value.statement.nl = DeclNaturalLanguageSection(text=text, origin=[*current, origin])
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        text = revision.value.statement.nl.text if revision.value.statement.nl is not None else None
        revision.value.statement.nl = DeclNaturalLanguageSection(text=text, origin=origins)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        text = revision.value.statement.nl.text if revision.value.statement.nl is not None else None
        revision.value.statement.nl = DeclNaturalLanguageSection(text=text, origin=[]) if text is not None else None
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        existing = self._matching_dep(revision.value.statement.deps, dep)
        if existing is not None:
            if existing == dep:
                return self.runtime.foundation.ok(
                    revision.value,
                    warnings=[
                        self.runtime.foundation.issue(
                            "statement_dep_already_present",
                            "The exact statement dependency is already present; no change was needed.",
                            severity="warning",
                            object_ref=decl_name,
                        )
                    ],
                )
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "statement_dep_conflict",
                    "A statement dependency with the same identity already exists with different metadata.",
                    object_ref=decl_name,
                    current=existing.model_dump_json(exclude_none=True),
                    expected=dep.model_dump_json(exclude_none=True),
                )
            )
        try:
            revision.value.statement.deps = [*revision.value.statement.deps, dep]
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_dep_invalid", str(exc), object_ref=decl_name)
            )
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

    def add_statement_dependencies(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        deps: list[DeclDep],
    ) -> ServiceResult[DeclRevision]:
        """Atomically add a validated batch to the statement dependency truth."""

        revision = self._revision_for_stage(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        merged = self._merge_dependency_batch(
            revision.value.statement.deps,
            deps,
            decl_name=decl_name,
            stage="statement",
        )
        if not merged.ok or merged.value is None:
            return self.runtime.foundation.fail(merged.issues)
        revision.value.statement.deps = merged.value
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=revision.value,
        )

    def _merge_dependency_batch(
        self,
        existing: list[DeclDep],
        requested: list[DeclDep],
        *,
        decl_name: str,
        stage: str,
    ) -> ServiceResult[list[DeclDep]]:
        requested_by_identity: dict[tuple[object, ...], DeclDep] = {}
        for dep in requested:
            identity = self._dep_identity(dep)
            if identity in requested_by_identity:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "duplicate_batch_item",
                        f"The {stage} dependency batch contains the same identity more than once.",
                        object_ref=decl_name,
                        current=dep.model_dump_json(exclude_none=True),
                    )
                )
            requested_by_identity[identity] = dep

        existing_by_identity = {self._dep_identity(dep): dep for dep in existing}
        added: list[DeclDep] = []
        for identity, dep in requested_by_identity.items():
            current = existing_by_identity.get(identity)
            if current is None:
                added.append(dep)
                continue
            if current != dep:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "batch_identity_conflict",
                        f"An existing {stage} dependency has the same identity with different metadata.",
                        object_ref=decl_name,
                        current=current.model_dump_json(exclude_none=True),
                        expected=dep.model_dump_json(exclude_none=True),
                    )
                )
        try:
            return self.runtime.foundation.ok([*existing, *added])
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    f"{stage}_dep_invalid",
                    str(exc),
                    object_ref=decl_name,
                )
            )

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
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        if revision.value.statement.nl is None or not (revision.value.statement.nl.text or "").strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_nl_missing", "Statement NL must be accepted before statement dependency refinement.", object_ref=decl_name)
            )
        revision.value.statement.deps = self._normalize_deps(deps)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        if revision.value.statement.formal is None or not (revision.value.statement.formal.code or "").strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_formal_missing", "Statement formal code must be written before proof planning.", object_ref=decl_name)
            )
        proof = revision.value._ensure_proof()
        proof.nl = DeclNaturalLanguageSection(text=nl.strip(), origin=self._normalize_origin(origin))
        proof.deps = self._normalize_deps(deps)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        if revision.value.statement.formal is None or not (revision.value.statement.formal.code or "").strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("statement_formal_missing", "Statement formal code must be written before proof planning.", object_ref=decl_name))
        proof = revision.value._ensure_proof()
        proof.nl = DeclNaturalLanguageSection(text=nl.strip(), origin=origin)
        proof.deps = deps
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        if revision.value.statement.formal is None or not (revision.value.statement.formal.code or "").strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("statement_formal_missing", "Accepted statement formal code must exist before proof planning.", object_ref=decl_name)
            )
        proof = revision.value._ensure_proof()
        origins = proof.nl.origin if proof.nl is not None else []
        proof.nl = DeclNaturalLanguageSection(text=nl.strip(), origin=origins)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        text = proof.nl.text if proof.nl is not None else None
        proof.nl = DeclNaturalLanguageSection(text=text, origin=[*current, origin])
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        proof = revision.value._ensure_proof()
        text = proof.nl.text if proof.nl is not None else None
        proof.nl = DeclNaturalLanguageSection(text=text, origin=origins)
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        proof = revision.value._ensure_proof()
        text = proof.nl.text if proof.nl is not None else None
        proof.nl = DeclNaturalLanguageSection(text=text, origin=[]) if text is not None else None
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        existing = self._matching_dep(proof.deps, dep)
        if existing is not None:
            if existing == dep:
                return self.runtime.foundation.ok(
                    revision.value,
                    warnings=[
                        self.runtime.foundation.issue(
                            "proof_dep_already_present",
                            "The exact proof dependency is already present; no change was needed.",
                            severity="warning",
                            object_ref=decl_name,
                        )
                    ],
                )
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "proof_dep_conflict",
                    "A proof dependency with the same identity already exists with different metadata.",
                    object_ref=decl_name,
                    current=existing.model_dump_json(exclude_none=True),
                    expected=dep.model_dump_json(exclude_none=True),
                )
            )
        try:
            proof.deps = [*proof.deps, dep]
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("proof_dep_invalid", str(exc), object_ref=decl_name)
            )
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

    def add_proof_dependencies(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        deps: list[DeclDep],
    ) -> ServiceResult[DeclRevision]:
        """Atomically add a validated batch to the proof dependency truth."""

        theorem_like = self._require_theorem_like(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        revision = self._revision_for_stage(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        proof = revision.value._ensure_proof()
        merged = self._merge_dependency_batch(
            proof.deps,
            deps,
            decl_name=decl_name,
            stage="proof",
        )
        if not merged.ok or merged.value is None:
            return self.runtime.foundation.fail(merged.issues)
        proof.deps = merged.value
        revision.value.updated_at = utc_now_iso()
        return self._write_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=revision.value,
        )

    @staticmethod
    def _matching_dep(deps: list[DeclDep], candidate: DeclDep) -> DeclDep | None:
        candidate_key = StageMutationComponent._dep_identity(candidate)
        return next((dep for dep in deps if StageMutationComponent._dep_identity(dep) == candidate_key), None)

    @staticmethod
    def _dep_identity(dep: DeclDep) -> tuple[object, ...]:
        if dep.kind == "repo_decl":
            return (dep.kind, dep.ref.repo, dep.ref.node, dep.ref.name, dep.ref.revision)
        return (dep.kind, dep.ref.module, dep.ref.name)

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
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        return self._write_revision(repo_root, node_path=node_path, decl_name=decl_name, revision=revision.value)

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
        revisions: list[tuple[str, DeclRevision]] = []
        for decl_name in decl_names:
            revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name)
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            revisions.append((decl_name, revision.value))

        advanced: list[str] = []
        with self.runtime.foundation.mutation(f"advance {stage} state") as mutation:
            for decl_name, revision in revisions:
                if self._state_rank(revision.state) < self._state_rank(target_state):
                    revision.state = target_state
                    revision.updated_at = utc_now_iso()
                    mutation.stage_json(
                        self.graph_store.revision_path(
                            repo_root,
                            node_path=node_path,
                            decl_name=decl_name,
                            revision=revision.revision,
                        ),
                        revision,
                        mode=WriteMode.UPDATE_EXISTING,
                    )
                advanced.append(decl_name)
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

    def _normalize_origin(self, origin: list[dict[str, Any]] | None) -> list[DeclOriginRef]:
        return [DeclOriginRef.model_validate(item) for item in origin or []]

    def _normalize_deps(self, deps: list[str] | None) -> list[DeclDep]:
        if deps is None:
            return []
        stripped = [dep.strip() for dep in deps]
        return [RepoDeclDep(ref=DeclRef(name=dep)) for dep in sorted({dep for dep in stripped if dep})]

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
