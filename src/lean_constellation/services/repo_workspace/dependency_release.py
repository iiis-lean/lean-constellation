"""Transactional exact Git dependency changes and lightweight Releases."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import uuid4

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.repo import RepoPublicationState, RepoPublicationStatus
from lean_constellation.domain.repo_release import (
    RepoDependencyChangeKind,
    RepoDependencyReleaseChange,
    RepoRelease,
    RepoReleaseKind,
    RepoReleaseValidationProfile,
)
from lean_constellation.services.external_clients import ToolchainCommandView
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.validation_snapshot.release_finalizer import (
    PreparedRepoReleaseView,
    RepoReleaseFinalizeView,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class DependencyReleaseMode(StrEnum):
    DEFER = "defer"
    DEPENDENCY_MAINTENANCE = "dependency_maintenance"


class RepoDependencyChangePreview(StrictModel):
    schema_version: Literal[1] = 1
    consumer_repo_key: str
    provider_repo_key: str
    target_provider_release_id: str
    target_provider_commit: str
    target_git_url: str
    change: RepoDependencyReleaseChange
    release_mode: DependencyReleaseMode
    validation_profile: RepoReleaseValidationProfile
    expected_consumer_head: str | None = None
    expected_base_release_id: str | None = None
    expected_semantic_manifest_digest: str
    expected_dependency_lock_digest: str
    expected_lakefile_digest: str
    recovery_token: str
    summary: str


class RepoDependencyChangeReceipt(StrictModel):
    schema_version: Literal[1] = 1
    change_id: str
    change: RepoDependencyReleaseChange
    release_mode: DependencyReleaseMode
    validation_profile: RepoReleaseValidationProfile
    targeted_update_summary: str
    build_summary: str
    finalized_release_id: str | None = None
    finalized_commit: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    summary: str


class RepoDependencyReleaseComponent:
    """Apply one allowlisted Lake dependency mutation with fail-closed CAS."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def preview(
        self,
        consumer_repo_root: Path,
        *,
        provider_repo_key: str,
        target_provider_release_id: str,
        target_git_url: str,
        release_mode: DependencyReleaseMode | str,
        validation_profile: RepoReleaseValidationProfile | str = (
            RepoReleaseValidationProfile.DEPENDENCY_MINIMAL
        ),
    ) -> ServiceResult[RepoDependencyChangePreview]:
        consumer_root = Path(consumer_repo_root).resolve()
        provider_repo_key = self.runtime.foundation.layout.ensure_safe_key(
            provider_repo_key
        )
        release_mode = DependencyReleaseMode(release_mode)
        validation_profile = RepoReleaseValidationProfile(validation_profile)
        if validation_profile == RepoReleaseValidationProfile.SEMANTIC_FULL:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_validation_profile_invalid",
                    "Dependency changes require a dependency validation profile.",
                )
            )
        provider_root = (consumer_root.parent / provider_repo_key).resolve()
        provider_release = self.runtime.repo_workspace.release.get_release(
            provider_root, release_id=target_provider_release_id
        )
        if not provider_release.ok or provider_release.value is None:
            return self.runtime.foundation.fail(provider_release.issues)
        provider_git = self.runtime.repo_workspace.git_release.validate_release(
            provider_root, release=provider_release.value.release
        )
        if not provider_git.ok or provider_git.value is None:
            return self.runtime.foundation.fail(provider_git.issues)
        dependencies = (
            self.runtime.repo_workspace.lake_dependency.parse_lake_dependencies(
                consumer_root
            )
        )
        if not dependencies.ok or dependencies.value is None:
            return self.runtime.foundation.fail(dependencies.issues)
        matches = [
            item
            for item in dependencies.value.dependencies
            if item.name == provider_repo_key
        ]
        if len(matches) != 1 or matches[0].source != "git":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_change_target_invalid",
                    "Dependency change requires one existing Git dependency.",
                    object_ref=provider_repo_key,
                )
            )
        current = matches[0]
        previous_release_id = self._release_for_commit(
            provider_root, current.rev
        )
        kind = (
            RepoDependencyChangeKind.LOCATOR_REBIND
            if current.rev == provider_git.value.commit
            else RepoDependencyChangeKind.PROVIDER_PIN_UPGRADE
        )
        change = RepoDependencyReleaseChange(
            kind=kind,
            provider_repo_key=provider_repo_key,
            previous_release_id=(
                target_provider_release_id
                if kind == RepoDependencyChangeKind.LOCATOR_REBIND
                else previous_release_id
            ),
            release_id=target_provider_release_id,
            previous_commit=current.rev,
            commit=provider_git.value.commit,
            previous_git_url=current.git,
            git_url=target_git_url,
        )
        state = self.runtime.repo_workspace.git_release.inspect_repo(consumer_root)
        if not state.ok or state.value is None:
            return self.runtime.foundation.fail(state.issues)
        if release_mode == DependencyReleaseMode.DEPENDENCY_MAINTENANCE:
            if not state.value.initialized or not state.value.independent:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "dependency_release_git_repo_missing",
                        "Dependency Maintenance Release requires an independent Git repo.",
                        object_ref=str(consumer_root),
                    )
                )
            changes = self.runtime.repo_workspace.git_release.list_worktree_changes(
                consumer_root
            )
            if not changes.ok or changes.value is None:
                return self.runtime.foundation.fail(changes.issues)
            if changes.value:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "dependency_release_worktree_not_clean",
                        "Dependency Maintenance Release requires a clean worktree.",
                        object_ref=str(consumer_root),
                        details={"paths": ",".join(changes.value)},
                    )
                )
        publication = (
            self.runtime.repo_workspace.metadata.get_repo_publication(consumer_root)
        )
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        semantic_digest = (
            self.runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
                consumer_root
            )
        )
        dependency_digest = (
            self.runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
                consumer_root
            )
        )
        lakefile_digest = self._file_digest(consumer_root / "lakefile.toml")
        token_payload = {
            "change": change.model_dump(mode="json"),
            "release_mode": release_mode.value,
            "validation_profile": validation_profile.value,
            "consumer_head": state.value.head_commit,
            "base_release_id": publication.value.publication.latest_release_id,
            "semantic_manifest_digest": semantic_digest,
            "dependency_lock_digest": dependency_digest,
            "lakefile_digest": lakefile_digest,
        }
        token = hashlib.sha256(
            json.dumps(
                token_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        return self.runtime.foundation.ok(
            RepoDependencyChangePreview(
                consumer_repo_key=consumer_root.name,
                provider_repo_key=provider_repo_key,
                target_provider_release_id=target_provider_release_id,
                target_provider_commit=provider_git.value.commit,
                target_git_url=target_git_url,
                change=change,
                release_mode=release_mode,
                validation_profile=validation_profile,
                expected_consumer_head=state.value.head_commit,
                expected_base_release_id=publication.value.publication.latest_release_id,
                expected_semantic_manifest_digest=semantic_digest,
                expected_dependency_lock_digest=dependency_digest,
                expected_lakefile_digest=lakefile_digest,
                recovery_token=token,
                summary=f"Previewed {kind.value} for {provider_repo_key}.",
            )
        )

    def apply(
        self,
        consumer_repo_root: Path,
        *,
        preview: RepoDependencyChangePreview,
        expected_recovery_token: str,
    ) -> ServiceResult[RepoDependencyChangeReceipt]:
        consumer_root = Path(consumer_repo_root).resolve()
        refreshed = self.preview(
            consumer_root,
            provider_repo_key=preview.provider_repo_key,
            target_provider_release_id=preview.target_provider_release_id,
            target_git_url=preview.target_git_url,
            release_mode=preview.release_mode,
            validation_profile=preview.validation_profile,
        )
        if not refreshed.ok or refreshed.value is None:
            return self.runtime.foundation.fail(refreshed.issues)
        if (
            expected_recovery_token != preview.recovery_token
            or refreshed.value.recovery_token != preview.recovery_token
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_change_token_mismatch",
                    "Dependency truth changed after preview.",
                    object_ref=preview.provider_repo_key,
                )
            )
        with tempfile.TemporaryDirectory(
            prefix="lean-constellation-dependency-release-"
        ) as backup_dir:
            backup = self._capture_consumer_dependency_state(
                consumer_root,
                provider_repo_key=preview.provider_repo_key,
                backup_root=Path(backup_dir),
            )
            replaced = (
                self.runtime.repo_workspace.lake_dependency.replace_released_repo_git_dependency(
                    consumer_root,
                    provider_repo_key=preview.provider_repo_key,
                    provider_release_id=preview.target_provider_release_id,
                    git_url=preview.target_git_url,
                    local_transport_repo=(
                        consumer_root.parent / preview.provider_repo_key
                    ),
                )
            )
            if not replaced.ok or replaced.value is None:
                return self.runtime.foundation.fail(replaced.issues)
            change_id = f"dependency_change_{uuid4().hex}"
            receipt = RepoDependencyChangeReceipt(
                change_id=change_id,
                change=preview.change,
                release_mode=preview.release_mode,
                validation_profile=preview.validation_profile,
                targeted_update_summary=replaced.value.targeted_update_summary,
                build_summary=replaced.value.build_summary,
                summary=f"Applied {preview.change.kind.value}.",
            )
            receipt_path = (
                consumer_root
                / ".lean_constellation"
                / "publication"
                / "dependency_changes"
                / f"{change_id}.json"
            )
            written = self.runtime.foundation.store.write_json_atomic(
                receipt_path, receipt
            )
            if not written.ok:
                self._restore_consumer_dependency_state(
                    consumer_root,
                    provider_repo_key=preview.provider_repo_key,
                    backup=backup,
                )
                return self.runtime.foundation.fail(written.issues)
            if preview.release_mode == DependencyReleaseMode.DEFER:
                return self.runtime.foundation.ok(receipt)
            finalized = self._commit_dependency_release(
                consumer_root,
                preview=preview,
                build_summary=replaced.value.build_summary,
            )
            if not finalized.ok or finalized.value is None:
                receipt_path.unlink(missing_ok=True)
                self._restore_consumer_dependency_state(
                    consumer_root,
                    provider_repo_key=preview.provider_repo_key,
                    backup=backup,
                )
                return self.runtime.foundation.fail(finalized.issues)
            return self.runtime.foundation.ok(
                receipt.model_copy(
                    update={
                        "finalized_release_id": finalized.value.release.release.release_id,
                        "finalized_commit": finalized.value.git_release.commit,
                        "summary": (
                            f"Applied {preview.change.kind.value} and committed "
                            "Dependency Maintenance Release."
                        ),
                    }
                )
            )

    def _commit_dependency_release(
        self,
        repo_root: Path,
        *,
        preview: RepoDependencyChangePreview,
        build_summary: str,
    ) -> ServiceResult[RepoReleaseFinalizeView]:
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(
            repo_root
        )
        if (
            not publication.ok
            or publication.value is None
            or publication.value.publication.status
            != RepoPublicationStatus.STABLE
            or publication.value.publication.latest_release_id
            != preview.expected_base_release_id
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_release_base_mismatch",
                    "Dependency Maintenance Release requires the previewed stable base.",
                )
            )
        if preview.expected_base_release_id is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_release_base_missing",
                    "Dependency Maintenance Release requires a prior Semantic Release.",
                )
            )
        base = self.runtime.repo_workspace.release.get_release(
            repo_root, release_id=preview.expected_base_release_id
        )
        if not base.ok or base.value is None:
            return self.runtime.foundation.fail(base.issues)
        semantic_digest = (
            self.runtime.validation_snapshot.release_finalizer.compute_semantic_manifest_digest(
                repo_root
            )
        )
        if semantic_digest != base.value.release.semantic_manifest_digest:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_release_semantic_drift",
                    "Semantic truth differs from the prior Release.",
                    current=semantic_digest,
                    expected=base.value.release.semantic_manifest_digest,
                )
            )
        state = self.runtime.repo_workspace.git_release.inspect_repo(repo_root)
        if not state.ok or state.value is None:
            return self.runtime.foundation.fail(state.issues)
        release = RepoRelease(
            release_id=f"release_{uuid4().hex}",
            parent_release_id=base.value.release.release_id,
            release_kind=RepoReleaseKind.DEPENDENCY_MAINTENANCE,
            validation_profile=preview.validation_profile,
            node_contract_versions=base.value.release.node_contract_versions,
            completion_mode=base.value.release.completion_mode,
            semantic_manifest_digest=semantic_digest,
            dependency_lock_digest=(
                self.runtime.validation_snapshot.release_finalizer.compute_dependency_lock_digest(
                    repo_root
                )
            ),
            dependency_change=preview.change,
            summary=(
                f"{preview.change.kind.value}: "
                f"{preview.change.provider_repo_key} "
                f"to {preview.change.release_id}."
            ),
        )
        publication_files = self.runtime.repo_workspace.publication.prepare_publication(
            repo_root,
            release_id=release.release_id,
            semantic_manifest_digest=release.semantic_manifest_digest,
            generated_at=release.created_at,
        )
        if not publication_files.ok:
            return self.runtime.foundation.fail(publication_files.issues)
        target_publication = RepoPublicationState(
            status=RepoPublicationStatus.STABLE,
            latest_release_id=release.release_id,
        )
        prepared = PreparedRepoReleaseView(
            release=release,
            publication=target_publication,
            candidate_digest=(
                self.runtime.validation_snapshot.release_finalizer.compute_candidate_digest(
                    repo_root
                )
            ),
            expected_git_head=state.value.head_commit,
            build=ToolchainCommandView(
                ok=True,
                command=["lake", "build"],
                exit_code=0,
                summary=build_summary,
            ),
            gate=self.runtime.foundation.gate_passed(
                "dependency_maintenance_release",
                summary="Dependency transaction and full build passed.",
            ),
            summary="Prepared Dependency Maintenance Release.",
        )
        return self.runtime.validation_snapshot.commit_prepared_release(
            repo_root, prepared=prepared
        )

    def _release_for_commit(
        self, provider_root: Path, commit: str | None
    ) -> str | None:
        if commit is None:
            return None
        releases = self.runtime.repo_workspace.release.list_releases(provider_root)
        if not releases.ok or releases.value is None:
            return None
        for item in releases.value:
            resolved = self.runtime.repo_workspace.git_release.resolve_release_commit(
                provider_root, release_id=item.release.release_id
            )
            if resolved.ok and resolved.value == commit:
                return item.release.release_id
        return None

    @staticmethod
    def _file_digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _capture_consumer_dependency_state(
        repo_root: Path,
        *,
        provider_repo_key: str,
        backup_root: Path,
    ) -> dict[str, object]:
        lakefile = repo_root / "lakefile.toml"
        manifest = repo_root / "lake-manifest.json"
        package = repo_root / ".lake" / "packages" / provider_repo_key
        package_backup = backup_root / "package"
        if package.exists():
            shutil.copytree(package, package_backup)
        return {
            "lakefile": lakefile.read_bytes(),
            "manifest": manifest.read_bytes() if manifest.exists() else None,
            "package_backup": package_backup,
            "package_existed": package.exists(),
        }

    @staticmethod
    def _restore_consumer_dependency_state(
        repo_root: Path,
        *,
        provider_repo_key: str,
        backup: dict[str, object],
    ) -> None:
        lakefile = repo_root / "lakefile.toml"
        manifest = repo_root / "lake-manifest.json"
        package = repo_root / ".lake" / "packages" / provider_repo_key
        lakefile.write_bytes(backup["lakefile"])  # type: ignore[arg-type]
        manifest_payload = backup["manifest"]
        if manifest_payload is None:
            manifest.unlink(missing_ok=True)
        else:
            manifest.write_bytes(manifest_payload)  # type: ignore[arg-type]
        if package.exists():
            shutil.rmtree(package)
        if bool(backup["package_existed"]):
            shutil.copytree(Path(backup["package_backup"]), package)


__all__ = [
    "DependencyReleaseMode",
    "RepoDependencyChangePreview",
    "RepoDependencyChangeReceipt",
    "RepoDependencyReleaseComponent",
]
