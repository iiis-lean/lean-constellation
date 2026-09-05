"""Immutable repository releases and format-aware release baselines."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from lean_constellation.domain.repo_release import (
    DeclAvailabilityEntry,
    DeclAvailabilityIndex,
    DeclReleaseStatusView,
    ResolvedDeclRefView,
    ReleasedDeclProtectionView,
    RepoRelease,
    RepoReleaseBaselineView,
    RepoReleaseView,
)
from lean_constellation.domain.refs import DeclRef
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
    DeclRevision,
    DeclRevisionStatus,
    DeclState,
    RepoDeclDep,
)
from lean_constellation.services.foundation import FoundationContext, ServiceResult, WriteMode
from lean_constellation.services.node.node_tree import (
    NodeContract,
    NodeContractStatus,
    NodeKind,
    NodeMetadata,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


_STATE_RANK = {
    DeclState.PLANNED: 0,
    DeclState.SPECIFIED: 1,
    DeclState.DECLARED: 2,
    DeclState.PROOF_PLANNED: 3,
    DeclState.PROVED: 4,
}


@dataclass
class RepoReleaseAuditContext:
    """Frozen release lookup owned by one outer service operation."""

    repo_root: Path
    release_id: str | None
    lineage: list[RepoRelease] = field(default_factory=list)
    baseline: RepoReleaseBaselineView | None = None
    node_ids_by_path: dict[str, str] = field(default_factory=dict)
    protected_by_key: dict[tuple[str, str], ReleasedDeclProtectionView] = field(
        default_factory=dict
    )
    latest_private_states: dict[tuple[str, str], str] = field(default_factory=dict)
    released_head_revisions: dict[tuple[str, str, str], int] = field(
        default_factory=dict
    )
    release_contracts: dict[tuple[str, str], NodeContract] = field(
        default_factory=dict
    )
    decl_ref_context: object | None = None


class RepoReleaseComponent:
    """Store immutable releases and derive their historical public closure."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime
        self._decl_availability_cache: OrderedDict[
            tuple[str, str], DeclAvailabilityIndex
        ] = OrderedDict()
        self._decl_availability_cache_size = 16

    def get_decl_availability_index(
        self,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult[DeclAvailabilityIndex]:
        """Read the current-schema Release declaration availability sidecar."""

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
        if not captured.ok:
            return self.runtime.foundation.fail(captured.issues)
        if captured.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_decl_availability_missing",
                    "Release declaration availability sidecar is missing.",
                    object_ref=f"{repo_root}:{release_id}",
                )
            )
        try:
            value = DeclAvailabilityIndex.model_validate_json(captured.value)
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_decl_availability_invalid",
                    f"Release declaration availability sidecar is invalid: {exc}",
                    object_ref=f"{repo_root}:{release_id}",
                )
            )
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
            return self.runtime.foundation.fail(index.issues)
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

        return self._resolve_release_baseline_from_lineage(
            repo_root,
            release_id=release_id,
            lineage=lineage.value,
        )

    def _resolve_release_baseline_from_lineage(
        self,
        repo_root: Path,
        *,
        release_id: str,
        lineage: list[RepoRelease],
        release_nodes_by_id: dict[str, list[tuple[NodeMetadata, NodeContract]]] | None = None,
        resolution_context=None,
    ) -> ServiceResult[RepoReleaseBaselineView]:
        resolution_context = (
            resolution_context
            or self.runtime.decl_graph.ref_compatibility.create_operation_context()
        )
        protections: dict[tuple[str, str], ReleasedDeclProtectionView] = {}
        protected_node_ids: set[str] = set()
        protected_scope_paths: set[str] = set()
        for release in lineage:
            result = self._accumulate_release_closure(
                repo_root,
                release=release,
                protections=protections,
                protected_node_ids=protected_node_ids,
                protected_scope_paths=protected_scope_paths,
                release_nodes=(release_nodes_by_id or {}).get(release.release_id),
                resolution_context=resolution_context,
            )
            if not result.ok:
                return self.runtime.foundation.fail(result.issues)
        latest = lineage[-1]
        return self.runtime.foundation.ok(
            RepoReleaseBaselineView(
                release_id=release_id,
                lineage_release_ids=[item.release_id for item in lineage],
                released_node_contract_versions=dict(latest.node_contract_versions),
                protected_decl_views=sorted(protections.values(), key=lambda item: (item.node_path, item.decl_name)),
                protected_node_ids=sorted(protected_node_ids),
                protected_scope_paths=sorted(protected_scope_paths),
                summary=f"Resolved {len(protections)} protected declarations across {len(lineage)} releases.",
            )
        )

    def create_release_audit_context(
        self,
        repo_root: Path,
    ) -> ServiceResult[RepoReleaseAuditContext]:
        """Freeze current latest release truth for one caller-owned operation."""

        repo_root = Path(repo_root).resolve()
        resolution_context = (
            self.runtime.decl_graph.ref_compatibility.create_operation_context()
        )
        latest = self.get_latest_release(repo_root)
        if not latest.ok:
            return self.runtime.foundation.fail(latest.issues)
        if latest.value is None:
            return self.runtime.foundation.ok(
                RepoReleaseAuditContext(
                    repo_root=repo_root,
                    release_id=None,
                    decl_ref_context=resolution_context,
                )
            )
        release_id = latest.value.release.release_id
        lineage = self.resolve_release_lineage(repo_root, release_id=release_id)
        if not lineage.ok or lineage.value is None:
            return self.runtime.foundation.fail(lineage.issues)

        node_index = self.runtime.node.node_tree.node_store.read_index(repo_root)
        if not node_index.ok or node_index.value is None:
            return self.runtime.foundation.fail(node_index.issues)
        release_nodes_by_id: dict[str, list[tuple[NodeMetadata, NodeContract]]] = {}
        node_ids_by_path = dict(node_index.value.active_path_to_node_id)
        for release in lineage.value:
            loaded = self._release_nodes(repo_root, release)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            release_nodes_by_id[release.release_id] = loaded.value
            self.runtime.decl_graph.ref_compatibility.prime_release_heads(
                resolution_context,
                repo_root,
                release=release,
                release_nodes=loaded.value,
            )
        baseline = self._resolve_release_baseline_from_lineage(
            repo_root,
            release_id=release_id,
            lineage=lineage.value,
            release_nodes_by_id=release_nodes_by_id,
            resolution_context=resolution_context,
        )
        if not baseline.ok or baseline.value is None:
            return self.runtime.foundation.fail(baseline.issues)

        latest_private_states: dict[tuple[str, str], str] = {}
        released_head_revisions: dict[tuple[str, str, str], int] = {}
        release_contracts: dict[tuple[str, str], NodeContract] = {}
        for release in lineage.value:
            for node, contract in release_nodes_by_id[release.release_id]:
                node_id = getattr(node, "node_id")
                node_path = getattr(node, "path")
                release_contracts[(release.release_id, node_id)] = contract
                for decl_name, revision_number in contract.decl_graph_head.items():
                    released_head_revisions[(release.release_id, node_id, decl_name)] = (
                        revision_number
                    )
                    revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
                        repo_root,
                        node_path=node_path,
                        name=decl_name,
                        revision=revision_number,
                    )
                    if not revision.ok or revision.value is None:
                        continue
                    key = (node_id, decl_name)
                    previous = latest_private_states.get(key)
                    if previous is None or _STATE_RANK[revision.value.state] > _STATE_RANK[
                        DeclState(previous)
                    ]:
                        latest_private_states[key] = revision.value.state.value

        return self.runtime.foundation.ok(
            RepoReleaseAuditContext(
                repo_root=repo_root,
                release_id=release_id,
                lineage=list(lineage.value),
                baseline=baseline.value,
                node_ids_by_path=node_ids_by_path,
                protected_by_key={
                    (item.node_id, item.decl_name): item
                    for item in baseline.value.protected_decl_views
                },
                latest_private_states=latest_private_states,
                released_head_revisions=released_head_revisions,
                release_contracts=release_contracts,
                decl_ref_context=resolution_context,
            )
        )

    def _audit_context_for_repo(
        self,
        repo_root: Path,
        audit_context: RepoReleaseAuditContext | None,
    ) -> ServiceResult[RepoReleaseAuditContext]:
        canonical_root = Path(repo_root).resolve()
        if audit_context is None:
            return self.create_release_audit_context(canonical_root)
        if audit_context.repo_root != canonical_root:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_audit_context_repo_mismatch",
                    "Release audit context belongs to a different repository.",
                    object_ref=str(canonical_root),
                    current=str(audit_context.repo_root),
                    expected=str(canonical_root),
                )
            )
        if audit_context.decl_ref_context is None:
            audit_context.decl_ref_context = (
                self.runtime.decl_graph.ref_compatibility.create_operation_context()
            )
        return self.runtime.foundation.ok(audit_context)

    def get_decl_release_status(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
    ) -> ServiceResult[DeclReleaseStatusView]:
        batch = self.get_decl_release_status_batch(
            repo_root,
            decls=[(node_path, decl_name)],
        )
        if not batch.ok or batch.value is None:
            return self.runtime.foundation.fail(batch.issues)
        return self.runtime.foundation.ok(batch.value[0], warnings=batch.issues)

    def get_decl_release_status_batch(
        self,
        repo_root: Path,
        *,
        decls: list[tuple[str, str]],
        audit_context: RepoReleaseAuditContext | None = None,
    ) -> ServiceResult[list[DeclReleaseStatusView]]:
        """Resolve statuses in order against one operation-local release lookup."""

        repo_root = Path(repo_root)
        if not decls:
            return self.runtime.foundation.ok([])
        context_result = self._audit_context_for_repo(repo_root, audit_context)
        if not context_result.ok or context_result.value is None:
            return self.runtime.foundation.fail(context_result.issues)
        context = context_result.value
        current_states: list[str] = []
        for node_path, decl_name in decls:
            current = self.runtime.decl_graph.decl_catalog.get_decl(
                repo_root,
                node_path=node_path,
                name=decl_name,
            )
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
            current_states.append(revision.value.state.value)

        if context.release_id is None:
            return self.runtime.foundation.ok(
                [
                    DeclReleaseStatusView(
                        current_state=current_state,
                        summary="Declaration has not appeared in a release.",
                    )
                    for current_state in current_states
                ]
            )

        values: list[DeclReleaseStatusView] = []
        for (node_path, decl_name), current_state in zip(
            decls,
            current_states,
            strict=True,
        ):
            node_id = context.node_ids_by_path.get(node_path)
            if node_id is None:
                node = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
                if not node.ok or node.value is None:
                    return self.runtime.foundation.fail(node.issues)
                node_id = node.value.node_id
            protected = context.protected_by_key.get((node_id, decl_name))
            released_state = (
                protected.released_state
                if protected is not None
                else context.latest_private_states.get((node_id, decl_name))
            )
            values.append(
                DeclReleaseStatusView(
                    current_state=current_state,
                    released_state=released_state,
                    release_protected=protected is not None,
                    summary=(
                        "Declaration is release protected."
                        if protected is not None
                        else "Declaration is not release protected."
                    ),
                )
            )
        return self.runtime.foundation.ok(values)

    def resolve_decl_release_protection(
        self,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        audit_context: RepoReleaseAuditContext | None = None,
    ) -> ServiceResult[tuple[ReleasedDeclProtectionView, DeclRevision] | None]:
        """Resolve one protected baseline revision from a caller-owned audit context."""

        repo_root = Path(repo_root)
        context_result = self._audit_context_for_repo(repo_root, audit_context)
        if not context_result.ok or context_result.value is None:
            return self.runtime.foundation.fail(context_result.issues)
        context = context_result.value
        if context.release_id is None:
            return self.runtime.foundation.ok(None)
        node_id = context.node_ids_by_path.get(node_path)
        if node_id is None:
            node = self.runtime.node.node_tree.get_node(repo_root, path=node_path)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(node.issues)
            node_id = node.value.node_id
        entry = context.protected_by_key.get((node_id, decl_name))
        if entry is None:
            return self.runtime.foundation.ok(None)
        revision_number = context.released_head_revisions.get(
            (entry.last_release_id, entry.node_id, decl_name)
        )
        if revision_number is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_decl_head_missing",
                    "Protected declaration is absent from its released head.",
                    object_ref=f"{node_path}:{decl_name}",
                )
            )
        revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
            repo_root,
            node_path=entry.node_path,
            name=decl_name,
            revision=revision_number,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.fail(revision.issues)
        return self.runtime.foundation.ok((entry, revision.value))

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
        release_nodes: list[tuple[NodeMetadata, NodeContract]] | None = None,
        resolution_context=None,
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
                release_nodes=release_nodes,
                resolution_context=resolution_context,
            )
        if release_nodes is None:
            nodes = self._release_nodes(repo_root, release)
            if not nodes.ok or nodes.value is None:
                return self.runtime.foundation.fail(nodes.issues)
            release_nodes = nodes.value
        if resolution_context is None:
            resolution_context = (
                self.runtime.decl_graph.ref_compatibility.create_operation_context()
            )
        self.runtime.decl_graph.ref_compatibility.prime_release_heads(
            resolution_context,
            repo_root,
            release=release,
            release_nodes=release_nodes,
        )
        by_path = {node.path: (node, contract) for node, contract in release_nodes}
        root = by_path.get("Main")
        if root is None or root[0].kind != NodeKind.SCOPE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("release_main_contract_missing", "Release does not contain a Main Scope contract.", object_ref=release.release_id)
            )
        queue = list(root[1].exports)
        seen: set[tuple[str, str]] = set()
        resolved_local: dict[
            tuple[str | None, str, str, int], ResolvedDeclRefView
        ] = {}
        resolved_external: dict[
            tuple[str | None, str, str, int], ResolvedDeclRefView
        ] = {}

        def ref_key(ref: DeclRef) -> tuple[str | None, str, str, int]:
            return (ref.repo, ref.node, ref.name, ref.revision)

        def prime_resolution(refs: list[DeclRef]) -> None:
            from lean_constellation.services.decl_graph.ref_compatibility import (
                RepoReleaseHeads,
            )

            local = [
                ref
                for ref in refs
                if ref.repo is None and ref_key(ref) not in resolved_local
            ]
            if local:
                resolved = self.runtime.decl_graph.ref_compatibility.resolve_decl_refs_batch(
                    repo_root,
                    refs=local,
                    required_availability=ProofAvailability.DECLARED,
                    target=RepoReleaseHeads(release_id=release.release_id),
                    operation_context=resolution_context,
                )
                if resolved.ok and resolved.value is not None:
                    resolved_local.update(
                        (ref_key(ref), value)
                        for ref, value in zip(local, resolved.value, strict=True)
                    )
            external = [
                ref
                for ref in refs
                if ref.repo is not None and ref_key(ref) not in resolved_external
            ]
            if external:
                resolved = self.runtime.decl_graph.ref_compatibility.resolve_public_decl_refs_batch(
                    repo_root,
                    refs=external,
                    required_availability=ProofAvailability.DECLARED,
                    operation_context=resolution_context,
                )
                if resolved.ok and resolved.value is not None:
                    resolved_external.update(
                        (ref_key(ref), value)
                        for ref, value in zip(external, resolved.value, strict=True)
                    )

        prime_resolution(queue)
        while queue:
            ref = queue.pop(0)
            if ref.repo is not None:
                cached = resolved_external.get(ref_key(ref))
                available = (
                    self.runtime.foundation.ok(cached)
                    if cached is not None
                    else self._resolve_public_release_ref_with_context(
                        repo_root,
                        ref=ref,
                        required_availability=ProofAvailability.DECLARED,
                        operation_context=resolution_context,
                    )
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
            cached = resolved_local.get(ref_key(ref))
            compatible = (
                self.runtime.foundation.ok(cached)
                if cached is not None
                else self._resolve_release_ref_with_context(
                    repo_root,
                    ref=ref,
                    release_id=release.release_id,
                    operation_context=resolution_context,
                )
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
                    resolution_context=resolution_context,
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
            statement_refs = [
                dep.ref
                for dep in revision.value.statement.deps
                if isinstance(dep, RepoDeclDep)
            ]
            prime_resolution(statement_refs)
            queue.extend(statement_refs)
        return self.runtime.foundation.ok(None)

    def _accumulate_adapter_release_closure(
        self,
        repo_root: Path,
        *,
        release: RepoRelease,
        protections: dict[tuple[str, str], ReleasedDeclProtectionView],
        protected_node_ids: set[str],
        protected_scope_paths: set[str],
        release_nodes: list[tuple[NodeMetadata, NodeContract]] | None = None,
        resolution_context=None,
    ) -> ServiceResult[None]:
        if release_nodes is None:
            nodes = self._release_nodes(repo_root, release)
            if not nodes.ok or nodes.value is None:
                return self.runtime.foundation.fail(nodes.issues)
            release_nodes = nodes.value
        if resolution_context is None:
            resolution_context = (
                self.runtime.decl_graph.ref_compatibility.create_operation_context()
            )
        self.runtime.decl_graph.ref_compatibility.prime_release_heads(
            resolution_context,
            repo_root,
            release=release,
            release_nodes=release_nodes,
        )
        if len(release_nodes) != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "adapter_release_main_contract_missing",
                    "Adapter release closure requires exactly one Main Scope contract.",
                    object_ref=release.release_id,
                )
            )
        main, contract = release_nodes[0]
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
        resolved_external: dict[
            tuple[str | None, str, str, int], ResolvedDeclRefView
        ] = {}

        def ref_key(ref: DeclRef) -> tuple[str | None, str, str, int]:
            return (ref.repo, ref.node, ref.name, ref.revision)

        def prime_external(refs: list[DeclRef]) -> None:
            pending = [
                ref
                for ref in refs
                if ref.repo is not None and ref_key(ref) not in resolved_external
            ]
            if not pending:
                return
            resolved = (
                self.runtime.decl_graph.ref_compatibility.resolve_public_decl_refs_batch(
                    repo_root,
                    refs=pending,
                    required_availability=ProofAvailability.DECLARED,
                    operation_context=resolution_context,
                )
            )
            if resolved.ok and resolved.value is not None:
                resolved_external.update(
                    (ref_key(ref), value)
                    for ref, value in zip(pending, resolved.value, strict=True)
                )

        prime_external(queue)
        while queue:
            ref = queue.pop(0)
            if ref.repo is not None:
                cached = resolved_external.get(ref_key(ref))
                available = (
                    self.runtime.foundation.ok(cached)
                    if cached is not None
                    else self._resolve_public_release_ref_with_context(
                        repo_root,
                        ref=ref,
                        required_availability=ProofAvailability.DECLARED,
                        operation_context=resolution_context,
                    )
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
            statement_refs = [
                dep.ref
                for dep in revision.value.statement.deps
                if isinstance(dep, RepoDeclDep)
            ]
            prime_external(statement_refs)
            queue.extend(statement_refs)
        return self.runtime.foundation.ok(None)

    def _validate_release_scope_chain(
        self,
        repo_root: Path,
        *,
        release: RepoRelease,
        by_path: dict[str, tuple[NodeMetadata, NodeContract]],
        ref,
        resolved_revision: int,
        resolution_context=None,
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
            resolved_batch = self.runtime.decl_graph.ref_compatibility.resolve_decl_refs_batch(
                repo_root,
                refs=matching,
                required_availability=ProofAvailability.DECLARED,
                target=RepoReleaseHeads(release_id=release.release_id),
                operation_context=resolution_context,
            )
            if resolved_batch.ok and resolved_batch.value is not None:
                valid = any(
                    resolved.compatible
                    and resolved.resolved_revision == resolved_revision
                    for resolved in resolved_batch.value
                )
            else:
                # Preserve the historical tolerant search semantics on malformed
                # alternatives; successful current release data takes the batch path.
                for candidate in matching:
                    resolved = self._resolve_release_ref_with_context(
                        repo_root,
                        ref=candidate,
                        release_id=release.release_id,
                        operation_context=resolution_context,
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

    def _resolve_release_ref_with_context(
        self,
        repo_root: Path,
        *,
        ref: DeclRef,
        release_id: str,
        operation_context,
    ) -> ServiceResult[ResolvedDeclRefView]:
        """Resolve one release ref through the batch core and its owned context."""

        from lean_constellation.services.decl_graph.ref_compatibility import (
            RepoReleaseHeads,
        )

        resolved = self.runtime.decl_graph.ref_compatibility.resolve_decl_refs_batch(
            repo_root,
            refs=[ref],
            required_availability=ProofAvailability.DECLARED,
            target=RepoReleaseHeads(release_id=release_id),
            operation_context=operation_context,
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        return self.runtime.foundation.ok(resolved.value[0], warnings=resolved.issues)

    def _resolve_public_release_ref_with_context(
        self,
        repo_root: Path,
        *,
        ref: DeclRef,
        required_availability: ProofAvailability,
        operation_context,
    ) -> ServiceResult[ResolvedDeclRefView]:
        """Resolve one public release ref through the shared batch core."""

        resolved = (
            self.runtime.decl_graph.ref_compatibility.resolve_public_decl_refs_batch(
                repo_root,
                refs=[ref],
                required_availability=required_availability,
                operation_context=operation_context,
            )
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        return self.runtime.foundation.ok(resolved.value[0], warnings=resolved.issues)

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

    def _ancestor_scopes(self, node_path: str) -> list[str]:
        parts = node_path.split(".")
        return [".".join(parts[:index]) for index in range(1, len(parts))]

    def _view(self, repo_root: Path, release: RepoRelease) -> RepoReleaseView:
        return RepoReleaseView(repo_root=str(repo_root), release=release, summary=f"Loaded repo release {release.release_id}.")


__all__ = ["RepoReleaseComponent"]
