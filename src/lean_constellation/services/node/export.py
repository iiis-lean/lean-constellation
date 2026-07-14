"""Scope export and content public declaration views."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import GateReport, IssueSeverity, ServiceIssue, ServiceResult
from lean_constellation.services.node.contract import ContractComponent
from lean_constellation.services.node.node_tree import NodeKind, NodeTreeComponent, NodeView
from lean_constellation.services.node.projection_transaction import persist_contract_with_projection

if TYPE_CHECKING:
    from lean_constellation.services.lean_projection.node_projection import NodeProjectionComponent
    from lean_constellation.services.runtime import LeanRuntimeServices


class DeclPublicView(StrictModel):
    ref: DeclRef
    resolved_revision: int | None = None
    resolution_reason: str | None = None
    kind: str | None = None
    summary: str | None = None
    public: bool = True
    ready: bool = True
    stale: bool = False
    source: str = "provider"
    released_state: str | None = None
    release_protected: bool = False


class DeclRefView(StrictModel):
    index: int
    ref: DeclRef
    repo: str | None = None
    node: str
    name: str
    revision: int
    resolved_revision: int | None = None
    resolution_reason: str | None = None
    valid: bool
    source: str | None = None
    summary: str
    issues: list[ServiceIssue] = Field(default_factory=list)


class ScopeExportCandidate(StrictModel):
    index: int = -1
    ref: DeclRef
    source_child: str
    source_kind: str
    kind: str | None = None
    summary: str | None = None
    ready: bool = True
    stale: bool = False
    already_exported: bool = False


class ScopeExportCandidateView(StrictModel):
    scope_path: str
    candidates: list[ScopeExportCandidate] = Field(default_factory=list)
    warnings: list[ServiceIssue] = Field(default_factory=list)
    summary: str


class ScopeExportView(StrictModel):
    scope_path: str
    exports: list[DeclRefView] = Field(default_factory=list)
    changed: bool
    summary: str


class ContentPublicDeclProvider(Protocol):
    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        ...


class _EmptyContentPublicDeclProvider:
    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.runtime.foundation.ok(
            [],
            warnings=[
                self.runtime.foundation.issue(
                    "content_public_decl_provider_missing",
                    "No content public declaration provider is configured.",
                    severity=IssueSeverity.WARNING,
                    object_ref=node_path,
                )
            ],
        )


class ExportComponent:
    """Maintain Scope contract exports and public boundary views."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        node_tree: NodeTreeComponent | None = None,
        contract: ContractComponent | None = None,
        public_decl_provider: ContentPublicDeclProvider | None = None,
        node_projection: "NodeProjectionComponent | None" = None,
    ) -> None:
        self.runtime = runtime
        self.node_tree = node_tree or NodeTreeComponent(runtime)
        self.contract = contract or ContractComponent(runtime, self.node_tree)
        self.public_decl_provider = public_decl_provider or _EmptyContentPublicDeclProvider(runtime)
        self.node_projection = node_projection

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        node = self.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.CONTENT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "node_not_content",
                    "Content public declarations can only be listed for Content nodes.",
                    object_ref=node_path,
                    current=node.value.kind.value,
                    expected=NodeKind.CONTENT.value,
                )
            )
        result = self.public_decl_provider.list_content_public_decls(repo_root, node_path=node_path)
        if not result.ok or result.value is None:
            return self.runtime.foundation.fail(result.issues)
        return result

    def list_scope_export_candidates(self, repo_root: Path, *, scope_path: str) -> ServiceResult[ScopeExportCandidateView]:
        scope = self._require_scope(repo_root, scope_path)
        if not scope.ok or scope.value is None:
            return self.runtime.foundation.fail(scope.issues)
        children = self.node_tree.list_children(repo_root, scope_path=scope_path)
        if not children.ok or children.value is None:
            return self.runtime.foundation.fail(children.issues)
        current = self.contract.get_current_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        current_export_keys = {self._decl_ref_key(ref) for ref in current.value.contract.exports}
        candidates: list[ScopeExportCandidate] = []
        warnings: list[ServiceIssue] = []
        for child in children.value:
            if child.kind == NodeKind.CONTENT:
                public = self.list_content_public_decls(repo_root, node_path=child.path)
                if not public.ok or public.value is None:
                    return self.runtime.foundation.fail(public.issues)
                warnings.extend(public.issues)
                for decl in public.value:
                    if not decl.public:
                        continue
                    candidates.append(
                        ScopeExportCandidate(
                            ref=decl.ref,
                            source_child=child.path,
                            source_kind=NodeKind.CONTENT.value,
                            kind=decl.kind,
                            summary=decl.summary,
                            ready=decl.ready,
                            stale=decl.stale,
                            already_exported=self._decl_ref_key(decl.ref) in current_export_keys,
                        )
                    )
            elif child.kind == NodeKind.SCOPE:
                child_contract = self.contract.get_visible_contract(repo_root, node_path=child.path)
                if not child_contract.ok or child_contract.value is None:
                    continue
                for ref in child_contract.value.contract.exports:
                    candidates.append(
                        ScopeExportCandidate(
                            ref=ref,
                            source_child=child.path,
                            source_kind=NodeKind.SCOPE.value,
                            ready=True,
                            stale=False,
                            already_exported=self._decl_ref_key(ref) in current_export_keys,
                        )
                    )
        candidates.sort(key=lambda item: (item.source_child, item.ref.node, item.ref.name, item.ref.revision))
        candidates = [item.model_copy(update={"index": index}) for index, item in enumerate(candidates)]
        return self.runtime.foundation.ok(
            ScopeExportCandidateView(
                scope_path=scope_path,
                candidates=candidates,
                warnings=warnings,
                summary=f"Loaded {len(candidates)} export candidates for {scope_path}.",
            ),
            warnings=warnings,
        )

    def add_scope_export(
        self,
        repo_root: Path,
        *,
        scope_path: str,
        decl_node: str,
        decl_name: str,
        decl_repo: str | None = None,
        revision: int = 1,
        bind_interface_name: str | None = None,
    ) -> ServiceResult[ScopeExportView]:
        ref = self._build_decl_ref(decl_repo=decl_repo, decl_node=decl_node, decl_name=decl_name, revision=revision)
        if not ref.ok or ref.value is None:
            return self.runtime.foundation.fail(ref.issues)
        current = self.contract.get_edit_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        candidate_contract = deepcopy(current.value.contract)
        candidate = self._find_visible_candidate(repo_root, scope_path=scope_path, ref=ref.value)
        if not candidate.ok or candidate.value is None:
            return self.runtime.foundation.fail(candidate.issues)
        if not candidate.value.ready or candidate.value.stale:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scope_export_decl_not_ready",
                    f"Declaration is not ready for export: {ref.value.name}",
                    object_ref=scope_path,
                    current=f"ready={candidate.value.ready}, stale={candidate.value.stale}",
                    expected="ready=True, stale=False",
                )
            )
        keys = {self._decl_ref_key(ref) for ref in candidate_contract.exports}
        changed = False
        warnings: list[ServiceIssue] = []
        if self._decl_ref_key(ref.value) in keys:
            warnings = [
                self.runtime.foundation.issue(
                    "scope_export_duplicate",
                    f"Scope export already exists: {ref.value.node}:{ref.value.name}@{ref.value.revision}",
                    severity=IssueSeverity.WARNING,
                    object_ref=scope_path,
                )
            ]
        else:
            candidate_contract.exports.append(ref.value)
            changed = True
        if bind_interface_name is not None:
            bound = self._bind_interface(candidate_contract, bind_interface_name, ref.value)
            if not bound.ok:
                return self.runtime.foundation.fail(bound.issues)
            changed = True
        if changed:
            guarded = self.runtime.node.release_guard.check_scope_contract_candidate(
                repo_root, scope_path=scope_path, candidate=candidate_contract
            )
            if not guarded.ok:
                return self.runtime.foundation.fail(guarded.issues)
            refreshed = self._save_and_refresh_interfaces(repo_root, scope_path, candidate_contract)
            if not refreshed.ok:
                return self.runtime.foundation.fail(refreshed.issues)
            warnings.extend(refreshed.issues)
        listed = self.list_scope_exports(repo_root, scope_path=scope_path)
        if not listed.ok or listed.value is None:
            return self.runtime.foundation.fail(listed.issues)
        return self.runtime.foundation.ok(
            ScopeExportView(
                scope_path=scope_path,
                exports=listed.value,
                changed=changed,
                summary=("Updated Scope exports." if changed else "Scope exports already contained the requested declaration."),
            ),
            warnings=warnings,
        )

    def remove_scope_export(self, repo_root: Path, *, scope_path: str, index: int) -> ServiceResult[ScopeExportView]:
        listed = self.list_scope_exports(repo_root, scope_path=scope_path)
        if not listed.ok or listed.value is None:
            return self.runtime.foundation.fail(listed.issues)
        if index < 0 or index >= len(listed.value):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scope_export_index_out_of_range",
                    f"Scope export index is out of range: {index}",
                    object_ref=scope_path,
                    field="index",
                )
            )
        ref = listed.value[index].ref
        current = self.contract.get_edit_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        candidate_contract = deepcopy(current.value.contract)
        key = self._decl_ref_key(ref)
        if not any(self._decl_ref_key(ref) == key for ref in candidate_contract.exports):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("scope_export_missing", f"Scope export not found at index: {index}", object_ref=scope_path, field="index")
            )
        bound_interfaces = [interface.name for interface in candidate_contract.interfaces if interface.bound_decl and self._decl_ref_key(interface.bound_decl) == key]
        if bound_interfaces:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scope_export_bound_interface",
                    "Scope export is still used by bound interfaces.",
                    object_ref=scope_path,
                    current=", ".join(sorted(bound_interfaces)),
                    suggested_action="Unbind or rebind the interfaces before removing the export.",
                )
            )
        candidate_contract.exports = [ref for ref in candidate_contract.exports if self._decl_ref_key(ref) != key]
        guarded = self.runtime.node.release_guard.check_scope_contract_candidate(
            repo_root, scope_path=scope_path, candidate=candidate_contract
        )
        if not guarded.ok:
            return self.runtime.foundation.fail(guarded.issues)
        refreshed = self._save_and_refresh_interfaces(repo_root, scope_path, candidate_contract)
        if not refreshed.ok:
            return self.runtime.foundation.fail(refreshed.issues)
        listed = self.list_scope_exports(repo_root, scope_path=scope_path)
        if not listed.ok or listed.value is None:
            return self.runtime.foundation.fail(listed.issues)
        return self.runtime.foundation.ok(
            ScopeExportView(
                scope_path=scope_path,
                exports=listed.value,
                changed=True,
                summary="Removed Scope export.",
            ),
            warnings=refreshed.issues,
        )

    def list_scope_exports(self, repo_root: Path, *, scope_path: str) -> ServiceResult[list[DeclRefView]]:
        scope = self._require_scope(repo_root, scope_path)
        if not scope.ok or scope.value is None:
            return self.runtime.foundation.fail(scope.issues)
        current = self.contract.get_current_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        views = [self._decl_ref_view(repo_root, scope_path, ref, index=-1) for ref in current.value.contract.exports]
        views.sort(key=lambda item: (item.ref.node, item.ref.name, item.ref.revision))
        views = [view.model_copy(update={"index": index}) for index, view in enumerate(views)]
        return self.runtime.foundation.ok(views)

    def validate_scope_exports(self, repo_root: Path, *, scope_path: str) -> ServiceResult[GateReport]:
        scope = self._require_scope(repo_root, scope_path)
        if not scope.ok or scope.value is None:
            return self.runtime.foundation.fail(scope.issues)
        current = self.contract.get_current_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        issues: list[ServiceIssue] = []
        keys = [self._decl_ref_key(ref) for ref in current.value.contract.exports]
        for duplicate in sorted({key for key in keys if keys.count(key) > 1}):
            issues.append(
                self.runtime.foundation.issue(
                    "scope_export_duplicate",
                    f"Duplicate Scope export: {duplicate[1]}:{duplicate[2]}",
                    object_ref=scope_path,
                )
            )
        for ref in current.value.contract.exports:
            candidate = self._find_visible_candidate(repo_root, scope_path=scope_path, ref=ref)
            if not candidate.ok:
                issues.extend(candidate.issues)
                continue
            if candidate.value is not None and (not candidate.value.ready or candidate.value.stale):
                issues.append(
                    self.runtime.foundation.issue(
                        "scope_export_decl_not_ready",
                        f"Declaration is not ready for export: {ref.name}",
                        object_ref=scope_path,
                    )
                )
        export_key_set = set(keys)
        for interface in current.value.contract.interfaces:
            if interface.bound_decl is not None and self._decl_ref_key(interface.bound_decl) not in export_key_set:
                issues.append(
                    self.runtime.foundation.issue(
                        "interface_binding_not_exported",
                        f"Interface binding is not present in Scope exports: {interface.name}",
                        object_ref=scope_path,
                        field=interface.name,
                    )
                )
        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("scope_exports", issues, summary=f"{len(issues)} Scope export checks failed."))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "scope_exports",
                summary=f"Checked {len(current.value.contract.exports)} Scope exports.",
            )
        )

    def _find_visible_candidate(self, repo_root: Path, *, scope_path: str, ref: DeclRef) -> ServiceResult[ScopeExportCandidate]:
        if ref.repo is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("scope_export_cross_repo_unsupported", "Scope exports must refer to current repo descendants.", object_ref=scope_path)
            )
        if not (ref.node == scope_path or ref.node.startswith(f"{scope_path}.")):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scope_export_not_descendant",
                    f"Scope export is not from this Scope subtree: {ref.node}",
                    object_ref=scope_path,
                )
            )
        direct_child = self._direct_child_path(scope_path, ref.node)
        if direct_child is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("scope_export_not_child_visible", "Scope cannot export itself as a declaration provider.", object_ref=scope_path)
            )
        child = self.node_tree.get_node(repo_root, path=direct_child)
        if not child.ok or child.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scope_export_child_missing",
                    f"Direct child boundary is missing: {direct_child}",
                    object_ref=scope_path,
                )
            )
        if child.value.kind == NodeKind.CONTENT:
            public = self.list_content_public_decls(repo_root, node_path=direct_child)
            if not public.ok or public.value is None:
                return self.runtime.foundation.fail(public.issues)
            for decl in public.value:
                if (decl.ref.repo, decl.ref.node, decl.ref.name) != (ref.repo, ref.node, ref.name) or not decl.public:
                    continue
                compatible_revision = decl.ref.revision == ref.revision
                if not compatible_revision:
                    compatible = self._resolve_semantic_ref(repo_root, ref)
                    if not compatible.ok or compatible.value is None:
                        return self.runtime.foundation.fail(compatible.issues)
                    compatible_revision = compatible.value.compatible and compatible.value.resolved_revision == decl.ref.revision
                if compatible_revision:
                    return self.runtime.foundation.ok(
                        ScopeExportCandidate(
                            ref=decl.ref,
                            source_child=direct_child,
                            source_kind=NodeKind.CONTENT.value,
                            kind=decl.kind,
                            summary=decl.summary,
                            ready=decl.ready,
                            stale=decl.stale,
                        )
                    )
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scope_export_not_public",
                    f"Declaration is not public on direct Content child: {ref.name}",
                    object_ref=scope_path,
                )
            )
        child_contract = self.contract.get_visible_contract(repo_root, node_path=direct_child)
        if not child_contract.ok or child_contract.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scope_export_child_scope_not_committed",
                    f"Child Scope boundary is not committed: {direct_child}",
                    object_ref=scope_path,
                )
            )
        for child_ref in child_contract.value.contract.exports:
            if (child_ref.repo, child_ref.node, child_ref.name) != (ref.repo, ref.node, ref.name):
                continue
            if child_ref.revision == ref.revision:
                return self.runtime.foundation.ok(
                    ScopeExportCandidate(
                        ref=child_ref,
                        source_child=direct_child,
                        source_kind=NodeKind.SCOPE.value,
                    )
                )
            parent_resolved = self._resolve_semantic_ref(repo_root, ref)
            child_resolved = self._resolve_semantic_ref(repo_root, child_ref)
            if not parent_resolved.ok or parent_resolved.value is None:
                return self.runtime.foundation.fail(parent_resolved.issues)
            if not child_resolved.ok or child_resolved.value is None:
                return self.runtime.foundation.fail(child_resolved.issues)
            if (
                parent_resolved.value.compatible
                and child_resolved.value.compatible
                and parent_resolved.value.resolved_revision == child_resolved.value.resolved_revision
            ):
                return self.runtime.foundation.ok(
                    ScopeExportCandidate(
                        ref=child_ref,
                        source_child=direct_child,
                        source_kind=NodeKind.SCOPE.value,
                    )
                )
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "scope_export_not_child_scope_export",
                f"Declaration is not exported by direct child Scope: {ref.name}",
                object_ref=scope_path,
            )
        )

    def _resolve_semantic_ref(self, repo_root: Path, ref: DeclRef):
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        return self.runtime.decl_graph.ref_compatibility.resolve_decl_ref(
            repo_root,
            ref=ref,
            required_availability=config.value.config.target_proof_availability,
        )

    def _decl_ref_view(self, repo_root: Path, scope_path: str, ref: DeclRef, *, index: int) -> DeclRefView:
        candidate = self._find_visible_candidate(repo_root, scope_path=scope_path, ref=ref)
        resolution = self._resolve_semantic_ref(repo_root, ref)
        resolved_revision = (
            resolution.value.resolved_revision
            if resolution.ok and resolution.value is not None
            else None
        )
        resolution_reason = (
            resolution.value.reason
            if resolution.ok and resolution.value is not None
            else None
        )
        if candidate.ok and candidate.value is not None:
            valid = candidate.value.ready and not candidate.value.stale
            return DeclRefView(
                index=index,
                ref=ref,
                repo=ref.repo,
                node=ref.node,
                name=ref.name,
                revision=ref.revision,
                resolved_revision=resolved_revision,
                resolution_reason=resolution_reason,
                valid=valid,
                source=candidate.value.source_child,
                summary=("Scope export is valid." if valid else "Scope export candidate is not ready."),
            )
        return DeclRefView(
            index=index,
            ref=ref,
            repo=ref.repo,
            node=ref.node,
            name=ref.name,
            revision=ref.revision,
            resolved_revision=resolved_revision,
            resolution_reason=resolution_reason,
            valid=False,
            summary="Scope export is not currently valid.",
            issues=candidate.issues,
        )

    def _bind_interface(self, contract: object, name: str, ref: DeclRef) -> ServiceResult[None]:
        normalized = name.strip()
        if not normalized:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_name_required", "Interface name is required.", field="bind_interface_name"))
        for interface in getattr(contract, "interfaces", []):
            if interface.name != normalized:
                continue
            if interface.bound_decl is not None and self._decl_ref_key(interface.bound_decl) != self._decl_ref_key(ref):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "interface_already_bound",
                        f"Interface is already bound to a different declaration: {normalized}",
                        field=normalized,
                    )
                )
            interface.bound_decl = ref
            return self.runtime.foundation.ok(None)
        return self.runtime.foundation.fail(self.runtime.foundation.issue("interface_missing", f"Interface not found: {normalized}", field=normalized))

    def _build_decl_ref(
        self,
        *,
        decl_repo: str | None,
        decl_node: str,
        decl_name: str,
        revision: int,
    ) -> ServiceResult[DeclRef]:
        repo = decl_repo.strip() if isinstance(decl_repo, str) and decl_repo.strip() else None
        node = decl_node.strip() if isinstance(decl_node, str) else ""
        name = decl_name.strip() if isinstance(decl_name, str) else ""
        if not node:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("decl_ref_node_required", "decl_node is required.", field="decl_node"))
        if not name:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("decl_ref_name_required", "decl_name is required.", field="decl_name"))
        if revision < 1:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("decl_ref_revision_invalid", "revision must be a positive integer.", field="revision"))
        return self.runtime.foundation.ok(DeclRef(repo=repo, node=node, name=name, revision=revision))

    def _require_scope(self, repo_root: Path, scope_path: str) -> ServiceResult[NodeView]:
        node = self.node_tree.get_node(repo_root, path=scope_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.SCOPE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_not_scope", "Scope export operation requires a Scope node.", object_ref=scope_path)
            )
        return node

    def _direct_child_path(self, scope_path: str, ref_node: str) -> str | None:
        if ref_node == scope_path or not ref_node.startswith(f"{scope_path}."):
            return None
        remaining = ref_node[len(scope_path) + 1 :]
        first = remaining.split(".", 1)[0]
        return f"{scope_path}.{first}"

    def _refresh_interfaces(self, repo_root: Path, scope_path: str) -> ServiceResult[object]:
        projection = self.node_projection
        if projection is None:
            lean_projection = self.runtime.app.lean_projection
            if lean_projection is None:
                return self.runtime.foundation.ok(None)
            projection = lean_projection.node_projection
        return projection.refresh_interfaces(repo_root, node_path=scope_path)

    def _save_and_refresh_interfaces(self, repo_root: Path, scope_path: str, contract: object) -> ServiceResult[object]:
        return persist_contract_with_projection(
            self.runtime,
            repo_root=repo_root,
            node_path=scope_path,
            candidate=contract,
            projection_kind="interfaces",
            save=lambda root, path, candidate: self.contract._persist_open_candidate(
                root, node_path=path, candidate=candidate
            ),
            refresh=lambda: self._refresh_interfaces(repo_root, scope_path),
        )

    def _decl_ref_key(self, ref: DeclRef) -> tuple[str | None, str, str, int]:
        return (ref.repo, ref.node, ref.name, ref.revision)
