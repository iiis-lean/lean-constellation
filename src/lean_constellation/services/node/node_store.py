"""Node identity store and active path index."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation import FoundationContext, ServiceResult, WriteMode

if TYPE_CHECKING:
    from lean_constellation.services.node.node_tree import NodeKind, NodeLifecycle, NodeMetadata
    from lean_constellation.services.runtime import LeanRuntimeServices


class NodeIndexEntry(StrictModel):
    node_id: str
    path: str
    kind: str
    lifecycle: str
    active: bool
    active_contract_version: int | None = None
    open_contract_version: int | None = None


class NodeIndex(StrictModel):
    schema_version: int = 1
    entries: list[NodeIndexEntry] = Field(default_factory=list)
    active_path_to_node_id: dict[str, str] = Field(default_factory=dict)
    rebuilt_at: str = Field(default_factory=utc_now_iso)
    summary: str


class NodeStorageMigrationView(StrictModel):
    migrated_nodes: list[str] = Field(default_factory=list)
    unchanged_nodes: list[str] = Field(default_factory=list)
    summary: str


class NodeStore:
    """Read and write node truth by stable node_id while resolving public paths through an index."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def allocate_node_id(self, repo_root: Path) -> ServiceResult[str]:
        nodes = self.list_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        existing = {node.node_id for node in nodes.value}
        return self.runtime.foundation.store.allocate_uuid(lambda candidate: candidate in existing, prefix="node")

    def read_index(self, repo_root: Path) -> ServiceResult[NodeIndex]:
        ctx = FoundationContext(repo_root=Path(repo_root))
        path = self.runtime.foundation.layout.node_index_path(ctx)
        loaded = self.runtime.foundation.store.read_json(path, NodeIndex)
        if loaded.ok and loaded.value is not None:
            return loaded
        return self.rebuild_index(repo_root)

    def rebuild_index(self, repo_root: Path) -> ServiceResult[NodeIndex]:
        nodes = self._scan_nodes(repo_root)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        active_path_to_node_id: dict[str, str] = {}
        entries: list[NodeIndexEntry] = []
        for node in sorted(nodes.value, key=lambda item: (item.path, item.node_id)):
            active = node.lifecycle.value == "active"
            if active:
                existing = active_path_to_node_id.get(node.path)
                if existing is not None and existing != node.node_id:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "node_index_active_path_conflict",
                            f"Multiple active nodes use path {node.path}.",
                            object_ref=node.path,
                            details={"node_ids": f"{existing},{node.node_id}"},
                        )
                    )
                active_path_to_node_id[node.path] = node.node_id
            entries.append(
                NodeIndexEntry(
                    node_id=node.node_id,
                    path=node.path,
                    kind=node.kind.value,
                    lifecycle=node.lifecycle.value,
                    active=active,
                    active_contract_version=getattr(node, "active_contract_version", None)
                    or getattr(node, "current_contract_version", None),
                    open_contract_version=getattr(node, "open_contract_version", None),
                )
            )
        index = NodeIndex(
            entries=entries,
            active_path_to_node_id=active_path_to_node_id,
            summary=f"Indexed {len(entries)} nodes; {len(active_path_to_node_id)} active paths.",
        )
        ctx = FoundationContext(repo_root=Path(repo_root))
        written = self.runtime.foundation.store.write_json_atomic(
            self.runtime.foundation.layout.node_index_path(ctx),
            index,
            mode=WriteMode.OVERWRITE,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(index)

    def resolve_active_node(self, repo_root: Path, *, path: str) -> ServiceResult[NodeMetadata]:
        index = self.read_index(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        node_id = index.value.active_path_to_node_id.get(path)
        if node_id is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("node_missing", f"Active node does not exist: {path}", object_ref=path)
            )
        return self.load_node_by_id(repo_root, node_id=node_id)

    def load_node_by_id(self, repo_root: Path, *, node_id: str) -> ServiceResult[NodeMetadata]:
        from lean_constellation.services.node.node_tree import NodeMetadata

        ctx = FoundationContext(repo_root=Path(repo_root))
        canonical_path = self.runtime.foundation.layout.node_metadata_path_by_id(ctx, node_id)
        loaded = self.runtime.foundation.store.read_json(canonical_path, NodeMetadata)
        if loaded.ok and loaded.value is not None:
            return loaded
        nodes_root = self.runtime.foundation.layout.nodes_root(ctx)
        for node_json in sorted(nodes_root.glob("*/node.json")):
            candidate = self.runtime.foundation.store.read_json(node_json, NodeMetadata)
            if candidate.ok and candidate.value is not None and candidate.value.node_id == node_id:
                return candidate
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue("node_missing", f"Node id does not exist: {node_id}", object_ref=node_id)
        )

    def list_nodes(self, repo_root: Path) -> ServiceResult[list[NodeMetadata]]:
        return self._scan_nodes(repo_root)

    def save_node(self, repo_root: Path, node: NodeMetadata, *, mode: WriteMode = WriteMode.UPDATE_EXISTING) -> ServiceResult[NodeMetadata]:
        ctx = FoundationContext(repo_root=Path(repo_root))
        saved = self.runtime.foundation.store.write_json_atomic(
            self.runtime.foundation.layout.node_metadata_path_by_id(ctx, node.node_id),
            node,
            mode=mode,
        )
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        rebuilt = self.rebuild_index(repo_root)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(node)

    def node_dir(self, repo_root: Path, *, node_id: str) -> Path:
        return self.runtime.foundation.layout.node_dir_by_id(FoundationContext(repo_root=Path(repo_root)), node_id)

    def node_file(self, repo_root: Path, *, node_id: str) -> Path:
        return self.runtime.foundation.layout.node_metadata_path_by_id(FoundationContext(repo_root=Path(repo_root)), node_id)

    def contract_path(self, repo_root: Path, *, node_id: str, version: int) -> Path:
        return self.runtime.foundation.layout.node_contract_path_by_id(FoundationContext(repo_root=Path(repo_root)), node_id, version)

    def decl_graph_dir(self, repo_root: Path, *, node_id: str) -> Path:
        return self.runtime.foundation.layout.node_decl_graph_dir_by_id(FoundationContext(repo_root=Path(repo_root)), node_id)

    def migrate_path_layout_to_node_id_layout(self, repo_root: Path) -> ServiceResult[NodeStorageMigrationView]:
        """Move legacy nodes/<encoded_path> directories to nodes/<node_id> directories."""

        from lean_constellation.services.node.node_tree import NodeMetadata

        ctx = FoundationContext(repo_root=Path(repo_root))
        nodes_root = self.runtime.foundation.layout.nodes_root(ctx)
        if not nodes_root.exists():
            rebuilt = self.rebuild_index(repo_root)
            if not rebuilt.ok:
                return self.runtime.foundation.fail(rebuilt.issues)
            return self.runtime.foundation.ok(
                NodeStorageMigrationView(
                    summary="Node storage migration found no node directory.",
                )
            )

        migrated: list[str] = []
        unchanged: list[str] = []
        for node_json in sorted(nodes_root.glob("*/node.json")):
            loaded = self.runtime.foundation.store.read_json(node_json, NodeMetadata)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            node = loaded.value
            source_dir = node_json.parent
            target_dir = self.node_dir(repo_root, node_id=node.node_id)
            if source_dir == target_dir:
                unchanged.append(node.node_id)
                continue
            if target_dir.exists():
                existing = self.runtime.foundation.store.read_json(target_dir / "node.json", NodeMetadata)
                if existing.ok and existing.value is not None and existing.value.node_id == node.node_id:
                    shutil.rmtree(source_dir)
                    unchanged.append(node.node_id)
                    continue
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "node_storage_migration_target_conflict",
                        "Cannot migrate legacy node path layout because the node_id target directory already exists.",
                        object_ref=node.node_id,
                        details={"source_dir": str(source_dir), "target_dir": str(target_dir)},
                    )
                )
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_dir), str(target_dir))
            migrated.append(node.node_id)

        rebuilt = self.rebuild_index(repo_root)
        if not rebuilt.ok:
            return self.runtime.foundation.fail(rebuilt.issues)
        return self.runtime.foundation.ok(
            NodeStorageMigrationView(
                migrated_nodes=migrated,
                unchanged_nodes=unchanged,
                summary=f"Migrated {len(migrated)} legacy node directories; {len(unchanged)} already used node_id layout.",
            )
        )

    def _scan_nodes(self, repo_root: Path) -> ServiceResult[list[NodeMetadata]]:
        from lean_constellation.services.node.node_tree import NodeMetadata

        ctx = FoundationContext(repo_root=Path(repo_root))
        root = self.runtime.foundation.layout.nodes_root(ctx)
        if not root.exists():
            return self.runtime.foundation.ok([])
        nodes: list[NodeMetadata] = []
        issues = []
        for node_json in sorted(root.glob("*/node.json")):
            result = self.runtime.foundation.store.read_json(node_json, NodeMetadata)
            if result.ok and result.value is not None:
                nodes.append(result.value)
            else:
                issues.extend(result.issues)
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(nodes)
