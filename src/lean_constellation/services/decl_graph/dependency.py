"""DeclGraph dependency closure and audit helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.repo import ProofAvailability, proof_availability_satisfies
from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph.models import Decl, DeclDep, DeclDependencyClosureView, DeclLifecycle, DeclRevision
from lean_constellation.services.foundation import GateReport, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclDependencyComponent:
    """Compute decl dependency closures and expose round dependency audits."""

    _THEOREM_LIKE_KINDS = {"theorem", "lemma", "proposition", "corollary"}

    def __init__(self, runtime: LeanRuntimeServices, decl_catalog: DeclCatalogComponent) -> None:
        self.runtime = runtime
        self.decl_catalog = decl_catalog

    def compute_dependency_closure(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[DeclDependencyClosureView]:
        roots = sorted({name.strip() for name in decl_names if name and name.strip()})
        if not roots:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("dependency_closure_empty", "At least one declaration name is required.")
            )
        decls = self.decl_catalog.list_decls(repo_root, node_path=node_path)
        if not decls.ok or decls.value is None:
            return self.runtime.foundation.fail(decls.issues)
        active = {decl.name: decl for decl in decls.value if decl.lifecycle == DeclLifecycle.ACTIVE}
        missing = sorted(name for name in roots if name not in active)

        upstream: set[str] = set()
        queue = list(roots)
        while queue:
            current = queue.pop(0)
            decl = active.get(current)
            if decl is None:
                continue
            revision = self.decl_catalog.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=current,
                revision=decl.current_revision,
            )
            if not revision.ok or revision.value is None:
                continue
            for dep_name in revision.value.decl_deps:
                if dep_name not in upstream:
                    upstream.add(dep_name)
                    queue.append(dep_name)

        delete_closure = self.decl_catalog.compute_delete_closure(repo_root, node_path=node_path, decl_names=roots)
        if not delete_closure.ok or delete_closure.value is None:
            return self.runtime.foundation.fail(delete_closure.issues)
        downstream = sorted(set(delete_closure.value.closure_decl_names) - set(roots))
        return self.runtime.foundation.ok(
            DeclDependencyClosureView(
                root_decl_names=roots,
                upstream_decl_names=sorted(upstream),
                downstream_decl_names=downstream,
                missing_decl_names=missing,
                summary=f"Dependency closure for {len(roots)} roots: {len(upstream)} upstream, {len(downstream)} downstream.",
            )
        )

    def check_delete_preflight(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_names: list[str],
    ) -> ServiceResult[GateReport]:
        closure = self.decl_catalog.compute_delete_closure(repo_root, node_path=node_path, decl_names=decl_names)
        if not closure.ok or closure.value is None:
            return self.runtime.foundation.fail(closure.issues)
        issues = []
        if closure.value.missing_decl_names:
            issues.append(
                self.runtime.foundation.issue(
                    "delete_target_missing",
                    "Some delete targets do not exist.",
                    current=", ".join(closure.value.missing_decl_names),
                )
            )
        missing_from_request = sorted(set(closure.value.closure_decl_names) - set(closure.value.requested_decl_names))
        if missing_from_request:
            issues.append(
                self.runtime.foundation.issue(
                    "delete_closure_incomplete",
                    "Delete request does not cover downstream dependent closure.",
                    current=", ".join(missing_from_request),
                )
            )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("decl_delete_preflight", issues, summary=f"{len(issues)} delete checks failed.")
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("decl_delete_preflight", summary="Delete preflight passed.")
        )

    def audit_round_dependencies(self, repo_root: Path, *, node_path: str, round_id: str) -> ServiceResult[GateReport]:
        return self.decl_catalog.validate_round_draft(repo_root, node_path=node_path, round_id=round_id)

    def statement_dependency_names(self, revision: DeclRevision) -> list[str]:
        return self._repo_decl_dep_names(revision.statement.deps)

    def proof_dependency_names(self, revision: DeclRevision) -> list[str]:
        return self._repo_decl_dep_names(revision.proof.deps) if revision.proof is not None else []

    def all_dependency_names(self, revision: DeclRevision) -> list[str]:
        return sorted(set(self.statement_dependency_names(revision)) | set(self.proof_dependency_names(revision)))

    def dependency_names_for_proof_policy(
        self,
        decl: Decl,
        revision: DeclRevision,
        *,
        target_proof_availability: ProofAvailability,
    ) -> list[str]:
        return [
            name
            for name, _required in self.dependency_requirements_for_proof_policy(
                decl,
                revision,
                target_proof_availability=target_proof_availability,
            )
        ]

    def dependency_requirements_for_proof_policy(
        self,
        decl: Decl,
        revision: DeclRevision,
        *,
        target_proof_availability: ProofAvailability,
    ) -> list[tuple[str, ProofAvailability]]:
        return [
            (ref.name, required)
            for ref, required in self.dependency_ref_requirements_for_proof_policy(
                decl,
                revision,
                target_proof_availability=target_proof_availability,
            )
        ]

    def dependency_ref_requirements_for_proof_policy(
        self,
        decl: Decl,
        revision: DeclRevision,
        *,
        target_proof_availability: ProofAvailability,
    ) -> list[tuple[DeclRef, ProofAvailability]]:
        requirements: dict[tuple[str, str, str, int], tuple[DeclRef, ProofAvailability]] = {}
        for dep in self._repo_decl_deps(revision.statement.deps):
            self._merge_ref_requirement(requirements, dep.ref, ProofAvailability.DECLARED)
        if target_proof_availability == ProofAvailability.PROVED and self._is_theorem_like(decl.kind):
            for dep in self._repo_decl_deps(revision.proof.deps if revision.proof is not None else []):
                self._merge_ref_requirement(requirements, dep.ref, ProofAvailability.PROVED)
        return [requirements[key] for key in sorted(requirements)]

    def _merge_ref_requirement(
        self,
        requirements: dict[tuple[str, str, str, int], tuple[DeclRef, ProofAvailability]],
        ref: DeclRef,
        candidate: ProofAvailability,
    ) -> None:
        key = (ref.repo or "", ref.node, ref.name, ref.revision)
        current = requirements.get(key)
        requirements[key] = (ref, self._stricter_requirement(current[1] if current is not None else None, candidate))

    def _repo_decl_deps(self, deps: list[DeclDep]) -> list:
        return sorted([dep for dep in deps if dep.kind == "repo_decl"], key=lambda dep: (dep.ref.repo or "", dep.ref.node, dep.ref.name, dep.ref.revision))

    def _repo_decl_dep_names(self, deps: list[DeclDep]) -> list[str]:
        return sorted({dep.ref.name for dep in deps if dep.kind == "repo_decl"})

    def _stricter_requirement(
        self,
        current: ProofAvailability | None,
        candidate: ProofAvailability,
    ) -> ProofAvailability:
        if current is None:
            return candidate
        if proof_availability_satisfies(current, candidate):
            return current
        return candidate

    def _is_theorem_like(self, kind: str) -> bool:
        return kind.strip().lower() in self._THEOREM_LIKE_KINDS
