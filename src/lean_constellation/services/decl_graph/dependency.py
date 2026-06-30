"""DeclGraph dependency closure and audit helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.services.decl_graph.decl_catalog import DeclCatalogComponent
from lean_constellation.services.decl_graph.models import DeclDependencyClosureView, DeclLifecycle
from lean_constellation.services.foundation import GateReport, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclDependencyComponent:
    """Compute decl dependency closures and expose round dependency audits."""

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
