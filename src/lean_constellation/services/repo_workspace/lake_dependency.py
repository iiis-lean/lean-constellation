"""Lake project skeleton and dependency operations."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.lake_project import NativeLakeProjectConfig
from lean_constellation.domain.preparation import UpstreamDependencyInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.domain.publication import RepoPortability
from lean_constellation.services.external_clients.lean_toolchain import ToolchainCommandView, ToolchainLeanCheckView
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.repo_workspace.repo_metadata import RepoMetadataComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class LakeDependencyEntry(StrictModel):
    name: str
    source: str | None = None
    scope: str | None = None
    path: str | None = None
    git: str | None = None
    rev: str | None = None
    subdir: str | None = None
    raw: str | None = None


class LakeDependencyView(StrictModel):
    repo_root: str
    lakefile_path: str | None = None
    dependencies: list[LakeDependencyEntry] = Field(default_factory=list)
    summary: str


class LakeDependencyAttachView(StrictModel):
    consumer_repo_root: str
    provider_repo_key: str
    dependency: LakeDependencyEntry
    changed: bool
    lake_update_summary: str | None = None
    summary: str


class LakeGitDependencyPin(StrictModel):
    provider_repo_key: str
    provider_release_id: str
    provider_commit: str
    git_url: str
    portability: RepoPortability


class LakeGitDependencyAttachView(StrictModel):
    consumer_repo_root: str
    pin: LakeGitDependencyPin
    dependency: LakeDependencyEntry
    changed: bool
    targeted_update_summary: str
    build_summary: str
    summary: str


class RepoSkeletonView(StrictModel):
    repo_format: RepoFormat = RepoFormat.NATIVE
    project_name: str
    lean_toolchain: str | None = None
    lake_manifest_path: str | None = None
    linked_packages: list[str] = Field(default_factory=list)
    lake_check_summary: str | None = None
    summary: str
    written_files: list[str] = Field(default_factory=list)


class AdapterSetupView(StrictModel):
    repo_format: RepoFormat = RepoFormat.ADAPTER
    upstream_summary: str
    lake_check_summary: str | None = None
    trusted_build: bool = False
    summary: str
    written_files: list[str] = Field(default_factory=list)


class LakeDependencyComponent:
    """Create deterministic Lean project skeletons and maintain Lake deps."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        metadata: RepoMetadataComponent,
        *,
        config: NativeLakeProjectConfig | None = None,
    ) -> None:
        self.runtime = runtime
        self.metadata = metadata
        self.config = config or NativeLakeProjectConfig()

    def parse_lake_dependencies(self, repo_root: Path) -> ServiceResult[LakeDependencyView]:
        lakefile = self._lakefile(repo_root)
        if lakefile is None:
            return self.runtime.foundation.ok(
                LakeDependencyView(repo_root=str(Path(repo_root)), summary="No lakefile found.")
            )
        text = lakefile.read_text(encoding="utf-8", errors="replace")
        if lakefile.name == "lakefile.toml":
            deps = self._parse_toml_deps(text)
        else:
            deps = self._parse_lean_deps(text)
        return self.runtime.foundation.ok(
            LakeDependencyView(
                repo_root=str(Path(repo_root)),
                lakefile_path=str(lakefile),
                dependencies=deps,
                summary=f"Found {len(deps)} Lake dependencies.",
            )
        )

    def attach_workspace_repo_dependency(
        self,
        consumer_repo_root: Path,
        *,
        provider_repo_key: str,
    ) -> ServiceResult[LakeDependencyAttachView]:
        provider_repo_key = self.runtime.foundation.layout.ensure_safe_key(provider_repo_key)
        consumer_root = Path(consumer_repo_root)
        provider_root = consumer_root.parent / provider_repo_key
        if not provider_root.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_not_found",
                    f"Provider repo does not exist in workspace: {provider_repo_key}",
                    object_ref=str(provider_root),
                )
            )
        lakefile = consumer_root / "lakefile.toml"
        if not lakefile.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lakefile_not_found",
                    "attach_workspace_repo_dependency currently requires lakefile.toml.",
                    object_ref=str(lakefile),
                )
            )
        deps = self.parse_lake_dependencies(consumer_root)
        if not deps.ok or deps.value is None:
            return self.runtime.foundation.fail(deps.issues)
        relative = provider_root.relative_to(consumer_root) if provider_root.is_relative_to(consumer_root) else None
        rel_path = relative.as_posix() if relative is not None else self._relative_path(provider_root, consumer_root)
        for dep in deps.value.dependencies:
            if dep.name == provider_repo_key:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "dependency_already_attached",
                        f"Lake dependency already attached: {provider_repo_key}",
                        object_ref=str(lakefile),
                    )
                )
        original_lakefile = lakefile.read_text(encoding="utf-8")
        block = f'\n[[require]]\nname = "{provider_repo_key}"\npath = "{rel_path}"\n'
        lakefile.write_text(original_lakefile + block, encoding="utf-8")
        update = self.run_lake_update(consumer_root)
        if not update.ok or update.value is None:
            lakefile.write_text(original_lakefile, encoding="utf-8")
            return self.runtime.foundation.fail(update.issues)
        dep = LakeDependencyEntry(name=provider_repo_key, source="path", path=rel_path)
        return self.runtime.foundation.ok(
            LakeDependencyAttachView(
                consumer_repo_root=str(consumer_root),
                provider_repo_key=provider_repo_key,
                dependency=dep,
                changed=True,
                lake_update_summary=update.value.summary,
                summary=f"Attached workspace provider dependency: {provider_repo_key}.",
            )
        )

    def attach_released_repo_git_dependency(
        self,
        consumer_repo_root: Path,
        *,
        provider_repo_key: str,
        provider_release_id: str,
        canonical_git_url: str | None = None,
    ) -> ServiceResult[LakeGitDependencyAttachView]:
        """Attach one LC provider by immutable Release commit, never dirty path truth."""
        provider_repo_key = self.runtime.foundation.layout.ensure_safe_key(
            provider_repo_key
        )
        consumer_root = Path(consumer_repo_root).resolve()
        provider_root = (consumer_root.parent / provider_repo_key).resolve()
        lakefile = consumer_root / "lakefile.toml"
        if not provider_root.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "provider_repo_not_found",
                    "Provider repository is not available in the workspace.",
                    object_ref=str(provider_root),
                )
            )
        if not lakefile.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lakefile_not_found",
                    "Git dependency attachment requires lakefile.toml.",
                    object_ref=str(lakefile),
                )
            )
        release = self.runtime.repo_workspace.release.get_release(
            provider_root, release_id=provider_release_id
        )
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(release.issues)
        validated = self.runtime.repo_workspace.git_release.validate_release(
            provider_root,
            release=release.value.release,
        )
        if not validated.ok or validated.value is None:
            return self.runtime.foundation.fail(validated.issues)
        existing = self.parse_lake_dependencies(consumer_root)
        if not existing.ok or existing.value is None:
            return self.runtime.foundation.fail(existing.issues)
        if any(item.name == provider_repo_key for item in existing.value.dependencies):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_already_attached",
                    f"Lake dependency already attached: {provider_repo_key}",
                    object_ref=str(lakefile),
                )
            )
        canonical = canonical_git_url.strip() if canonical_git_url else None
        git_url = canonical or self._relative_path(provider_root, consumer_root)
        portability = (
            RepoPortability.PORTABLE
            if canonical is not None
            else RepoPortability.LOCAL_WORKSPACE
        )
        transport_rewrites = (
            {canonical: provider_root.as_uri()} if canonical is not None else None
        )
        original_lakefile = lakefile.read_bytes()
        manifest_path = consumer_root / "lake-manifest.json"
        original_manifest = (
            manifest_path.read_bytes() if manifest_path.exists() else None
        )
        package_path = consumer_root / ".lake" / "packages" / provider_repo_key
        with tempfile.TemporaryDirectory(
            prefix="lean-constellation-lake-dependency-"
        ) as temp_dir:
            package_backup = Path(temp_dir) / "package"
            if package_path.exists():
                if package_path.is_symlink():
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "lake_dependency_package_symlink_unsupported",
                            "Git dependency transaction cannot replace a symlinked package.",
                            object_ref=str(package_path),
                        )
                    )
                shutil.copytree(package_path, package_backup)
            block = (
                "\n[[require]]\n"
                f'name = "{provider_repo_key}"\n'
                f'git = "{git_url}"\n'
                f'rev = "{validated.value.commit}"\n'
            )
            lakefile.write_text(
                original_lakefile.decode("utf-8") + block,
                encoding="utf-8",
            )
            update = self.run_lake_update(
                consumer_root,
                packages=[provider_repo_key],
                transport_rewrites=transport_rewrites,
            )
            if not update.ok or update.value is None:
                self._restore_dependency_transaction(
                    lakefile=lakefile,
                    original_lakefile=original_lakefile,
                    manifest_path=manifest_path,
                    original_manifest=original_manifest,
                    package_path=package_path,
                    package_backup=package_backup,
                )
                return self.runtime.foundation.fail(update.issues)
            manifest_gate = self._validate_manifest_git_pin(
                manifest_path,
                package=provider_repo_key,
                git_url=git_url,
                commit=validated.value.commit,
            )
            if not manifest_gate.ok:
                self._restore_dependency_transaction(
                    lakefile=lakefile,
                    original_lakefile=original_lakefile,
                    manifest_path=manifest_path,
                    original_manifest=original_manifest,
                    package_path=package_path,
                    package_backup=package_backup,
                )
                return self.runtime.foundation.fail(manifest_gate.issues)
            build = self.run_lake_build(
                consumer_root,
                transport_rewrites=transport_rewrites,
            )
            if not build.ok or build.value is None:
                self._restore_dependency_transaction(
                    lakefile=lakefile,
                    original_lakefile=original_lakefile,
                    manifest_path=manifest_path,
                    original_manifest=original_manifest,
                    package_path=package_path,
                    package_backup=package_backup,
                )
                return self.runtime.foundation.fail(build.issues)
        pin = LakeGitDependencyPin(
            provider_repo_key=provider_repo_key,
            provider_release_id=provider_release_id,
            provider_commit=validated.value.commit,
            git_url=git_url,
            portability=portability,
        )
        return self.runtime.foundation.ok(
            LakeGitDependencyAttachView(
                consumer_repo_root=str(consumer_root),
                pin=pin,
                dependency=LakeDependencyEntry(
                    name=provider_repo_key,
                    source="git",
                    git=git_url,
                    rev=validated.value.commit,
                ),
                changed=True,
                targeted_update_summary=update.value.summary,
                build_summary=build.value.summary,
                summary=(
                    f"Attached {provider_repo_key} at immutable Release "
                    f"{provider_release_id}."
                ),
            )
        )

    def replace_released_repo_git_dependency(
        self,
        consumer_repo_root: Path,
        *,
        provider_repo_key: str,
        provider_release_id: str,
        git_url: str,
        local_transport_repo: Path | None = None,
    ) -> ServiceResult[LakeGitDependencyAttachView]:
        """Replace one existing Git locator/pin and verify Lake's exact resolution."""
        provider_repo_key = self.runtime.foundation.layout.ensure_safe_key(
            provider_repo_key
        )
        consumer_root = Path(consumer_repo_root).resolve()
        provider_root = (
            Path(local_transport_repo).resolve()
            if local_transport_repo is not None
            else (consumer_root.parent / provider_repo_key).resolve()
        )
        release = self.runtime.repo_workspace.release.get_release(
            provider_root, release_id=provider_release_id
        )
        if not release.ok or release.value is None:
            return self.runtime.foundation.fail(release.issues)
        validated = self.runtime.repo_workspace.git_release.validate_release(
            provider_root, release=release.value.release
        )
        if not validated.ok or validated.value is None:
            return self.runtime.foundation.fail(validated.issues)
        lakefile = consumer_root / "lakefile.toml"
        if not lakefile.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lakefile_not_found",
                    "Git dependency replacement requires lakefile.toml.",
                    object_ref=str(lakefile),
                )
            )
        existing = self.parse_lake_dependencies(consumer_root)
        if not existing.ok or existing.value is None:
            return self.runtime.foundation.fail(existing.issues)
        matches = [
            item
            for item in existing.value.dependencies
            if item.name == provider_repo_key
        ]
        if len(matches) != 1 or matches[0].source != "git":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_dependency_not_replaceable",
                    "Dependency replacement requires exactly one existing Git dependency.",
                    object_ref=provider_repo_key,
                )
            )
        normalized_git_url = git_url.strip()
        if not normalized_git_url:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_dependency_url_missing",
                    "Replacement Git URL must be non-empty.",
                    object_ref=provider_repo_key,
                )
            )
        original_lakefile = lakefile.read_bytes()
        replaced = self._replace_toml_git_dependency(
            original_lakefile.decode("utf-8"),
            package=provider_repo_key,
            git_url=normalized_git_url,
            commit=validated.value.commit,
        )
        if replaced is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "git_dependency_block_uneditable",
                    "Could not locate a unique editable dependency block.",
                    object_ref=provider_repo_key,
                )
            )
        if replaced.encode("utf-8") == original_lakefile:
            return self.runtime.foundation.ok(
                LakeGitDependencyAttachView(
                    consumer_repo_root=str(consumer_root),
                    pin=LakeGitDependencyPin(
                        provider_repo_key=provider_repo_key,
                        provider_release_id=provider_release_id,
                        provider_commit=validated.value.commit,
                        git_url=normalized_git_url,
                        portability=(
                            RepoPortability.LOCAL_WORKSPACE
                            if normalized_git_url.startswith("../")
                            else RepoPortability.PORTABLE
                        ),
                    ),
                    dependency=matches[0],
                    changed=False,
                    targeted_update_summary="Dependency already uses the requested pin.",
                    build_summary="Build not required for an unchanged pin.",
                    summary="Git dependency pin is already current.",
                )
            )
        manifest_path = consumer_root / "lake-manifest.json"
        original_manifest = (
            manifest_path.read_bytes() if manifest_path.exists() else None
        )
        package_path = consumer_root / ".lake" / "packages" / provider_repo_key
        transport_rewrites = (
            {normalized_git_url: provider_root.as_uri()}
            if local_transport_repo is not None
            else None
        )
        with tempfile.TemporaryDirectory(
            prefix="lean-constellation-lake-dependency-"
        ) as temp_dir:
            package_backup = Path(temp_dir) / "package"
            if package_path.exists():
                if package_path.is_symlink():
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "lake_dependency_package_symlink_unsupported",
                            "Git dependency transaction cannot replace a symlinked package.",
                            object_ref=str(package_path),
                        )
                    )
                shutil.copytree(package_path, package_backup)
            lakefile.write_text(replaced, encoding="utf-8")
            update = self.run_lake_update(
                consumer_root,
                packages=[provider_repo_key],
                transport_rewrites=transport_rewrites,
            )
            if not update.ok or update.value is None:
                self._restore_dependency_transaction(
                    lakefile=lakefile,
                    original_lakefile=original_lakefile,
                    manifest_path=manifest_path,
                    original_manifest=original_manifest,
                    package_path=package_path,
                    package_backup=package_backup,
                )
                return self.runtime.foundation.fail(update.issues)
            manifest_gate = self._validate_manifest_git_pin(
                manifest_path,
                package=provider_repo_key,
                git_url=normalized_git_url,
                commit=validated.value.commit,
            )
            if not manifest_gate.ok:
                self._restore_dependency_transaction(
                    lakefile=lakefile,
                    original_lakefile=original_lakefile,
                    manifest_path=manifest_path,
                    original_manifest=original_manifest,
                    package_path=package_path,
                    package_backup=package_backup,
                )
                return self.runtime.foundation.fail(manifest_gate.issues)
            build = self.run_lake_build(
                consumer_root,
                transport_rewrites=transport_rewrites,
            )
            if not build.ok or build.value is None:
                self._restore_dependency_transaction(
                    lakefile=lakefile,
                    original_lakefile=original_lakefile,
                    manifest_path=manifest_path,
                    original_manifest=original_manifest,
                    package_path=package_path,
                    package_backup=package_backup,
                )
                return self.runtime.foundation.fail(build.issues)
        pin = LakeGitDependencyPin(
            provider_repo_key=provider_repo_key,
            provider_release_id=provider_release_id,
            provider_commit=validated.value.commit,
            git_url=normalized_git_url,
            portability=(
                RepoPortability.LOCAL_WORKSPACE
                if normalized_git_url.startswith("../")
                else RepoPortability.PORTABLE
            ),
        )
        return self.runtime.foundation.ok(
            LakeGitDependencyAttachView(
                consumer_repo_root=str(consumer_root),
                pin=pin,
                dependency=LakeDependencyEntry(
                    name=provider_repo_key,
                    source="git",
                    git=normalized_git_url,
                    rev=validated.value.commit,
                ),
                changed=True,
                targeted_update_summary=update.value.summary,
                build_summary=build.value.summary,
                summary=f"Replaced exact Git dependency for {provider_repo_key}.",
            )
        )

    def initialize_native_repo_skeleton(
        self,
        repo_root: Path,
        *,
        project_name: str,
        lean_toolchain: str | None = None,
        config: NativeLakeProjectConfig | None = None,
    ) -> ServiceResult[RepoSkeletonView]:
        effective_config = config or self.config
        normalized = self._normalize_module_name(project_name)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        project_name = normalized.value
        repo_root = Path(repo_root)
        ensured = self.metadata.ensure_repo_model(repo_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        fmt = self.metadata.set_repo_format(
            repo_root,
            repo_format=RepoFormat.NATIVE,
            reason="Initialize native Lean project skeleton.",
        )
        if not fmt.ok:
            return self.runtime.foundation.fail(fmt.issues)
        written = self._write_native_skeleton(repo_root, project_name, lean_toolchain, config=effective_config)
        ignored = self.runtime.repo_workspace.publication.refresh_managed_gitignore(
            repo_root
        )
        if not ignored.ok:
            return self.runtime.foundation.fail(ignored.issues)
        if ignored.value:
            written.append(repo_root / ".gitignore")
        git_repo = self.runtime.repo_workspace.git_release.ensure_independent_repo(
            repo_root
        )
        if not git_repo.ok:
            return self.runtime.foundation.fail(git_repo.issues)
        linked_packages = self._prepare_local_package_cache(repo_root, effective_config)
        if not linked_packages.ok or linked_packages.value is None:
            return self.runtime.foundation.fail(linked_packages.issues)
        written.extend(Path(path) for path in linked_packages.value.get("written_paths", []))
        build = self.run_lake_build(repo_root)
        if not build.ok or build.value is None:
            return self.runtime.foundation.fail(build.issues)
        build_summary = build.value.summary
        return self.runtime.foundation.ok(
            RepoSkeletonView(
                project_name=project_name,
                lean_toolchain=lean_toolchain or effective_config.lean_toolchain,
                lake_manifest_path=str(repo_root / "lake-manifest.json") if (repo_root / "lake-manifest.json").exists() else None,
                linked_packages=list(linked_packages.value.get("linked_packages", [])),
                lake_check_summary=build_summary,
                summary=f"Initialized native Lean project skeleton for {project_name}.",
                written_files=[str(path) for path in written],
            )
        )

    def initialize_adapter_repo_skeleton(
        self,
        repo_root: Path,
        *,
        project_name: str,
        upstream: UpstreamDependencyInput,
    ) -> ServiceResult[AdapterSetupView]:
        normalized = self._normalize_module_name(project_name)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        project_name = normalized.value
        repo_root = Path(repo_root)
        ensured = self.metadata.ensure_repo_model(repo_root)
        if not ensured.ok:
            return self.runtime.foundation.fail(ensured.issues)
        fmt = self.metadata.set_repo_format(
            repo_root,
            repo_format=RepoFormat.ADAPTER,
            reason="Initialize adapter Lean project skeleton.",
        )
        if not fmt.ok:
            return self.runtime.foundation.fail(fmt.issues)
        written = self._write_native_skeleton(repo_root, project_name, None, config=self.config)
        lakefile = repo_root / "lakefile.toml"
        package = upstream.package_name or self._package_from_git_url(upstream.git_url)
        dep_block = f'\n[[require]]\nname = "{package}"\ngit = "{upstream.git_url}"\n'
        if upstream.revision:
            dep_block += f'rev = "{upstream.revision}"\n'
        if upstream.subdir:
            dep_block += f'subDir = "{upstream.subdir}"\n'
        lakefile.write_text(lakefile.read_text(encoding="utf-8") + dep_block, encoding="utf-8")
        written.append(lakefile)
        linked_packages = self._prepare_local_package_cache(repo_root, self.config)
        if not linked_packages.ok or linked_packages.value is None:
            return self.runtime.foundation.fail(linked_packages.issues)
        written.extend(Path(path) for path in linked_packages.value.get("written_paths", []))
        update = self.run_lake_update(repo_root)
        if not update.ok or update.value is None:
            return self.runtime.foundation.fail(update.issues)
        module = upstream.module_name or package
        build = self.run_lake_build(repo_root, target=module)
        if not build.ok or build.value is None:
            return self.runtime.foundation.fail(build.issues)
        check = self.run_minimal_import_check(repo_root, module=module)
        if not check.ok or check.value is None:
            return self.runtime.foundation.fail(check.issues)
        summaries = [
            part
            for part in [
                update.value.summary,
                build.value.summary,
                check.value.summary,
            ]
            if part
        ]
        return self.runtime.foundation.ok(
            AdapterSetupView(
                upstream_summary=self._adapter_upstream_summary(upstream),
                lake_check_summary="; ".join(summaries) if summaries else None,
                trusted_build=True,
                summary=f"Initialized adapter Lean project skeleton for {project_name}.",
                written_files=[str(path) for path in written],
            )
        )

    def _adapter_upstream_summary(self, upstream: UpstreamDependencyInput) -> str:
        parts = [upstream.evidence_summary or f"Adapter upstream: {upstream.git_url}"]
        if upstream.known_risks:
            parts.append("Known risks: " + "; ".join(upstream.known_risks))
        return " ".join(parts)

    def check_native_repo_skeleton(self, repo_root: Path) -> ServiceResult[GateReport]:
        repo_root = Path(repo_root)
        project_name = self._project_name_from_lakefile(repo_root)
        project_root = repo_root / project_name
        issues = []
        required = [
            repo_root / "lakefile.toml",
            repo_root / "lean-toolchain",
            repo_root / f"{project_name}.lean",
            project_root / "Main" / "Prelude.lean",
            project_root / "Main" / "Interfaces.lean",
            repo_root / ".lean_constellation",
        ]
        if self.config.mathlib_enabled and self.config.local_package_cache is not None:
            required.append(repo_root / "lake-manifest.json")
        for path in required:
            if not path.exists():
                issues.append(
                    self.runtime.foundation.issue(
                        "native_skeleton_missing_file",
                        f"Native skeleton file is missing: {path.name}",
                        object_ref=str(path),
                    )
                )
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed("native_repo_skeleton", issues, summary=f"{len(issues)} files missing.")
            )
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("native_repo_skeleton", summary="Native repo skeleton is present.")
        )

    def run_lake_update(
        self,
        repo_root: Path,
        *,
        packages: list[str] | None = None,
        transport_rewrites: dict[str, str] | None = None,
    ) -> ServiceResult[ToolchainCommandView]:
        summary = self.runtime.external.lean_toolchain.run_lake_update(
            Path(repo_root),
            packages=packages,
            transport_rewrites=transport_rewrites,
        )
        if not summary.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lake_update_failed",
                    summary.summary,
                    object_ref=str(repo_root),
                    details={"exit_code": str(summary.exit_code), "stderr": summary.stderr_excerpt or ""},
                )
            )
        return self.runtime.foundation.ok(summary)

    def run_lake_build(
        self,
        repo_root: Path,
        *,
        target: str | None = None,
        transport_rewrites: dict[str, str] | None = None,
    ) -> ServiceResult[ToolchainCommandView]:
        summary = self.runtime.external.lean_toolchain.run_lake_build(
            Path(repo_root),
            target=target,
            transport_rewrites=transport_rewrites,
        )
        if not summary.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lake_build_failed",
                    summary.summary,
                    object_ref=str(repo_root),
                    details={"exit_code": str(summary.exit_code), "stderr": summary.stderr_excerpt or ""},
                )
            )
        return self.runtime.foundation.ok(summary)

    def run_minimal_import_check(self, repo_root: Path, *, module: str) -> ServiceResult[ToolchainLeanCheckView]:
        result = self.runtime.external.lean_toolchain.run_minimal_import_check(Path(repo_root), module)
        if not result.ok:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "minimal_import_check_failed",
                    result.summary,
                    object_ref=str(repo_root),
                    details={"module": module, "diagnostics": result.diagnostics_excerpt or ""},
                )
            )
        return self.runtime.foundation.ok(result)

    def _write_native_skeleton(
        self,
        repo_root: Path,
        project_name: str,
        lean_toolchain: str | None,
        *,
        config: NativeLakeProjectConfig,
    ) -> list[Path]:
        repo_root.mkdir(parents=True, exist_ok=True)
        (repo_root / ".lean_constellation").mkdir(parents=True, exist_ok=True)
        main_dir = repo_root / project_name / "Main"
        main_dir.mkdir(parents=True, exist_ok=True)
        files: dict[Path, str] = {
            repo_root / "lakefile.toml": self._native_lakefile_text(project_name, config),
            repo_root / "lean-toolchain": (lean_toolchain or config.lean_toolchain or f"leanprover/lean4:v{config.lean_version}") + "\n",
            repo_root / f"{project_name}.lean": f"import {project_name}.Main.Prelude\nimport {project_name}.Main.Interfaces\n",
            main_dir / "Prelude.lean": "import Mathlib\n" if config.mathlib_enabled else "",
            main_dir / "Interfaces.lean": f"import {project_name}.Main.Prelude\n",
            main_dir / "Basic.lean": f"import {project_name}.Main.Prelude\n",
        }
        written: list[Path] = []
        for path, text in files.items():
            if not path.exists() or path.read_text(encoding="utf-8", errors="replace") != text:
                path.write_text(text, encoding="utf-8")
                written.append(path)
        return written

    def _native_lakefile_text(self, project_name: str, config: NativeLakeProjectConfig) -> str:
        parts = [
            f'name = "{project_name}"',
            'version = "0.1.0"',
            f'defaultTargets = ["{project_name}"]',
            "",
            "[leanOptions]",
            "pp.unicode.fun = true",
            "relaxedAutoImplicit = false",
            "weak.linter.mathlibStandardSet = true",
            "maxSynthPendingDepth = 3",
            "",
        ]
        if config.mathlib_enabled:
            parts.extend(
                [
                    "[[require]]",
                    'name = "mathlib"',
                    f'scope = "{config.mathlib_scope}"',
                    f'rev = "{config.mathlib_rev}"',
                    "",
                ]
            )
        parts.extend(["[[lean_lib]]", f'name = "{project_name}"', ""])
        return "\n".join(parts)

    def _prepare_local_package_cache(
        self,
        repo_root: Path,
        config: NativeLakeProjectConfig,
    ) -> ServiceResult[dict[str, object]]:
        cache = config.local_package_cache
        if cache is None:
            return self.runtime.foundation.ok({"linked_packages": [], "written_paths": []})
        if not config.mathlib_enabled:
            return self.runtime.foundation.ok({"linked_packages": [], "written_paths": []})
        if cache.manifest_path is None or not cache.manifest_path.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "local_lake_cache_manifest_missing",
                    "Local Lake package cache manifest is missing.",
                    object_ref=str(cache.manifest_path) if cache.manifest_path else None,
                )
            )
        if cache.packages_root is None or not cache.packages_root.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "local_lake_cache_packages_missing",
                    "Local Lake package cache packages directory is missing.",
                    object_ref=str(cache.packages_root) if cache.packages_root else None,
                )
            )
        manifest = self._read_local_lake_manifest(cache.manifest_path)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        package_names = cache.package_names or [str(package.get("name")) for package in manifest.value.get("packages", []) if package.get("name")]
        packages = [package for package in manifest.value.get("packages", []) if package.get("name") in set(package_names)]
        missing_manifest = sorted(set(package_names) - {str(package.get("name")) for package in packages})
        if missing_manifest and cache.require_all_packages:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "local_lake_cache_manifest_package_missing",
                    "Local Lake package cache manifest is missing requested packages.",
                    details={"missing_packages": ",".join(missing_manifest)},
                    object_ref=str(cache.manifest_path),
                )
            )
        written: list[Path] = []
        lake_dir = repo_root / ".lake"
        packages_dir = lake_dir / "packages"
        packages_dir.mkdir(parents=True, exist_ok=True)
        written.extend([lake_dir, packages_dir])
        for name in package_names:
            source = cache.packages_root / name
            if not source.exists():
                if cache.require_all_packages:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "local_lake_cache_package_missing",
                            f"Local Lake package cache package is missing: {name}",
                            object_ref=str(source),
                        )
                    )
                continue
            target = packages_dir / name
            if target.exists() or target.is_symlink():
                if target.is_symlink() and target.resolve(strict=False) == source.resolve(strict=False):
                    continue
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "local_lake_cache_package_conflict",
                        f"Repo package path already exists and is not the expected symlink: {name}",
                        object_ref=str(target),
                    )
                )
            target.symlink_to(source, target_is_directory=source.is_dir())
            written.append(target)
        manifest_payload = dict(manifest.value)
        manifest_payload["name"] = self._project_name_from_lakefile(repo_root)
        manifest_payload["packagesDir"] = ".lake/packages"
        manifest_payload["lakeDir"] = ".lake"
        manifest_payload["packages"] = packages
        manifest_path = repo_root / "lake-manifest.json"
        manifest_path.write_text(json.dumps(manifest_payload, indent=1) + "\n", encoding="utf-8")
        written.append(manifest_path)
        return self.runtime.foundation.ok({"linked_packages": package_names, "written_paths": [str(path) for path in written]})

    def _read_local_lake_manifest(self, manifest_path: Path) -> ServiceResult[dict[str, object]]:
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - file boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "local_lake_cache_manifest_invalid",
                    f"Local Lake package cache manifest is invalid: {exc}",
                    object_ref=str(manifest_path),
                )
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("packages"), list):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "local_lake_cache_manifest_invalid",
                    "Local Lake package cache manifest must contain a packages list.",
                    object_ref=str(manifest_path),
                )
            )
        return self.runtime.foundation.ok(payload)

    def _validate_manifest_git_pin(
        self,
        manifest_path: Path,
        *,
        package: str,
        git_url: str,
        commit: str,
    ) -> ServiceResult[bool]:
        loaded = self._read_local_lake_manifest(manifest_path)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        candidates = [
            item
            for item in loaded.value.get("packages", [])
            if isinstance(item, dict) and item.get("name") == package
        ]
        if len(candidates) != 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lake_manifest_dependency_missing",
                    "Lake manifest must contain exactly one attached provider package.",
                    object_ref=package,
                )
            )
        candidate = candidates[0]
        candidate_url = str(
            candidate.get("url")
            or candidate.get("git")
            or candidate.get("source")
            or ""
        )
        candidate_commit = str(
            candidate.get("rev")
            or candidate.get("revision")
            or candidate.get("commit")
            or ""
        )
        if candidate_url != git_url or candidate_commit != commit:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lake_manifest_dependency_pin_mismatch",
                    "Lake manifest did not resolve the requested exact Git dependency.",
                    object_ref=package,
                    current=f"{candidate_url}@{candidate_commit}",
                    expected=f"{git_url}@{commit}",
                )
            )
        if Path(candidate_url).is_absolute():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "lake_manifest_absolute_dependency_path",
                    "Lake manifest contains a machine-specific absolute dependency path.",
                    object_ref=package,
                )
            )
        return self.runtime.foundation.ok(True)

    @staticmethod
    def _replace_toml_git_dependency(
        text: str,
        *,
        package: str,
        git_url: str,
        commit: str,
    ) -> str | None:
        marker = re.compile(r"(?m)^\s*\[\[require\]\]\s*$")
        starts = [match.start() for match in marker.finditer(text)]
        matches: list[tuple[int, int]] = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(text)
            block = text[start:end]
            name = re.search(r'(?m)^\s*name\s*=\s*"([^"]+)"\s*$', block)
            if name and name.group(1) == package:
                matches.append((start, end))
        if len(matches) != 1:
            return None
        start, end = matches[0]
        block = text[start:end]
        if re.search(r"(?m)^\s*git\s*=", block) is None:
            return None
        block = re.sub(
            r'(?m)^\s*git\s*=\s*"[^"]*"\s*$',
            f'git = "{git_url}"',
            block,
            count=1,
        )
        if re.search(r"(?m)^\s*rev\s*=", block):
            block = re.sub(
                r'(?m)^\s*rev\s*=\s*"[^"]*"\s*$',
                f'rev = "{commit}"',
                block,
                count=1,
            )
        else:
            block = block.rstrip() + f'\nrev = "{commit}"\n'
        return text[:start] + block + text[end:]

    @staticmethod
    def _restore_dependency_transaction(
        *,
        lakefile: Path,
        original_lakefile: bytes,
        manifest_path: Path,
        original_manifest: bytes | None,
        package_path: Path,
        package_backup: Path,
    ) -> None:
        lakefile.write_bytes(original_lakefile)
        if original_manifest is None:
            manifest_path.unlink(missing_ok=True)
        else:
            manifest_path.write_bytes(original_manifest)
        if package_path.exists():
            shutil.rmtree(package_path)
        if package_backup.exists():
            shutil.copytree(package_backup, package_path)

    def _lakefile(self, repo_root: Path) -> Path | None:
        repo_root = Path(repo_root)
        for name in ("lakefile.toml", "lakefile.lean"):
            path = repo_root / name
            if path.exists():
                return path
        return None

    def _parse_toml_deps(self, text: str) -> list[LakeDependencyEntry]:
        deps: list[LakeDependencyEntry] = []
        for block in re.split(r"(?m)^\s*\[\[require\]\]\s*$", text)[1:]:
            values: dict[str, str] = {}
            for line in block.splitlines():
                if line.strip().startswith("["):
                    break
                match = re.match(r'\s*([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"', line)
                if match:
                    values[match.group(1)] = match.group(2)
            name = values.get("name")
            if name:
                source = "path" if "path" in values else "git" if "git" in values else "registry" if "scope" in values else None
                deps.append(
                    LakeDependencyEntry(
                        name=name,
                        source=source,
                        scope=values.get("scope"),
                        path=values.get("path"),
                    git=values.get("git"),
                    rev=values.get("rev") or values.get("revision"),
                    subdir=values.get("subDir") or values.get("subdir"),
                )
            )
        return deps

    def _project_name_from_lakefile(self, repo_root: Path) -> str:
        lakefile = repo_root / "lakefile.toml"
        if lakefile.exists():
            text = lakefile.read_text(encoding="utf-8", errors="replace")
            match = re.search(r'(?m)^\s*name\s*=\s*"([A-Za-z][A-Za-z0-9_]*)"\s*$', text)
            if match:
                return match.group(1)
        return "Main"

    def _parse_lean_deps(self, text: str) -> list[LakeDependencyEntry]:
        deps: list[LakeDependencyEntry] = []
        for match in re.finditer(r'require\s+([A-Za-z0-9_.-]+)\s+from\s+("[^"]+"|git\s+"[^"]+"|path\s+"[^"]+")', text):
            name = match.group(1)
            raw = match.group(0)
            git_match = re.search(r'git\s+"([^"]+)"', raw)
            path_match = re.search(r'path\s+"([^"]+)"', raw)
            deps.append(
                LakeDependencyEntry(
                    name=name,
                    source="git" if git_match else "path" if path_match else None,
                    git=git_match.group(1) if git_match else None,
                    path=path_match.group(1) if path_match else None,
                    raw=raw,
                )
            )
        return deps

    def _normalize_module_name(self, value: str) -> ServiceResult[str]:
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_lean_project_name",
                    "Lean project/module name must start with a letter and contain only letters, digits, or underscores.",
                    current=value,
                )
            )
        return self.runtime.foundation.ok(value)

    @staticmethod
    def _relative_path(target: Path, base: Path) -> str:
        import os

        return os.path.relpath(target, start=base)

    @staticmethod
    def _package_from_git_url(url: str) -> str:
        name = url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return re.sub(r"[^A-Za-z0-9_]", "_", name) or "upstream"

    @staticmethod
    def _issue_summary(issues: list[object]) -> str:
        return "; ".join(getattr(issue, "message", str(issue)) for issue in issues)
