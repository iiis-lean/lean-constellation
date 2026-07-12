"""Dynamic compatibility resolution for anchored declaration references."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability, RepoFormat
from lean_constellation.domain.repo_release import ResolvedDeclRefView
from lean_constellation.services.decl_graph.declared_api import DeclaredApiFingerprintComponent
from lean_constellation.services.decl_graph.availability_policy import required_state_for_availability
from lean_constellation.services.decl_graph.models import DeclLifecycle, DeclRevisionStatus, DeclState
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.node.node_tree import NodeKind

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class CurrentContractHeads(StrictModel):
    kind: str = "current"


class RepoReleaseHeads(StrictModel):
    kind: str = "release"
    release_id: str


DeclRefTarget: TypeAlias = CurrentContractHeads | RepoReleaseHeads


_STATE_RANK = {
    DeclState.PLANNED: 0,
    DeclState.SPECIFIED: 1,
    DeclState.DECLARED: 2,
    DeclState.PROOF_PLANNED: 3,
    DeclState.PROVED: 4,
    DeclState.OBSOLETE: -1,
}


class DeclRefCompatibilityComponent:
    """Resolve an immutable anchor ref against current or released heads."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        fingerprint: DeclaredApiFingerprintComponent,
    ) -> None:
        self.runtime = runtime
        self.fingerprint = fingerprint

    def resolve_decl_ref(
        self,
        repo_root: Path,
        *,
        ref: DeclRef,
        required_availability: ProofAvailability,
        target: DeclRefTarget | None = None,
    ) -> ServiceResult[ResolvedDeclRefView]:
        repo_root = Path(repo_root)
        target = target or CurrentContractHeads()
        target_repo = repo_root
        if ref.repo is not None:
            try:
                target_repo = repo_root.parent / self.runtime.foundation.layout.ensure_safe_key(ref.repo)
            except ValueError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "decl_ref_repo_invalid", str(exc), object_ref=ref.repo
                    )
                )
            available = self.runtime.repo_workspace.provider_availability.check_provider_available(target_repo)
            if not available.ok or available.value is None:
                return self.runtime.foundation.fail(available.issues)
            if not available.value.passed:
                return self.runtime.foundation.ok(
                    self._unresolved(ref, "provider_not_stable")
                )
            repo_format = self.runtime.repo_workspace.metadata.get_repo_format(target_repo)
            if not repo_format.ok or repo_format.value is None:
                return self.runtime.foundation.fail(repo_format.issues)
            if repo_format.value.repo_format == RepoFormat.ADAPTER:
                return self._resolve_adapter_anchor(target_repo, ref=ref, required_availability=required_availability)
            if isinstance(target, CurrentContractHeads):
                publication = self.runtime.repo_workspace.metadata.get_repo_publication(target_repo)
                if not publication.ok or publication.value is None:
                    return self.runtime.foundation.fail(publication.issues)
                release_id = publication.value.publication.latest_release_id
                if release_id is None:
                    return self.runtime.foundation.ok(self._unresolved(ref, "provider_release_missing"))
                target = RepoReleaseHeads(release_id=release_id)

        try:
            anchor = self.runtime.decl_graph.decl_catalog.get_decl_revision(
                target_repo, node_path=ref.node, name=ref.name, revision=ref.revision
            )
        except ValueError:
            return self.runtime.foundation.ok(self._unresolved(ref, "anchor_missing"))
        if not anchor.ok or anchor.value is None or anchor.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.ok(self._unresolved(ref, "anchor_missing"))
        resolved_revision = self._target_revision(target_repo, ref=ref, target=target)
        if not resolved_revision.ok:
            return self.runtime.foundation.fail(resolved_revision.issues)
        if resolved_revision.value is None:
            return self.runtime.foundation.ok(self._unresolved(ref, "target_missing"))
        current = self.runtime.decl_graph.decl_catalog.get_decl(target_repo, node_path=ref.node, name=ref.name)
        if not current.ok or current.value is None:
            return self.runtime.foundation.ok(self._unresolved(ref, "target_missing"))
        if current.value.lifecycle != DeclLifecycle.ACTIVE:
            return self.runtime.foundation.ok(self._unresolved(ref, "target_deleted"))
        resolved = self.runtime.decl_graph.decl_catalog.get_decl_revision(
            target_repo, node_path=ref.node, name=ref.name, revision=resolved_revision.value
        )
        if not resolved.ok or resolved.value is None or resolved.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.ok(self._unresolved(ref, "target_missing"))
        if resolved_revision.value != ref.revision:
            anchor_fp = self.fingerprint.fingerprint(
                target_repo, node_path=ref.node, decl_name=ref.name, revision=ref.revision
            )
            target_fp = self.fingerprint.fingerprint(
                target_repo, node_path=ref.node, decl_name=ref.name, revision=resolved_revision.value
            )
            if not anchor_fp.ok or anchor_fp.value is None:
                return self.runtime.foundation.ok(self._unresolved(ref, "anchor_missing"))
            if not target_fp.ok or target_fp.value is None:
                return self.runtime.foundation.ok(self._unresolved(ref, "target_missing"))
            if (
                anchor_fp.value.node_id != target_fp.value.node_id
                or anchor_fp.value.node_path != target_fp.value.node_path
                or anchor_fp.value.decl_name != target_fp.value.decl_name
                or anchor_fp.value.decl_kind != target_fp.value.decl_kind
                or anchor_fp.value.module != target_fp.value.module
            ):
                return self.runtime.foundation.ok(self._unresolved(ref, "identity_changed"))
            if anchor_fp.value.sha256 != target_fp.value.sha256:
                return self.runtime.foundation.ok(self._unresolved(ref, "declared_api_changed"))
        floor = required_state_for_availability(current.value.kind, required_availability)
        if _STATE_RANK[resolved.value.state] < _STATE_RANK[floor]:
            return self.runtime.foundation.ok(
                ResolvedDeclRefView(
                    anchor=ref,
                    resolved_revision=resolved_revision.value,
                    compatible=False,
                    current_state=resolved.value.state.value,
                    reason="state_too_low",
                )
            )
        return self.runtime.foundation.ok(
            ResolvedDeclRefView(
                anchor=ref,
                resolved_revision=resolved_revision.value,
                compatible=True,
                current_state=resolved.value.state.value,
                reason=("exact_revision" if resolved_revision.value == ref.revision else "compatible_revision"),
            )
        )

    def resolve_public_decl_ref(
        self,
        consumer_repo_root: Path,
        *,
        ref: DeclRef,
        required_availability: ProofAvailability,
    ) -> ServiceResult[ResolvedDeclRefView]:
        """Resolve an external ref only when it crosses the provider's public Main boundary."""

        if ref.repo is None:
            return self.runtime.foundation.ok(self._unresolved(ref, "external_repo_missing"))
        try:
            provider_root = Path(consumer_repo_root).parent / self.runtime.foundation.layout.ensure_safe_key(ref.repo)
        except ValueError:
            return self.runtime.foundation.ok(self._unresolved(ref, "external_repo_invalid"))
        available = self.runtime.repo_workspace.provider_availability.check_provider_available(provider_root)
        if not available.ok or available.value is None:
            return self.runtime.foundation.fail(available.issues)
        if not available.value.passed:
            return self.runtime.foundation.ok(self._unresolved(ref, "provider_not_stable"))
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(provider_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        local_ref = ref.model_copy(update={"repo": None})
        context = self._public_boundary_context(provider_root, repo_format=repo_format.value.repo_format)
        if not context.ok or context.value is None:
            return self.runtime.foundation.fail(context.issues)
        boundary_refs, target = context.value
        boundary_ref = next(
            (
                candidate
                for candidate in boundary_refs
                if candidate.node == local_ref.node and candidate.name == local_ref.name
            ),
            None,
        )
        if boundary_ref is None:
            return self.runtime.foundation.ok(self._unresolved(ref, "provider_decl_not_exported"))
        boundary = self._resolve_public_local_ref(
            provider_root,
            repo_format=repo_format.value.repo_format,
            ref=boundary_ref,
            required_availability=required_availability,
            target=target,
        )
        requested = self._resolve_public_local_ref(
            provider_root,
            repo_format=repo_format.value.repo_format,
            ref=local_ref,
            required_availability=required_availability,
            target=target,
        )
        if not boundary.ok or boundary.value is None:
            return self.runtime.foundation.fail(boundary.issues)
        if not requested.ok or requested.value is None:
            return self.runtime.foundation.fail(requested.issues)
        if (
            not boundary.value.compatible
            or not requested.value.compatible
            or boundary.value.resolved_revision != requested.value.resolved_revision
        ):
            reason = requested.value.reason if not requested.value.compatible else boundary.value.reason
            return self.runtime.foundation.ok(self._unresolved(ref, reason or "provider_public_ref_incompatible"))
        return self.runtime.foundation.ok(
            requested.value.model_copy(update={"anchor": ref})
        )

    def list_public_decl_refs(
        self,
        provider_repo_root: Path,
        *,
        required_availability: ProofAvailability,
    ) -> ServiceResult[list[ResolvedDeclRefView]]:
        """Enumerate the format-aware Main public boundary of a stable provider."""

        provider_repo_root = Path(provider_repo_root)
        available = self.runtime.repo_workspace.provider_availability.check_provider_available(provider_repo_root)
        if not available.ok or available.value is None:
            return self.runtime.foundation.fail(available.issues)
        if not available.value.passed:
            return self.runtime.foundation.ok([], warnings=available.value.issues)
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(provider_repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        context = self._public_boundary_context(
            provider_repo_root,
            repo_format=repo_format.value.repo_format,
        )
        if not context.ok or context.value is None:
            return self.runtime.foundation.fail(context.issues)
        refs, target = context.value
        values: list[ResolvedDeclRefView] = []
        for boundary_ref in refs:
            resolved = self._resolve_public_local_ref(
                provider_repo_root,
                repo_format=repo_format.value.repo_format,
                ref=boundary_ref,
                required_availability=required_availability,
                target=target,
            )
            if not resolved.ok or resolved.value is None:
                return self.runtime.foundation.fail(resolved.issues)
            values.append(resolved.value)
        return self.runtime.foundation.ok(values)

    def _public_boundary_context(
        self,
        provider_repo_root: Path,
        *,
        repo_format: RepoFormat,
    ) -> ServiceResult[tuple[list[DeclRef], RepoReleaseHeads | None]]:
        if repo_format == RepoFormat.ADAPTER:
            main = self.runtime.node.contract.get_current_contract(provider_repo_root, node_path="Main")
            if not main.ok or main.value is None:
                return self.runtime.foundation.fail(main.issues)
            return self.runtime.foundation.ok(
                ([item.bound_decl for item in main.value.contract.interfaces if item.bound_decl is not None], None)
            )
        if repo_format != RepoFormat.NATIVE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_format_unsupported",
                    "Only native and adapter repos expose a public declaration boundary.",
                    object_ref=str(provider_repo_root),
                    current=repo_format.value,
                )
            )
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(provider_repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        release_id = publication.value.publication.latest_release_id
        if release_id is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_release_missing",
                    "Native provider public boundary requires a published release.",
                    object_ref=str(provider_repo_root),
                )
            )
        release = self.runtime.repo_workspace.release.get_release(provider_repo_root, release_id=release_id)
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(release.issues)
        for node_id, version in release.value.release.node_contract_versions.items():
            node = self.runtime.node.node_tree.node_store.load_node_by_id(provider_repo_root, node_id=node_id)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(node.issues)
            if node.value.path != "Main" or node.value.kind != NodeKind.SCOPE:
                continue
            loaded = self.runtime.repo_workspace.release._load_contract(
                provider_repo_root, node_id=node_id, version=version
            )
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            return self.runtime.foundation.ok(
                (list(loaded.value.exports), RepoReleaseHeads(release_id=release_id))
            )
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "provider_public_boundary_missing",
                "Provider release does not contain a Main Scope contract.",
                object_ref=release_id,
            )
        )

    def _resolve_public_local_ref(
        self,
        provider_repo_root: Path,
        *,
        repo_format: RepoFormat,
        ref: DeclRef,
        required_availability: ProofAvailability,
        target: RepoReleaseHeads | None,
    ) -> ServiceResult[ResolvedDeclRefView]:
        if repo_format == RepoFormat.ADAPTER:
            return self._resolve_adapter_anchor(
                provider_repo_root,
                ref=ref,
                required_availability=required_availability,
            )
        assert target is not None
        return self.resolve_decl_ref(
            provider_repo_root,
            ref=ref,
            required_availability=required_availability,
            target=target,
        )

    def _target_revision(
        self,
        repo_root: Path,
        *,
        ref: DeclRef,
        target: DeclRefTarget,
    ) -> ServiceResult[int | None]:
        if isinstance(target, CurrentContractHeads):
            node = self.runtime.node.node_tree.get_node(repo_root, path=ref.node)
            if not node.ok or node.value is None or node.value.kind != NodeKind.CONTENT:
                return self.runtime.foundation.ok(None)
            contract = self.runtime.node.contract.get_visible_contract(repo_root, node_path=ref.node)
            if not contract.ok or contract.value is None:
                return self.runtime.foundation.ok(None)
            return self.runtime.foundation.ok(contract.value.contract.decl_graph_head.get(ref.name))
        release = self.runtime.repo_workspace.release.get_release(repo_root, release_id=target.release_id)
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(release.issues)
        for node_id, version in release.value.release.node_contract_versions.items():
            node = self.runtime.node.node_tree.node_store.load_node_by_id(repo_root, node_id=node_id)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(node.issues)
            if node.value.path != ref.node or node.value.kind != NodeKind.CONTENT:
                continue
            contract = self.runtime.repo_workspace.release._load_contract(repo_root, node_id=node_id, version=version)
            if not contract.ok or contract.value is None:
                return self.runtime.foundation.fail(contract.issues)
            return self.runtime.foundation.ok(contract.value.decl_graph_head.get(ref.name))
        return self.runtime.foundation.ok(None)

    def _resolve_adapter_anchor(
        self,
        repo_root: Path,
        *,
        ref: DeclRef,
        required_availability: ProofAvailability,
    ) -> ServiceResult[ResolvedDeclRefView]:
        revision = self.runtime.decl_graph.decl_catalog.get_decl_revision(
            repo_root, node_path=ref.node, name=ref.name, revision=ref.revision
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.ok(self._unresolved(ref, "anchor_missing"))
        decl = self.runtime.decl_graph.decl_catalog.get_decl(repo_root, node_path=ref.node, name=ref.name)
        if not decl.ok or decl.value is None or decl.value.lifecycle != DeclLifecycle.ACTIVE:
            return self.runtime.foundation.ok(self._unresolved(ref, "target_missing"))
        floor = required_state_for_availability(decl.value.kind, required_availability)
        compatible = _STATE_RANK[revision.value.state] >= _STATE_RANK[floor]
        return self.runtime.foundation.ok(
            ResolvedDeclRefView(
                anchor=ref,
                resolved_revision=ref.revision,
                compatible=compatible,
                current_state=revision.value.state.value,
                reason="exact_revision" if compatible else "state_too_low",
            )
        )

    def _unresolved(self, ref: DeclRef, reason: str) -> ResolvedDeclRefView:
        return ResolvedDeclRefView(anchor=ref, compatible=False, reason=reason)


__all__ = [
    "CurrentContractHeads",
    "DeclRefCompatibilityComponent",
    "DeclRefTarget",
    "RepoReleaseHeads",
]
