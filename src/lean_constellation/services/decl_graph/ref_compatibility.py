"""Dynamic compatibility resolution for anchored declaration references."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeAlias, TypeVar, cast

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoFormat,
    proof_availability_satisfies,
)
from lean_constellation.domain.repo_release import (
    DeclAvailabilityEntry,
    RepoRelease,
    ResolvedDeclRefView,
)
from lean_constellation.services.decl_graph.declared_api import DeclaredApiFingerprintComponent
from lean_constellation.services.decl_graph.availability_policy import required_state_for_availability
from lean_constellation.services.decl_graph.models import DeclLifecycle, DeclRevisionStatus, DeclState
from lean_constellation.services.foundation import IssueSeverity, ServiceResult
from lean_constellation.services.node.node_tree import (
    NodeContract,
    NodeKind,
    NodeMetadata,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class CurrentContractHeads(StrictModel):
    kind: str = "current"


class RepoReleaseHeads(StrictModel):
    kind: str = "release"
    release_id: str


DeclRefTarget: TypeAlias = CurrentContractHeads | RepoReleaseHeads


_T = TypeVar("_T")


@dataclass
class _DeclRefResolutionContext:
    """Operation-local memo shared only by one resolver batch."""

    values: dict[tuple[object, ...], object] = field(default_factory=dict)

    def get(self, key: tuple[object, ...], loader: Callable[[], _T]) -> _T:
        if key not in self.values:
            self.values[key] = loader()
        return cast(_T, self.values[key])


_STATE_RANK = {
    DeclState.PLANNED: 0,
    DeclState.SPECIFIED: 1,
    DeclState.DECLARED: 2,
    DeclState.PROOF_PLANNED: 3,
    DeclState.PROVED: 4,
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
        resolved = self.resolve_decl_refs_batch(
            repo_root,
            refs=[ref],
            required_availability=required_availability,
            target=target,
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        return self.runtime.foundation.ok(resolved.value[0], warnings=resolved.issues)

    def resolve_decl_refs_batch(
        self,
        repo_root: Path,
        *,
        refs: list[DeclRef],
        required_availability: ProofAvailability,
        target: DeclRefTarget | None = None,
        operation_context: _DeclRefResolutionContext | None = None,
    ) -> ServiceResult[list[ResolvedDeclRefView]]:
        """Resolve refs in order with caches scoped to this call only."""

        context = operation_context or _DeclRefResolutionContext()
        values: list[ResolvedDeclRefView] = []
        warnings = []
        for ref in refs:
            resolved = self._resolve_decl_ref(
                context,
                Path(repo_root),
                ref=ref,
                required_availability=required_availability,
                target=target,
            )
            if not resolved.ok or resolved.value is None:
                return self.runtime.foundation.fail(resolved.issues)
            values.append(resolved.value)
            warnings.extend(resolved.issues)
        return self.runtime.foundation.ok(values, warnings=warnings)

    def _resolve_decl_ref(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        ref: DeclRef,
        required_availability: ProofAvailability,
        target: DeclRefTarget | None = None,
        enforce_mutable_availability: bool = True,
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
            available = self._provider_availability(context, target_repo)
            if not available.ok or available.value is None:
                return self.runtime.foundation.fail(available.issues)
            if not available.value.passed:
                return self.runtime.foundation.ok(
                    self._unresolved(ref, "provider_not_stable")
                )
            repo_format = self._repo_format(context, target_repo)
            if not repo_format.ok or repo_format.value is None:
                return self.runtime.foundation.fail(repo_format.issues)
            if isinstance(target, CurrentContractHeads):
                publication = self._repo_publication(context, target_repo)
                if not publication.ok or publication.value is None:
                    return self.runtime.foundation.fail(publication.issues)
                release_id = publication.value.publication.latest_release_id
                if release_id is None:
                    return self.runtime.foundation.ok(self._unresolved(ref, "provider_release_missing"))
                target = RepoReleaseHeads(release_id=release_id)

        try:
            anchor = self._decl_revision(
                context,
                target_repo,
                node_path=ref.node,
                name=ref.name,
                revision=ref.revision,
            )
        except ValueError:
            return self.runtime.foundation.ok(self._unresolved(ref, "anchor_missing"))
        if not anchor.ok or anchor.value is None or anchor.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.ok(self._unresolved(ref, "anchor_missing"))
        resolved_revision = self._target_revision(
            context,
            target_repo,
            ref=ref,
            target=target,
        )
        if not resolved_revision.ok:
            return self.runtime.foundation.fail(resolved_revision.issues)
        if resolved_revision.value is None:
            return self.runtime.foundation.ok(self._unresolved(ref, "target_missing"))
        resolved = self._decl_revision(
            context,
            target_repo,
            node_path=ref.node,
            name=ref.name,
            revision=resolved_revision.value,
        )
        if not resolved.ok or resolved.value is None or resolved.value.status != DeclRevisionStatus.COMMITTED:
            return self.runtime.foundation.ok(self._unresolved(ref, "target_missing"))
        if resolved_revision.value != ref.revision:
            anchor_fp = self._fingerprint(
                context,
                target_repo,
                node_path=ref.node,
                decl_name=ref.name,
                revision=ref.revision,
            )
            target_fp = self._fingerprint(
                context,
                target_repo,
                node_path=ref.node,
                decl_name=ref.name,
                revision=resolved_revision.value,
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
                or anchor_fp.value.lean_decl_name != target_fp.value.lean_decl_name
            ):
                return self.runtime.foundation.ok(self._unresolved(ref, "identity_changed"))
            if anchor_fp.value.sha256 != target_fp.value.sha256:
                return self.runtime.foundation.ok(self._unresolved(ref, "declared_api_changed"))
        if enforce_mutable_availability:
            current = self._decl(
                context,
                target_repo,
                node_path=ref.node,
                name=ref.name,
            )
            if not current.ok or current.value is None:
                return self.runtime.foundation.ok(self._unresolved(ref, "target_missing"))
            if current.value.lifecycle != DeclLifecycle.ACTIVE:
                return self.runtime.foundation.ok(self._unresolved(ref, "target_deleted"))
            floor = required_state_for_availability(
                current.value.kind,
                required_availability,
            )
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

        resolved = self.resolve_public_decl_refs_batch(
            consumer_repo_root,
            refs=[ref],
            required_availability=required_availability,
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        return self.runtime.foundation.ok(resolved.value[0], warnings=resolved.issues)

    def resolve_public_decl_refs_batch(
        self,
        consumer_repo_root: Path,
        *,
        refs: list[DeclRef],
        required_availability: ProofAvailability,
        operation_context: _DeclRefResolutionContext | None = None,
    ) -> ServiceResult[list[ResolvedDeclRefView]]:
        """Resolve external refs in order with one operation-local context."""

        context = operation_context or _DeclRefResolutionContext()
        values: list[ResolvedDeclRefView] = []
        warnings = []
        for ref in refs:
            resolved = self._resolve_public_decl_ref(
                context,
                Path(consumer_repo_root),
                ref=ref,
                required_availability=required_availability,
            )
            if not resolved.ok or resolved.value is None:
                return self.runtime.foundation.fail(resolved.issues)
            values.append(resolved.value)
            warnings.extend(resolved.issues)
        return self.runtime.foundation.ok(values, warnings=warnings)

    def create_operation_context(self) -> _DeclRefResolutionContext:
        """Create an unpersisted context for one trusted service operation."""

        return _DeclRefResolutionContext()

    def prime_release_heads(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        release: RepoRelease,
        release_nodes: list[tuple[NodeMetadata, NodeContract]],
    ) -> None:
        """Seed exact immutable release heads already loaded by an outer service."""

        repo_root = Path(repo_root)
        context.values[("release", repo_root, release.release_id)] = (
            self.runtime.foundation.ok(
                self.runtime.repo_workspace.release._view(repo_root, release)
            )
        )
        for node, contract in release_nodes:
            node_id = getattr(node, "node_id")
            context.values[("release_node", repo_root, node_id)] = (
                self.runtime.foundation.ok(node)
            )
            context.values[
                (
                    "release_contract",
                    repo_root,
                    node_id,
                    release.node_contract_versions[node_id],
                )
            ] = self.runtime.foundation.ok(contract)

    def _resolve_public_decl_ref(
        self,
        context: _DeclRefResolutionContext,
        consumer_repo_root: Path,
        *,
        ref: DeclRef,
        required_availability: ProofAvailability,
    ) -> ServiceResult[ResolvedDeclRefView]:

        if ref.repo is None:
            return self.runtime.foundation.ok(self._unresolved(ref, "external_repo_missing"))
        try:
            provider_root = Path(consumer_repo_root).parent / self.runtime.foundation.layout.ensure_safe_key(ref.repo)
        except ValueError:
            return self.runtime.foundation.ok(self._unresolved(ref, "external_repo_invalid"))
        available = self._provider_availability(context, provider_root)
        if not available.ok or available.value is None:
            return self.runtime.foundation.fail(available.issues)
        if not available.value.passed:
            return self.runtime.foundation.ok(self._unresolved(ref, "provider_not_stable"))
        repo_format = self._repo_format(context, provider_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        local_ref = ref.model_copy(update={"repo": None})
        boundary_context = self._public_boundary_context(
            context,
            provider_root,
            repo_format=repo_format.value.repo_format,
        )
        if not boundary_context.ok or boundary_context.value is None:
            return self.runtime.foundation.fail(boundary_context.issues)
        boundary_refs, target = boundary_context.value
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
            context,
            provider_root,
            repo_format=repo_format.value.repo_format,
            ref=boundary_ref,
            required_availability=required_availability,
            target=target,
        )
        requested = self._resolve_public_local_ref(
            context,
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
            failed = requested.value if not requested.value.compatible else boundary.value
            if failed.reason == "state_too_low":
                return self.runtime.foundation.ok(
                    failed.model_copy(update={"anchor": ref})
                )
            reason = failed.reason
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
        resolution_context = _DeclRefResolutionContext()
        available = self._provider_availability(resolution_context, provider_repo_root)
        if not available.ok or available.value is None:
            return self.runtime.foundation.fail(available.issues)
        if not available.value.passed:
            return self.runtime.foundation.ok(
                [],
                warnings=[
                    issue.model_copy(update={"severity": IssueSeverity.WARNING})
                    for issue in available.value.issues
                ],
            )
        repo_format = self._repo_format(resolution_context, provider_repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        boundary_context = self._public_boundary_context(
            resolution_context,
            provider_repo_root,
            repo_format=repo_format.value.repo_format,
        )
        if not boundary_context.ok or boundary_context.value is None:
            return self.runtime.foundation.fail(boundary_context.issues)
        refs, target = boundary_context.value
        return self._resolve_public_boundary_refs(
            resolution_context,
            provider_repo_root,
            repo_format=repo_format.value.repo_format,
            refs=refs,
            required_availability=required_availability,
            target=target,
        )

    def list_current_public_decl_refs(
        self,
        provider_repo_root: Path,
        *,
        required_availability: ProofAvailability,
    ) -> ServiceResult[list[ResolvedDeclRefView]]:
        """Enumerate the format-aware Main boundary before stable publication."""

        provider_repo_root = Path(provider_repo_root)
        context = _DeclRefResolutionContext()
        repo_format = self._repo_format(context, provider_repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if repo_format.value.repo_format == RepoFormat.ADAPTER:
            current = self._current_contract(context, provider_repo_root, node_path="Main")
            if not current.ok or current.value is None:
                return self.runtime.foundation.fail(current.issues)
            return self._resolve_public_boundary_refs(
                context,
                provider_repo_root,
                repo_format=repo_format.value.repo_format,
                refs=list(current.value.contract.exports),
                required_availability=required_availability,
                target=None,
            )
        if repo_format.value.repo_format != RepoFormat.NATIVE:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_format_unsupported",
                    "Only native and adapter repos expose a public declaration boundary.",
                    object_ref=str(provider_repo_root),
                    current=repo_format.value.repo_format.value,
                )
            )
        exports = self.runtime.node.export.list_scope_exports(
            provider_repo_root,
            scope_path="Main",
        )
        if not exports.ok or exports.value is None:
            return self.runtime.foundation.fail(exports.issues)
        return self.runtime.foundation.ok(
            [
                ResolvedDeclRefView(
                    anchor=item.ref,
                    resolved_revision=item.resolved_revision or item.ref.revision,
                    compatible=item.valid,
                    reason=(
                        None
                        if item.valid
                        else "; ".join(issue.kind for issue in item.issues)
                    ),
                )
                for item in exports.value
            ]
        )

    def _resolve_public_boundary_refs(
        self,
        context: _DeclRefResolutionContext,
        provider_repo_root: Path,
        *,
        repo_format: RepoFormat,
        refs: list[DeclRef],
        required_availability: ProofAvailability,
        target: RepoReleaseHeads | None,
    ) -> ServiceResult[list[ResolvedDeclRefView]]:
        values: list[ResolvedDeclRefView] = []
        for boundary_ref in refs:
            resolved = self._resolve_public_local_ref(
                context,
                provider_repo_root,
                repo_format=repo_format,
                ref=boundary_ref,
                required_availability=required_availability,
                target=target,
            )
            if not resolved.ok or resolved.value is None:
                return self.runtime.foundation.fail(resolved.issues)
            values.append(resolved.value)
        return self.runtime.foundation.ok(values)

    def list_public_interface_bindings(
        self,
        provider_repo_root: Path,
        *,
        require_stable: bool,
    ) -> ServiceResult[dict[str, DeclRef | None]]:
        """Read Main interface bindings from the same current or released public boundary."""

        provider_repo_root = Path(provider_repo_root)
        context = _DeclRefResolutionContext()
        repo_format = self._repo_format(context, provider_repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if require_stable and repo_format.value.repo_format in {
            RepoFormat.NATIVE,
            RepoFormat.ADAPTER,
        }:
            released = self._released_main_contract(context, provider_repo_root)
            if not released.ok or released.value is None:
                return self.runtime.foundation.fail(released.issues)
            contract = released.value[0]
        else:
            current = self._current_contract(context, provider_repo_root, node_path="Main")
            if not current.ok or current.value is None:
                return self.runtime.foundation.fail(current.issues)
            contract = current.value.contract
        return self.runtime.foundation.ok(
            {interface.name: interface.bound_decl for interface in contract.interfaces}
        )

    def _public_boundary_context(
        self,
        context: _DeclRefResolutionContext,
        provider_repo_root: Path,
        *,
        repo_format: RepoFormat,
    ) -> ServiceResult[tuple[list[DeclRef], RepoReleaseHeads | None]]:
        if repo_format not in {RepoFormat.NATIVE, RepoFormat.ADAPTER}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_format_unsupported",
                    "Only native and adapter repos expose a public declaration boundary.",
                    object_ref=str(provider_repo_root),
                    current=repo_format.value,
                )
            )
        key = (
            "public_boundary",
            provider_repo_root,
            repo_format.value,
        )
        return context.get(
            key,
            lambda: self._load_public_boundary_context(
                context,
                provider_repo_root,
                repo_format=repo_format,
            ),
        )

    def _load_public_boundary_context(
        self,
        context: _DeclRefResolutionContext,
        provider_repo_root: Path,
        *,
        repo_format: RepoFormat,
    ) -> ServiceResult[tuple[list[DeclRef], RepoReleaseHeads | None]]:
        released = self._released_main_contract(context, provider_repo_root)
        if not released.ok or released.value is None:
            return self.runtime.foundation.fail(released.issues)
        contract, target = released.value
        return self.runtime.foundation.ok((list(contract.exports), target))

    def _released_main_contract(
        self,
        context: _DeclRefResolutionContext,
        provider_repo_root: Path,
    ) -> ServiceResult[tuple[NodeContract, RepoReleaseHeads]]:
        publication = self._repo_publication(context, provider_repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        release_id = publication.value.publication.latest_release_id
        if release_id is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_release_missing",
                    "Stable provider public boundary requires a published release.",
                    object_ref=str(provider_repo_root),
                )
            )
        release = self._release(context, provider_repo_root, release_id=release_id)
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(release.issues)
        for node_id, version in release.value.release.node_contract_versions.items():
            node = self._release_node(context, provider_repo_root, node_id=node_id)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(node.issues)
            if node.value.path != "Main" or node.value.kind != NodeKind.SCOPE:
                continue
            loaded = self._release_contract(
                context,
                provider_repo_root,
                node_id=node_id,
                version=version,
            )
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            return self.runtime.foundation.ok(
                (loaded.value, RepoReleaseHeads(release_id=release_id))
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
        context: _DeclRefResolutionContext,
        provider_repo_root: Path,
        *,
        repo_format: RepoFormat,
        ref: DeclRef,
        required_availability: ProofAvailability,
        target: RepoReleaseHeads | None,
    ) -> ServiceResult[ResolvedDeclRefView]:
        if repo_format == RepoFormat.ADAPTER and target is None:
            return self._resolve_adapter_anchor(
                context,
                provider_repo_root,
                ref=ref,
                required_availability=required_availability,
            )
        assert target is not None
        identity = self._resolve_decl_ref(
            context,
            provider_repo_root,
            ref=ref,
            required_availability=required_availability,
            target=target,
            enforce_mutable_availability=False,
        )
        if not identity.ok or identity.value is None:
            return self.runtime.foundation.fail(identity.issues)
        if not identity.value.compatible or identity.value.resolved_revision is None:
            return identity
        availability = self._release_decl_availability(
            context,
            provider_repo_root,
            release_id=target.release_id,
            ref=ref,
            resolved_revision=identity.value.resolved_revision,
        )
        if not availability.ok:
            return self.runtime.foundation.fail(availability.issues)
        if availability.value is None:
            return self.runtime.foundation.ok(
                ResolvedDeclRefView(
                    anchor=ref,
                    resolved_revision=identity.value.resolved_revision,
                    compatible=False,
                    reason="provider_release_decl_availability_missing",
                )
            )
        entry = availability.value
        compatible = proof_availability_satisfies(
            entry.availability,
            required_availability,
        )
        return self.runtime.foundation.ok(
            identity.value.model_copy(
                update={
                    "compatible": compatible,
                    "current_state": entry.decl_state,
                    "reason": identity.value.reason if compatible else "state_too_low",
                }
            )
        )

    def _release_decl_availability(
        self,
        context: _DeclRefResolutionContext,
        provider_repo_root: Path,
        *,
        release_id: str,
        ref: DeclRef,
        resolved_revision: int,
    ) -> ServiceResult[DeclAvailabilityEntry | None]:
        return context.get(
            (
                "release_decl_availability",
                provider_repo_root,
                release_id,
                ref.node,
                ref.name,
                resolved_revision,
            ),
            lambda: self.runtime.repo_workspace.release.lookup_decl_availability(
                provider_repo_root,
                release_id=release_id,
                node_path=ref.node,
                decl_name=ref.name,
                revision=resolved_revision,
            ),
        )

    def _target_revision(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        ref: DeclRef,
        target: DeclRefTarget,
    ) -> ServiceResult[int | None]:
        target_key = (
            target.kind,
            target.release_id if isinstance(target, RepoReleaseHeads) else None,
        )
        return context.get(
            (
                "target_revision",
                repo_root,
                target_key,
                ref.node,
                ref.name,
            ),
            lambda: self._load_target_revision(
                context,
                repo_root,
                ref=ref,
                target=target,
            ),
        )

    def _load_target_revision(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        ref: DeclRef,
        target: DeclRefTarget,
    ) -> ServiceResult[int | None]:
        repo_format = self._repo_format(context, repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if isinstance(target, CurrentContractHeads):
            if repo_format.value.repo_format == RepoFormat.ADAPTER:
                main = self._current_contract(context, repo_root, node_path="Main")
                if not main.ok or main.value is None:
                    return self.runtime.foundation.fail(main.issues)
                match = next(
                    (
                        item.revision
                        for item in main.value.contract.exports
                        if item.repo is None
                        and item.node == ref.node
                        and item.name == ref.name
                    ),
                    None,
                )
                return self.runtime.foundation.ok(match)
            node = self._node(context, repo_root, node_path=ref.node)
            if not node.ok or node.value is None or node.value.kind != NodeKind.CONTENT:
                return self.runtime.foundation.ok(None)
            contract = self._visible_contract(context, repo_root, node_path=ref.node)
            if not contract.ok or contract.value is None:
                return self.runtime.foundation.ok(None)
            return self.runtime.foundation.ok(contract.value.contract.decl_graph_head.get(ref.name))
        release = self._release(context, repo_root, release_id=target.release_id)
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(release.issues)
        for node_id, version in release.value.release.node_contract_versions.items():
            node = self._release_node(context, repo_root, node_id=node_id)
            if not node.ok or node.value is None:
                return self.runtime.foundation.fail(node.issues)
            contract = self._release_contract(
                context,
                repo_root,
                node_id=node_id,
                version=version,
            )
            if not contract.ok or contract.value is None:
                return self.runtime.foundation.fail(contract.issues)
            if repo_format.value.repo_format == RepoFormat.ADAPTER:
                if node.value.path != "Main" or node.value.kind != NodeKind.SCOPE:
                    continue
                return self.runtime.foundation.ok(
                    next(
                        (
                            item.revision
                            for item in contract.value.exports
                            if item.repo is None
                            and item.node == ref.node
                            and item.name == ref.name
                        ),
                        None,
                    )
                )
            if node.value.path != ref.node or node.value.kind != NodeKind.CONTENT:
                continue
            return self.runtime.foundation.ok(contract.value.decl_graph_head.get(ref.name))
        return self.runtime.foundation.ok(None)

    def _resolve_adapter_anchor(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        ref: DeclRef,
        required_availability: ProofAvailability,
    ) -> ServiceResult[ResolvedDeclRefView]:
        revision = self._decl_revision(
            context,
            repo_root,
            node_path=ref.node,
            name=ref.name,
            revision=ref.revision,
        )
        if not revision.ok or revision.value is None:
            return self.runtime.foundation.ok(self._unresolved(ref, "anchor_missing"))
        decl = self._decl(context, repo_root, node_path=ref.node, name=ref.name)
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

    def _provider_availability(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
    ) -> ServiceResult:
        return context.get(
            ("provider_availability", repo_root),
            lambda: self.runtime.repo_workspace.provider_availability.check_provider_available(repo_root),
        )

    def _repo_format(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
    ) -> ServiceResult:
        return context.get(
            ("repo_format", repo_root),
            lambda: self.runtime.repo_workspace.metadata.get_repo_format(repo_root),
        )

    def _repo_publication(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
    ) -> ServiceResult:
        return context.get(
            ("repo_publication", repo_root),
            lambda: self.runtime.repo_workspace.metadata.get_repo_publication(repo_root),
        )

    def _release(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        release_id: str,
    ) -> ServiceResult:
        return context.get(
            ("release", repo_root, release_id),
            lambda: self.runtime.repo_workspace.release.get_release(
                repo_root,
                release_id=release_id,
            ),
        )

    def _release_node(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        node_id: str,
    ) -> ServiceResult:
        return context.get(
            ("release_node", repo_root, node_id),
            lambda: self.runtime.node.node_tree.node_store.load_node_by_id(
                repo_root,
                node_id=node_id,
            ),
        )

    def _release_contract(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        node_id: str,
        version: int,
    ) -> ServiceResult:
        return context.get(
            ("release_contract", repo_root, node_id, version),
            lambda: self.runtime.repo_workspace.release._load_contract(
                repo_root,
                node_id=node_id,
                version=version,
            ),
        )

    def _current_contract(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        node_path: str,
    ) -> ServiceResult:
        return context.get(
            ("current_contract", repo_root, node_path),
            lambda: self.runtime.node.contract.get_current_contract(
                repo_root,
                node_path=node_path,
            ),
        )

    def _visible_contract(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        node_path: str,
    ) -> ServiceResult:
        return context.get(
            ("visible_contract", repo_root, node_path),
            lambda: self.runtime.node.contract.get_visible_contract(
                repo_root,
                node_path=node_path,
            ),
        )

    def _node(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        node_path: str,
    ) -> ServiceResult:
        return context.get(
            ("node", repo_root, node_path),
            lambda: self.runtime.node.node_tree.get_node(repo_root, path=node_path),
        )

    def _decl(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
    ) -> ServiceResult:
        return context.get(
            ("decl", repo_root, node_path, name),
            lambda: self.runtime.decl_graph.decl_catalog.get_decl(
                repo_root,
                node_path=node_path,
                name=name,
            ),
        )

    def _decl_revision(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        node_path: str,
        name: str,
        revision: int,
    ) -> ServiceResult:
        return context.get(
            ("decl_revision", repo_root, node_path, name, revision),
            lambda: self.runtime.decl_graph.decl_catalog.get_decl_revision(
                repo_root,
                node_path=node_path,
                name=name,
                revision=revision,
            ),
        )

    def _fingerprint(
        self,
        context: _DeclRefResolutionContext,
        repo_root: Path,
        *,
        node_path: str,
        decl_name: str,
        revision: int,
    ) -> ServiceResult:
        return context.get(
            ("declared_api_fingerprint", repo_root, node_path, decl_name, revision),
            lambda: self.fingerprint.fingerprint(
                repo_root,
                node_path=node_path,
                decl_name=decl_name,
                revision=revision,
            ),
        )

    def _unresolved(self, ref: DeclRef, reason: str) -> ResolvedDeclRefView:
        return ResolvedDeclRefView(anchor=ref, compatible=False, reason=reason)


__all__ = [
    "CurrentContractHeads",
    "DeclRefCompatibilityComponent",
    "DeclRefTarget",
    "RepoReleaseHeads",
]
