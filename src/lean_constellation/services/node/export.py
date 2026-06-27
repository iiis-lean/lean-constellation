"""Scope export and content public declaration views."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import FoundationContext, FoundationService, GateReport, IssueSeverity, ServiceIssue, ServiceResult, WriteMode
from lean_constellation.services.node.contract import ContractComponent, NodeContractView
from lean_constellation.services.node.node_tree import NodeKind, NodeTreeComponent, NodeView

if TYPE_CHECKING:
    from lean_constellation.services.lean_projection.node_projection import NodeProjectionComponent


class DeclPublicView(StrictModel):
    ref: DeclRef
    kind: str | None = None
    summary: str | None = None
    public: bool = True
    ready: bool = True
    stale: bool = False
    source: str = "provider"


class DeclRefView(StrictModel):
    ref: DeclRef
    valid: bool
    source: str | None = None
    summary: str
    issues: list[ServiceIssue] = Field(default_factory=list)


class ScopeExportCandidate(StrictModel):
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
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        return self.foundation.ok(
            [],
            warnings=[
                self.foundation.issue(
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
        foundation: FoundationService | None = None,
        node_tree: NodeTreeComponent | None = None,
        contract: ContractComponent | None = None,
        public_decl_provider: ContentPublicDeclProvider | None = None,
        node_projection: "NodeProjectionComponent | None" = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.node_tree = node_tree or NodeTreeComponent(self.foundation)
        self.contract = contract or ContractComponent(self.foundation, self.node_tree)
        self.public_decl_provider = public_decl_provider or _EmptyContentPublicDeclProvider(self.foundation)
        self.node_projection = node_projection

    def list_content_public_decls(self, repo_root: Path, *, node_path: str) -> ServiceResult[list[DeclPublicView]]:
        node = self.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.foundation.fail(node.issues)
        if node.value.kind != NodeKind.CONTENT:
            return self.foundation.fail(
                self.foundation.issue(
                    "node_not_content",
                    "Content public declarations can only be listed for Content nodes.",
                    object_ref=node_path,
                    current=node.value.kind.value,
                    expected=NodeKind.CONTENT.value,
                )
            )
        result = self.public_decl_provider.list_content_public_decls(repo_root, node_path=node_path)
        if not result.ok or result.value is None:
            return self.foundation.fail(result.issues)
        return result

    def list_scope_export_candidates(self, repo_root: Path, *, scope_path: str) -> ServiceResult[ScopeExportCandidateView]:
        scope = self._require_scope(repo_root, scope_path)
        if not scope.ok or scope.value is None:
            return self.foundation.fail(scope.issues)
        children = self.node_tree.list_children(repo_root, scope_path=scope_path)
        if not children.ok or children.value is None:
            return self.foundation.fail(children.issues)
        current = self.contract.get_current_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        current_export_keys = {self._decl_ref_key(ref) for ref in current.value.contract.exports}
        candidates: list[ScopeExportCandidate] = []
        warnings: list[ServiceIssue] = []
        for child in children.value:
            if child.kind == NodeKind.CONTENT:
                public = self.list_content_public_decls(repo_root, node_path=child.path)
                if not public.ok or public.value is None:
                    return self.foundation.fail(public.issues)
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
                child_contract = self.contract.get_current_contract(repo_root, node_path=child.path)
                if not child_contract.ok or child_contract.value is None:
                    return self.foundation.fail(child_contract.issues)
                if child_contract.value.version_status.value != "committed":
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
        return self.foundation.ok(
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
        decl_ref: str,
        bind_interface_name: str | None = None,
    ) -> ServiceResult[ScopeExportView]:
        parsed = self._parse_decl_ref(decl_ref)
        if not parsed.ok or parsed.value is None:
            return self.foundation.fail(parsed.issues)
        opened = self.contract.ensure_scope_contract(repo_root, scope_path=scope_path)
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        candidate = self._find_visible_candidate(repo_root, scope_path=scope_path, ref=parsed.value)
        if not candidate.ok or candidate.value is None:
            return self.foundation.fail(candidate.issues)
        if not candidate.value.ready or candidate.value.stale:
            return self.foundation.fail(
                self.foundation.issue(
                    "scope_export_decl_not_ready",
                    f"Declaration is not ready for export: {parsed.value.name}",
                    object_ref=scope_path,
                    current=f"ready={candidate.value.ready}, stale={candidate.value.stale}",
                    expected="ready=True, stale=False",
                )
            )
        keys = {self._decl_ref_key(ref) for ref in opened.value.contract.exports}
        changed = False
        warnings: list[ServiceIssue] = []
        if self._decl_ref_key(parsed.value) in keys:
            warnings = [
                self.foundation.issue(
                    "scope_export_duplicate",
                    f"Scope export already exists: {decl_ref}",
                    severity=IssueSeverity.WARNING,
                    object_ref=scope_path,
                )
            ]
        else:
            opened.value.contract.exports.append(parsed.value)
            changed = True
        if bind_interface_name is not None:
            bound = self._bind_interface(opened.value.contract, bind_interface_name, parsed.value)
            if not bound.ok:
                return self.foundation.fail(bound.issues)
            changed = True
        if changed:
            saved = self._save_contract(repo_root, scope_path, opened.value.contract)
            if not saved.ok:
                return self.foundation.fail(saved.issues)
            refreshed = self._refresh_interfaces(repo_root, scope_path)
            if not refreshed.ok:
                return self.foundation.fail(refreshed.issues)
            warnings.extend(refreshed.issues)
        listed = self.list_scope_exports(repo_root, scope_path=scope_path)
        if not listed.ok or listed.value is None:
            return self.foundation.fail(listed.issues)
        return self.foundation.ok(
            ScopeExportView(
                scope_path=scope_path,
                exports=listed.value,
                changed=changed,
                summary=("Updated Scope exports." if changed else "Scope exports already contained the requested declaration."),
            ),
            warnings=warnings,
        )

    def remove_scope_export(self, repo_root: Path, *, scope_path: str, decl_ref: str) -> ServiceResult[ScopeExportView]:
        parsed = self._parse_decl_ref(decl_ref)
        if not parsed.ok or parsed.value is None:
            return self.foundation.fail(parsed.issues)
        opened = self.contract.ensure_scope_contract(repo_root, scope_path=scope_path)
        if not opened.ok or opened.value is None:
            return self.foundation.fail(opened.issues)
        key = self._decl_ref_key(parsed.value)
        if not any(self._decl_ref_key(ref) == key for ref in opened.value.contract.exports):
            return self.foundation.fail(
                self.foundation.issue("scope_export_missing", f"Scope export not found: {decl_ref}", object_ref=scope_path)
            )
        bound_interfaces = [interface.name for interface in opened.value.contract.interfaces if interface.bound_decl and self._decl_ref_key(interface.bound_decl) == key]
        if bound_interfaces:
            return self.foundation.fail(
                self.foundation.issue(
                    "scope_export_bound_interface",
                    "Scope export is still used by bound interfaces.",
                    object_ref=scope_path,
                    current=", ".join(sorted(bound_interfaces)),
                    suggested_action="Unbind or rebind the interfaces before removing the export.",
                )
            )
        opened.value.contract.exports = [ref for ref in opened.value.contract.exports if self._decl_ref_key(ref) != key]
        saved = self._save_contract(repo_root, scope_path, opened.value.contract)
        if not saved.ok:
            return self.foundation.fail(saved.issues)
        refreshed = self._refresh_interfaces(repo_root, scope_path)
        if not refreshed.ok:
            return self.foundation.fail(refreshed.issues)
        listed = self.list_scope_exports(repo_root, scope_path=scope_path)
        if not listed.ok or listed.value is None:
            return self.foundation.fail(listed.issues)
        return self.foundation.ok(
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
            return self.foundation.fail(scope.issues)
        current = self.contract.get_current_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        views = [self._decl_ref_view(repo_root, scope_path, ref) for ref in current.value.contract.exports]
        views.sort(key=lambda item: (item.ref.node, item.ref.name, item.ref.revision))
        return self.foundation.ok(views)

    def validate_scope_exports(self, repo_root: Path, *, scope_path: str) -> ServiceResult[GateReport]:
        scope = self._require_scope(repo_root, scope_path)
        if not scope.ok or scope.value is None:
            return self.foundation.fail(scope.issues)
        current = self.contract.get_current_contract(repo_root, node_path=scope_path)
        if not current.ok or current.value is None:
            return self.foundation.fail(current.issues)
        issues: list[ServiceIssue] = []
        keys = [self._decl_ref_key(ref) for ref in current.value.contract.exports]
        for duplicate in sorted({key for key in keys if keys.count(key) > 1}):
            issues.append(
                self.foundation.issue(
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
                    self.foundation.issue(
                        "scope_export_decl_not_ready",
                        f"Declaration is not ready for export: {ref.name}",
                        object_ref=scope_path,
                    )
                )
        export_key_set = set(keys)
        for interface in current.value.contract.interfaces:
            if interface.bound_decl is not None and self._decl_ref_key(interface.bound_decl) not in export_key_set:
                issues.append(
                    self.foundation.issue(
                        "interface_binding_not_exported",
                        f"Interface binding is not present in Scope exports: {interface.name}",
                        object_ref=scope_path,
                        field=interface.name,
                    )
                )
        if issues:
            return self.foundation.ok(self.foundation.gate_failed("scope_exports", issues, summary=f"{len(issues)} Scope export checks failed."))
        return self.foundation.ok(
            self.foundation.gate_passed(
                "scope_exports",
                summary=f"Checked {len(current.value.contract.exports)} Scope exports.",
            )
        )

    def _find_visible_candidate(self, repo_root: Path, *, scope_path: str, ref: DeclRef) -> ServiceResult[ScopeExportCandidate]:
        if ref.repo is not None:
            return self.foundation.fail(
                self.foundation.issue("scope_export_cross_repo_unsupported", "Scope exports must refer to current repo descendants.", object_ref=scope_path)
            )
        if not (ref.node == scope_path or ref.node.startswith(f"{scope_path}.")):
            return self.foundation.fail(
                self.foundation.issue(
                    "scope_export_not_descendant",
                    f"Scope export is not from this Scope subtree: {ref.node}",
                    object_ref=scope_path,
                )
            )
        direct_child = self._direct_child_path(scope_path, ref.node)
        if direct_child is None:
            return self.foundation.fail(
                self.foundation.issue("scope_export_not_child_visible", "Scope cannot export itself as a declaration provider.", object_ref=scope_path)
            )
        child = self.node_tree.get_node(repo_root, path=direct_child)
        if not child.ok or child.value is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "scope_export_child_missing",
                    f"Direct child boundary is missing: {direct_child}",
                    object_ref=scope_path,
                )
            )
        if child.value.kind == NodeKind.CONTENT:
            public = self.list_content_public_decls(repo_root, node_path=direct_child)
            if not public.ok or public.value is None:
                return self.foundation.fail(public.issues)
            for decl in public.value:
                if self._decl_ref_key(decl.ref) == self._decl_ref_key(ref) and decl.public:
                    return self.foundation.ok(
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
            return self.foundation.fail(
                self.foundation.issue(
                    "scope_export_not_public",
                    f"Declaration is not public on direct Content child: {ref.name}",
                    object_ref=scope_path,
                )
            )
        child_contract = self.contract.get_current_contract(repo_root, node_path=direct_child)
        if not child_contract.ok or child_contract.value is None:
            return self.foundation.fail(child_contract.issues)
        if child_contract.value.version_status.value != "committed":
            return self.foundation.fail(
                self.foundation.issue(
                    "scope_export_child_scope_not_committed",
                    f"Child Scope boundary is not committed: {direct_child}",
                    object_ref=scope_path,
                )
            )
        for child_ref in child_contract.value.contract.exports:
            if self._decl_ref_key(child_ref) == self._decl_ref_key(ref):
                return self.foundation.ok(
                    ScopeExportCandidate(
                        ref=child_ref,
                        source_child=direct_child,
                        source_kind=NodeKind.SCOPE.value,
                    )
                )
        return self.foundation.fail(
            self.foundation.issue(
                "scope_export_not_child_scope_export",
                f"Declaration is not exported by direct child Scope: {ref.name}",
                object_ref=scope_path,
            )
        )

    def _decl_ref_view(self, repo_root: Path, scope_path: str, ref: DeclRef) -> DeclRefView:
        candidate = self._find_visible_candidate(repo_root, scope_path=scope_path, ref=ref)
        if candidate.ok and candidate.value is not None:
            valid = candidate.value.ready and not candidate.value.stale
            return DeclRefView(
                ref=ref,
                valid=valid,
                source=candidate.value.source_child,
                summary=("Scope export is valid." if valid else "Scope export candidate is not ready."),
            )
        return DeclRefView(
            ref=ref,
            valid=False,
            summary="Scope export is not currently valid.",
            issues=candidate.issues,
        )

    def _bind_interface(self, contract: object, name: str, ref: DeclRef) -> ServiceResult[None]:
        normalized = name.strip()
        if not normalized:
            return self.foundation.fail(self.foundation.issue("interface_name_required", "Interface name is required.", field="bind_interface_name"))
        for interface in getattr(contract, "interfaces", []):
            if interface.name != normalized:
                continue
            if interface.bound_decl is not None and self._decl_ref_key(interface.bound_decl) != self._decl_ref_key(ref):
                return self.foundation.fail(
                    self.foundation.issue(
                        "interface_already_bound",
                        f"Interface is already bound to a different declaration: {normalized}",
                        field=normalized,
                    )
                )
            interface.bound_decl = ref
            return self.foundation.ok(None)
        return self.foundation.fail(self.foundation.issue("interface_missing", f"Interface not found: {normalized}", field=normalized))

    def _parse_decl_ref(self, value: str) -> ServiceResult[DeclRef]:
        text = value.strip() if value else ""
        if not text or ":" not in text:
            return self.foundation.fail(
                self.foundation.issue("decl_ref_invalid", "decl_ref must use the flat form '<node_path>:<decl_name>' or '<node_path>:<decl_name>@<revision>'.", field="decl_ref")
            )
        node, name_part = text.rsplit(":", 1)
        revision = 1
        name = name_part
        if "@" in name_part:
            name, revision_text = name_part.rsplit("@", 1)
            try:
                revision = int(revision_text)
            except ValueError:
                return self.foundation.fail(self.foundation.issue("decl_ref_revision_invalid", "DeclRef revision must be an integer.", field="decl_ref"))
        if not node.strip() or not name.strip() or revision < 1:
            return self.foundation.fail(self.foundation.issue("decl_ref_invalid", "DeclRef node/name/revision are invalid.", field="decl_ref"))
        return self.foundation.ok(DeclRef(repo=None, node=node.strip(), name=name.strip(), revision=revision))

    def _require_scope(self, repo_root: Path, scope_path: str) -> ServiceResult[NodeView]:
        node = self.node_tree.get_node(repo_root, path=scope_path)
        if not node.ok or node.value is None:
            return self.foundation.fail(node.issues)
        if node.value.kind != NodeKind.SCOPE:
            return self.foundation.fail(
                self.foundation.issue("node_not_scope", "Scope export operation requires a Scope node.", object_ref=scope_path)
            )
        return node

    def _direct_child_path(self, scope_path: str, ref_node: str) -> str | None:
        if ref_node == scope_path or not ref_node.startswith(f"{scope_path}."):
            return None
        remaining = ref_node[len(scope_path) + 1 :]
        first = remaining.split(".", 1)[0]
        return f"{scope_path}.{first}"

    def _save_contract(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[object]:
        path = self.foundation.layout.node_contract_path(FoundationContext(repo_root=Path(repo_root)), node_path, getattr(contract, "version"))
        return self.foundation.store.write_json_atomic(path, contract, mode=WriteMode.UPDATE_EXISTING)

    def _refresh_interfaces(self, repo_root: Path, scope_path: str) -> ServiceResult[object]:
        projection = self.node_projection
        if projection is None:
            from lean_constellation.services.lean_projection.node_projection import NodeProjectionComponent

            projection = NodeProjectionComponent(self.foundation, self.contract, export=self)
        return projection.refresh_interfaces(repo_root, node_path=scope_path)

    def _decl_ref_key(self, ref: DeclRef) -> tuple[str | None, str, str, int]:
        return (ref.repo, ref.node, ref.name, ref.revision)
