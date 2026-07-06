"""Public declaration access resolver for node/repo boundary reads."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import RepoPublicationStatus
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.node.dependency import DependencyComponent
from lean_constellation.services.node.export import DeclPublicView, ExportComponent
from lean_constellation.services.node.node_tree import NodeKind, NodeTreeComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class VisibleNodeAccessItem(StrictModel):
    node_path: str
    node_kind: str
    source: str
    summary: str


class VisibleNodeAccessView(StrictModel):
    current_node_path: str | None = None
    nodes: list[VisibleNodeAccessItem] = Field(default_factory=list)
    summary: str


class ImportedRepoAccessItem(StrictModel):
    repo_key: str
    repo_root: str | None = None
    source: str
    summary: str


class ImportedRepoAccessView(StrictModel):
    current_node_path: str | None = None
    repos: list[ImportedRepoAccessItem] = Field(default_factory=list)
    summary: str


class PublicDeclAccessResolver:
    """Resolve public boundary visibility without starting provider runtimes."""

    _COORDINATOR_ROLES = {"coordinator", "admin"}

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        node_tree: NodeTreeComponent,
        dependency: DependencyComponent,
        export: ExportComponent,
    ) -> None:
        self.runtime = runtime
        self.node_tree = node_tree
        self.dependency = dependency
        self.export = export

    def list_visible_nodes(
        self,
        repo_root: Path,
        *,
        actor_role: str,
        current_node_path: str | None = None,
    ) -> ServiceResult[VisibleNodeAccessView]:
        if self._is_coordinator(actor_role):
            tree = self.node_tree.get_node_tree(repo_root)
            if not tree.ok or tree.value is None:
                return self.runtime.foundation.fail(tree.issues)
            items = [
                VisibleNodeAccessItem(
                    node_path=node.path,
                    node_kind=node.kind.value,
                    source="current_repo_node_tree",
                    summary=f"{node.kind.value} node {node.path}.",
                )
                for node in tree.value.nodes
            ]
            return self.runtime.foundation.ok(
                VisibleNodeAccessView(
                    current_node_path=current_node_path,
                    nodes=sorted(items, key=lambda item: item.node_path),
                    summary=f"Loaded {len(items)} visible current-repo nodes.",
                )
            )
        current = self._require_current_node(current_node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        visible = self.dependency.list_visible_node_boundaries(repo_root, node_path=current.value)
        if not visible.ok or visible.value is None:
            return self.runtime.foundation.fail(visible.issues)
        items = [
            VisibleNodeAccessItem(
                node_path=current.value,
                node_kind="current",
                source="current_node",
                summary=f"Current node {current.value}.",
            )
        ]
        for boundary in visible.value.boundaries:
            if boundary.repo is not None:
                continue
            items.append(
                VisibleNodeAccessItem(
                    node_path=boundary.node_path,
                    node_kind=boundary.node_kind,
                    source="visible_boundary",
                    summary=boundary.summary,
                )
            )
        return self.runtime.foundation.ok(
            VisibleNodeAccessView(
                current_node_path=current.value,
                nodes=sorted(items, key=lambda item: item.node_path),
                summary=f"Loaded {len(items)} visible current-repo nodes for {current.value}.",
            )
        )

    def list_imported_repos(
        self,
        repo_root: Path,
        *,
        actor_role: str,
        current_node_path: str | None = None,
    ) -> ServiceResult[ImportedRepoAccessView]:
        if self._is_coordinator(actor_role):
            workspace = self.runtime.repo_workspace.workspace_catalog.list_ready_provider_repos(
                Path(repo_root).parent,
                current_repo=Path(repo_root).name,
            )
            if not workspace.ok or workspace.value is None:
                return self.runtime.foundation.fail(workspace.issues)
            items = [
                ImportedRepoAccessItem(
                    repo_key=repo.repo_key,
                    repo_root=repo.repo_root,
                    source="workspace_ready_provider",
                    summary=f"Ready provider repo {repo.repo_key}.",
                )
                for repo in workspace.value
            ]
            return self.runtime.foundation.ok(
                ImportedRepoAccessView(
                    current_node_path=current_node_path,
                    repos=items,
                    summary=f"Loaded {len(items)} visible imported repos for coordinator.",
                )
            )
        current = self._require_current_node(current_node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        visible = self.dependency.list_visible_node_boundaries(repo_root, node_path=current.value)
        if not visible.ok or visible.value is None:
            return self.runtime.foundation.fail(visible.issues)
        items = [
            ImportedRepoAccessItem(
                repo_key=boundary.repo,
                repo_root=str(Path(repo_root).parent / boundary.repo),
                source="current_node_imported_boundary",
                summary=boundary.summary,
            )
            for boundary in visible.value.boundaries
            if boundary.repo is not None
        ]
        return self.runtime.foundation.ok(
            ImportedRepoAccessView(
                current_node_path=current.value,
                repos=items,
                summary=f"Loaded {len(items)} imported repos visible to {current.value}.",
            )
        )

    def list_node_public_decls(
        self,
        repo_root: Path,
        *,
        node_path: str,
        actor_role: str,
        current_node_path: str | None = None,
    ) -> ServiceResult[list[DeclPublicView]]:
        visible = self.assert_node_visible(repo_root, node_path=node_path, actor_role=actor_role, current_node_path=current_node_path)
        if not visible.ok:
            return self.runtime.foundation.fail(visible.issues)
        node = self.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind == NodeKind.CONTENT:
            return self.export.list_content_public_decls(repo_root, node_path=node_path)
        exports = self.export.list_scope_exports(repo_root, scope_path=node_path)
        if not exports.ok or exports.value is None:
            return self.runtime.foundation.fail(exports.issues)
        return self.runtime.foundation.ok(
            [
                DeclPublicView(
                    ref=view.ref,
                    public=True,
                    ready=view.valid,
                    stale=not view.valid,
                    source="scope_exports",
                    summary=view.summary,
                )
                for view in exports.value
            ],
            warnings=exports.issues,
        )

    def list_repo_public_decls(
        self,
        repo_root: Path,
        *,
        repo_key: str,
        actor_role: str,
        current_node_path: str | None = None,
    ) -> ServiceResult[list[DeclPublicView]]:
        visible = self.assert_repo_visible(repo_root, repo_key=repo_key, actor_role=actor_role, current_node_path=current_node_path)
        if not visible.ok:
            return self.runtime.foundation.fail(visible.issues)
        provider_root = Path(repo_root).parent / self.runtime.foundation.layout.ensure_safe_key(repo_key)
        exports = self.export.list_scope_exports(provider_root, scope_path="Main")
        if not exports.ok or exports.value is None:
            return self.runtime.foundation.fail(exports.issues)
        return self.runtime.foundation.ok(
            [
                DeclPublicView(
                    ref=self._with_repo(view.ref, repo_key=repo_key),
                    public=True,
                    ready=view.valid,
                    stale=not view.valid,
                    source="repo_main_exports",
                    summary=view.summary,
                )
                for view in exports.value
            ],
            warnings=exports.issues,
        )

    def assert_node_visible(
        self,
        repo_root: Path,
        *,
        node_path: str,
        actor_role: str,
        current_node_path: str | None = None,
    ) -> ServiceResult[None]:
        if self._is_coordinator(actor_role):
            node = self.node_tree.get_node(repo_root, path=node_path)
            if not node.ok:
                return self.runtime.foundation.fail(node.issues)
            return self.runtime.foundation.ok(None)
        visible = self.list_visible_nodes(repo_root, actor_role=actor_role, current_node_path=current_node_path)
        if not visible.ok or visible.value is None:
            return self.runtime.foundation.fail(visible.issues)
        if any(item.node_path == node_path for item in visible.value.nodes):
            return self.runtime.foundation.ok(None)
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "node_public_decl_not_visible",
                "Requested node public declarations are not visible to the current actor.",
                object_ref=node_path,
            )
        )

    def assert_repo_visible(
        self,
        repo_root: Path,
        *,
        repo_key: str,
        actor_role: str,
        current_node_path: str | None = None,
    ) -> ServiceResult[None]:
        repo_key = self.runtime.foundation.layout.ensure_safe_key(repo_key)
        visible = self.list_imported_repos(repo_root, actor_role=actor_role, current_node_path=current_node_path)
        if not visible.ok or visible.value is None:
            return self.runtime.foundation.fail(visible.issues)
        if any(item.repo_key == repo_key for item in visible.value.repos):
            publication = self.runtime.repo_workspace.metadata.get_repo_publication(Path(repo_root).parent / repo_key)
            if not publication.ok or publication.value is None:
                return self.runtime.foundation.fail(publication.issues)
            if publication.value.publication.status != RepoPublicationStatus.STABLE:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "repo_public_decl_provider_not_stable",
                        "Provider repo public declarations can only be read after stable publication.",
                        object_ref=repo_key,
                        current=publication.value.publication.status.value,
                        expected=RepoPublicationStatus.STABLE.value,
                    )
                )
            return self.runtime.foundation.ok(None)
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "repo_public_decl_not_visible",
                "Requested repo public declarations are not visible to the current actor.",
                object_ref=repo_key,
            )
        )

    def _require_current_node(self, current_node_path: str | None) -> ServiceResult[str]:
        if current_node_path is None or not current_node_path.strip():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("current_node_required", "A node-scoped public decl access request requires current_node_path.")
            )
        return self.runtime.foundation.ok(current_node_path.strip())

    def _is_coordinator(self, actor_role: str) -> bool:
        return actor_role.strip().lower() in self._COORDINATOR_ROLES

    def _with_repo(self, ref: DeclRef, *, repo_key: str) -> DeclRef:
        return DeclRef(repo=repo_key, node=ref.node, name=ref.name, revision=ref.revision)
