"""Stage-specific mutation APIs for declaration revisions."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from lean_constellation.domain.repo import ProofAvailability
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
    DeclRevisionStatus,
    DeclRoundStatus,
    DeclState,
    RepoDeclDep,
)
from lean_constellation.services.decl_graph.strategy_round import StrategyRoundComponent
from lean_constellation.services.decl_graph.origin_validation import validate_nl_origin
from lean_constellation.services.decl_graph.proof_nl_validation import (
    validate_proof_deps,
    validate_proof_origin_ref,
)
from lean_constellation.services.decl_graph.statement_nl_validation import validate_statement_deps
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
        revision.value.statement.deps = self._normalize_deps(deps, node_path=node_path)
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

    def prepare_statement_nl_from_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        source_revision: int,
    ) -> ServiceResult[DeclRevision]:
        """Atomically prepare an empty current Statement NL candidate from committed history."""

        current = self._revision_for_stage(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
        )
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        head = self._require_current_round_head(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=current.value,
        )
        if not head.ok:
            return self.runtime.foundation.fail(head.issues)
        if current.value.state != DeclState.PLANNED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "statement_nl_prepare_state_invalid",
                    "Historical Statement NL preparation requires the current revision at planned state.",
                    object_ref=decl_name,
                    current=current.value.state.value,
                    expected=DeclState.PLANNED.value,
                )
            )
        if current.value.statement.nl is not None or current.value.statement.deps:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "statement_nl_prepare_target_not_empty",
                    "Historical Statement NL preparation cannot overwrite a current candidate.",
                    object_ref=decl_name,
                )
            )
        source = self._committed_source_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            source_revision=source_revision,
        )
        if not source.ok or source.value is None:
            return self.runtime.foundation.fail(source.issues)
        if source.value.statement.nl is None or not (source.value.statement.nl.text or "").strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "statement_nl_prepare_source_missing",
                    "The source revision has no Statement NL candidate to prepare.",
                    object_ref=f"{node_path}:{decl_name}@{source_revision}",
                )
            )

        candidate = current.value.model_copy(deep=True)
        statement_nl = source.value.statement.nl.model_copy(deep=True)
        dependencies = [item.model_copy(deep=True) for item in source.value.statement.deps]
        self._rebind_same_node_dependencies(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            dependencies=dependencies,
        )
        issues = []
        for origin in statement_nl.origin:
            issue = validate_nl_origin(
                self.runtime,
                repo_root,
                origin=origin,
                decl_name=decl_name,
                stage="statement",
            )
            if issue is not None:
                issues.append(issue)
        visibility_cache: dict[tuple[str, str], object] = {}
        dependency_validation = validate_statement_deps(
            self.runtime,
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            deps=dependencies,
            visibility_cache=visibility_cache,
        )
        if not dependency_validation.ok:
            issues.extend(dependency_validation.issues)
        else:
            issues.extend(
                self._validate_prepared_public_dependencies(
                    repo_root,
                    node_path=node_path,
                    decl_name=decl_name,
                    dependencies=dependencies,
                    stage="statement",
                    visibility_cache=visibility_cache,
                )
            )
        if issues:
            return self.runtime.foundation.fail(issues)

        candidate.statement.nl = statement_nl
        candidate.statement.deps = dependencies
        candidate.updated_at = utc_now_iso()
        return self._write_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=candidate,
        )

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
        allow_draft: bool = False,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, allow_draft=allow_draft)
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
        allow_draft: bool = False,
    ) -> ServiceResult[DeclRevision]:
        """Atomically add a validated batch to the statement dependency truth."""

        revision = self._revision_for_stage(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            allow_draft=allow_draft,
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
        allow_draft: bool = False,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, allow_draft=allow_draft)
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
        allow_draft: bool = False,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, allow_draft=allow_draft)
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
        revision.value.statement.deps = self._normalize_deps(deps, node_path=node_path)
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
        proof.deps = self._normalize_deps(deps, node_path=node_path)
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

    def prepare_proof_nl_from_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        round_id: str,
        decl_name: str,
        source_revision: int,
    ) -> ServiceResult[DeclRevision]:
        """Atomically prepare an empty current Proof NL candidate from committed history."""

        theorem_like = self._require_theorem_like(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
        )
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        current = self._revision_for_stage(
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
        )
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        head = self._require_current_round_head(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=current.value,
        )
        if not head.ok:
            return self.runtime.foundation.fail(head.issues)
        if current.value.state != DeclState.DECLARED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "proof_nl_prepare_state_invalid",
                    "Historical Proof NL preparation requires the current revision at declared state.",
                    object_ref=decl_name,
                    current=current.value.state.value,
                    expected=DeclState.DECLARED.value,
                )
            )
        if current.value.statement.formal is None or not (current.value.statement.formal.code or "").strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "statement_formal_missing",
                    "Accepted statement formal code must exist before Proof NL preparation.",
                    object_ref=decl_name,
                )
            )
        current_proof = current.value.proof
        if current_proof is not None and (
            (
                current_proof.nl is not None
                and (
                    bool((current_proof.nl.text or "").strip())
                    or bool(current_proof.nl.origin)
                )
            )
            or current_proof.deps
            or current_proof.formal is not None
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "proof_nl_prepare_target_not_empty",
                    "Historical Proof NL preparation cannot overwrite a current candidate.",
                    object_ref=decl_name,
                )
            )
        source = self._committed_source_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            source_revision=source_revision,
        )
        if not source.ok or source.value is None:
            return self.runtime.foundation.fail(source.issues)
        source_proof = source.value.proof
        if source_proof is None or source_proof.nl is None or not (source_proof.nl.text or "").strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "proof_nl_prepare_source_missing",
                    "The source revision has no Proof NL candidate to prepare.",
                    object_ref=f"{node_path}:{decl_name}@{source_revision}",
                )
            )

        candidate = current.value.model_copy(deep=True)
        proof_nl = source_proof.nl.model_copy(deep=True)
        dependencies = [item.model_copy(deep=True) for item in source_proof.deps]
        self._rebind_same_node_dependencies(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            dependencies=dependencies,
        )
        issues = []
        for origin in proof_nl.origin:
            validated = validate_proof_origin_ref(
                self.runtime,
                repo_root,
                origin=origin,
                decl_name=decl_name,
            )
            if not validated.ok:
                issues.extend(validated.issues)
        visibility_cache: dict[tuple[str, str], object] = {}
        dependency_validation = validate_proof_deps(
            self.runtime,
            repo_root,
            node_path=node_path,
            round_id=round_id,
            decl_name=decl_name,
            deps=dependencies,
            visibility_cache=visibility_cache,
        )
        if not dependency_validation.ok:
            issues.extend(dependency_validation.issues)
        else:
            issues.extend(
                self._validate_prepared_public_dependencies(
                    repo_root,
                    node_path=node_path,
                    decl_name=decl_name,
                    dependencies=dependencies,
                    stage="proof",
                    visibility_cache=visibility_cache,
                )
            )
        if issues:
            return self.runtime.foundation.fail(issues)

        proof = candidate._ensure_proof()
        proof.nl = proof_nl
        proof.deps = dependencies
        candidate.updated_at = utc_now_iso()
        return self._write_revision(
            repo_root,
            node_path=node_path,
            decl_name=decl_name,
            revision=candidate,
        )

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
        allow_draft: bool = False,
    ) -> ServiceResult[DeclRevision]:
        theorem_like = self._require_theorem_like(repo_root, node_path=node_path, decl_name=decl_name)
        if not theorem_like.ok:
            return self.runtime.foundation.fail(theorem_like.issues)
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, allow_draft=allow_draft)
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
        allow_draft: bool = False,
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
            allow_draft=allow_draft,
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
        allow_draft: bool = False,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, allow_draft=allow_draft)
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
        allow_draft: bool = False,
    ) -> ServiceResult[DeclRevision]:
        revision = self._revision_for_stage(repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, allow_draft=allow_draft)
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
        allow_draft: bool = False,
    ) -> ServiceResult[DeclRevision]:
        round_record = self.strategy_round.get_round(repo_root, node_path=node_path, round_id=round_id)
        if not round_record.ok or round_record.value is None:
            return self.runtime.foundation.fail(round_record.issues)
        allowed_statuses = (
            {DeclRoundStatus.DRAFT, DeclRoundStatus.RUNNING}
            if allow_draft
            else {DeclRoundStatus.RUNNING}
        )
        if round_record.value.status not in allowed_statuses:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "round_not_running",
                    "Dependency mutation requires a draft or running decl round."
                    if allow_draft
                    else "Stage mutation requires a running decl round.",
                    object_ref=round_id,
                    current=round_record.value.status.value,
                    expected=",".join(item.value for item in sorted(allowed_statuses, key=lambda item: item.value)),
                )
            )
        target_revision: int | None = None
        for ref in round_record.value.revision_refs:
            if ref.decl_name != decl_name:
                continue
            revision = self.decl_catalog.get_decl_revision(repo_root, node_path=node_path, name=ref.decl_name, revision=ref.revision)
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
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

    def _committed_source_revision(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        source_revision: int,
    ) -> ServiceResult[DeclRevision]:
        source = self.decl_catalog.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=decl_name,
            revision=source_revision,
        )
        if not source.ok or source.value is None:
            return self.runtime.foundation.fail(source.issues)
        if source.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_prepare_source_not_committed",
                    "Historical preparation requires an exact committed source revision.",
                    object_ref=f"{node_path}:{decl_name}@{source_revision}",
                    current=source.value.status.value,
                    expected=DeclRevisionStatus.COMMITTED.value,
                )
            )
        return self.runtime.foundation.ok(source.value)

    def _require_current_round_head(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        revision: DeclRevision,
    ) -> ServiceResult[None]:
        decl = self.decl_catalog.get_decl(
            repo_root,
            node_path=node_path,
            name=decl_name,
        )
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        if decl.value.current_revision != revision.revision:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_prepare_target_not_current_head",
                    "Historical preparation must target the current open round revision.",
                    object_ref=decl_name,
                    current=str(decl.value.current_revision),
                    expected=str(revision.revision),
                )
            )
        return self.runtime.foundation.ok(None)

    def _rebind_same_node_dependencies(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        dependencies: list[DeclDep],
    ) -> None:
        for dep in dependencies:
            if not isinstance(dep, RepoDeclDep) or dep.ref.repo is not None:
                continue
            effective_node = (
                node_path
                if dep.ref.node in {"", "Main"} and node_path != "Main"
                else dep.ref.node
            )
            if effective_node != node_path or dep.ref.name == decl_name:
                continue
            provider = self.decl_catalog.get_decl(
                repo_root,
                node_path=node_path,
                name=dep.ref.name,
            )
            if provider.ok and provider.value is not None:
                dep.ref.node = node_path
                dep.ref.revision = provider.value.current_revision

    def _validate_prepared_public_dependencies(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        dependencies: list[DeclDep],
        stage: Literal["statement", "proof"],
        visibility_cache: dict[tuple[str, str], object],
    ) -> list[object]:
        """Fail closed when a historical public dependency anchor is stale."""

        local: list[tuple[DeclRef, tuple[str, str]]] = []
        external: list[tuple[DeclRef, tuple[str, str]]] = []
        for dependency in dependencies:
            if not isinstance(dependency, RepoDeclDep):
                continue
            ref = dependency.ref
            if ref.repo is not None:
                repo_key = self.runtime.foundation.layout.ensure_safe_key(ref.repo)
                external.append((ref, ("repo", repo_key)))
                continue
            effective_node = (
                node_path
                if ref.node in {"", "Main"} and node_path != "Main"
                else ref.node
            )
            if effective_node == node_path:
                continue
            local.append(
                (
                    ref.model_copy(update={"node": effective_node}),
                    ("node", effective_node),
                )
            )
        if not local and not external:
            return []

        resolver = self.runtime.decl_graph.ref_compatibility
        operation_context = resolver.create_operation_context()
        resolved: list[tuple[DeclRef, tuple[str, str], object]] = []
        if local:
            local_result = resolver.resolve_decl_refs_batch(
                repo_root,
                refs=[ref for ref, _cache_key in local],
                required_availability=ProofAvailability.DECLARED,
                operation_context=operation_context,
            )
            if not local_result.ok or local_result.value is None:
                return list(local_result.issues)
            resolved.extend(
                (ref, cache_key, item)
                for (ref, cache_key), item in zip(
                    local,
                    local_result.value,
                    strict=True,
                )
            )
        if external:
            external_result = resolver.resolve_public_decl_refs_batch(
                repo_root,
                refs=[ref for ref, _cache_key in external],
                required_availability=ProofAvailability.DECLARED,
                operation_context=operation_context,
            )
            if not external_result.ok or external_result.value is None:
                return list(external_result.issues)
            resolved.extend(
                (ref, cache_key, item)
                for (ref, cache_key), item in zip(
                    external,
                    external_result.value,
                    strict=True,
                )
            )

        issues = []
        for ref, cache_key, item in resolved:
            resolved_revision = getattr(item, "resolved_revision", None)
            if not getattr(item, "compatible", False) or resolved_revision is None:
                issues.append(
                    self.runtime.foundation.issue(
                        f"{stage}_dep_prepare_incompatible",
                        "Historical dependency anchor is not compatible with the current public boundary.",
                        object_ref=self._prepared_dependency_label(ref),
                        current=getattr(item, "reason", None),
                        expected="compatible current public declaration",
                    )
                )
                continue
            public = visibility_cache.get(cache_key)
            values = getattr(public, "value", None) if getattr(public, "ok", False) else None
            candidate = next(
                (
                    public_item
                    for public_item in values or []
                    if public_item.ref.node == ref.node
                    and public_item.ref.name == ref.name
                    and (
                        public_item.resolved_revision or public_item.ref.revision
                    )
                    == resolved_revision
                ),
                None,
            )
            if (
                candidate is None
                or not getattr(candidate, "ready", False)
                or getattr(candidate, "stale", True)
            ):
                issues.append(
                    self.runtime.foundation.issue(
                        f"{stage}_dep_prepare_not_ready",
                        "Historical dependency is not ready on the exact current public boundary.",
                        object_ref=self._prepared_dependency_label(ref),
                        current=(
                            "missing"
                            if candidate is None
                            else f"ready={candidate.ready}, stale={candidate.stale}"
                        ),
                        expected=f"revision={resolved_revision}, ready=True, stale=False",
                    )
                )
        return issues

    @staticmethod
    def _prepared_dependency_label(ref: DeclRef) -> str:
        prefix = f"{ref.repo}:" if ref.repo is not None else ""
        return f"{prefix}{ref.node}:{ref.name}@{ref.revision}"

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

    def _normalize_deps(self, deps: list[str] | None, *, node_path: str) -> list[DeclDep]:
        if deps is None:
            return []
        stripped = [dep.strip() for dep in deps]
        return [
            RepoDeclDep(ref=DeclRef(node=node_path, name=dep))
            for dep in sorted({dep for dep in stripped if dep})
        ]

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
            DeclState.PLANNED: 0,
            DeclState.SPECIFIED: 1,
            DeclState.DECLARED: 2,
            DeclState.PROOF_PLANNED: 3,
            DeclState.PROVED: 4,
        }[state]
