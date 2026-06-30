"""Decl graph store layout and basic index maintenance."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.common import utc_now_iso
from lean_constellation.services.decl_graph.models import DeclGraphIndex, DeclGraphStoreView
from lean_constellation.services.foundation import FoundationContext, ServiceResult, WriteMode
from lean_constellation.services.node import NodeKind

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class GraphStoreComponent:
    """Own the Content-node-local decl graph directory and index file."""

    INDEX_FILENAME = "index.json"

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def ensure_graph(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStoreView]:
        content = self._require_content_node(repo_root, node_path)
        if not content.ok:
            return self.runtime.foundation.fail(content.issues)

        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)

        for directory in (
            paths.graph_root,
            paths.strategies_dir,
            paths.rounds_dir,
            paths.decls_dir,
            paths.changes_dir,
            paths.reviews_dir,
        ):
            ensured = self.runtime.foundation.store.ensure_dir(directory)
            if not ensured.ok:
                return self.runtime.foundation.fail(ensured.issues)

        if not paths.index_path.exists():
            index = self._empty_index(node_path)
            written = self.runtime.foundation.store.write_json_atomic(
                paths.index_path,
                index,
                mode=WriteMode.CREATE_ONLY,
            )
            if not written.ok:
                return self.runtime.foundation.fail(written.issues)

        return self.get_store_view(repo_root, node_path=node_path)

    def get_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphIndex]:
        content = self._require_content_node(repo_root, node_path)
        if not content.ok:
            return self.runtime.foundation.fail(content.issues)
        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)
        return self.runtime.foundation.store.read_json(paths.index_path, DeclGraphIndex)

    def get_store_view(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphStoreView]:
        index = self.get_index(repo_root, node_path=node_path)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)
        return self.runtime.foundation.ok(
            DeclGraphStoreView(
                repo_root=str(Path(repo_root).expanduser().resolve(strict=False)),
                node_path=node_path,
                graph_root=str(paths.graph_root),
                index_path=str(paths.index_path),
                strategy_count=len(index.value.strategy_ids),
                round_count=len(index.value.round_ids),
                decl_count=len(index.value.decl_names),
                summary=(
                    f"Decl graph for {node_path}: "
                    f"{len(index.value.decl_names)} decls, "
                    f"{len(index.value.round_ids)} rounds, "
                    f"{len(index.value.strategy_ids)} strategies."
                ),
            )
        )

    def rebuild_index(self, repo_root: Path, *, node_path: str) -> ServiceResult[DeclGraphIndex]:
        ensured = self.ensure_graph(repo_root, node_path=node_path)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        paths = self._paths(repo_root, node_path)
        if paths is None:
            return self._invalid_node_path(node_path)

        strategy_ids = sorted(path.stem for path in paths.strategies_dir.glob("*.json") if path.is_file())
        round_ids = sorted(path.stem for path in paths.rounds_dir.glob("*.json") if path.is_file())
        decl_names = (
            sorted(path.name for path in paths.decls_dir.iterdir() if (path / "decl.json").is_file())
            if paths.decls_dir.exists()
            else []
        )
        index = DeclGraphIndex(
            node_path=node_path,
            strategy_ids=strategy_ids,
            round_ids=round_ids,
            decl_names=decl_names,
            updated_at=utc_now_iso(),
            summary=(
                f"DeclGraph index rebuilt for {node_path}: "
                f"{len(decl_names)} decls, {len(round_ids)} rounds, {len(strategy_ids)} strategies."
            ),
        )
        written = self.runtime.foundation.store.write_json_atomic(paths.index_path, index, mode=WriteMode.OVERWRITE)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(index)

    def graph_root(self, repo_root: Path, *, node_path: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.graph_root

    def index_path(self, repo_root: Path, *, node_path: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.index_path

    def strategy_path(self, repo_root: Path, *, node_path: str, strategy_id: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.strategies_dir / f"{self.runtime.foundation.layout.ensure_safe_key(strategy_id)}.json"

    def round_path(self, repo_root: Path, *, node_path: str, round_id: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.rounds_dir / f"{self.runtime.foundation.layout.ensure_safe_key(round_id)}.json"

    def decl_dir(self, repo_root: Path, *, node_path: str, decl_name: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.decls_dir / self.runtime.foundation.layout.ensure_safe_key(decl_name)

    def decl_record_path(self, repo_root: Path, *, node_path: str, decl_name: str) -> Path:
        return self.decl_dir(repo_root, node_path=node_path, decl_name=decl_name) / "decl.json"

    def decl_revisions_dir(self, repo_root: Path, *, node_path: str, decl_name: str) -> Path:
        return self.decl_dir(repo_root, node_path=node_path, decl_name=decl_name) / "revisions"

    def revision_path(self, repo_root: Path, *, node_path: str, decl_name: str, revision: int) -> Path:
        if revision < 1:
            raise ValueError("revision must be >= 1")
        return self.decl_revisions_dir(repo_root, node_path=node_path, decl_name=decl_name) / f"{revision}.json"

    def change_path(self, repo_root: Path, *, node_path: str, change_id: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return paths.changes_dir / f"{self.runtime.foundation.layout.ensure_safe_key(change_id)}.json"

    def stage_review_dir(self, repo_root: Path, *, node_path: str, round_id: str, stage: str) -> Path:
        paths = self._paths(repo_root, node_path)
        if paths is None:
            raise ValueError(f"Invalid node path: {node_path}")
        return (
            paths.reviews_dir
            / self.runtime.foundation.layout.ensure_safe_key(round_id)
            / self.runtime.foundation.layout.ensure_safe_key(stage)
        )

    def review_mark_path(self, repo_root: Path, *, node_path: str, round_id: str, stage: str, decl_name: str) -> Path:
        return self.stage_review_dir(repo_root, node_path=node_path, round_id=round_id, stage=stage) / (
            f"{self.runtime.foundation.layout.ensure_safe_key(decl_name)}.json"
        )

    def _require_content_node(self, repo_root: Path, node_path: str) -> ServiceResult[object]:
        try:
            node = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
        except ValueError:
            return self._invalid_node_path(node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind != NodeKind.CONTENT:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "decl_graph_node_not_content",
                    "Decl graphs can only be attached to Content nodes.",
                    object_ref=node_path,
                    current=node.value.kind.value,
                    expected=NodeKind.CONTENT.value,
                )
            )
        return self.runtime.foundation.ok(node.value)

    def _empty_index(self, node_path: str) -> DeclGraphIndex:
        return DeclGraphIndex(
            node_path=node_path,
            summary=f"Empty DeclGraph index for Content node {node_path}.",
        )

    def _paths(self, repo_root: Path, node_path: str) -> "_GraphPaths | None":
        try:
            ctx = FoundationContext(repo_root=Path(repo_root))
            graph_root = self.runtime.foundation.layout.node_dir(ctx, node_path) / "decl_graph"
        except ValueError:
            return None
        return _GraphPaths(
            graph_root=graph_root,
            index_path=graph_root / self.INDEX_FILENAME,
            strategies_dir=graph_root / "strategies",
            rounds_dir=graph_root / "rounds",
            decls_dir=graph_root / "decls",
            changes_dir=graph_root / "changes",
            reviews_dir=graph_root / "reviews",
        )

    def _invalid_node_path(self, node_path: str) -> ServiceResult[object]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "decl_graph_node_path_invalid",
                "DeclGraph node path is invalid.",
                object_ref=node_path,
            )
        )


@dataclass(frozen=True)
class _GraphPaths:
    graph_root: Path
    index_path: Path
    strategies_dir: Path
    rounds_dir: Path
    decls_dir: Path
    changes_dir: Path
    reviews_dir: Path
