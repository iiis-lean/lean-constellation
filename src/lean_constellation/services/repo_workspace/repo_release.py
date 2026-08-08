"""Immutable repository releases and format-aware release baselines."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.repo_release import (
    DeclAvailabilityEntry,
    DeclAvailabilityIndex,
    DeclReleaseStatusView,
    ReleasedDeclProtectionView,
    RepoRelease,
    RepoReleaseBaselineView,
    RepoReleaseView,
)
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoFormat,
    completion_mode_satisfies,
)
from lean_constellation.services.decl_graph.availability_policy import (
    required_state_for_availability,
)
from lean_constellation.services.decl_graph.models import (
    DeclLifecycle,
    DeclRevisionStatus,
    DeclState,
    RepoDeclDep,
)
from lean_constellation.services.foundation import FoundationContext, ServiceResult, WriteMode
from lean_constellation.services.node.node_tree import NodeContract, NodeContractStatus, NodeKind

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


_STATE_RANK = {
    DeclState.PLANNED: 0,
    DeclState.SPECIFIED: 1,
    DeclState.DECLARED: 2,
    DeclState.PROOF_PLANNED: 3,
    DeclState.PROVED: 4,
    DeclState.OBSOLETE: -1,
}


class RepoReleaseComponent:
    """Store immutable releases and derive their historical public closure."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime
        self._decl_availability_cache: OrderedDict[
            tuple[str, str], DeclAvailabilityIndex | None
        ] = OrderedDict()
        self._decl_availability_cache_size = 16

    def get_decl_availability_index(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[DeclAvailabilityIndex | None]:
        """Read an optional Release sidecar; any miss falls back to live recursion."""

        repo_root = Path(repo_root).resolve()
        key = (str(repo_root), release_id)
        if key in self._decl_availability_cache:
            value = self._decl_availability_cache.pop(key)
            self._decl_availability_cache[key] = value
            return self.runtime.foundation.ok(value)
        relative_path = self.runtime.foundation.layout.release_decl_availability_path(
            FoundationContext(repo_root=repo_root),
            release_id,
        ).relative_to(repo_root).as_posix()
        captured = self.runtime.repo_workspace.git_release.read_release_file(
            repo_root,
            release_id=release_id,
            relative_path=relative_path,
        )
        value: DeclAvailabilityIndex | None = None
        if captured.ok and captured.value is not None:
            try:
                value = DeclAvailabilityIndex.model_validate_json(captured.value)
            except ValueError:
                value = None
        self._decl_availability_cache[key] = value
        while len(self._decl_availability_cache) > self._decl_availability_cache_size:
            self._decl_availability_cache.popitem(last=False)
        return self.runtime.foundation.ok(value)

    def lookup_decl_availability(
        self,
        repo_root: Path,
        *,
        release_id: str,
        node_path: str,
        decl_name: str,
        revision: int,
    ) -> ServiceResult[DeclAvailabilityEntry | None]:
        index = self.get_decl_availability_index(repo_root, release_id=release_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.ok(None)
        return self.runtime.foundation.ok(
            next(
                (
                    entry
                    for entry in index.value.entries
                    if entry.node == node_path
                    and entry.name == decl_name
                    and entry.revision == revision
                    and entry.main_export
                ),
                None,
            )
        )

    def write_decl_availability_index(
        self,
        repo_root: Path,
        *,
        release_id: str,
        index: DeclAvailabilityIndex,
    ) -> ServiceResult[Path]:
        path = self.runtime.foundation.layout.release_decl_availability_path(
            FoundationContext(repo_root=Path(repo_root)),
            release_id,
        )
        written = self.runtime.foundation.store.write_json_atomic(
            path,
            index,
            mode=WriteMode.CREATE_ONLY,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        self._decl_availability_cache.pop(
            (str(Path(repo_root).resolve()), release_id),
            None,
        )
        return self.runtime.foundation.ok(path)

    def allocate_release_id(self, repo_root: Path) -> ServiceResult[str]:
        root = self.runtime.foundation.layout.releases_root(FoundationContext(repo_root=Path(repo_root)))
        existing = {path.stem for path in root.glob("*.json")} if root.exists() else set()
        return self.runtime.foundation.store.allocate_uuid(lambda candidate: candidate in existing, prefix="release")

    def create_release(self, repo_root: Path, *, release: RepoRelease) -> ServiceResult[RepoReleaseView]:
        repo_root = Path(repo_root)
        path = self.runtime.foundation.layout.release_path(FoundationContext(repo_root=repo_root), release.release_id)
        if path.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("release_exists", "Repo release already exists.", object_ref=release.release_id)
            )
        if release.parent_release_id is not None:
            parent = self.get_release(repo_root, release_id=release.parent_release_id)
            if not parent.ok or parent.value is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_parent_missing",
                        "Repo release parent does not exist.",
                        object_ref=release.release_id,
                        expected=release.parent_release_id,
                    )
                )
            parent_lineage = self.resolve_release_lineage(repo_root, release_id=release.parent_release_id)
            if not parent_lineage.ok:
                return self.runtime.foundation.fail(parent_lineage.issues)
            if not completion_mode_satisfies(
                release.completion_mode,
                parent.value.release.completion_mode,
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_parent_completion_regression",
                        "A child release cannot lower its parent's completion requirement.",
                        object_ref=release.release_id,
                        current=release.completion_mode.value,
                        expected=parent.value.release.completion_mode.value,
                    )
                )
        validated = self._validate_release_heads(repo_root, release)
        if not validated.ok:
            return self.runtime.foundation.fail(validated.issues)
        written = self.runtime.foundation.store.write_json_atomic(path, release, mode=WriteMode.CREATE_ONLY)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(self._view(repo_root, release))

    def get_release(self, repo_root: Path, *, release_id: str) -> ServiceResult[RepoReleaseView]:
        try:
            path = self.runtime.foundation.layout.release_path(FoundationContext(repo_root=Path(repo_root)), release_id)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("release_id_invalid", str(exc), object_ref=release_id)
            )
        loaded = self.runtime.foundation.store.read_json(path, RepoRelease)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        if loaded.value.release_id != release_id:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_identity_mismatch",
                    "Release file identity does not match its requested id.",
                    object_ref=release_id,
                    current=loaded.value.release_id,
                )
            )
        return self.runtime.foundation.ok(self._view(Path(repo_root), loaded.value))

    def list_releases(self, repo_root: Path) -> ServiceResult[list[RepoReleaseView]]:
        repo_root = Path(repo_root)
        root = self.runtime.foundation.layout.releases_root(FoundationContext(repo_root=repo_root))
        if not root.exists():
            return self.runtime.foundation.ok([])
        views: list[RepoReleaseView] = []
        for path in sorted(root.glob("*.json")):
            loaded = self.runtime.foundation.store.read_json(path, RepoRelease)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            if path.stem != loaded.value.release_id:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_identity_mismatch",
                        "Release file identity does not match its filename.",
                        object_ref=str(path),
                        current=loaded.value.release_id,
                        expected=path.stem,
                    )
                )
            views.append(self._view(repo_root, loaded.value))
        return self.runtime.foundation.ok(sorted(views, key=lambda item: (item.release.created_at, item.release.release_id)))

    def get_latest_release(self, repo_root: Path) -> ServiceResult[RepoReleaseView | None]:
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        release_id = publication.value.publication.latest_release_id
        if release_id is None:
            return self.runtime.foundation.ok(None)
        return self.get_release(repo_root, release_id=release_id)

    def resolve_release_lineage(self, repo_root: Path, *, release_id: str) -> ServiceResult[list[RepoRelease]]:
        lineage: list[RepoRelease] = []
        seen: set[str] = set()
        current_id: str | None = release_id
        while current_id is not None:
            if current_id in seen:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_lineage_cycle",
                        "Repo release lineage contains a cycle.",
                        object_ref=current_id,
                    )
                )
            seen.add(current_id)
            loaded = self.get_release(repo_root, release_id=current_id)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_parent_missing",
                        "Repo release lineage references a missing release.",
                        object_ref=current_id,
                    )
                )
            lineage.append(loaded.value.release)
            current_id = loaded.value.release.parent_release_id
        lineage.reverse()
        for parent, child in zip(lineage, lineage[1:], strict=False):
            if not completion_mode_satisfies(
                child.completion_mode,
                parent.completion_mode,
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_parent_completion_regression",
                        "Repo release lineage lowers an earlier completion requirement.",
                        object_ref=child.release_id,
                        current=child.completion_mode.value,
                        expected=parent.completion_mode.value,
                    )
                )
        return self.runtime.foundation.ok(lineage)

    def resolve_release_baseline(
        self,
        repo_root: Path,
        *,
        release_id: str | None = None,
    ) -> ServiceResult[RepoReleaseBaselineView]:
        repo_root = Path(repo_root)
        if release_id is None:
            latest = self.get_latest_release(repo_root)
            if not latest.ok:
                return self.runtime.foundation.fail(latest.issues)
            if latest.value is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("release_missing", "Repository has no latest release.", object_ref=str(repo_root))
                )
            release_id = latest.value.release.release_id
        lineage = self.resolve_release_lineage(repo_root, release_id=release_id)
        if not lineage.ok or lineage.value is None:
            return self.runtime.foundation.fail(lineage.issues)

        protections: dict[tuple[str, str], ReleasedDeclProtectionView] = {}
        protected_node_ids: set[str] = set()
        protected_scope_paths: set[str] = set()
        for release in lineage.value:
            result = self._accumulate_release_closure(
                repo_root,
                release=release,
                protections=protections,
                protected_node_ids=protected_node_ids,
                protected_scope_paths=protected_scope_paths,
            )
            if not result.ok:
                return self.runtime.foundation.fail(result.issues)
        latest = lineage.value[-1]
        return self.runtime.foundation.ok(
            RepoReleaseBaselineView(
                release_id=release_id,
                lineage_release_ids=[item.release_id for item in lineage.value],
                released_node_contract_versions=dict(latest.node_contract_versions),
                protected_decl_views=sorted(protections.values(), key=lambda item: (item.node_path, item.decl_name)),
                protected_node_ids=sorted(protected_node_ids),
                protected_scope_paths=sorted(protected_scope_paths),
                summary=f"Resolved {len(protections)} protected declarations across {len(lineage.value)} releases.",
            )
        )

    def get_decl_release_status(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[DeclReleaseStatusView]:
        current = self.runtime.decl_graph.decl_catalog.get_decl(repo_root, node_path=node_path, name=decl_name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
            repo_root,
            node_path=node_path,
            name=decl_name,
            revision=current.value.current_revision,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        latest = self.get_latest_release(repo_root)
        if not latest.ok:
            return self.runtime.foundation.fail(latest.issues)
        if latest.value is None:
            return self.runtime.foundation.ok(
                DeclReleaseStatusView(current_state=revision.value.state.value, summary="Declaration has not appeared in a release.")
            )
        baseline = self.resolve_release_baseline(repo_root, release_id=latest.value.release.release_id)
        if not baseline.ok or baseline.value is None:
            return self.runtime.foundation.fail(baseline.issues)
        node = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
        if not node.ok or node.value is None:
            return self.runtime.foundation.fail(node.issues)
        protected = next(
            (item for item in baseline.value.protected_decl_views if item.node_id == node.value.node_id and item.decl_name == decl_name),
            None,
        )
        released_state = protected.released_state if protected is not None else self._latest_released_private_state(
            repo_root, lineage_release_ids=baseline.value.lineage_release_ids, node_id=node.value.node_id, decl_name=decl_name
        )
        return self.runtime.foundation.ok(
            DeclReleaseStatusView(
                current_state=revision.value.state.value,
                released_state=released_state,
                release_protected=protected is not None,
                summary=("Declaration is release protected." if protected is not None else "Declaration is not release protected."),
            )
        )

    def _validate_release_heads(self, repo_root: Path, release: RepoRelease) -> ServiceResult[None]:
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        adapter = repo_format.value.repo_format == RepoFormat.ADAPTER
        adapter_main_count = 0
        for node_id, version in release.node_contract_versions.items():
            node = self.runtime.node.node_tree.node_store.load_node_by_id(repo_root, node_id=node_id)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("release_contract_missing", "Release node does not exist.", object_ref=node_id)
                )
            contract = self._load_contract(repo_root, node_id=node_id, version=version)
            if not contract.ok or contract.value is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_contract_missing", "Release contract does not exist.", object_ref=f"{node_id}@{version}"
                    )
                )
            if contract.value.status != NodeContractStatus.COMMITTED:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_contract_not_committed",
                        "Release contracts must be committed.",
                        object_ref=f"{node_id}@{version}",
                    )
                )
            if node.value.kind == NodeKind.SCOPE and contract.value.decl_graph_head:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("release_decl_head_invalid", "Scope contract DeclGraph head must be empty.", object_ref=node_id)
                )
            if adapter:
                if node.value.path != "Main" or node.value.kind != NodeKind.SCOPE:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "adapter_release_head_invalid",
                            "Adapter releases may contain only the committed Main Scope contract.",
                            object_ref=f"{node.value.path}@{version}",
                        )
                    )
                adapter_main_count += 1
                seen_exports: set[tuple[str, str, int]] = set()
                for ref in contract.value.exports:
                    key = (ref.node, ref.name, ref.revision)
                    if ref.repo is not None or ref.node != "Main" or key in seen_exports:
                        return self.runtime.foundation.fail(
                            self.runtime.foundation.issue(
                                "adapter_release_export_invalid",
                                "Adapter release exports must be unique local Main declaration references.",
                                object_ref=f"{ref.repo or '<local>'}:{ref.node}:{ref.name}@{ref.revision}",
                            )
                        )
                    seen_exports.add(key)
                    decl = self.runtime.decl_graph.decl_catalog.get_decl(
                        repo_root,
                        node_path="Main",
                        name=ref.name,
                    )
                    revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
                        repo_root,
                        node_path="Main",
                        name=ref.name,
                        revision=ref.revision,
                    )
                    if (
                        not decl.ok
                        or decl.value is None
                        or decl.value.lifecycle != DeclLifecycle.ACTIVE
                        or not decl.value.public
                        or not revision.ok
                        or revision.value is None
                        or revision.value.status != DeclRevisionStatus.COMMITTED
                    ):
                        return self.runtime.foundation.fail(
                            self.runtime.foundation.issue(
                                "adapter_release_export_invalid",
                                "Adapter release exports must reference active public committed Main declarations.",
                                object_ref=f"Main:{ref.name}@{ref.revision}",
                            )
                        )
            if node.value.kind == NodeKind.CONTENT:
                for name, revision_number in contract.value.decl_graph_head.items():
                    revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
                        repo_root, node_path=node.value.path, name=name, revision=revision_number
                    )
                    if not revision.ok or revision.value is None or revision.value.status != DeclRevisionStatus.COMMITTED:
                        return self.runtime.foundation.fail(
                            self.runtime.foundation.issue(
                                "release_decl_head_invalid",
                                "Content contract DeclGraph head must reference committed revisions.",
                                object_ref=f"{node.value.path}:{name}@{revision_number}",
                            )
                        )
        if adapter and adapter_main_count != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_release_main_contract_missing",
                    "Adapter releases require exactly one committed Main Scope contract.",
                    object_ref=release.release_id,
                )
            )
        return self.runtime.foundation.ok(None)

    def _accumulate_release_closure(
        self,
        repo_root: Path,
        *,
        release: RepoRelease,
        protections: dict[tuple[str, str], ReleasedDeclProtectionView],
        protected_node_ids: set[str],
        protected_scope_paths: set[str],
    ) -> ServiceResult[None]:
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if repo_format.value.repo_format == RepoFormat.ADAPTER:
            return self._accumulate_adapter_release_closure(
                repo_root,
                release=release,
                protections=protections,
                protected_node_ids=protected_node_ids,
                protected_scope_paths=protected_scope_paths,
            )
        nodes = self._release_nodes(repo_root, release)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        by_path = {node.path: (node, contract) for node, contract in nodes.value}
        root = by_path.get("Main")
        if root is None or root[0].kind != NodeKind.SCOPE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("release_main_contract_missing", "Release does not contain a Main Scope contract.", object_ref=release.release_id)
            )
        queue = list(root[1].exports)
        seen: set[tuple[str, str]] = set()
        while queue:
            ref = queue.pop(0)
            if ref.repo is not None:
                available = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
                    repo_root,
                    ref=ref,
                    required_availability=ProofAvailability.DECLARED,
                )
                if (
                    not available.ok
                    or available.value is None
                    or not available.value.compatible
                ):
                    reason = (
                        available.value.reason
                        if available.ok and available.value is not None
                        else "; ".join(issue.kind for issue in available.issues)
                    )
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "release_external_ref_unavailable",
                            "Released statement dependency is not available through the provider public boundary.",
                            object_ref=f"{ref.repo}:{ref.node}:{ref.name}@{ref.revision}",
                            current=reason,
                        )
                    )
                continue
            target = by_path.get(ref.node)
            if target is None or target[0].kind != NodeKind.CONTENT:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("release_decl_head_invalid", "Release declaration node is absent from the release.", object_ref=ref.node)
                )
            node, contract = target
            key = (node.node_id, ref.name)
            if key in seen:
                continue
            seen.add(key)
            from lean_constellation.services.decl_graph.ref_compatibility import RepoReleaseHeads

            compatible = self.runtime.decl_graph.ref_compatibility.resolve_decl_ref(
                repo_root,
                ref=ref,
                required_availability=ProofAvailability.DECLARED,
                target=RepoReleaseHeads(release_id=release.release_id),
            )
            if not compatible.ok or compatible.value is None:
                return self.runtime.foundation.fail(compatible.issues)
            revision_number = compatible.value.resolved_revision
            if not compatible.value.compatible or revision_number is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_decl_head_invalid",
                        "Release head does not contain a compatible referenced declaration.",
                        object_ref=ref.name,
                        current=compatible.value.reason,
                    )
                )
            if ref in root[1].exports:
                scope_chain = self._validate_release_scope_chain(
                    repo_root,
                    release=release,
                    by_path=by_path,
                    ref=ref,
                    resolved_revision=revision_number,
                )
                if not scope_chain.ok:
                    return self.runtime.foundation.fail(scope_chain.issues)
            revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
                repo_root, node_path=node.path, name=ref.name, revision=revision_number
            )
            if not revision.ok or revision.value is None:
                return self.runtime.foundation.fail(revision.issues)
            decl = self.runtime.decl_graph.decl_catalog.get_decl(repo_root, node_path=node.path, name=ref.name)
            if not decl.ok or decl.value is None or decl.value.lifecycle != DeclLifecycle.ACTIVE:
                return self.runtime.foundation.fail(decl.issues)
            previous = protections.get(key)
            released_state = revision.value.state.value
            if previous is not None and _STATE_RANK[DeclState(previous.released_state)] > _STATE_RANK[revision.value.state]:
                released_state = previous.released_state
            protections[key] = ReleasedDeclProtectionView(
                node_id=node.node_id,
                node_path=node.path,
                decl_name=ref.name,
                released_state=released_state,
                first_release_id=previous.first_release_id if previous is not None else release.release_id,
                last_release_id=release.release_id,
                summary=f"{node.path}:{ref.name} is protected by released public statement closure.",
            )
            protected_node_ids.add(node.node_id)
            for scope in self._ancestor_scopes(node.path):
                protected_scope_paths.add(scope)
                scope_entry = by_path.get(scope)
                if scope_entry is not None:
                    protected_node_ids.add(scope_entry[0].node_id)
            for dep in revision.value.statement.deps:
                if isinstance(dep, RepoDeclDep):
                    queue.append(dep.ref)
        return self.runtime.foundation.ok(None)

    def _accumulate_adapter_release_closure(
        self,
        repo_root: Path,
        *,
        release: RepoRelease,
        protections: dict[tuple[str, str], ReleasedDeclProtectionView],
        protected_node_ids: set[str],
        protected_scope_paths: set[str],
    ) -> ServiceResult[None]:
        nodes = self._release_nodes(repo_root, release)
        if not nodes.ok or nodes.value is None:
            return self.runtime.foundation.fail(nodes.issues)
        if len(nodes.value) != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_release_main_contract_missing",
                    "Adapter release closure requires exactly one Main Scope contract.",
                    object_ref=release.release_id,
                )
            )
        main, contract = nodes.value[0]
        if main.path != "Main" or main.kind != NodeKind.SCOPE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_release_main_contract_missing",
                    "Adapter release closure requires a committed Main Scope contract.",
                    object_ref=release.release_id,
                )
            )
        queue = list(contract.exports)
        seen: set[tuple[str, str, int]] = set()
        while queue:
            ref = queue.pop(0)
            if ref.repo is not None:
                available = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_ref(
                    repo_root,
                    ref=ref,
                    required_availability=ProofAvailability.DECLARED,
                )
                if (
                    not available.ok
                    or available.value is None
                    or not available.value.compatible
                ):
                    reason = (
                        available.value.reason
                        if available.ok and available.value is not None
                        else "; ".join(issue.kind for issue in available.issues)
                    )
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "release_external_ref_unavailable",
                            "Released statement dependency is not available through the provider public boundary.",
                            object_ref=f"{ref.repo}:{ref.node}:{ref.name}@{ref.revision}",
                            current=reason,
                        )
                    )
                continue
            if ref.node != "Main":
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "adapter_release_decl_ref_invalid",
                        "Adapter release-local declarations must belong to flat Main.",
                        object_ref=f"{ref.node}:{ref.name}@{ref.revision}",
                    )
                )
            key = (ref.node, ref.name, ref.revision)
            if key in seen:
                continue
            seen.add(key)
            decl = self.runtime.decl_graph.decl_catalog.get_decl(
                repo_root,
                node_path="Main",
                name=ref.name,
            )
            revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
                repo_root,
                node_path="Main",
                name=ref.name,
                revision=ref.revision,
            )
            if (
                not decl.ok
                or decl.value is None
                or decl.value.lifecycle != DeclLifecycle.ACTIVE
                or not revision.ok
                or revision.value is None
                or revision.value.status != DeclRevisionStatus.COMMITTED
            ):
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "adapter_release_decl_ref_invalid",
                        "Adapter release closure references a missing or uncommitted Main declaration.",
                        object_ref=f"Main:{ref.name}@{ref.revision}",
                    )
                )
            required_state = required_state_for_availability(
                decl.value.kind,
                ProofAvailability.DECLARED,
            )
            if _STATE_RANK[revision.value.state] < _STATE_RANK[required_state]:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "adapter_release_decl_state_too_low",
                        "Adapter release closure declaration does not satisfy declared availability.",
                        object_ref=f"Main:{ref.name}@{ref.revision}",
                        current=revision.value.state.value,
                        expected=required_state.value,
                    )
                )
            protection_key = (main.node_id, ref.name)
            previous = protections.get(protection_key)
            released_state = revision.value.state.value
            if (
                previous is not None
                and _STATE_RANK[DeclState(previous.released_state)]
                > _STATE_RANK[revision.value.state]
            ):
                released_state = previous.released_state
            protections[protection_key] = ReleasedDeclProtectionView(
                node_id=main.node_id,
                node_path="Main",
                decl_name=ref.name,
                released_state=released_state,
                first_release_id=(
                    previous.first_release_id
                    if previous is not None
                    else release.release_id
                ),
                last_release_id=release.release_id,
                summary=f"Main:{ref.name} is protected by the Adapter release public statement closure.",
            )
            protected_node_ids.add(main.node_id)
            protected_scope_paths.add("Main")
            for dep in revision.value.statement.deps:
                if isinstance(dep, RepoDeclDep):
                    queue.append(dep.ref)
        return self.runtime.foundation.ok(None)

    def _validate_release_scope_chain(
        self,
        repo_root: Path,
        *,
        release: RepoRelease,
        by_path: dict[str, tuple[object, NodeContract]],
        ref,
        resolved_revision: int,
    ) -> ServiceResult[None]:
        from lean_constellation.services.decl_graph.ref_compatibility import RepoReleaseHeads

        for scope_path in self._ancestor_scopes(ref.node)[1:]:
            scope_entry = by_path.get(scope_path)
            if scope_entry is None or getattr(scope_entry[0], "kind", None) != NodeKind.SCOPE:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_scope_chain_broken",
                        "Released Main export is missing an intermediate Scope contract.",
                        object_ref=scope_path,
                    )
                )
            matching = [
                candidate
                for candidate in scope_entry[1].exports
                if candidate.repo is None and candidate.node == ref.node and candidate.name == ref.name
            ]
            valid = False
            for candidate in matching:
                resolved = self.runtime.decl_graph.ref_compatibility.resolve_decl_ref(
                    repo_root,
                    ref=candidate,
                    required_availability=ProofAvailability.DECLARED,
                    target=RepoReleaseHeads(release_id=release.release_id),
                )
                if (
                    resolved.ok
                    and resolved.value is not None
                    and resolved.value.compatible
                    and resolved.value.resolved_revision == resolved_revision
                ):
                    valid = True
                    break
            if not valid:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "release_scope_chain_broken",
                        "Released Main export is not preserved by an intermediate Scope export.",
                        object_ref=scope_path,
                    )
                )
        return self.runtime.foundation.ok(None)

    def _release_nodes(self, repo_root: Path, release: RepoRelease):
        values = []
        for node_id, version in release.node_contract_versions.items():
            node = self.runtime.node.node_tree.node_store.load_node_by_id(repo_root, node_id=node_id)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(node.issues)
            contract = self._load_contract(repo_root, node_id=node_id, version=version)
            if not contract.ok or contract.value is None:
                return self.runtime.foundation.fail(contract.issues)
            values.append((node.value, contract.value))
        return self.runtime.foundation.ok(values)

    def _load_contract(self, repo_root: Path, *, node_id: str, version: int) -> ServiceResult[NodeContract]:
        path = self.runtime.node.node_tree.node_store.contract_path(repo_root, node_id=node_id, version=version)
        return self.runtime.foundation.store.read_json(path, NodeContract)

    def _latest_released_private_state(
        self, repo_root: Path, *, lineage_release_ids: list[str], node_id: str, decl_name: str
    ) -> str | None:
        best: DeclState | None = None
        for release_id in lineage_release_ids:
            release = self.get_release(repo_root, release_id=release_id)
            if not release.ok or release.value is None:
                continue
            version = release.value.release.node_contract_versions.get(node_id)
            if version is None:
                continue
            node = self.runtime.node.node_tree.node_store.load_node_by_id(repo_root, node_id=node_id)
            contract = self._load_contract(repo_root, node_id=node_id, version=version)
            if not node.ok or node.value is None or not contract.ok or contract.value is None:
                continue
            revision_number = contract.value.decl_graph_head.get(decl_name)
            if revision_number is None:
                continue
            revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
                repo_root, node_path=node.value.path, name=decl_name, revision=revision_number
            )
            if revision.ok and revision.value is not None and (best is None or _STATE_RANK[revision.value.state] > _STATE_RANK[best]):
                best = revision.value.state
        return best.value if best is not None else None

    def _ancestor_scopes(self, node_path: str) -> list[str]:
        parts = node_path.split(".")
        return [".".join(parts[:index]) for index in range(1, len(parts))]

    def _view(self, repo_root: Path, release: RepoRelease) -> RepoReleaseView:
        return RepoReleaseView(repo_root=str(repo_root), release=release, summary=f"Loaded repo release {release.release_id}.")


__all__ = ["RepoReleaseComponent"]
