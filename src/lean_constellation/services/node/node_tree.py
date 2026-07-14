"""Scope / Content node tree metadata management."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import (
    FoundationContext,
    MutationSummaryView,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.node.contract_fields import (
    ContractMaterialRef,
    NodeDep,
    NodeMathlibDeclUse,
    NodeMathlibModuleUse,
)
from lean_constellation.services.node.node_store import NodeStore

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class NodeKind(StrEnum):
    SCOPE = "scope"
    CONTENT = "content"


class NodeLifecycle(StrEnum):
    ACTIVE = "active"
    OBSOLETE = "obsolete"


class NodeContractStatus(StrEnum):
    OPEN = "open"
    COMMITTED = "committed"


class NodeMetadata(StrictModel):
    node_id: str
    path: str
    kind: NodeKind
    lifecycle: NodeLifecycle = NodeLifecycle.ACTIVE
    current_contract_version: int | None = None
    active_contract_version: int | None = None
    open_contract_version: int | None = None


class NodeContract(StrictModel):
    contract_kind: NodeKind
    version: int = 1
    status: NodeContractStatus = NodeContractStatus.OPEN
    goal: str
    boundary: str
    objective: str | None = None
    summary: str | None = None
    success_criteria: str | None = None
    constraints: str | None = None
    owned_refs: list[ContractMaterialRef] = Field(default_factory=list)
    context_refs: list[ContractMaterialRef] = Field(default_factory=list)
    interfaces: list[DeclInterface] = Field(default_factory=list)
    deps: list[NodeDep] = Field(default_factory=list)
    mathlib_modules: list[NodeMathlibModuleUse] = Field(default_factory=list)
    mathlib_decls: list[NodeMathlibDeclUse] = Field(default_factory=list)
    exports: list[DeclRef] = Field(default_factory=list)
    decl_graph_head: dict[str, int] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    committed_at: str | None = None

    @field_validator("goal", "boundary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field must be non-empty")
        return value.strip()

    @field_validator("exports", mode="before")
    @classmethod
    def _default_missing_exports(cls, value: object) -> object:
        if value is None:
            return []
        return value


NodeContractSnapshot = NodeContract


class NodeView(StrictModel):
    path: str
    node_id: str
    kind: NodeKind
    lifecycle: NodeLifecycle
    current_contract_version: int | None = None
    active_contract_version: int | None = None
    open_contract_version: int | None = None
    contract_status: str | None = None
    parent_path: str | None = None
    child_count: int = 0
    summary: str


class NodeTreeView(StrictModel):
    root_path: str | None = None
    nodes: list[NodeView] = Field(default_factory=list)
    active_count: int = 0
    summary: str


class DeleteImpactView(StrictModel):
    path: str
    impact_identity: str
    deletable: bool
    affected_children: list[str] = Field(default_factory=list)
    inbound_refs: list[str] = Field(default_factory=list)
    blocking_reasons: list[str] = Field(default_factory=list)
    public_decl_count: int = 0
    summary: str

    @classmethod
    def build(
        cls,
        *,
        path: str,
        deletable: bool,
        affected_children: list[str],
        inbound_refs: list[str],
        blocking_reasons: list[str],
        public_decl_count: int,
        summary: str,
    ) -> "DeleteImpactView":
        payload = {
            "path": path,
            "deletable": deletable,
            "affected_children": sorted(affected_children),
            "inbound_refs": sorted(inbound_refs),
            "blocking_reasons": sorted(blocking_reasons),
            "public_decl_count": public_decl_count,
        }
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(impact_identity=f"node_delete_{digest}", summary=summary, **payload)


class RunnableContentNodeView(StrictModel):
    candidates: list[NodeView] = Field(default_factory=list)
    max_count: int
    truncated: bool = False
    skipped: list[str] = Field(default_factory=list)
    summary: str


class NodeTreeComponent:
    """Create, inspect, soft-delete, and list runnable node metadata."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime
        self.node_store = NodeStore(runtime)

    def create_scope_node(
        self,
        repo_root: Path,
        *,
        path: str,
        goal: str,
        boundary: str,
        objective: str | None = None,
        constraints: str | None = None,
        success_criteria: str | None = None,
    ) -> ServiceResult[NodeView]:
        return self._create_node(
            repo_root,
            path=path,
            kind=NodeKind.SCOPE,
            goal=goal,
            boundary=boundary,
            objective=objective,
            constraints=constraints,
            success_criteria=success_criteria,
        )

    def create_content_node(
        self,
        repo_root: Path,
        *,
        path: str,
        goal: str,
        boundary: str,
        objective: str,
        success_criteria: str,
        constraints: str | None = None,
    ) -> ServiceResult[NodeView]:
        if not objective or not objective.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_objective_required", "Content node objective is required.", field="objective"))
        if not success_criteria or not success_criteria.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_success_criteria_required", "Content node success_criteria is required.", field="success_criteria")
            )
        return self._create_node(
            repo_root,
            path=path,
            kind=NodeKind.CONTENT,
            goal=goal,
            boundary=boundary,
            objective=objective,
            constraints=constraints,
            success_criteria=success_criteria,
        )

    def ensure_root_scope_node(self, repo_root: Path, *, path: str = "Main") -> ServiceResult[NodeView]:
        valid = self._validate_dot_path(path)
        if not valid.ok:
            return self.runtime.foundation.fail(valid.issues)
        if path != "Main":
            return self.runtime.foundation.fail(self.runtime.foundation.issue("root_scope_path_invalid", "Root scope path must be Main.", current=path, expected="Main"))
        existing = self._load_node(repo_root, path)
        if existing.ok and existing.value is not None:
            node = existing.value
            if node.kind != NodeKind.SCOPE:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("root_node_wrong_kind", "Existing Main node is not a Scope node.", current=node.kind.value, expected="scope")
                )
            if node.lifecycle != NodeLifecycle.ACTIVE:
                return self.runtime.foundation.fail(self.runtime.foundation.issue("root_node_deleted", "Existing Main node is obsolete.", object_ref=path))
            return self.get_node(repo_root, path=path)
        return self.create_scope_node(
            repo_root,
            path=path,
            goal="Organize and complete the full repository formalization goal.",
            boundary="Covers the entire repository Main tree, its source corpus, formalized content, and repo-level public interface.",
            objective="Prepare and maintain the root scope for the repository coordinator.",
            success_criteria="All required repository interfaces are bound and the Main scope can be committed as the repo public boundary.",
        )

    def get_node(self, repo_root: Path, *, path: str) -> ServiceResult[NodeView]:
        node = self._load_node(repo_root, path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        nodes = self._load_all_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        return self.runtime.foundation.ok(self._node_view(repo_root, node.value, nodes.value))

    def get_node_tree(self, repo_root: Path) -> ServiceResult[NodeTreeView]:
        nodes = self._load_all_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        active = [node for node in nodes.value if node.lifecycle == NodeLifecycle.ACTIVE]
        active.sort(key=lambda item: (item.path.count("."), item.path))
        views = [self._node_view(repo_root, node, active) for node in active]
        root_path = "Main" if any(node.path == "Main" for node in active) else None
        return self.runtime.foundation.ok(
            NodeTreeView(
                root_path=root_path,
                nodes=views,
                active_count=len(views),
                summary=f"Loaded {len(views)} active nodes.",
            )
        )

    def list_children(self, repo_root: Path, *, scope_path: str) -> ServiceResult[list[NodeView]]:
        scope = self._load_active_node(repo_root, scope_path)
        if not scope.ok or scope.value is None:
            return self.runtime.foundation.fail(scope.issues)
        if scope.value.kind != NodeKind.SCOPE:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_not_scope", "Only Scope nodes can have children.", object_ref=scope_path))
        nodes = self._load_all_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        children = [
            node
            for node in nodes.value
            if node.lifecycle == NodeLifecycle.ACTIVE and self._parent_path(node.path) == scope_path
        ]
        children.sort(key=lambda item: item.path)
        return self.runtime.foundation.ok([self._node_view(repo_root, node, nodes.value) for node in children])

    def _mark_node_deleted_after_guard(self, repo_root: Path, *, path: str, reason: str) -> ServiceResult[MutationSummaryView]:
        """System-only soft-delete primitive after an aggregate guard passes."""
        node = self._load_active_node(repo_root, path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        node.value.lifecycle = NodeLifecycle.OBSOLETE
        saved = self.node_store.save_node(repo_root, node.value, mode=WriteMode.UPDATE_EXISTING)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=path,
                changed=True,
                summary=f"Marked node obsolete: {reason.strip()}",
                changed_items=["lifecycle"],
            )
        )

    def list_runnable_content_candidates(self, repo_root: Path, *, max_count: int) -> ServiceResult[RunnableContentNodeView]:
        if max_count < 1:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("max_count_invalid", "max_count must be >= 1.", field="max_count"))
        nodes = self._load_all_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        active = sorted(
            [node for node in nodes.value if node.lifecycle == NodeLifecycle.ACTIVE],
            key=lambda item: (item.path.count("."), item.path),
        )
        active_paths = {node.path for node in active}
        candidates: list[NodeView] = []
        skipped: list[str] = []
        for node in active:
            if node.kind != NodeKind.CONTENT:
                continue
            contract = self._load_current_contract(repo_root, node)
            if not contract.ok or contract.value is None:
                skipped.append(f"{node.path}: missing_contract")
                continue
            reason = self._content_admission_issue(contract.value, active_paths)
            if reason:
                skipped.append(f"{node.path}: {reason}")
                continue
            candidates.append(self._node_view(repo_root, node, active))
        truncated = len(candidates) > max_count
        shown = candidates[:max_count]
        return self.runtime.foundation.ok(
            RunnableContentNodeView(
                candidates=shown,
                max_count=max_count,
                truncated=truncated,
                skipped=skipped,
                summary=f"Found {len(candidates)} runnable content node candidates.",
            )
        )

    def _create_node(
        self,
        repo_root: Path,
        *,
        path: str,
        kind: NodeKind,
        goal: str,
        boundary: str,
        objective: str | None,
        constraints: str | None,
        success_criteria: str | None,
    ) -> ServiceResult[NodeView]:
        valid = self._validate_dot_path(path)
        if not valid.ok:
            return self.runtime.foundation.fail(valid.issues)
        if not goal or not goal.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_goal_required", "Node goal is required.", field="goal"))
        if not boundary or not boundary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_boundary_required", "Node boundary is required.", field="boundary"))
        existing = self._load_node(repo_root, path)
        if existing.ok and existing.value is not None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_already_exists", f"Node path already has active metadata: {path}", object_ref=path))
        if path != "Main":
            parent_path = self._parent_path(path)
            if parent_path is None:
                return self.runtime.foundation.fail(self.runtime.foundation.issue("node_parent_missing", "Non-root node must have a parent Scope.", object_ref=path))
            parent = self._load_active_node(repo_root, parent_path)
            if not parent.ok or parent.value is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("node_parent_missing", f"Parent Scope node does not exist: {parent_path}", object_ref=path)
                )
            if parent.value.kind != NodeKind.SCOPE:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("node_parent_not_scope", "Parent node must be a Scope node.", object_ref=parent_path, current=parent.value.kind.value)
                )
        if kind == NodeKind.CONTENT:
            descendants = self._active_descendant_paths(repo_root, path)
            if descendants:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "content_node_has_children",
                        "Content node path cannot have active descendants.",
                        object_ref=path,
                        current=", ".join(descendants),
                    )
                )
        node_ids = self._all_node_ids(repo_root)
        allocated = self.runtime.foundation.store.allocate_uuid(lambda candidate: candidate in node_ids, prefix="node")
        if not allocated.ok or allocated.value is None:
            return self.runtime.foundation.fail(allocated.issues)
        node = NodeMetadata(
            node_id=allocated.value,
            path=path,
            kind=kind,
            lifecycle=NodeLifecycle.ACTIVE,
            current_contract_version=1,
            active_contract_version=None,
            open_contract_version=1,
        )
        contract = NodeContract(
            contract_kind=kind,
            version=1,
            status=NodeContractStatus.OPEN,
            goal=goal,
            boundary=boundary,
            objective=objective.strip() if objective else None,
            success_criteria=success_criteria.strip() if success_criteria else None,
            constraints=constraints.strip() if constraints else None,
        )
        projection_dir = self.runtime.foundation.layout.node_projection_dir(FoundationContext(repo_root=Path(repo_root)), path)
        ensured = self.runtime.foundation.store.ensure_dir(projection_dir)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        with self.runtime.foundation.store.mutation("create_node") as mutation:
            mutation.stage_json(self._node_file_by_id(repo_root, node.node_id), node, mode=WriteMode.CREATE_ONLY)
            mutation.stage_json(self._contract_file_by_id(repo_root, node.node_id, 1), contract, mode=WriteMode.CREATE_ONLY)
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        rebuilt = self.node_store.rebuild_index(repo_root)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        nodes = self._load_all_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        return self.runtime.foundation.ok(self._node_view(repo_root, node, nodes.value))

    def _content_admission_issue(self, contract: NodeContract, active_paths: set[str]) -> str | None:
        if contract.status != NodeContractStatus.OPEN:
            return "contract_not_open"
        for field_name in ["goal", "boundary", "objective", "success_criteria"]:
            value = getattr(contract, field_name)
            if not isinstance(value, str) or not value.strip():
                return f"{field_name}_missing"
        for dep in contract.deps:
            node_path = dep.target.node
            repo = dep.target.repo
            if repo is None and node_path and node_path not in active_paths:
                return f"dep_missing:{node_path}"
        return None

    def _node_view(self, repo_root: Path, node: NodeMetadata, nodes: list[NodeMetadata]) -> NodeView:
        contract = self._load_current_contract(repo_root, node)
        status = contract.value.status if contract.ok and contract.value is not None else None
        child_count = sum(1 for item in nodes if item.lifecycle == NodeLifecycle.ACTIVE and self._parent_path(item.path) == node.path)
        return NodeView(
            path=node.path,
            node_id=node.node_id,
            kind=node.kind,
            lifecycle=node.lifecycle,
            current_contract_version=node.current_contract_version,
            active_contract_version=node.active_contract_version,
            open_contract_version=node.open_contract_version,
            contract_status=status,
            parent_path=self._parent_path(node.path),
            child_count=child_count,
            summary=f"{node.kind.value} node {node.path} ({node.lifecycle.value})",
        )

    def _load_node(self, repo_root: Path, path: str) -> ServiceResult[NodeMetadata]:
        return self.node_store.resolve_active_node(repo_root, path=path)

    def _load_active_node(self, repo_root: Path, path: str) -> ServiceResult[NodeMetadata]:
        node = self._load_node(repo_root, path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.lifecycle != NodeLifecycle.ACTIVE:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_not_active", "Node is not active.", object_ref=path))
        return node

    def _load_all_nodes(self, repo_root: Path) -> ServiceResult[list[NodeMetadata]]:
        return self.node_store.list_nodes(repo_root)

    def _load_current_contract(self, repo_root: Path, node: NodeMetadata) -> ServiceResult[NodeContract]:
        if node.current_contract_version is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_contract_missing", "Node has no current contract version.", object_ref=node.path))
        return self.runtime.foundation.store.read_json(self._contract_file_for_node(repo_root, node, node.current_contract_version), NodeContract)

    def _node_file(self, repo_root: Path, path: str) -> Path:
        node = self.node_store.resolve_active_node(repo_root, path=path)
        if not node.ok or node.value is None:
            raise ValueError(f"Cannot resolve active node path: {path}")
        return self._node_file_by_id(repo_root, node.value.node_id)

    def _node_file_by_id(self, repo_root: Path, node_id: str) -> Path:
        return self.node_store.node_file(repo_root, node_id=node_id)

    def _contract_file(self, repo_root: Path, path: str, version: int) -> Path:
        node = self.node_store.resolve_active_node(repo_root, path=path)
        if not node.ok or node.value is None:
            raise ValueError(f"Cannot resolve active node path: {path}")
        return self._contract_file_by_id(repo_root, node.value.node_id, version)

    def _contract_file_by_id(self, repo_root: Path, node_id: str, version: int) -> Path:
        return self.node_store.contract_path(repo_root, node_id=node_id, version=version)

    def _contract_file_for_node(self, repo_root: Path, node: NodeMetadata, version: int) -> Path:
        return self._contract_file_by_id(repo_root, node.node_id, version)

    def _all_node_ids(self, repo_root: Path) -> set[str]:
        nodes = self._load_all_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return set()
        return {node.node_id for node in nodes.value}

    def _active_descendant_paths(self, repo_root: Path, path: str) -> list[str]:
        nodes = self._load_all_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return []
        return sorted(node.path for node in nodes.value if node.lifecycle == NodeLifecycle.ACTIVE and self._is_descendant(node.path, path))

    def _validate_dot_path(self, path: str) -> ServiceResult[None]:
        if not path or not path.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_path_empty", "Node path must be non-empty.", field="path"))
        parts = [part.strip() for part in path.split(".")]
        if any(not part for part in parts):
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_path_invalid", f"Invalid node path: {path}", field="path"))
        if parts[0] != "Main":
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_path_root_invalid", "Node path must start with Main.", field="path", current=parts[0]))
        try:
            for part in parts:
                self.runtime.foundation.layout.ensure_safe_key(part)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("node_path_invalid", str(exc), field="path"))
        return self.runtime.foundation.ok(None)

    def _parent_path(self, path: str) -> str | None:
        parts = path.split(".")
        if len(parts) <= 1:
            return None
        return ".".join(parts[:-1])

    def _is_descendant(self, candidate_path: str, ancestor_path: str) -> bool:
        return candidate_path.startswith(f"{ancestor_path}.")
