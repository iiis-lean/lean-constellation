"""Derived release guards for declaration mutations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.services.decl_graph.models import (
    Decl,
    DeclRevision,
    DeclState,
    RepoDeclDep,
)
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.node.node_tree import NodeContract, NodeKind, NodeLifecycle

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclReleaseGuard:
    """Check current declaration writes against the latest released public closure."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def check_update_candidate(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl: Decl,
        candidate: DeclRevision,
    ) -> ServiceResult[None]:
        protected = self._protected_entry(repo_root, node_path=node_path, decl_name=decl.name)
        if not protected.ok:
            return self.runtime.foundation.fail(protected.issues)
        if protected.value is None:
            return self.runtime.foundation.ok(None)
        baseline_decl, baseline_revision = protected.value
        if candidate.state == DeclState.OBSOLETE:
            return self._blocked("release_protected_decl_delete", node_path, decl.name)
        if candidate.state in {DeclState.PLANNED, DeclState.SPECIFIED}:
            return self._blocked("release_protected_statement_floor", node_path, decl.name)
        if candidate.statement.formal is None or not (candidate.statement.formal.code or "").strip():
            return self._blocked("release_protected_statement_reset", node_path, decl.name)
        current_fingerprint = self._fingerprint_candidate(
            repo_root, node_path=node_path, decl=decl, revision=candidate
        )
        if not current_fingerprint.ok or current_fingerprint.value is None:
            return self.runtime.foundation.fail(current_fingerprint.issues)
        released_fingerprint = self.runtime.decl_graph.declared_api.fingerprint(
            repo_root,
            node_path=baseline_decl.node_path,
            decl_name=baseline_decl.name,
            revision=baseline_revision.revision,
        )
        if not released_fingerprint.ok or released_fingerprint.value is None:
            return self.runtime.foundation.fail(released_fingerprint.issues)
        if current_fingerprint.value != released_fingerprint.value.sha256:
            return self._blocked("release_protected_declared_api_changed", node_path, decl.name)
        return self.runtime.foundation.ok(None)

    def check_delete(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[None]:
        protected = self._protected_entry(repo_root, node_path=node_path, decl_name=decl_name)
        if not protected.ok:
            return self.runtime.foundation.fail(protected.issues)
        if protected.value is not None:
            return self._blocked("release_protected_decl_delete", node_path, decl_name)
        inbound = self.current_inbound_refs(repo_root, node_path=node_path, decl_name=decl_name)
        if not inbound.ok or inbound.value is None:
            return self.runtime.foundation.fail(inbound.issues)
        if inbound.value:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_delete_current_inbound_refs",
                    "Declaration is still referenced by current repository truth.",
                    object_ref=f"{node_path}:{decl_name}",
                    current=", ".join(inbound.value),
                )
            )
        return self.runtime.foundation.ok(None)

    def current_inbound_refs(self, repo_root: Path, *, node_path: str, decl_name: str) -> ServiceResult[list[str]]:
        refs: list[str] = []
        nodes = self.runtime.node.node_tree.node_store.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        for node in nodes.value:
            if node.lifecycle != NodeLifecycle.ACTIVE:
                continue
            if node.kind == NodeKind.CONTENT:
                decls = self.runtime.decl_graph.decl_catalog.list_decls(repo_root, node_path=node.path)
                if not decls.ok or decls.value is None:
                    return self.runtime.foundation.fail(decls.issues)
                for dependent in decls.value:
                    revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
                        repo_root,
                        node_path=node.path,
                        name=dependent.name,
                        revision=dependent.current_revision,
                    )
                    if not revision.ok or revision.value is None:
                        return self.runtime.foundation.fail(revision.issues)
                    if revision.value.state == DeclState.OBSOLETE:
                        continue
                    for section, deps in (("statement", revision.value.statement.deps), ("proof", revision.value.proof.deps if revision.value.proof else [])):
                        for dep in deps:
                            if not isinstance(dep, RepoDeclDep) or dep.ref.repo is not None:
                                continue
                            target_node = dep.ref.node if dep.ref.node != "Main" else node.path
                            if target_node == node_path and dep.ref.name == decl_name:
                                refs.append(f"current:decl:{node.path}:{dependent.name}:{section}")
            if node.current_contract_version is None:
                continue
            contract_path = self.runtime.node.node_tree.node_store.contract_path(
                repo_root, node_id=node.node_id, version=node.current_contract_version
            )
            contract = self.runtime.foundation.store.read_json(contract_path, NodeContract)
            if not contract.ok or contract.value is None:
                return self.runtime.foundation.fail(contract.issues)
            for label, candidates in (("exports", contract.value.exports), ("interfaces", [item.bound_decl for item in contract.value.interfaces if item.bound_decl]),):
                for ref in candidates:
                    if ref.repo is None and ref.node == node_path and ref.name == decl_name:
                        refs.append(f"current:contract:{node.path}:{label}")
            for dep in contract.value.deps:
                for ref in dep.expected_decl_refs:
                    if ref.repo is None and ref.node == node_path and ref.name == decl_name:
                        refs.append(f"current:contract:{node.path}:deps")
        return self.runtime.foundation.ok(sorted(set(refs)))

    def _protected_entry(self, repo_root: Path, *, node_path: str, decl_name: str):
        latest = self.runtime.repo_workspace.release.get_latest_release(repo_root)
        if not latest.ok:
            return self.runtime.foundation.fail(latest.issues)
        if latest.value is None:
            return self.runtime.foundation.ok(None)
        baseline = self.runtime.repo_workspace.release.resolve_release_baseline(
            repo_root, release_id=latest.value.release.release_id
        )
        if not baseline.ok or baseline.value is None:
            return self.runtime.foundation.fail(baseline.issues)
        node = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        entry = next(
            (
                item
                for item in baseline.value.protected_decl_views
                if item.node_id == node.value.node_id and item.decl_name == decl_name
            ),
            None,
        )
        if entry is None:
            return self.runtime.foundation.ok(None)
        release = self.runtime.repo_workspace.release.get_release(repo_root, release_id=entry.last_release_id)
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(release.issues)
        version = release.value.release.node_contract_versions.get(entry.node_id)
        if version is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("release_contract_missing", "Protected declaration node is absent from its release.", object_ref=entry.node_id)
            )
        contract_path = self.runtime.node.node_tree.node_store.contract_path(repo_root, node_id=entry.node_id, version=version)
        contract = self.runtime.foundation.store.read_json(contract_path, NodeContract)
        if not contract.ok or contract.value is None:
            return self.runtime.foundation.fail(contract.issues)
        revision_number = contract.value.decl_graph_head.get(decl_name)
        if revision_number is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("release_decl_head_missing", "Protected declaration is absent from its released head.", object_ref=f"{node_path}:{decl_name}")
            )
        decl = self.runtime.decl_graph.decl_catalog.get_decl(repo_root, node_path=node_path, name=decl_name)
        revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
            repo_root, node_path=node_path, name=decl_name, revision=revision_number
        )
        if not decl.ok or decl.value is None:
            return self.runtime.foundation.fail(decl.issues)
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        return self.runtime.foundation.ok((decl.value, revision.value))

    def _fingerprint_candidate(self, repo_root: Path, *, node_path: str, decl: Decl, revision: DeclRevision) -> ServiceResult[str]:
        fingerprint = self.runtime.decl_graph.declared_api.fingerprint_candidate(
            repo_root,
            node_path=node_path,
            decl=decl,
            revision=revision,
        )
        if not fingerprint.ok or fingerprint.value is None:
            return self.runtime.foundation.fail(fingerprint.issues)
        return self.runtime.foundation.ok(fingerprint.value.sha256)

    def _blocked(self, kind: str, node_path: str, decl_name: str) -> ServiceResult[None]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                kind,
                "Released public declaration statements and their statement dependency closure are immutable.",
                object_ref=f"{node_path}:{decl_name}",
            )
        )


__all__ = ["DeclReleaseGuard"]
