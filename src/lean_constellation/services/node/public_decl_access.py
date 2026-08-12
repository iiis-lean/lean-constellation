"""Public declaration access resolver for node/repo boundary reads."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import (
    RepoFormat,
    RepoPublicationStatus,
    proof_availability_for_completion_mode,
)
from lean_constellation.services.foundation import ServiceIssue, ServiceResult
from lean_constellation.services.node.dependency import DependencyComponent
from lean_constellation.services.decl_graph.models import (
    DeclOriginRef,
    MathlibDeclDep,
    RepoDeclDep,
)
from lean_constellation.services.node.export import DeclPublicView, ExportComponent
from lean_constellation.services.node.node_tree import NodeKind, NodeTreeComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class PublicDeclListItem(StrictModel):
    name: str
    kind: str | None = None
    module: str | None = None
    lean_full_name: str | None = None
    statement_nl: Literal["accepted", "missing"]
    statement_formal: Literal["accepted", "missing"]
    proof_nl: Literal["accepted", "missing", "not_required"]
    proof_formal: Literal["accepted", "missing", "not_required"]
    summary: str | None = None
    ready: bool
    stale: bool


class PublicDeclDependencyItem(StrictModel):
    kind: Literal["repo_decl", "mathlib_decl"]
    repository: str
    node_path: str | None = None
    name: str
    module: str | None = None
    reason: str | None = None


class PublicDeclOriginItem(StrictModel):
    kind: str
    source_path: str | None = None
    resource_key: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    locator: str | None = None
    note: str | None = None


class PublicDeclDetailView(PublicDeclListItem):
    repository: str
    node_path: str | None = None
    statement_dependencies: list[PublicDeclDependencyItem] = Field(
        default_factory=list
    )
    proof_dependencies: list[PublicDeclDependencyItem] = Field(default_factory=list)
    statement_origins: list[PublicDeclOriginItem] = Field(default_factory=list)
    proof_origins: list[PublicDeclOriginItem] = Field(default_factory=list)


class VisibleNodeAccessItem(StrictModel):
    node_path: str
    node_kind: str
    import_module: str
    public_declarations: list[PublicDeclListItem] = Field(default_factory=list)
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
    _PUBLIC_DECL_CACHE_LIMIT = 64

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
        self._stable_public_decl_cache: OrderedDict[
            tuple[str, str, str], tuple[list[DeclPublicView], tuple[ServiceIssue, ...]]
        ] = OrderedDict()

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
            items: list[VisibleNodeAccessItem] = []
            for node in tree.value.nodes:
                public = self._list_node_public_decls_unchecked(
                    repo_root,
                    node_path=node.path,
                    stable_boundary=False,
                )
                if not public.ok or public.value is None:
                    return self.runtime.foundation.fail(public.issues)
                compact = self._compact_public_decls(repo_root, public.value)
                if not compact.ok or compact.value is None:
                    return self.runtime.foundation.fail(compact.issues)
                items.append(
                    VisibleNodeAccessItem(
                        node_path=node.path,
                        node_kind=node.kind.value,
                        import_module=f"{node.path}.Interfaces",
                        public_declarations=compact.value,
                        source="current_repo_node_tree",
                        summary=(
                            f"{node.kind.value} node {node.path} exposes "
                            f"{len(compact.value)} public declarations."
                        ),
                    )
                )
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
        current_public = self._list_node_public_decls_unchecked(
            repo_root,
            node_path=current.value,
            stable_boundary=False,
        )
        if not current_public.ok or current_public.value is None:
            return self.runtime.foundation.fail(current_public.issues)
        current_compact = self._compact_public_decls(repo_root, current_public.value)
        if not current_compact.ok or current_compact.value is None:
            return self.runtime.foundation.fail(current_compact.issues)
        items = [
            VisibleNodeAccessItem(
                node_path=current.value,
                node_kind="current",
                import_module=f"{current.value}.Interfaces",
                public_declarations=current_compact.value,
                source="current_node",
                summary=(
                    f"Current node {current.value} exposes "
                    f"{len(current_compact.value)} public declarations."
                ),
            )
        ]
        for boundary in visible.value.boundaries:
            if boundary.repo is not None:
                continue
            public = self._list_node_public_decls_unchecked(
                repo_root,
                node_path=boundary.node_path,
                stable_boundary=True,
            )
            if not public.ok or public.value is None:
                return self.runtime.foundation.fail(public.issues)
            compact = self._compact_public_decls(repo_root, public.value)
            if not compact.ok or compact.value is None:
                return self.runtime.foundation.fail(compact.issues)
            items.append(
                VisibleNodeAccessItem(
                    node_path=boundary.node_path,
                    node_kind=boundary.node_kind,
                    import_module=boundary.import_module,
                    public_declarations=compact.value,
                    source="visible_boundary",
                    summary=(
                        f"Visible {boundary.node_kind} node {boundary.node_path} exposes "
                        f"{len(compact.value)} public declarations."
                    ),
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
                    source="workspace_stable_provider",
                    summary=(
                        f"Stable provider repo {repo.repo_key} publishes "
                        f"{repo.completion_mode.value}."
                    ),
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
        stable_boundary: bool,
        current_node_path: str | None = None,
    ) -> ServiceResult[list[DeclPublicView]]:
        visible = self.assert_node_visible(repo_root, node_path=node_path, actor_role=actor_role, current_node_path=current_node_path)
        if not visible.ok:
            return self.runtime.foundation.fail(visible.issues)
        return self._list_node_public_decls_unchecked(
            repo_root,
            node_path=node_path,
            stable_boundary=stable_boundary,
        )

    def list_node_public_decl_items(
        self,
        repo_root: Path,
        *,
        node_path: str,
        actor_role: str,
        stable_boundary: bool,
        current_node_path: str | None = None,
    ) -> ServiceResult[list[PublicDeclListItem]]:
        public = self.list_node_public_decls(
            repo_root,
            node_path=node_path,
            actor_role=actor_role,
            stable_boundary=stable_boundary,
            current_node_path=current_node_path,
        )
        if not public.ok or public.value is None:
            return self.runtime.foundation.fail(public.issues)
        compact = self._compact_public_decls(repo_root, public.value)
        if not compact.ok or compact.value is None:
            return self.runtime.foundation.fail(compact.issues)
        return self.runtime.foundation.ok(compact.value, warnings=public.issues)

    def list_repo_public_decl_items(
        self,
        repo_root: Path,
        *,
        repo_key: str,
        actor_role: str,
        current_node_path: str | None = None,
    ) -> ServiceResult[list[PublicDeclListItem]]:
        public = self.list_repo_public_decls(
            repo_root,
            repo_key=repo_key,
            actor_role=actor_role,
            current_node_path=current_node_path,
        )
        if not public.ok or public.value is None:
            return self.runtime.foundation.fail(public.issues)
        provider_root = (
            Path(repo_root).parent
            / self.runtime.foundation.layout.ensure_safe_key(repo_key)
        )
        compact = self._compact_public_decls(provider_root, public.value)
        if not compact.ok or compact.value is None:
            return self.runtime.foundation.fail(compact.issues)
        return self.runtime.foundation.ok(compact.value, warnings=public.issues)

    def inspect_public_decl_item(
        self,
        repo_root: Path,
        *,
        public_decl: DeclPublicView,
        repository: str,
        expose_node_path: bool,
        revision: int | None = None,
    ) -> ServiceResult[PublicDeclDetailView]:
        ref = public_decl.ref
        resolved_revision = (
            revision or public_decl.resolved_revision or ref.revision
        )
        decl = self.runtime.decl_graph.get_decl_view(
            repo_root, node_path=ref.node, name=ref.name
        )
        loaded = self.runtime.decl_graph.get_decl_revision(
            repo_root,
            node_path=ref.node,
            name=ref.name,
            revision=resolved_revision,
        )
        if (
            not decl.ok
            or decl.value is None
            or not loaded.ok
            or loaded.value is None
        ):
            return self.runtime.foundation.fail([*decl.issues, *loaded.issues])
        compact = self._compact_public_decl(
            public_decl,
            decl=decl.value,
            revision=loaded.value,
        )
        return self.runtime.foundation.ok(
            PublicDeclDetailView(
                **compact.model_dump(),
                repository=repository,
                node_path=ref.node if expose_node_path else None,
                statement_dependencies=[
                    self._dependency_item(
                        dependency,
                        repository=repository,
                        expose_node_path=expose_node_path,
                    )
                    for dependency in loaded.value.statement.deps
                ],
                proof_dependencies=[
                    self._dependency_item(
                        dependency,
                        repository=repository,
                        expose_node_path=expose_node_path,
                    )
                    for dependency in (
                        loaded.value.proof.deps
                        if loaded.value.proof is not None
                        else []
                    )
                ],
                statement_origins=[
                    self._origin_item(origin)
                    for origin in (
                        loaded.value.statement.nl.origin
                        if loaded.value.statement.nl is not None
                        else []
                    )
                ],
                proof_origins=[
                    self._origin_item(origin)
                    for origin in (
                        loaded.value.proof.nl.origin
                        if loaded.value.proof is not None
                        and loaded.value.proof.nl is not None
                        else []
                    )
                ],
            )
        )

    def _list_node_public_decls_unchecked(
        self,
        repo_root: Path,
        *,
        node_path: str,
        stable_boundary: bool = False,
    ) -> ServiceResult[list[DeclPublicView]]:
        node = self.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        if node.value.kind == NodeKind.CONTENT:
            if stable_boundary:
                return self.export.list_committed_content_public_decls(
                    repo_root,
                    node_path=node_path,
                )
            return self.export.list_content_public_decls(repo_root, node_path=node_path)
        exports = (
            self.export.list_committed_scope_exports(
                repo_root,
                scope_path=node_path,
            )
            if stable_boundary
            else self.export.list_scope_exports(repo_root, scope_path=node_path)
        )
        if not exports.ok or exports.value is None:
            return self.runtime.foundation.fail(exports.issues)
        values: list[DeclPublicView] = []
        for view in exports.value:
            status = self.runtime.repo_workspace.release.get_decl_release_status(
                repo_root, node_path=view.ref.node, decl_name=view.ref.name
            )
            if not status.ok or status.value is None:
                return self.runtime.foundation.fail(status.issues)
            values.append(
                DeclPublicView(
                    ref=view.ref,
                    resolved_revision=view.resolved_revision,
                    resolution_reason=view.resolution_reason,
                    public=True,
                    ready=view.valid,
                    stale=not view.valid,
                    source="scope_exports",
                    summary=view.summary,
                    released_state=status.value.released_state,
                    release_protected=status.value.release_protected,
                )
            )
        return self.runtime.foundation.ok(values, warnings=exports.issues)

    def _compact_public_decls(
        self, repo_root: Path, values: list[DeclPublicView]
    ) -> ServiceResult[list[PublicDeclListItem]]:
        compact: list[PublicDeclListItem] = []
        for public in values:
            ref = public.ref
            revision_id = public.resolved_revision or ref.revision
            decl = self.runtime.decl_graph.get_decl_view(
                repo_root, node_path=ref.node, name=ref.name
            )
            revision = self.runtime.decl_graph.get_decl_revision(
                repo_root,
                node_path=ref.node,
                name=ref.name,
                revision=revision_id,
            )
            if (
                not decl.ok
                or decl.value is None
                or not revision.ok
                or revision.value is None
            ):
                theorem_like = (public.kind or "").lower() in {
                    "theorem",
                    "lemma",
                    "corollary",
                    "proposition",
                }
                compact.append(
                    PublicDeclListItem(
                        name=ref.name,
                        kind=public.kind,
                        module=public.module,
                        statement_nl="accepted" if public.ready else "missing",
                        statement_formal="accepted" if public.ready else "missing",
                        proof_nl=(
                            "accepted"
                            if theorem_like and public.ready
                            else "missing"
                            if theorem_like
                            else "not_required"
                        ),
                        proof_formal=(
                            "accepted"
                            if theorem_like and public.ready
                            else "missing"
                            if theorem_like
                            else "not_required"
                        ),
                        summary=public.summary,
                        ready=public.ready,
                        stale=public.stale,
                    )
                )
                continue
            compact.append(
                self._compact_public_decl(
                    public,
                    decl=decl.value,
                    revision=revision.value,
                )
            )
        compact.sort(key=lambda item: item.name)
        return self.runtime.foundation.ok(compact)

    def _compact_public_decl(
        self,
        public: DeclPublicView,
        *,
        decl,
        revision,
    ) -> PublicDeclListItem:
        state = revision.state.value
        theorem_like = decl.kind.lower() in {
            "theorem",
            "lemma",
            "corollary",
            "proposition",
        }
        return PublicDeclListItem(
            name=public.ref.name,
            kind=decl.kind,
            module=decl.module,
            lean_full_name=revision.lean_decl_name,
            statement_nl="accepted" if state != "planned" else "missing",
            statement_formal=(
                "accepted"
                if state in {"declared", "proof_planned", "proved"}
                else "missing"
            ),
            proof_nl=(
                "accepted"
                if theorem_like and state in {"proof_planned", "proved"}
                else "missing"
                if theorem_like
                else "not_required"
            ),
            proof_formal=(
                "accepted"
                if theorem_like and state == "proved"
                else "missing"
                if theorem_like
                else "not_required"
            ),
            summary=decl.summary or public.summary,
            ready=public.ready,
            stale=public.stale,
        )

    def _dependency_item(
        self,
        dependency,
        *,
        repository: str,
        expose_node_path: bool,
    ) -> PublicDeclDependencyItem:
        if isinstance(dependency, MathlibDeclDep):
            return PublicDeclDependencyItem(
                kind="mathlib_decl",
                repository="Mathlib",
                name=dependency.ref.name,
                module=dependency.ref.module,
                reason=dependency.reason,
            )
        assert isinstance(dependency, RepoDeclDep)
        return PublicDeclDependencyItem(
            kind="repo_decl",
            repository=dependency.ref.repo or repository,
            node_path=dependency.ref.node if expose_node_path else None,
            name=dependency.ref.name,
            reason=dependency.reason,
        )

    @staticmethod
    def _origin_item(origin: DeclOriginRef) -> PublicDeclOriginItem:
        locator = None
        if origin.start_locator or origin.end_locator:
            locator = (
                origin.start_locator
                if origin.start_locator == origin.end_locator
                else f"{origin.start_locator or ''}–{origin.end_locator or ''}"
            )
        return PublicDeclOriginItem(
            kind=origin.kind,
            source_path=origin.source_path,
            resource_key=origin.resource_key,
            start_line=origin.start_line,
            end_line=origin.end_line,
            locator=locator,
            note=origin.note,
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
        config = self.runtime.repo_workspace.metadata.get_repo_config(provider_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        cache_key = self._stable_native_cache_key(
            provider_root,
            required_availability=proof_availability_for_completion_mode(
                config.value.config.completion_mode
            ).value,
        )
        if cache_key is not None and cache_key in self._stable_public_decl_cache:
            values, warnings = self._stable_public_decl_cache.pop(cache_key)
            self._stable_public_decl_cache[cache_key] = (values, warnings)
            return self.runtime.foundation.ok(list(values), warnings=list(warnings))
        public_refs = self.runtime.decl_graph.ref_compatibility.list_public_decl_refs(
            provider_root,
            required_availability=proof_availability_for_completion_mode(
                config.value.config.completion_mode
            ),
        )
        if not public_refs.ok or public_refs.value is None:
            return self.runtime.foundation.fail(public_refs.issues)
        values: list[DeclPublicView] = []
        seen: set[tuple[str, str, int]] = set()
        for resolved in public_refs.value:
            ref = resolved.anchor
            key = (ref.node, ref.name, ref.revision)
            if key in seen:
                continue
            seen.add(key)
            decl = self.runtime.decl_graph.decl_catalog.get_decl(
                provider_root, node_path=ref.node, name=ref.name
            )
            if not decl.ok or decl.value is None:
                return self.runtime.foundation.fail(decl.issues)
            status = self.runtime.repo_workspace.release.get_decl_release_status(
                provider_root, node_path=ref.node, decl_name=ref.name
            )
            if not status.ok or status.value is None:
                return self.runtime.foundation.fail(status.issues)
            values.append(
                DeclPublicView(
                    ref=self._with_repo(ref, repo_key=repo_key),
                    resolved_revision=resolved.resolved_revision,
                    resolution_reason=resolved.reason,
                    kind=decl.value.kind,
                    module=decl.value.module,
                    public=True,
                    ready=resolved.compatible,
                    stale=not resolved.compatible,
                    source="repo_main_public_boundary",
                    summary=decl.value.summary,
                    released_state=status.value.released_state,
                    release_protected=status.value.release_protected,
                )
            )
        if cache_key is not None:
            self._stable_public_decl_cache[cache_key] = (list(values), tuple(public_refs.issues))
            while len(self._stable_public_decl_cache) > self._PUBLIC_DECL_CACHE_LIMIT:
                self._stable_public_decl_cache.popitem(last=False)
        return self.runtime.foundation.ok(values, warnings=public_refs.issues)

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
        current = self._require_current_node(current_node_path)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        if node_path == current.value:
            return self.runtime.foundation.ok(None)
        visible = self.dependency.list_visible_node_boundaries(
            repo_root, node_path=current.value
        )
        if not visible.ok or visible.value is None:
            return self.runtime.foundation.fail(visible.issues)
        if any(
            boundary.repo is None and boundary.node_path == node_path
            for boundary in visible.value.boundaries
        ):
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
            availability = self.runtime.repo_workspace.provider_availability.check_provider_available(Path(repo_root).parent / repo_key)
            if not availability.ok or availability.value is None:
                return self.runtime.foundation.fail(availability.issues)
            if not availability.value.passed:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "repo_public_decl_provider_not_stable",
                        "Provider repo public declarations can only be read when its format-aware availability gate passes.",
                        object_ref=repo_key,
                        details={"issues": "; ".join(issue.kind for issue in availability.value.issues)},
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

    def _stable_native_cache_key(
        self,
        provider_root: Path,
        *,
        required_availability: str,
    ) -> tuple[str, str, str] | None:
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(provider_root)
        if (
            not repo_format.ok
            or repo_format.value is None
            or repo_format.value.repo_format != RepoFormat.NATIVE
        ):
            return None
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(provider_root)
        if not publication.ok or publication.value is None:
            return None
        state = publication.value.publication
        if state.status != RepoPublicationStatus.STABLE or state.latest_release_id is None:
            return None
        return (str(provider_root.resolve()), state.latest_release_id, required_availability)
