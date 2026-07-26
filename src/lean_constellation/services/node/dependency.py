"""NodeContract dependency management and visibility gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, TypeAdapter

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef, NodeRef
from lean_constellation.domain.repo import (
    ProofAvailability,
    proof_availability_for_completion_mode,
)
from lean_constellation.services.foundation import (
    GateReport,
    IssueSeverity,
    ServiceIssue,
    ServiceResult,
)
from lean_constellation.services.node.contract import ContractComponent, ContractVersionStatus
from lean_constellation.services.node.contract_fields import NodeDep, NodeDepActor
from lean_constellation.services.node.export import ContentPublicDeclProvider
from lean_constellation.services.node.node_tree import NodeKind, NodeTreeComponent
from lean_constellation.services.node.projection_transaction import persist_contract_with_projection

if TYPE_CHECKING:
    from lean_constellation.services.lean_projection.node_projection import NodeProjectionComponent
    from lean_constellation.services.runtime import LeanRuntimeServices


class NodeDepView(StrictModel):
    index: int
    target_repo: str | None = None
    target_node: str
    expected_decl_refs: list[DeclRef] = Field(default_factory=list)
    reason: str | None = None
    added_by: NodeDepActor = NodeDepActor.COORDINATOR
    summary: str


class NodeDepsView(StrictModel):
    node_path: str
    deps: list[NodeDepView] = Field(default_factory=list)
    summary: str


class NodeDependencyMutationReceipt(StrictModel):
    """Exact delta from one node-dependency mutation."""

    node_path: str
    operation: Literal["add", "remove"]
    changed: bool
    added: list[NodeDep] = Field(default_factory=list)
    updated: list[NodeDep] = Field(default_factory=list)
    removed: list[NodeDep] = Field(default_factory=list)
    managed_projection_changed: bool = False
    changed_files: list[str] = Field(default_factory=list)
    reread_required: bool = False
    summary: str


class VisibleNodeBoundaryItem(StrictModel):
    index: int = -1
    repo: str | None = None
    node_path: str
    node_kind: str
    ready: bool
    import_module: str
    exported_decl_refs: list[DeclRef] = Field(default_factory=list)
    interface_names: list[str] = Field(default_factory=list)
    summary: str


class VisibleBoundaryView(StrictModel):
    node_path: str
    boundaries: list[VisibleNodeBoundaryItem] = Field(default_factory=list)
    summary: str


class DependencyComponent:
    """Maintain NodeDep entries embedded in NodeContract."""

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
        self.public_decl_provider = public_decl_provider
        self.node_projection = node_projection

    def list_visible_node_boundaries(self, repo_root: Path, *, node_path: str) -> ServiceResult[VisibleBoundaryView]:
        current = self.node_tree.get_node(repo_root, path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        tree = self.node_tree.get_node_tree(repo_root)
        if not tree.ok or tree.value is None:
            return self.runtime.foundation.fail(tree.issues)

        boundaries: list[VisibleNodeBoundaryItem] = []
        for node in tree.value.nodes:
            if node.path == node_path:
                continue
            contract = self.contract.get_visible_contract(repo_root, node_path=node.path)
            if not contract.ok or contract.value is None:
                continue
            exported_decl_refs = self._public_decl_refs(contract.value.contract)
            if node.kind == NodeKind.CONTENT and self.public_decl_provider is not None:
                public = self.public_decl_provider.list_content_public_decls(repo_root, node_path=node.path)
                if not public.ok or public.value is None:
                    return self.runtime.foundation.fail(public.issues)
                exported_decl_refs = self._merge_decl_refs(
                    exported_decl_refs,
                    [item.ref for item in public.value if item.public and item.ready and not item.stale],
                )
            boundaries.append(
                VisibleNodeBoundaryItem(
                    repo=None,
                    node_path=node.path,
                    node_kind=node.kind.value,
                    ready=True,
                    import_module=f"{node.path}.Interfaces",
                    exported_decl_refs=exported_decl_refs,
                    interface_names=[interface.name for interface in contract.value.contract.interfaces],
                    summary=f"Ready {node.kind.value} boundary {node.path}.",
                )
            )

        external = self._external_lake_boundaries(repo_root)
        if not external.ok or external.value is None:
            return self.runtime.foundation.fail(external.issues)
        boundaries.extend(external.value)
        boundaries.sort(key=lambda item: (item.repo or "", item.node_path))
        boundaries = [item.model_copy(update={"index": index}) for index, item in enumerate(boundaries)]
        return self.runtime.foundation.ok(
            VisibleBoundaryView(
                node_path=node_path,
                boundaries=boundaries,
                summary=f"Loaded {len(boundaries)} visible ready node boundaries for {node_path}.",
            )
        )

    def list_node_deps(self, repo_root: Path, *, node_path: str) -> ServiceResult[NodeDepsView]:
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        deps = self._normalize_deps(current.value.contract.deps)
        if not deps.ok or deps.value is None:
            return self.runtime.foundation.fail(deps.issues)
        views = [self._dep_view(index, dep) for index, dep in enumerate(deps.value)]
        return self.runtime.foundation.ok(
            NodeDepsView(
                node_path=node_path,
                deps=views,
                summary=f"Loaded {len(views)} node dependencies for {node_path}.",
            )
        )

    def add_node_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        target_node: str,
        reason: str,
        actor: str | NodeDepActor,
        expected_decl_names: list[str] | None = None,
        target_repo: str | None = None,
    ) -> ServiceResult[NodeDependencyMutationReceipt]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.runtime.foundation.fail(normalized_actor.issues)
        if not reason or not reason.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_dep_reason_required", "Node dependency reason is required.", field="reason"))
        repo = target_repo.strip() if isinstance(target_repo, str) and target_repo.strip() else None
        target = self._normalize_node_path(target_node, field="target_node")
        if not target.ok or target.value is None:
            return self.runtime.foundation.fail(target.issues)
        if repo is None and target.value == node_path:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_dep_self_dependency", "A node cannot depend on itself.", object_ref=node_path, field="target_node")
            )
        visible = self.list_visible_node_boundaries(repo_root, node_path=node_path)
        if not visible.ok or visible.value is None:
            return self.runtime.foundation.fail(visible.issues)
        boundary = next((item for item in visible.value.boundaries if item.repo == repo and item.node_path == target.value), None)
        if boundary is None:
            target_label = f"{repo}:{target.value}" if repo else target.value
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "node_dep_target_not_visible",
                    f"Target node is not a visible ready boundary: {target_label}",
                    object_ref=node_path,
                    field="target_node",
                )
            )
        return self._add_node_dep_from_boundary(
            repo_root,
            node_path=node_path,
            boundary=boundary,
            expected_decl_names=expected_decl_names or [],
            reason=reason,
            actor=normalized_actor.value,
        )

    def _add_node_dep_from_boundary(
        self,
        repo_root: Path,
        *,
        node_path: str,
        boundary: VisibleNodeBoundaryItem,
        expected_decl_names: list[str],
        reason: str,
        actor: NodeDepActor,
    ) -> ServiceResult[NodeDependencyMutationReceipt]:
        expected_refs = self._resolve_expected_decl_names(boundary, expected_decl_names)
        if not expected_refs.ok or expected_refs.value is None:
            return self.runtime.foundation.fail(expected_refs.issues)

        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        current = self._normalize_deps(opened.value.contract.deps)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)

        target_ref = NodeRef(repo=boundary.repo, node=boundary.node_path)
        dep_id = self._stable_dep_id(target_ref)
        existing = next((item for item in current.value if item.dep_id == dep_id or self._same_target(item.target, target_ref)), None)
        if existing is not None:
            return self._merge_existing_dep(
                repo_root,
                node_path=node_path,
                contract=opened.value.contract,
                deps=current.value,
                existing=existing,
                expected_refs=expected_refs.value,
                reason=reason,
                actor=actor,
            )

        added_dep = NodeDep(
            dep_id=dep_id,
            target=target_ref,
            expected_decl_refs=expected_refs.value,
            reason=reason.strip(),
            added_by=actor,
        )
        current.value.append(added_dep)
        opened.value.contract.deps = list(current.value)
        persisted = self._save_and_refresh_prelude(repo_root, node_path, opened.value.contract)
        if not persisted.ok:
            return self.runtime.foundation.fail(persisted.issues)
        return self._dependency_mutation_receipt(
            node_path=node_path,
            operation="add",
            added=[added_dep],
            projection=persisted.value,
            warnings=persisted.issues,
        )

    def remove_node_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        index: int,
        actor: str | NodeDepActor,
    ) -> ServiceResult[NodeDependencyMutationReceipt]:
        normalized_actor = self._normalize_actor(actor)
        if not normalized_actor.ok or normalized_actor.value is None:
            return self.runtime.foundation.fail(normalized_actor.issues)
        opened = self.contract.get_edit_contract(repo_root, node_path=node_path)
        if not opened.ok or opened.value is None:
            return self.runtime.foundation.fail(opened.issues)
        current = self._normalize_deps(opened.value.contract.deps)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        if index < 0 or index >= len(current.value):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "node_dep_index_out_of_range",
                    f"Node dependency index is out of range: {index}",
                    object_ref=node_path,
                    field="index",
                )
            )
        target = current.value[index]
        permission = self._check_remove_permission(node_path, target.added_by, normalized_actor.value)
        if not permission.ok:
            return self.runtime.foundation.fail(permission.issues)
        opened.value.contract.deps = [item for item_index, item in enumerate(current.value) if item_index != index]
        persisted = self._save_and_refresh_prelude(repo_root, node_path, opened.value.contract)
        if not persisted.ok:
            return self.runtime.foundation.fail(persisted.issues)
        return self._dependency_mutation_receipt(
            node_path=node_path,
            operation="remove",
            removed=[target],
            projection=persisted.value,
            warnings=persisted.issues,
        )

    def validate_node_deps(self, repo_root: Path, *, node_path: str) -> ServiceResult[GateReport]:
        current = self.contract.get_current_contract(repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        deps = self._normalize_deps(current.value.contract.deps)
        if not deps.ok or deps.value is None:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("node_deps", deps.issues, summary="Node dependency entries are invalid."))

        issues: list[ServiceIssue] = []
        warnings: list[ServiceIssue] = []
        external_boundaries: list[VisibleNodeBoundaryItem] = []
        if any(dep.target.repo is not None for dep in deps.value):
            external = self._external_lake_boundaries(repo_root)
            if not external.ok or external.value is None:
                issues.extend(external.issues)
            else:
                external_boundaries = external.value
        target_keys = [self._target_key(dep.target) for dep in deps.value]
        for key in sorted({item for item in target_keys if target_keys.count(item) > 1}):
            issues.append(
                self.runtime.foundation.issue(
                    "node_dep_duplicate",
                    f"Duplicate node dependency target: {key}",
                    object_ref=node_path,
                    field="deps",
                )
            )
        for index, dep in enumerate(deps.value):
            dep_field = f"deps.{index}"
            if dep.target.repo is not None:
                boundary = next(
                    (
                        item
                        for item in external_boundaries
                        if item.repo == dep.target.repo and item.node_path == dep.target.node
                    ),
                    None,
                )
                if boundary is None:
                    issues.append(
                        self.runtime.foundation.issue(
                            "node_dep_external_boundary_unavailable",
                            "External dependency is not an attached stable provider boundary.",
                            object_ref=f"{dep.target.repo}:{dep.target.node}",
                            field=dep_field,
                        )
                    )
                    continue
                for ref in dep.expected_decl_refs:
                    if ref.repo != dep.target.repo:
                        issues.append(
                            self.runtime.foundation.issue(
                                "node_dep_external_expected_decl_repo_mismatch",
                                "Expected declaration does not belong to the external dependency repo.",
                                object_ref=f"{ref.repo or ''}:{ref.node}:{ref.name}@{ref.revision}",
                                field=dep_field,
                                current=ref.repo,
                                expected=dep.target.repo,
                            )
                        )
                        continue
                    resolved = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
                        repo_root,
                        ref=ref,
                        required_availability=ProofAvailability.DECLARED,
                    )
                    if not resolved.ok or resolved.value is None:
                        issues.extend(resolved.issues)
                        continue
                    if not resolved.value.compatible:
                        issues.append(
                            self.runtime.foundation.issue(
                                "node_dep_external_expected_decl_incompatible",
                                "Expected declaration is no longer compatible on the provider public boundary.",
                                object_ref=f"{ref.repo}:{ref.node}:{ref.name}@{ref.revision}",
                                field=dep_field,
                                current=resolved.value.reason,
                                expected="compatible public declaration",
                            )
                        )
                continue
            if dep.target.node == node_path:
                issues.append(self.runtime.foundation.issue("node_dep_self_dependency", "A node cannot depend on itself.", object_ref=node_path, field=dep_field))
                continue
            target_node = self.node_tree.get_node(repo_root, path=dep.target.node)
            if not target_node.ok or target_node.value is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "node_dep_target_missing",
                        f"Node dependency target is missing or inactive: {dep.target.node}",
                        object_ref=node_path,
                        field=dep_field,
                    )
                )
                continue
            target_contract = self.contract.get_visible_contract(repo_root, node_path=dep.target.node)
            if not target_contract.ok or target_contract.value is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "node_dep_target_not_ready",
                        f"Node dependency target is missing, inactive, or not a committed ready boundary: {dep.target.node}",
                        object_ref=node_path,
                        field=dep_field,
                        current="missing_or_uncommitted",
                        expected=ContractVersionStatus.COMMITTED.value,
                    )
                )
                continue
            target_public_refs = self._public_decl_refs(target_contract.value.contract)
            if target_node.value.kind == NodeKind.CONTENT and self.public_decl_provider is not None:
                public = self.public_decl_provider.list_content_public_decls(
                    repo_root,
                    node_path=dep.target.node,
                )
                if not public.ok or public.value is None:
                    issues.extend(public.issues)
                    continue
                target_public_refs = self._merge_decl_refs(
                    target_public_refs,
                    [item.ref for item in public.value if item.public and item.ready and not item.stale],
                )
            public_refs = {self._decl_ref_key(ref) for ref in target_public_refs}
            for ref in dep.expected_decl_refs:
                if self._decl_ref_key(ref) not in public_refs:
                    issues.append(
                        self.runtime.foundation.issue(
                            "node_dep_expected_decl_not_public",
                            f"Expected declaration is not public on provider boundary: {ref.name}",
                            object_ref=node_path,
                            field=dep_field,
                        )
                    )
            if self._has_local_dep_path(repo_root, start=dep.target.node, target=node_path):
                issues.append(
                    self.runtime.foundation.issue(
                        "node_dep_cycle",
                        f"Node dependency introduces a cycle through {dep.target.node}.",
                        object_ref=node_path,
                        field=dep_field,
                    )
                )

        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("node_deps", issues, summary=f"{len(issues)} node dependency checks failed."))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "node_deps",
                summary=f"Checked {len(deps.value)} node dependencies.",
                warnings=warnings,
            )
        )

    def check_content_batch_independent(self, repo_root: Path, *, node_paths: list[str]) -> ServiceResult[GateReport]:
        normalized_paths = [path.strip() for path in node_paths if path and path.strip()]
        if not normalized_paths:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("content_batch_empty", "node_paths must be non-empty.", field="node_paths"))
        issues: list[ServiceIssue] = []
        duplicates = sorted({path for path in normalized_paths if normalized_paths.count(path) > 1})
        for duplicate in duplicates:
            issues.append(self.runtime.foundation.issue("content_batch_duplicate", f"Duplicate content node in batch: {duplicate}", field="node_paths"))
        batch = set(normalized_paths)
        for path in normalized_paths:
            node = self.node_tree.get_node(repo_root, path=path)
            if not node.ok or node.value is None:
                issues.append(self.runtime.foundation.issue("content_batch_node_missing", f"Content node is missing: {path}", object_ref=path))
                continue
            if node.value.kind != NodeKind.CONTENT:
                issues.append(
                    self.runtime.foundation.issue(
                        "content_batch_node_not_content",
                        f"Batch item is not a Content node: {path}",
                        object_ref=path,
                        current=node.value.kind.value,
                        expected=NodeKind.CONTENT.value,
                    )
                )
                continue
            for target in sorted(batch - {path}):
                if self._has_local_dep_path(repo_root, start=path, target=target):
                    issues.append(
                        self.runtime.foundation.issue(
                            "content_batch_dependency_present",
                            f"Content batch is not independent: {path} depends on {target}.",
                            object_ref=path,
                            details={"provider": target},
                        )
                    )
        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("content_batch_independent", issues, summary=f"{len(issues)} batch checks failed."))
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed(
                "content_batch_independent",
                summary=f"{len(normalized_paths)} content nodes are independent.",
            )
        )

    def _merge_existing_dep(
        self,
        repo_root: Path,
        *,
        node_path: str,
        contract: object,
        deps: list[NodeDep],
        existing: NodeDep,
        expected_refs: list[DeclRef],
        reason: str,
        actor: NodeDepActor,
    ) -> ServiceResult[NodeDependencyMutationReceipt]:
        existing.added_by = NodeDepActor(existing.added_by)
        existing.dep_id = existing.dep_id or self._stable_dep_id(existing.target)
        existing_keys = {self._decl_ref_key(ref) for ref in existing.expected_decl_refs}
        new_refs = [ref for ref in expected_refs if self._decl_ref_key(ref) not in existing_keys]
        reason_change = bool(reason.strip()) and existing.reason != reason.strip()
        permission = self._check_mutation_permission(node_path, existing.added_by, actor)
        if not permission.ok and (new_refs or reason_change):
            return self.runtime.foundation.fail(permission.issues)
        warnings: list[ServiceIssue] = []
        changed = False
        if new_refs:
            existing.expected_decl_refs.extend(new_refs)
            changed = True
        if reason_change:
            existing.reason = reason.strip()
            changed = True
        if not changed:
            warnings.append(
                self.runtime.foundation.issue(
                    "node_dep_duplicate",
                    f"Node dependency already exists: {existing.target.node}",
                    severity=IssueSeverity.WARNING,
                    object_ref=node_path,
                    field="deps",
                )
            )
            refreshed = self._refresh_prelude(repo_root, node_path=node_path)
            if not refreshed.ok:
                return self.runtime.foundation.fail(refreshed.issues)
            return self._dependency_mutation_receipt(
                node_path=node_path,
                operation="add",
                projection=refreshed.value,
                warnings=[*warnings, *refreshed.issues],
            )
        setattr(contract, "deps", list(deps))
        persisted = self._save_and_refresh_prelude(repo_root, node_path, contract)
        if not persisted.ok:
            return self.runtime.foundation.fail(persisted.issues)
        return self._dependency_mutation_receipt(
            node_path=node_path,
            operation="add",
            updated=[existing],
            projection=persisted.value,
            warnings=persisted.issues,
        )

    def _resolve_expected_decl_names(
        self,
        boundary: VisibleNodeBoundaryItem,
        expected_decl_names: list[str],
    ) -> ServiceResult[list[DeclRef]]:
        refs_by_name = {ref.name: ref for ref in boundary.exported_decl_refs}
        resolved: list[DeclRef] = []
        issues: list[ServiceIssue] = []
        for name in expected_decl_names:
            normalized = name.strip() if name else ""
            if not normalized:
                issues.append(self.runtime.foundation.issue("node_dep_expected_decl_name_empty", "Expected declaration name is empty.", field="expected_decl_names"))
                continue
            ref = refs_by_name.get(normalized)
            if ref is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "node_dep_expected_decl_missing",
                        f"Expected declaration is not public on target boundary: {normalized}",
                        object_ref=boundary.node_path,
                        field="expected_decl_names",
                    )
                )
                continue
            resolved.append(ref)
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(resolved)

    def _normalize_deps(self, values: list[NodeDep]) -> ServiceResult[list[NodeDep]]:
        adapter = TypeAdapter(NodeDep)
        normalized: list[NodeDep] = []
        issues: list[ServiceIssue] = []
        for index, value in enumerate(values):
            try:
                item = adapter.validate_python(value)
            except Exception as exc:  # noqa: BLE001 - validation details are returned to caller.
                issues.append(self.runtime.foundation.issue("node_dep_invalid", f"Node dependency entry is invalid: {exc}", field=f"deps.{index}"))
                continue
            target = self._normalize_node_ref(item.target, field=f"deps.{index}.target")
            if not target.ok or target.value is None:
                issues.extend(target.issues)
                continue
            item = item.model_copy(update={"target": target.value, "dep_id": item.dep_id or self._stable_dep_id(target.value)})
            normalized.append(item)
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(normalized)

    def _normalize_node_ref(self, ref: NodeRef, *, field: str) -> ServiceResult[NodeRef]:
        repo = ref.repo.strip() if isinstance(ref.repo, str) and ref.repo.strip() else None
        node = self._normalize_node_path(ref.node, field=f"{field}.node")
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        return self.runtime.foundation.ok(NodeRef(repo=repo, node=node.value))

    def _normalize_node_path(self, value: str, *, field: str) -> ServiceResult[str]:
        normalized = value.strip() if isinstance(value, str) else ""
        if not normalized:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_dep_target_required", "Node dependency target is required.", field=field))
        parts = normalized.split(".")
        if parts[0] != "Main" or any(not part for part in parts):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_dep_target_invalid", "Node dependency target must be a dot path rooted at Main.", field=field, current=value)
            )
        return self.runtime.foundation.ok(normalized)

    def _dep_view(self, index: int, dep: NodeDep) -> NodeDepView:
        target_label = f"{dep.target.repo}:{dep.target.node}" if dep.target.repo else dep.target.node
        expected_names = [ref.name for ref in dep.expected_decl_refs]
        return NodeDepView(
            index=index,
            target_repo=dep.target.repo,
            target_node=dep.target.node,
            expected_decl_refs=dep.expected_decl_refs,
            reason=dep.reason,
            added_by=dep.added_by,
            summary=f"{index}: {target_label}"
            + (f" expecting {', '.join(expected_names)}" if expected_names else "")
            + f" ({dep.added_by.value}).",
        )

    def _public_decl_refs(self, contract: object) -> list[DeclRef]:
        refs: list[DeclRef] = []
        seen: set[tuple[str | None, str, str, int]] = set()
        for ref in getattr(contract, "exports", []) or []:
            if isinstance(ref, DeclRef):
                key = self._decl_ref_key(ref)
                if key not in seen:
                    refs.append(ref)
                    seen.add(key)
        for interface in getattr(contract, "interfaces", []) or []:
            ref = getattr(interface, "bound_decl", None)
            if isinstance(ref, DeclRef):
                key = self._decl_ref_key(ref)
                if key not in seen:
                    refs.append(ref)
                    seen.add(key)
        return refs

    def _merge_decl_refs(self, *groups: list[DeclRef]) -> list[DeclRef]:
        merged: dict[tuple[str | None, str, str, int], DeclRef] = {}
        for group in groups:
            for ref in group:
                merged[self._decl_ref_key(ref)] = ref
        return [
            merged[key]
            for key in sorted(merged, key=lambda item: (item[0] or "", item[1], item[2], item[3]))
        ]

    def _external_lake_boundaries(self, repo_root: Path) -> ServiceResult[list[VisibleNodeBoundaryItem]]:
        repo_workspace = self.runtime.app.repo_workspace
        if repo_workspace is None:
            return self.runtime.foundation.ok([])
        deps = repo_workspace.workspace_catalog.list_current_lake_dependency_repos(repo_root)
        if not deps.ok or deps.value is None:
            return self.runtime.foundation.fail(deps.issues)
        boundaries: list[VisibleNodeBoundaryItem] = []
        for dep in deps.value:
            if dep.path is None:
                continue
            provider_root = (Path(repo_root) / dep.path).resolve(strict=False)
            expected_root = (Path(repo_root).parent / dep.name).resolve(strict=False)
            if provider_root != expected_root or not (provider_root / ".lean_constellation").is_dir():
                continue
            availability = self.runtime.repo_workspace.provider_availability.check_provider_available(provider_root)
            if not availability.ok or availability.value is None:
                return self.runtime.foundation.fail(availability.issues)
            if not availability.value.passed:
                continue
            config = self.runtime.repo_workspace.metadata.get_repo_config(provider_root)
            if not config.ok or config.value is None:
                return self.runtime.foundation.fail(config.issues)
            public_refs = self.runtime.decl_graph.ref_compatibility.list_public_decl_refs(
                provider_root,
                required_availability=proof_availability_for_completion_mode(
                    config.value.config.completion_mode
                ),
            )
            if not public_refs.ok or public_refs.value is None:
                return self.runtime.foundation.fail(public_refs.issues)
            exported_refs = [
                DeclRef(
                    repo=dep.name,
                    node=item.anchor.node,
                    name=item.anchor.name,
                    revision=item.anchor.revision,
                )
                for item in public_refs.value
                if item.compatible
            ]
            boundaries.append(
                VisibleNodeBoundaryItem(
                    repo=dep.name,
                    node_path="Main",
                    node_kind=NodeKind.SCOPE.value,
                    ready=True,
                    import_module=f"{dep.name}.Main.Interfaces",
                    exported_decl_refs=exported_refs,
                    interface_names=[ref.name for ref in exported_refs],
                    summary=f"Attached stable workspace dependency boundary {dep.name}:Main.",
                )
            )
        return self.runtime.foundation.ok(boundaries)

    def _refresh_prelude(self, repo_root: Path, *, node_path: str) -> ServiceResult[object]:
        node_projection = self.node_projection
        if node_projection is None:
            lean_projection = self.runtime.app.lean_projection
            if lean_projection is None:
                return self.runtime.foundation.ok(None)
            node_projection = lean_projection.node_projection
        return node_projection.refresh_prelude(repo_root, node_path=node_path)

    def _save_and_refresh_prelude(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[object]:
        return persist_contract_with_projection(
            self.runtime,
            repo_root=repo_root,
            node_path=node_path,
            candidate=contract,
            projection_kind="prelude",
            save=self._save_contract,
            refresh=lambda: self._refresh_prelude(repo_root, node_path=node_path),
        )

    def _dependency_mutation_receipt(
        self,
        *,
        node_path: str,
        operation: Literal["add", "remove"],
        added: list[NodeDep] | None = None,
        updated: list[NodeDep] | None = None,
        removed: list[NodeDep] | None = None,
        projection: object | None,
        warnings: list[ServiceIssue] | None = None,
    ) -> ServiceResult[NodeDependencyMutationReceipt]:
        path = getattr(projection, "path", None)
        projection_changed = bool(getattr(projection, "changed", False))
        added_items = list(added or [])
        updated_items = list(updated or [])
        removed_items = list(removed or [])
        return self.runtime.foundation.ok(
            NodeDependencyMutationReceipt(
                node_path=node_path,
                operation=operation,
                changed=bool(added_items or updated_items or removed_items),
                added=added_items,
                updated=updated_items,
                removed=removed_items,
                managed_projection_changed=projection_changed,
                changed_files=[path] if projection_changed and path else [],
                reread_required=projection_changed,
                summary=f"{operation.capitalize()} node dependency.",
            ),
            warnings=warnings or [],
        )

    def _has_local_dep_path(self, repo_root: Path, *, start: str, target: str) -> bool:
        graph = self._local_dep_graph(repo_root)
        stack = list(graph.get(start, set()))
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(graph.get(node, set()) - seen)
        return False

    def _local_dep_graph(self, repo_root: Path) -> dict[str, set[str]]:
        tree = self.node_tree.get_node_tree(repo_root)
        if not tree.ok or tree.value is None:
            return {}
        graph: dict[str, set[str]] = {}
        for node in tree.value.nodes:
            current = self.contract.get_current_contract(repo_root, node_path=node.path)
            if not current.ok or current.value is None:
                continue
            deps = self._normalize_deps(current.value.contract.deps)
            if not deps.ok or deps.value is None:
                continue
            graph[node.path] = {dep.target.node for dep in deps.value if dep.target.repo is None}
        return graph

    def _check_remove_permission(self, node_path: str, target_actor: NodeDepActor, actor: NodeDepActor) -> ServiceResult[None]:
        return self._check_mutation_permission(node_path, target_actor, actor)

    def _check_mutation_permission(self, node_path: str, target_actor: NodeDepActor, actor: NodeDepActor) -> ServiceResult[None]:
        allowed = (
            target_actor == NodeDepActor.WORKER
            if actor == NodeDepActor.WORKER
            else target_actor != NodeDepActor.OPERATOR
            if actor == NodeDepActor.COORDINATOR
            else target_actor == NodeDepActor.OPERATOR
        )
        if not allowed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "node_dep_permission_denied",
                    "The caller cannot modify a node dependency owned by another authority.",
                    object_ref=node_path,
                    current=target_actor.value,
                    expected=actor.value,
                )
            )
        return self.runtime.foundation.ok(None)

    def _normalize_actor(self, actor: str | NodeDepActor) -> ServiceResult[NodeDepActor]:
        try:
            return self.runtime.foundation.ok(NodeDepActor(actor))
        except ValueError:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_dep_actor_invalid", "actor must be coordinator, worker, or operator.", field="actor"))

    def _save_contract(self, repo_root: Path, node_path: str, contract: object) -> ServiceResult[object]:
        return self.contract._persist_open_candidate(repo_root, node_path=node_path, candidate=contract)

    def _stable_dep_id(self, target: NodeRef) -> str:
        payload = json.dumps(target.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        return f"dep_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"

    def _stable_dep_id_from_any(self, target: Any) -> str:
        try:
            ref = TypeAdapter(NodeRef).validate_python(target)
        except Exception:  # noqa: BLE001 - invalid target is reported by normal validation later.
            return ""
        return self._stable_dep_id(ref)

    def _same_target(self, left: NodeRef, right: NodeRef) -> bool:
        return (left.repo, left.node) == (right.repo, right.node)

    def _target_key(self, target: NodeRef) -> str:
        return f"{target.repo or ''}:{target.node}"

    def _decl_ref_key(self, ref: DeclRef) -> tuple[str | None, str, str, int]:
        return (ref.repo, ref.node, ref.name, ref.revision)
