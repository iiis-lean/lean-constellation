"""Portable repository publication manifests, policy, and derived documents."""

from __future__ import annotations

import hashlib
import html
from importlib.resources import files as package_files
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlencode

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.publication import (
    EffectiveRepoPublicationPolicy,
    RepoPortability,
    RepoPublicationOverride,
    RepoPublicationPolicy,
    RepoPublicationPresentation,
    WorkspacePublicationPolicy,
)
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoFormat,
    proof_availability_for_completion_mode,
)
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.foundation import (
    FoundationContext,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.repo_workspace.publication_graph import (
    PublicationGraphvizUnavailable,
    render_publication_graph_svg,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


_MANAGED_IGNORE_BEGIN = "# BEGIN Lean Constellation managed ignores"
_MANAGED_IGNORE_END = "# END Lean Constellation managed ignores"
_MANAGED_IGNORE_BODY = """# BEGIN Lean Constellation managed ignores
/.lake/
/.agent_runtime/
/.runtime/
/.lean_constellation/snapshots/
/.lean_constellation/checkpoints/
/.lean_constellation/locks/
/.lean_constellation/.locks/
/.lean_constellation/staging/
/.lean_constellation/publication/remote_receipts/
/.env
/.env.*
!/.env.example
# END Lean Constellation managed ignores"""
_README_BEGIN = "<!-- BEGIN Lean Constellation: project-summary -->"
_README_END = "<!-- END Lean Constellation: project-summary -->"
_PUBLICATION_MARK_PATH = "docs/lean-constellation/assets/lean-constellation-mark.svg"
_PUBLICATION_MANIFEST_PATH = ".lean_constellation/publication/manifest.json"
_LEAN_CONSTELLATION_REPOSITORY_URL = (
    "https://github.com/iiis-lean/lean-constellation"
)
_LEAN_MCP_TOOLKIT_REPOSITORY_URL = (
    "https://github.com/iiis-lean/lean-mcp-toolkit"
)
_EXCLUDED_TOP_LEVEL = {".git", ".lake", ".agent_runtime", ".runtime"}
_EXCLUDED_CONSTELLATION_DIRS = {
    ".locks",
    "checkpoints",
    "locks",
    "snapshots",
    "staging",
}
_CITATION_TEMPLATE = """cff-version: 1.2.0
message: "If you use this formalization, please cite it using this metadata."
title: "REPLACE WITH THE FORMALIZATION TITLE"
type: software
authors:
  - name: "REPLACE WITH THE FORMALIZATION AUTHOR OR TEAM"
repository-code: "REPLACE WITH THE CANONICAL REPOSITORY URL"
license: "REPLACE WITH AN SPDX IDENTIFIER"
"""
_LICENSING_TEMPLATE = """# Licensing

Replace this template with explicit, scope-aware licensing before public
distribution.

- State the license for Lean source code and repository documentation.
- Attribute retained source papers, TeX, figures, or datasets separately.
- Link each scope to its corresponding `LICENSE` or `LICENSES/*` file.
- Do not infer copyright ownership or a license from SourceCorpus metadata.
"""


class PublicationFileEntry(StrictModel):
    path: str
    disposition: Literal["include", "exclude", "warning"]
    reason: str
    size_bytes: int = 0
    sha256: str | None = None


class PublicationDirectoryExclusion(StrictModel):
    path: str
    recursive: Literal[True] = True
    reason: str


class RepoPublicationManifest(StrictModel):
    schema_version: int = 2
    repo_key: str
    generated_at: str | None = None
    excluded_directories: list[PublicationDirectoryExclusion] = Field(
        default_factory=list
    )
    entries: list[PublicationFileEntry] = Field(default_factory=list)
    included_file_count: int
    included_size_bytes: int
    manifest_digest: str
    summary: str


class PublicApiDeclaration(StrictModel):
    name: str
    revision: int
    kind: str
    node_path: str
    module: str
    lean_full_name: str | None = None
    state: str
    status: str
    proof_available: bool = False
    formal_code: str | None = None
    statement_dependencies: list[str] = Field(default_factory=list)
    proof_dependencies: list[str] = Field(default_factory=list)
    source_origins: list[str] = Field(default_factory=list)
    summary: str | None = None


class RepoPublicationDependency(StrictModel):
    repo: str
    required_proof_availability: str
    provider_completion_mode: str | None = None
    provider_release_id: str | None = None
    source: str | None = None
    status: str


class AdapterUpstreamPublication(StrictModel):
    source_kind: Literal["git", "local_path"]
    git_url: str | None = None
    revision: str | None = None
    subdir: str | None = None
    package_name: str
    dependency_name: str
    visible_modules: list[str] = Field(default_factory=list)
    trusted_build: bool


class PublicApiDocument(StrictModel):
    schema_version: int = 4
    repo_key: str
    repo_format: Literal["native", "adapter"] = "native"
    release_id: str | None = None
    completion_mode: str
    proof_availability: str
    dependencies: list[RepoPublicationDependency] = Field(default_factory=list)
    adapter_upstream: AdapterUpstreamPublication | None = None
    declarations: list[PublicApiDeclaration] = Field(default_factory=list)
    summary: str


class PublicBoundaryDeclaration(StrictModel):
    declaration: PublicApiDeclaration
    exported_scope_paths: list[str] = Field(default_factory=list)
    main_export: bool = False


class PublicBoundariesDocument(StrictModel):
    schema_version: int = 2
    repo_key: str
    repo_format: Literal["native", "adapter"] = "native"
    release_id: str | None = None
    completion_mode: str
    proof_availability: str
    dependencies: list[RepoPublicationDependency] = Field(default_factory=list)
    declarations: list[PublicBoundaryDeclaration] = Field(default_factory=list)
    summary: str


class RepoProvenanceDocument(StrictModel):
    schema_version: int = 2
    repo_key: str
    repo_format: Literal["native", "adapter"] = "native"
    release_id: str | None = None
    semantic_manifest_digest: str | None = None
    lean_toolchain: str | None = None
    source_manifest_digest: str | None = None
    source_files: dict[str, str] = Field(default_factory=dict)
    adapter_upstream: AdapterUpstreamPublication | None = None
    build_command: str = "lake build"
    generated_at: str | None = None
    summary: str


class RepoPublicationPreparationView(StrictModel):
    repo_key: str
    manifest_path: str
    presentation_path: str
    readme_path: str
    public_api_markdown_path: str
    public_api_json_path: str
    declarations_dir: str
    public_boundaries_markdown_path: str
    public_boundaries_json_path: str
    provenance_path: str
    gitignore_path: str
    topics: list[str] = Field(default_factory=list)
    written_files: list[str] = Field(default_factory=list)
    summary: str


class RepoPublicationComponent:
    """Derive portable publication files without embedding runtime paths."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        workspace_policy: WorkspacePublicationPolicy | None = None,
    ) -> None:
        self.runtime = runtime
        self.workspace_policy = workspace_policy or WorkspacePublicationPolicy()

    def get_presentation(
        self,
        repo_root: Path,
    ) -> ServiceResult[RepoPublicationPresentation]:
        path = self.runtime.foundation.layout.publication_presentation_path(
            FoundationContext(repo_root=Path(repo_root))
        )
        if not path.exists():
            return self.runtime.foundation.ok(RepoPublicationPresentation())
        loaded = self.runtime.foundation.store.read_json(
            path, RepoPublicationPresentation
        )
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self.runtime.foundation.ok(loaded.value)

    def set_presentation(
        self,
        repo_root: Path,
        *,
        presentation: RepoPublicationPresentation,
    ) -> ServiceResult[RepoPublicationPresentation]:
        path = self.runtime.foundation.layout.publication_presentation_path(
            FoundationContext(repo_root=Path(repo_root))
        )
        written = self.runtime.foundation.store.write_json_atomic(
            path,
            presentation,
            mode=WriteMode.OVERWRITE,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(presentation)

    def resolve_policy(
        self,
        repo_root: Path,
        *,
        repo_key: str | None = None,
        repo_override: RepoPublicationOverride | None = None,
    ) -> ServiceResult[EffectiveRepoPublicationPolicy]:
        repo_root = Path(repo_root)
        repo_key = repo_key or repo_root.name
        defaults = self.workspace_policy.repo_defaults
        if repo_override is None:
            config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
            if not config.ok or config.value is None:
                return self.runtime.foundation.fail(config.issues)
            repo_override = config.value.config.publication
        values = defaults.model_dump()
        sources = {field: "workspace_default" for field in values}
        profile_name = self.workspace_policy.repo_remote_profile
        if profile_name is not None:
            profile = self.workspace_policy.remote_profiles.get(profile_name)
            if profile is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "publication_remote_profile_missing",
                        "Workspace publication references an unknown remote profile.",
                        object_ref=profile_name,
                    )
                )
            workspace_slug = repo_root.parent.name
            try:
                repo_name = self.workspace_policy.repo_remote_name_template.format(
                    repo_key=repo_key,
                    repo_slug=repo_key,
                    workspace_slug=workspace_slug,
                )
                format_values = {
                    **profile.values,
                    "repo_key": repo_key,
                    "repo_slug": repo_key,
                    "workspace_slug": workspace_slug,
                    "repo_name": repo_name,
                }
                values["canonical_fetch_url"] = (
                    profile.fetch_url_template.format(**format_values)
                )
                values["canonical_push_url"] = (
                    profile.push_url_template.format(**format_values)
                    if profile.push_url_template is not None
                    else None
                )
            except KeyError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "publication_remote_template_value_missing",
                        f"Remote profile template value is missing: {exc.args[0]}",
                        object_ref=profile_name,
                    )
                )
            sources["canonical_fetch_url"] = f"workspace_profile:{profile_name}"
            sources["canonical_push_url"] = f"workspace_profile:{profile_name}"
        if repo_override is not None:
            for field, value in repo_override.model_dump(exclude_none=True).items():
                values[field] = value
                sources[field] = "repo_override"
        policy = RepoPublicationPolicy.model_validate(values)
        portability = (
            RepoPortability.PORTABLE
            if policy.canonical_fetch_url is not None
            else RepoPortability.LOCAL_WORKSPACE
        )
        return self.runtime.foundation.ok(
            EffectiveRepoPublicationPolicy(
                repo_key=repo_key,
                policy=policy,
                source_by_field=sources,
                portability=portability,
                summary=(
                    f"Resolved publication policy for {repo_key} "
                    f"with {portability.value} portability."
                ),
            )
        )

    def build_manifest(
        self,
        repo_root: Path,
        *,
        generated_at: str | None = None,
    ) -> ServiceResult[RepoPublicationManifest]:
        repo_root = Path(repo_root).resolve()
        policy = self.resolve_policy(repo_root)
        if not policy.ok or policy.value is None:
            return self.runtime.foundation.fail(policy.issues)
        excluded_directories: list[PublicationDirectoryExclusion] = []
        entries: list[PublicationFileEntry] = []
        included_size = 0
        for current_root, dirnames, filenames in os.walk(
            repo_root,
            topdown=True,
            followlinks=False,
        ):
            root = Path(current_root)
            retained_directories: list[str] = []
            for dirname in sorted(dirnames):
                path = root / dirname
                if path.is_symlink():
                    continue
                relpath = path.relative_to(repo_root).as_posix()
                excluded_reason = self._excluded_directory_reason(relpath)
                if excluded_reason is not None:
                    excluded_directories.append(
                        PublicationDirectoryExclusion(
                            path=relpath,
                            reason=excluded_reason,
                        )
                    )
                    continue
                retained_directories.append(dirname)
            dirnames[:] = retained_directories

            for filename in sorted(filenames):
                path = root / filename
                if not path.is_file() or path.is_symlink():
                    continue
                relpath = path.relative_to(repo_root).as_posix()
                excluded_reason = self._exclusion_reason(relpath)
                if (
                    relpath == "lake-manifest.json"
                    and not policy.value.policy.include_lake_manifest
                ):
                    excluded_reason = (
                        "publication_policy_excludes_lake_manifest"
                    )
                if excluded_reason is not None:
                    if excluded_reason == "publication_manifest_self":
                        continue
                    entries.append(
                        PublicationFileEntry(
                            path=relpath,
                            disposition="exclude",
                            reason=excluded_reason,
                            size_bytes=path.stat().st_size,
                        )
                    )
                    continue
                digest = self._file_digest(path)
                size = path.stat().st_size
                included_size += size
                entries.append(
                    PublicationFileEntry(
                        path=relpath,
                        disposition="include",
                        reason="portable_project_or_business_truth",
                        size_bytes=size,
                        sha256=digest,
                    )
                )
        entries.append(
            PublicationFileEntry(
                path=_PUBLICATION_MANIFEST_PATH,
                disposition="exclude",
                reason="publication_manifest_self",
            )
        )
        excluded_directories.sort(
            key=lambda item: PurePosixPath(item.path).parts
        )
        entries.sort(key=lambda item: PurePosixPath(item.path).parts)
        digest_payload = [
            (entry.path, entry.size_bytes, entry.sha256)
            for entry in entries
            if entry.disposition == "include"
        ]
        manifest_digest = hashlib.sha256(
            json.dumps(
                digest_payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        included_count = sum(entry.disposition == "include" for entry in entries)
        return self.runtime.foundation.ok(
            RepoPublicationManifest(
                repo_key=repo_root.name,
                **({"generated_at": generated_at} if generated_at is not None else {}),
                excluded_directories=excluded_directories,
                entries=entries,
                included_file_count=included_count,
                included_size_bytes=included_size,
                manifest_digest=manifest_digest,
                summary=(
                    f"Publication manifest includes {included_count} files "
                    f"({included_size} bytes)."
                ),
            )
        )

    def refresh_managed_gitignore(self, repo_root: Path) -> ServiceResult[bool]:
        path = Path(repo_root) / ".gitignore"
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = self._replace_managed_block(
            current,
            begin=_MANAGED_IGNORE_BEGIN,
            end=_MANAGED_IGNORE_END,
            replacement=_MANAGED_IGNORE_BODY,
        )
        if updated == current:
            return self.runtime.foundation.ok(False)
        path.write_text(updated, encoding="utf-8")
        return self.runtime.foundation.ok(True)

    def render_public_api(
        self,
        repo_root: Path,
        *,
        release_id: str | None = None,
    ) -> ServiceResult[PublicApiDocument]:
        documents = self._render_public_documents(
            Path(repo_root), release_id=release_id
        )
        if not documents.ok or documents.value is None:
            return self.runtime.foundation.fail(documents.issues)
        return self.runtime.foundation.ok(documents.value[0])

    def render_public_boundaries(
        self,
        repo_root: Path,
        *,
        release_id: str | None = None,
    ) -> ServiceResult[PublicBoundariesDocument]:
        documents = self._render_public_documents(
            Path(repo_root), release_id=release_id
        )
        if not documents.ok or documents.value is None:
            return self.runtime.foundation.fail(documents.issues)
        return self.runtime.foundation.ok(documents.value[1])

    def _render_public_documents(
        self,
        repo_root: Path,
        *,
        release_id: str | None,
    ) -> ServiceResult[tuple[PublicApiDocument, PublicBoundariesDocument]]:
        repo_root = Path(repo_root)
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        config = self.runtime.repo_workspace.metadata.get_repo_config(repo_root)
        if not config.ok or config.value is None:
            return self.runtime.foundation.fail(config.issues)
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        dependencies = self._publication_dependencies(repo_root)
        if not dependencies.ok or dependencies.value is None:
            return self.runtime.foundation.fail(dependencies.issues)
        adapter_upstream = self._adapter_upstream_publication(repo_root)
        if not adapter_upstream.ok:
            return self.runtime.foundation.fail(adapter_upstream.issues)
        tree = self.runtime.node.node_tree.get_node_tree(repo_root)
        if not tree.ok or tree.value is None:
            return self.runtime.foundation.fail(tree.issues)
        declarations: list[PublicApiDeclaration] = []
        seen: set[tuple[str, str, int]] = set()
        public_refs: list[tuple[DeclRef, int]] = []
        if repo_format.value.repo_format == RepoFormat.ADAPTER:
            main = self.runtime.node.contract.get_visible_contract(
                repo_root,
                node_path="Main",
            )
            if not main.ok or main.value is None:
                return self.runtime.foundation.fail(main.issues)
            public_refs = [
                (ref, ref.revision)
                for ref in main.value.contract.exports
            ]
        else:
            public_values = []
            for node in tree.value.nodes:
                if node.kind.value != "content":
                    continue
                public = self.runtime.decl_graph.list_content_public_decls(
                    repo_root, node_path=node.path
                )
                if not public.ok or public.value is None:
                    return self.runtime.foundation.fail(public.issues)
                public_values.extend(public.value)
            public_refs = [
                (public.ref, public.resolved_revision or public.ref.revision)
                for public in public_values
                if public.ready
            ]
        for ref, resolved_revision in public_refs:
            key = (ref.node, ref.name, resolved_revision)
            if key in seen:
                continue
            seen.add(key)
            decl = self.runtime.decl_graph.get_decl_view(
                repo_root, node_path=ref.node, name=ref.name
            )
            revision = self.runtime.decl_graph.get_decl_revision(
                repo_root,
                node_path=ref.node,
                name=ref.name,
                revision=resolved_revision,
            )
            if (
                not decl.ok
                or decl.value is None
                or not revision.ok
                or revision.value is None
            ):
                return self.runtime.foundation.fail([*decl.issues, *revision.issues])
            statement_dependencies = [
                self._dependency_label(item)
                for item in revision.value.statement.deps
            ]
            proof_dependencies = [
                self._dependency_label(item)
                for item in (
                    revision.value.proof.deps
                    if revision.value.proof is not None
                    else []
                )
            ]
            source_origins = [
                self._origin_label(item)
                for item in (
                    revision.value.statement.nl.origin
                    if revision.value.statement.nl is not None
                    else []
                )
            ]
            declarations.append(
                PublicApiDeclaration(
                    name=ref.name,
                    revision=resolved_revision,
                    kind=str(decl.value.kind),
                    node_path=ref.node,
                    module=decl.value.module or ref.node,
                    lean_full_name=revision.value.lean_decl_name,
                    state=str(revision.value.state),
                    status=str(revision.value.status),
                    proof_available=(
                        revision.value.proof is not None
                        and revision.value.proof.formal is not None
                    ),
                    formal_code=(
                        revision.value.proof.formal.code
                        if (
                            revision.value.proof is not None
                            and revision.value.proof.formal is not None
                        )
                        else (
                            revision.value.statement.formal.code
                            if revision.value.statement.formal is not None
                            else None
                        )
                    ),
                    statement_dependencies=statement_dependencies,
                    proof_dependencies=proof_dependencies,
                    source_origins=source_origins,
                    summary=decl.value.summary,
                )
            )
        declarations.sort(key=lambda item: (item.node_path, item.name, item.revision))
        by_key = {
            (item.node_path, item.name, item.revision): item
            for item in declarations
        }
        scope_exports: dict[tuple[str, str, int], list[str]] = {}
        for node in tree.value.nodes:
            if node.kind.value != "scope":
                continue
            contract = self.runtime.node.contract.get_visible_contract(
                repo_root, node_path=node.path
            )
            if not contract.ok or contract.value is None:
                return self.runtime.foundation.fail(contract.issues)
            for ref in contract.value.contract.exports:
                key = (ref.node, ref.name, ref.revision)
                if key not in by_key:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "publication_scope_export_not_public",
                            "Scope export is not an exact ready public declaration.",
                            object_ref=f"{node.path}:{ref.node}.{ref.name}@{ref.revision}",
                        )
                    )
                scope_exports.setdefault(key, []).append(node.path)
        main_contract = self.runtime.node.contract.get_visible_contract(
            repo_root, node_path="Main"
        )
        if not main_contract.ok or main_contract.value is None:
            return self.runtime.foundation.fail(main_contract.issues)
        main_keys = {
            (ref.node, ref.name, ref.revision)
            for ref in main_contract.value.contract.exports
        }
        api_declarations = [by_key[key] for key in sorted(main_keys)]
        resolved_release_id = (
            release_id
            if release_id is not None
            else publication.value.publication.latest_release_id
        )
        completion_mode = config.value.config.completion_mode.value
        proof_availability = proof_availability_for_completion_mode(
            config.value.config.completion_mode
        ).value
        api = PublicApiDocument(
            repo_key=repo_root.name,
            repo_format=repo_format.value.repo_format.value,
            release_id=resolved_release_id,
            completion_mode=completion_mode,
            proof_availability=proof_availability,
            dependencies=dependencies.value,
            adapter_upstream=adapter_upstream.value,
            declarations=api_declarations,
            summary=f"Exported {len(api_declarations)} Main declarations.",
        )
        boundaries = PublicBoundariesDocument(
            repo_key=repo_root.name,
            repo_format=repo_format.value.repo_format.value,
            release_id=resolved_release_id,
            completion_mode=completion_mode,
            proof_availability=proof_availability,
            dependencies=dependencies.value,
            declarations=[
                PublicBoundaryDeclaration(
                    declaration=declaration,
                    exported_scope_paths=sorted(
                        scope_exports.get(
                            (
                                declaration.node_path,
                                declaration.name,
                                declaration.revision,
                            ),
                            [],
                        ),
                        key=lambda value: (value.count("."), value),
                        reverse=True,
                    ),
                    main_export=(
                        declaration.node_path,
                        declaration.name,
                        declaration.revision,
                    )
                    in main_keys,
                )
                for declaration in declarations
            ],
            summary=(
                f"Catalogued {len(declarations)} public declarations; "
                f"{len(api_declarations)} are exported through Main."
            ),
        )
        return self.runtime.foundation.ok((api, boundaries))

    def prepare_publication(
        self,
        repo_root: Path,
        *,
        title: str | None = None,
        presentation: RepoPublicationPresentation | None = None,
        release_id: str | None = None,
        semantic_manifest_digest: str | None = None,
        generated_at: str | None = None,
    ) -> ServiceResult[RepoPublicationPreparationView]:
        repo_root = Path(repo_root).resolve()
        presentation_path = (
            self.runtime.foundation.layout.publication_presentation_path(
                FoundationContext(repo_root=repo_root)
            )
        )
        presentation_written = False
        if presentation is not None:
            saved_presentation = self.set_presentation(
                repo_root,
                presentation=presentation,
            )
            if not saved_presentation.ok or saved_presentation.value is None:
                return self.runtime.foundation.fail(saved_presentation.issues)
            presentation_value = saved_presentation.value
            presentation_written = True
        else:
            loaded_presentation = self.get_presentation(repo_root)
            if (
                not loaded_presentation.ok
                or loaded_presentation.value is None
            ):
                return self.runtime.foundation.fail(loaded_presentation.issues)
            presentation_value = loaded_presentation.value
            if not presentation_path.exists():
                initialized = self.set_presentation(
                    repo_root,
                    presentation=presentation_value,
                )
                if not initialized.ok:
                    return self.runtime.foundation.fail(initialized.issues)
                presentation_written = True
        if title is not None:
            presentation_value = presentation_value.model_copy(
                update={"title": title.strip()}
            )
        gitignore = self.refresh_managed_gitignore(repo_root)
        if not gitignore.ok:
            return self.runtime.foundation.fail(gitignore.issues)
        public_documents = self._render_public_documents(
            repo_root, release_id=release_id
        )
        if not public_documents.ok or public_documents.value is None:
            return self.runtime.foundation.fail(public_documents.issues)
        api, boundaries = public_documents.value
        provenance = self._build_provenance(
            repo_root,
            release_id=release_id,
            semantic_manifest_digest=semantic_manifest_digest,
            generated_at=generated_at,
        )
        if not provenance.ok or provenance.value is None:
            return self.runtime.foundation.fail(provenance.issues)
        readme_text = self._render_readme_block(
            repo_root,
            api=api,
            presentation=presentation_value,
        )
        readme_path = repo_root / "README.md"
        existing = (
            readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
        )
        updated_readme = self._replace_managed_block(
            existing,
            begin=_README_BEGIN,
            end=_README_END,
            replacement=readme_text,
        )
        written: list[str] = []
        if presentation_written:
            written.append(presentation_path.relative_to(repo_root).as_posix())
        if updated_readme != existing:
            readme_path.write_text(updated_readme, encoding="utf-8")
            written.append("README.md")
        docs_root = repo_root / "docs" / "lean-constellation"
        docs_root.mkdir(parents=True, exist_ok=True)
        mark_asset = self._ensure_publication_mark(repo_root, docs_root=docs_root)
        if not mark_asset.ok or mark_asset.value is None:
            return self.runtime.foundation.fail(mark_asset.issues)
        written.extend(mark_asset.value)
        templates = self._ensure_publication_templates(
            repo_root,
            docs_root=docs_root,
        )
        if not templates.ok or templates.value is None:
            return self.runtime.foundation.fail(templates.issues)
        written.extend(templates.value)
        api_json_path = docs_root / "public-api.json"
        api_md_path = docs_root / "PUBLIC_API.md"
        boundaries_json_path = docs_root / "public-boundaries.json"
        boundaries_md_path = docs_root / "PUBLIC_BOUNDARIES.md"
        declarations_root = docs_root / "declarations"
        declarations_root.mkdir(parents=True, exist_ok=True)
        legacy_declarations_root = docs_root / "public-api"
        if legacy_declarations_root.is_dir():
            for legacy_page in legacy_declarations_root.glob("*.md"):
                legacy_page.unlink()
            try:
                legacy_declarations_root.rmdir()
            except OSError:
                pass
        assets_root = docs_root / "assets"
        api_svg_path = assets_root / "public-api.svg"
        boundaries_svg_path = assets_root / "public-boundaries.svg"
        provenance_path = docs_root / "provenance.json"
        publication_tree = self.runtime.node.node_tree.get_node_tree(repo_root)
        if not publication_tree.ok or publication_tree.value is None:
            return self.runtime.foundation.fail(publication_tree.issues)
        stale_pages = self._stale_declaration_pages(
            declarations_root=declarations_root,
            current=[item.declaration for item in boundaries.declarations],
        )
        for stale_page in stale_pages:
            stale_page.unlink()
        for path, value in (
            (api_json_path, api),
            (boundaries_json_path, boundaries),
            (provenance_path, provenance.value),
        ):
            result = self.runtime.foundation.store.write_json_atomic(path, value)
            if not result.ok:
                return self.runtime.foundation.fail(result.issues)
            written.append(path.relative_to(repo_root).as_posix())
        api_svg_path.write_text(
            self._render_public_boundary_svg(
                tree=publication_tree.value,
                declarations=api.declarations,
                propagation={
                    (item.declaration.node_path, item.declaration.name): item.exported_scope_paths
                    for item in boundaries.declarations
                    if item.main_export
                },
                title="Repository Public API",
            ),
            encoding="utf-8",
        )
        boundaries_svg_path.write_text(
            self._render_public_boundary_svg(
                tree=publication_tree.value,
                declarations=[item.declaration for item in boundaries.declarations],
                propagation={
                    (item.declaration.node_path, item.declaration.name): item.exported_scope_paths
                    for item in boundaries.declarations
                },
                title="Repository Public Boundaries",
            ),
            encoding="utf-8",
        )
        written.extend(
            [
                api_svg_path.relative_to(repo_root).as_posix(),
                boundaries_svg_path.relative_to(repo_root).as_posix(),
            ]
        )
        api_md_path.write_text(self._render_public_api_markdown(api), encoding="utf-8")
        written.append(api_md_path.relative_to(repo_root).as_posix())
        boundaries_md_path.write_text(
            self._render_public_boundaries_markdown(boundaries),
            encoding="utf-8",
        )
        written.append(boundaries_md_path.relative_to(repo_root).as_posix())
        for declaration in [
            item.declaration for item in boundaries.declarations
        ]:
            declaration_path = (
                declarations_root
                / f"{self._public_api_slug(declaration)}.md"
            )
            declaration_path.write_text(
                self._render_public_api_declaration_markdown(
                    api,
                    declaration=declaration,
                ),
                encoding="utf-8",
            )
            written.append(declaration_path.relative_to(repo_root).as_posix())
        manifest = self.build_manifest(repo_root, generated_at=generated_at)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        manifest_path = (
            repo_root
            / ".lean_constellation"
            / "publication"
            / "manifest.json"
        )
        result = self.runtime.foundation.store.write_json_atomic(
            manifest_path, manifest.value
        )
        if not result.ok:
            return self.runtime.foundation.fail(result.issues)
        written.append(manifest_path.relative_to(repo_root).as_posix())
        if gitignore.value:
            written.append(".gitignore")
        return self.runtime.foundation.ok(
            RepoPublicationPreparationView(
                repo_key=repo_root.name,
                manifest_path=manifest_path.relative_to(repo_root).as_posix(),
                presentation_path=presentation_path.relative_to(
                    repo_root
                ).as_posix(),
                readme_path="README.md",
                public_api_markdown_path=api_md_path.relative_to(repo_root).as_posix(),
                public_api_json_path=api_json_path.relative_to(repo_root).as_posix(),
                declarations_dir=declarations_root.relative_to(
                    repo_root
                ).as_posix(),
                public_boundaries_markdown_path=boundaries_md_path.relative_to(
                    repo_root
                ).as_posix(),
                public_boundaries_json_path=boundaries_json_path.relative_to(
                    repo_root
                ).as_posix(),
                provenance_path=provenance_path.relative_to(repo_root).as_posix(),
                gitignore_path=".gitignore",
                topics=presentation_value.topics,
                written_files=sorted(set(written)),
                summary="Prepared portable repository publication files.",
            )
        )

    def _build_provenance(
        self,
        repo_root: Path,
        *,
        release_id: str | None = None,
        semantic_manifest_digest: str | None = None,
        generated_at: str | None = None,
    ) -> ServiceResult[RepoProvenanceDocument]:
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        adapter_upstream = self._adapter_upstream_publication(repo_root)
        if not adapter_upstream.ok:
            return self.runtime.foundation.fail(adapter_upstream.issues)
        release = self.runtime.repo_workspace.release.get_latest_release(repo_root)
        release_value = release.value.release if release.ok and release.value else None
        source_manifest = self.runtime.material.source_corpus.get_source_corpus_manifest(
            repo_root
        )
        source_digest = None
        source_files: dict[str, str] = {}
        if source_manifest.ok and source_manifest.value is not None:
            source_digest = self.runtime.material.source_corpus.canonical_manifest_digest(
                source_manifest.value
            )
            source_files = {
                item.path: item.sha256
                for item in source_manifest.value.files
                if item.sha256 is not None
            }
        toolchain_path = repo_root / "lean-toolchain"
        toolchain = (
            toolchain_path.read_text(encoding="utf-8").strip()
            if toolchain_path.exists()
            else None
        )
        return self.runtime.foundation.ok(
            RepoProvenanceDocument(
                repo_key=repo_root.name,
                repo_format=repo_format.value.repo_format.value,
                release_id=(
                    release_id
                    if release_id is not None
                    else publication.value.publication.latest_release_id
                ),
                semantic_manifest_digest=(
                    semantic_manifest_digest
                    if semantic_manifest_digest is not None
                    else (
                        release_value.semantic_manifest_digest
                        if release_value is not None
                        else None
                    )
                ),
                **({"generated_at": generated_at} if generated_at is not None else {}),
                lean_toolchain=toolchain,
                source_manifest_digest=source_digest,
                source_files=source_files,
                adapter_upstream=adapter_upstream.value,
                summary="Portable Lean Constellation provenance.",
            )
        )

    def _adapter_upstream_publication(
        self,
        repo_root: Path,
    ) -> ServiceResult[AdapterUpstreamPublication | None]:
        repo_format = self.runtime.repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return self.runtime.foundation.fail(repo_format.issues)
        if repo_format.value.repo_format != RepoFormat.ADAPTER:
            return self.runtime.foundation.ok(None)
        upstream = self.runtime.adapter.get_adapter_upstream_metadata(repo_root)
        modules = self.runtime.adapter.list_visible_upstream_modules(repo_root)
        if not upstream.ok or upstream.value is None:
            return self.runtime.foundation.fail(upstream.issues)
        if not modules.ok or modules.value is None:
            return self.runtime.foundation.fail(modules.issues)
        return self.runtime.foundation.ok(
            AdapterUpstreamPublication(
                source_kind=upstream.value.source_kind,
                git_url=upstream.value.git_url,
                revision=upstream.value.revision,
                subdir=upstream.value.subdir,
                package_name=upstream.value.package_name,
                dependency_name=upstream.value.dependency_name,
                visible_modules=list(modules.value.modules),
                trusted_build=upstream.value.trusted_build,
            )
        )

    def _publication_dependencies(
        self,
        repo_root: Path,
    ) -> ServiceResult[list[RepoPublicationDependency]]:
        listed = self.runtime.repo_workspace.requirement.list_requirements(repo_root)
        if not listed.ok or listed.value is None:
            return self.runtime.foundation.fail(listed.issues)
        grouped: dict[str, RepoPublicationDependency] = {}
        for view in listed.value:
            requirement = view.requirement
            if requirement.status.value == "obsolete":
                continue
            provider_repo = requirement.provider_repo or requirement.target_repo
            provider_completion_mode = None
            provider_release_id = requirement.provider_release_id
            provider_root = repo_root.parent / provider_repo
            if provider_release_id is not None and provider_root.is_dir():
                release = self.runtime.repo_workspace.release.get_release(
                    provider_root,
                    release_id=provider_release_id,
                )
                if release.ok and release.value is not None:
                    provider_completion_mode = (
                        release.value.release.completion_mode.value
                    )
            source = requirement.provider_git_url
            if source is None and provider_root.is_dir():
                source = "local workspace"
            current = grouped.get(provider_repo)
            required = requirement.required_proof_availability
            if (
                current is None
                or required == ProofAvailability.PROVED
                and current.required_proof_availability
                != ProofAvailability.PROVED.value
            ):
                grouped[provider_repo] = RepoPublicationDependency(
                    repo=provider_repo,
                    required_proof_availability=required.value,
                    provider_completion_mode=provider_completion_mode,
                    provider_release_id=provider_release_id,
                    source=source,
                    status=requirement.status.value,
                )
        return self.runtime.foundation.ok(
            [grouped[key] for key in sorted(grouped)]
        )

    def _ensure_publication_mark(
        self,
        repo_root: Path,
        *,
        docs_root: Path,
    ) -> ServiceResult[list[str]]:
        asset_root = docs_root / "assets"
        asset_root.mkdir(parents=True, exist_ok=True)
        target = asset_root / "lean-constellation-mark.svg"
        source = (
            package_files("lean_constellation")
            .joinpath("assets")
            .joinpath("lean-constellation-mark.svg")
            .read_text(encoding="utf-8")
        )
        if target.exists() and target.read_text(encoding="utf-8") == source:
            return self.runtime.foundation.ok([])
        target.write_text(source, encoding="utf-8")
        return self.runtime.foundation.ok(
            [target.relative_to(repo_root).as_posix()]
        )

    def _ensure_publication_templates(
        self,
        repo_root: Path,
        *,
        docs_root: Path,
    ) -> ServiceResult[list[str]]:
        written: list[str] = []
        candidates = (
            (
                repo_root / "CITATION.cff",
                docs_root / "CITATION_TEMPLATE.cff",
                _CITATION_TEMPLATE,
            ),
            (
                repo_root / "LICENSE",
                docs_root / "LICENSING_TEMPLATE.md",
                _LICENSING_TEMPLATE,
            ),
        )
        for authoritative, template_path, content in candidates:
            if authoritative.exists() or template_path.exists():
                continue
            template_path.write_text(content, encoding="utf-8")
            written.append(template_path.relative_to(repo_root).as_posix())
        return self.runtime.foundation.ok(written)

    @staticmethod
    def _dependency_label(value: object) -> str:
        ref = getattr(value, "ref", value)
        repository = getattr(ref, "repo", None) or "current repo"
        node = getattr(ref, "node", None)
        name = getattr(ref, "name", None) or getattr(ref, "decl", None)
        module = getattr(ref, "module", None)
        if node and name:
            return f"{repository}:{node}.{name}"
        if module and name:
            return f"Mathlib:{module}.{name}"
        return str(name or value)

    @staticmethod
    def _origin_label(value: object) -> str:
        path = getattr(value, "source_path", None)
        start = getattr(value, "start_line", None)
        end = getattr(value, "end_line", None)
        if path:
            suffix = f":{start}-{end}" if start is not None else ""
            return f"{path}{suffix}"
        locator = getattr(value, "locator", None)
        return str(locator or getattr(value, "kind", value))

    @classmethod
    def _render_public_api_markdown(cls, value: PublicApiDocument) -> str:
        declarations = cls._ordered_public_api_declarations(value.declarations)
        declaration_count = len(declarations)
        node_count = len({item.node_path for item in declarations})
        declaration_noun = "declaration" if declaration_count == 1 else "declarations"
        node_noun = "node" if node_count == 1 else "nodes"
        lines = [
            "# Public API",
            "",
            f"- Repository format: `{value.repo_format}`",
            f"- Repository completion: `{value.completion_mode}`",
            f"- Proof availability: `{value.proof_availability}`",
            f"- Public {declaration_noun}: `{declaration_count}` across `{node_count}` {node_noun}",
        ]
        if value.adapter_upstream is not None:
            upstream = value.adapter_upstream
            locator = upstream.git_url or upstream.source_kind
            lines.extend(
                [
                    "",
                    "## Adapter upstream",
                    "",
                    f"- Source: {cls._markdown_cell(locator)}",
                    f"- Revision: `{upstream.revision or 'unversioned'}`",
                    f"- Package: `{upstream.package_name}`",
                    f"- Visible modules: `{len(upstream.visible_modules)}`",
                ]
            )
        lines.extend([
            "",
            "## Dependency graph",
            "",
            "Arrows run from each consumer to the public declarations it depends "
            "on. Solid arrows are Statement dependencies; dashed arrows are Proof "
            "dependencies. Transitively implied edges are omitted for readability; "
            "each declaration page lists the complete direct dependency set. "
            "Mathlib and non-public project dependencies are not shown.",
            "",
            "[![Public API dependency graph](assets/public-api.svg)]"
            "(assets/public-api.svg)",
            "",
            "## Declarations",
            "",
            "| Node | Declaration | Kind | Status |",
            "| --- | --- | --- | --- |",
        ])
        lines.extend(
            "| "
            + " | ".join(
                [
                    f"`{decl.node_path}`",
                    (
                        "[`"
                        + decl.name
                        + "`](declarations/"
                        + cls._public_api_slug(decl)
                        + ".md)"
                    ),
                    f"`{decl.kind}`",
                    f"`{decl.state}`",
                ]
            )
            + " |"
            for decl in declarations
        )
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def _render_public_boundaries_markdown(
        cls,
        value: PublicBoundariesDocument,
    ) -> str:
        declarations = cls._ordered_public_api_declarations(
            [item.declaration for item in value.declarations]
        )
        by_key = {
            (item.declaration.node_path, item.declaration.name): item
            for item in value.declarations
        }
        adapter = value.repo_format == RepoFormat.ADAPTER.value
        lines = [
            "# Public Boundaries",
            "",
            (
                "This Adapter catalog records the flat committed `Main` public boundary. "
                "Every listed declaration is an exact wrapper reference to the pinned upstream package."
                if adapter
                else "This catalog shows every Content-public declaration and how it is "
                "selectively propagated through Scope boundaries. Only declarations "
                "exported through `Main` belong to the repository Public API."
            ),
            "",
            f"- {'Adapter public' if adapter else 'Content-public'} declarations: `{len(declarations)}`",
            f"- Main exports: `{sum(item.main_export for item in value.declarations)}`",
            "",
            "## Boundary graph",
            "",
            (
                "The flat graph shows Adapter declaration dependencies and the committed Main API."
                if adapter
                else "Solid gray arrows are Statement dependencies; dashed amber arrows "
                "are Proof dependencies. A thick teal declaration frame marks a Main "
                "export; compact upward labels record intermediate Scope exports."
            ),
            "",
            "[![Repository public boundary graph](assets/public-boundaries.svg)]"
            "(assets/public-boundaries.svg)",
            "",
            "## Declarations",
            "",
            "| Node | Declaration | Kind | Status | Exported through | Main API |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for declaration in declarations:
            boundary = by_key[(declaration.node_path, declaration.name)]
            exported = (
                ", ".join(f"`{item}`" for item in boundary.exported_scope_paths)
                or "—"
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{declaration.node_path}`",
                        (
                            "[`"
                            + declaration.name
                            + "`](declarations/"
                            + cls._public_api_slug(declaration)
                            + ".md)"
                        ),
                        f"`{declaration.kind}`",
                        f"`{declaration.state}`",
                        exported,
                        "yes" if boundary.main_export else "no",
                    ]
                )
                + " |"
            )
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def _render_public_boundary_svg(
        cls,
        *,
        tree: object,
        declarations: list[PublicApiDeclaration],
        propagation: dict[tuple[str, str], list[str]],
        title: str,
    ) -> str:
        try:
            return render_publication_graph_svg(
                tree=tree,
                declarations=declarations,
                propagation=propagation,
                dependency_edges=cls._transitively_reduced_public_dependency_edges(
                    declarations
                ),
                declaration_links={
                    (declaration.node_path, declaration.name): (
                        "../declarations/"
                        + cls._public_api_slug(declaration)
                        + ".md"
                    )
                    for declaration in declarations
                },
                title=title,
            )
        except (PublicationGraphvizUnavailable, OSError):
            return cls._render_public_boundary_svg_legacy(
                tree=tree,
                declarations=declarations,
                propagation=propagation,
                title=title,
            )

    @classmethod
    def _render_public_boundary_svg_legacy(
        cls,
        *,
        tree: object,
        declarations: list[PublicApiDeclaration],
        propagation: dict[tuple[str, str], list[str]],
        title: str,
    ) -> str:
        """Render one deterministic nested Scope/Content declaration graph."""

        ordered = cls._ordered_public_api_declarations(declarations)
        if not ordered:
            return (
                '<svg xmlns="http://www.w3.org/2000/svg" width="760" height="160" '
                'viewBox="0 0 760 160" role="img">\n'
                f'  <title>{html.escape(title)}</title>\n'
                '  <rect width="760" height="160" fill="#ffffff"/>\n'
                f'  <text x="28" y="42" font-family="ui-sans-serif, system-ui" '
                f'font-size="20" font-weight="700" fill="#102a43">{html.escape(title)}</text>\n'
                '  <text x="28" y="86" font-family="ui-sans-serif, system-ui" '
                'font-size="14" fill="#627d98">No exported declarations.</text>\n'
                '</svg>\n'
            )
        by_content: dict[str, list[PublicApiDeclaration]] = {}
        for declaration in ordered:
            by_content.setdefault(declaration.node_path, []).append(declaration)
        nodes = {getattr(item, "path"): item for item in getattr(tree, "nodes")}
        relevant: set[str] = set(by_content)
        for content_path in by_content:
            parent = getattr(nodes.get(content_path), "parent_path", None)
            while parent is not None:
                relevant.add(parent)
                parent = getattr(nodes.get(parent), "parent_path", None)
        relevant.add("Main")
        children: dict[str, list[str]] = {path: [] for path in relevant}
        for path in relevant:
            if path == "Main":
                continue
            parent = getattr(nodes.get(path), "parent_path", None)
            if parent in relevant:
                children[parent].append(path)
        declaration_rank = {
            (item.node_path, item.name): index for index, item in enumerate(ordered)
        }

        def subtree_rank(path: str) -> tuple[int, str]:
            ranks = [
                declaration_rank[(item.node_path, item.name)]
                for content_path, items in by_content.items()
                if content_path == path or content_path.startswith(path + ".")
                for item in items
            ]
            return (min(ranks) if ranks else len(ordered), path)

        for values in children.values():
            values.sort(key=subtree_rank)
        sizes: dict[str, tuple[float, float]] = {}
        header = 34.0
        padding = 18.0
        gap = 14.0
        decl_height = 46.0

        def measure(path: str) -> tuple[float, float]:
            if path in by_content:
                label_width = max(
                    [len(path) * 7.8]
                    + [len(item.name) * 8.4 for item in by_content[path]]
                )
                value = (
                    max(330.0, min(760.0, label_width + 58.0)),
                    header
                    + padding
                    + len(by_content[path]) * decl_height
                    + max(0, len(by_content[path]) - 1) * 8.0
                    + padding,
                )
            else:
                child_sizes = [measure(child) for child in children.get(path, [])]
                value = (
                    max(
                        380.0,
                        len(path) * 7.4 + 62.0,
                        max((width for width, _ in child_sizes), default=280.0)
                        + 116.0,
                    ),
                    header
                    + padding
                    + sum(height for _, height in child_sizes)
                    + max(0, len(child_sizes) - 1) * gap
                    + padding,
                )
            sizes[path] = value
            return value

        root_width, root_height = measure("Main")
        node_boxes: dict[str, tuple[float, float, float, float]] = {}
        decl_boxes: dict[tuple[str, str], tuple[float, float, float, float]] = {}
        left_margin = 104.0
        top_margin = 70.0

        def place(path: str, x: float, y: float) -> None:
            width, height = sizes[path]
            node_boxes[path] = (x, y, width, height)
            if path in by_content:
                decl_y = y + header + padding
                for declaration in by_content[path]:
                    decl_boxes[(path, declaration.name)] = (
                        x + padding,
                        decl_y,
                        width - 2 * padding,
                        decl_height,
                    )
                    decl_y += decl_height + 8.0
                return
            child_y = y + header + padding
            for child in children.get(path, []):
                place(child, x + padding, child_y)
                child_y += sizes[child][1] + gap

        place("Main", left_margin, top_margin)
        canvas_width = root_width + left_margin + 155.0
        canvas_height = root_height + top_margin + 72.0
        svg: list[str] = [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_width:.0f}" '
                f'height="{canvas_height:.0f}" viewBox="0 0 {canvas_width:.0f} '
                f'{canvas_height:.0f}" role="img">'
            ),
            f"  <title>{html.escape(title)}</title>",
            "  <defs>",
            '    <marker id="dep-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#829ab1"/></marker>',
            '    <marker id="proof-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="#b7791f"/></marker>',
            '    <marker id="export-arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0,0 L9,4.5 L0,9 Z" fill="#087ea4"/></marker>',
            "  </defs>",
            f'  <rect width="{canvas_width:.0f}" height="{canvas_height:.0f}" fill="#ffffff"/>',
            f'  <text x="{left_margin:.0f}" y="34" font-family="ui-sans-serif, system-ui" font-size="21" font-weight="700" fill="#102a43">{html.escape(title)}</text>',
        ]
        dependency_lines: list[str] = []
        for consumer, provider, kind in cls._transitively_reduced_public_dependency_edges(
            declarations
        ):
            consumer_box = decl_boxes.get(consumer)
            provider_box = decl_boxes.get(provider)
            if consumer_box is None or provider_box is None:
                continue
            cx, cy, _, ch = consumer_box
            px, py, _, ph = provider_box
            lane_seed = hashlib.sha256(
                repr((consumer, provider)).encode("utf-8")
            ).digest()[0]
            lane_x = 34.0 + (lane_seed % 4) * 12.0
            dash = ' stroke-dasharray="7 5"' if kind == "Proof" else ""
            color = "#b7791f" if kind == "Proof" else "#829ab1"
            marker = "proof-arrow" if kind == "Proof" else "dep-arrow"
            dependency_lines.append(
                f'    <path d="M {cx:.1f} {cy + ch / 2:.1f} H {lane_x:.1f} V '
                f'{py + ph / 2:.1f} H {px - 5:.1f}" stroke="{color}" '
                f'stroke-width="1.5"{dash} marker-end="url(#{marker})"/>'
            )
        svg.append('  <g id="boundaries">')
        for path, (x, y, width, height) in sorted(
            node_boxes.items(), key=lambda item: (item[0].count("."), item[0])
        ):
            is_content = path in by_content
            fill = "#f8fbfd" if is_content else "#f3f8fb"
            stroke = "#9fb3c8" if is_content else "#486581"
            label = "Content" if is_content else "Scope"
            svg.extend(
                [
                    f'    <rect data-node="{html.escape(path, quote=True)}" x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="1.4"/>',
                    f'    <text x="{x + 14:.1f}" y="{y + 22:.1f}" font-family="ui-sans-serif, system-ui" font-size="13" font-weight="700" fill="#243b53">{html.escape(path)}</text>',
                    f'    <text x="{x + width - 14:.1f}" y="{y + 22:.1f}" text-anchor="end" font-family="ui-sans-serif, system-ui" font-size="10" letter-spacing="0.7" fill="#627d98">{label}</text>',
                ]
            )
        svg.append("  </g>")
        svg.append('  <g id="declarations">')
        for declaration in ordered:
            x, y, width, height = decl_boxes[
                (declaration.node_path, declaration.name)
            ]
            state_color = "#0f8f88" if declaration.proof_available else "#2563eb"
            svg.extend(
                [
                    f'    <rect data-declaration="{html.escape(declaration.node_path + "." + declaration.name, quote=True)}" x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" height="{height:.1f}" rx="6" fill="#ffffff" stroke="#bcccdc"/>',
                    f'    <rect x="{x:.1f}" y="{y:.1f}" width="5" height="{height:.1f}" rx="2.5" fill="{state_color}"/>',
                    f'    <text x="{x + 16:.1f}" y="{y + 20:.1f}" font-family="ui-monospace, SFMono-Regular, monospace" font-size="12.5" font-weight="600" fill="#102a43">{html.escape(declaration.name)}</text>',
                    f'    <text x="{x + 16:.1f}" y="{y + 36:.1f}" font-family="ui-sans-serif, system-ui" font-size="10.5" fill="#627d98">{html.escape(declaration.kind)} · {html.escape(declaration.state)}</text>',
                ]
            )
        svg.append("  </g>")
        svg.append('  <g id="dependencies" fill="none" stroke-linecap="round">')
        svg.extend(dependency_lines)
        svg.append("  </g>")
        svg.append('  <g id="exports" fill="none" stroke="#087ea4" stroke-width="1.8">')
        main_export_keys = [
            (declaration.node_path, declaration.name)
            for declaration in ordered
            if "Main"
            in propagation.get((declaration.node_path, declaration.name), [])
        ]
        main_export_rank = {
            key: index for index, key in enumerate(main_export_keys)
        }
        for index, declaration in enumerate(ordered):
            key = (declaration.node_path, declaration.name)
            exported_scopes = propagation.get(key, [])
            if not exported_scopes:
                continue
            x, y, width, height = decl_boxes[key]
            points = [(x + width, y + height / 2)]
            for scope_path in sorted(
                exported_scopes,
                key=lambda value: (value.count("."), value),
                reverse=True,
            ):
                scope_box = node_boxes.get(scope_path)
                if scope_box is None:
                    continue
                sx, sy, sw, _ = scope_box
                target_x = sx + sw - 22.0 - (index % 4) * 8.0
                points.append((target_x, points[-1][1]))
                target_y = (
                    sy + 24.0 + main_export_rank[key] * 11.0
                    if scope_path == "Main"
                    else sy + 17.0 + (index % 4) * 3.0
                )
                points.append((target_x, target_y))
            path_data = "M " + " L ".join(
                f"{point_x:.1f} {point_y:.1f}" for point_x, point_y in points
            )
            svg.append(f'    <path d="{path_data}" opacity="0.78"/>')
            svg.append(
                f'    <circle cx="{x + width:.1f}" cy="{y + height / 2:.1f}" r="3.2" fill="#087ea4" stroke="none"/>'
            )
            if "Main" in exported_scopes:
                main_x, main_y, main_width, _ = node_boxes["Main"]
                end_y = main_y + 24.0 + main_export_rank[key] * 11.0
                svg.append(
                    f'    <path d="M {main_x + main_width - 22.0 - (index % 4) * 8.0:.1f} '
                    f'{end_y:.1f} H {canvas_width - 28.0:.1f}" '
                    'marker-end="url(#export-arrow)"/>'
                )
        if main_export_keys:
            svg.append(
                f'    <text x="{canvas_width - 30.0:.1f}" y="{top_margin - 10.0:.1f}" text-anchor="end" '
                'font-family="ui-sans-serif, system-ui" font-size="10" fill="#087ea4" stroke="none">external API</text>'
            )
        svg.extend(
            [
                "  </g>",
                f'  <text x="{left_margin:.0f}" y="{canvas_height - 24:.0f}" font-family="ui-sans-serif, system-ui" font-size="10.5" fill="#829ab1">Solid: Statement dependency · dashed: Proof dependency · blue: Scope export propagation</text>',
                "</svg>",
                "",
            ]
        )
        return "\n".join(svg)

    @staticmethod
    def _render_public_api_declaration_markdown(
        value: PublicApiDocument,
        *,
        declaration: PublicApiDeclaration,
    ) -> str:
        lines = [
            "[← Public API](../PUBLIC_API.md) · "
            "[Public boundaries](../PUBLIC_BOUNDARIES.md)",
            "",
            f"# `{declaration.name}`",
            "",
            declaration.summary or "No declaration summary is available.",
            "",
            f"- Kind: `{declaration.kind}`",
            f"- Node: `{declaration.node_path}`",
            f"- Module: `{declaration.module}`",
            f"- State: `{declaration.state}`",
            f"- Revision status: `{declaration.status}`",
            f"- Repository completion: `{value.completion_mode}`",
            (
                "- Formal code: final proof projection"
                if declaration.proof_available
                else "- Formal code: final statement projection"
            ),
        ]
        if declaration.formal_code:
            lines.extend(
                ["", "## Lean code", "", "```lean", declaration.formal_code.rstrip(), "```"]
            )
        if declaration.statement_dependencies:
            lines.extend(
                [
                    "",
                    "## Statement dependencies",
                    "",
                    *[
                        f"- `{item}`"
                        for item in declaration.statement_dependencies
                    ],
                ]
            )
        if declaration.proof_dependencies:
            lines.extend(
                [
                    "",
                    "## Proof dependencies",
                    "",
                    *[f"- `{item}`" for item in declaration.proof_dependencies],
                ]
            )
        if declaration.source_origins:
            lines.extend(
                [
                    "",
                    "## Sources",
                    "",
                    *[f"- `{item}`" for item in declaration.source_origins],
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def _ordered_public_api_declarations(
        cls,
        declarations: list[PublicApiDeclaration],
    ) -> list[PublicApiDeclaration]:
        by_key = {
            (item.node_path, item.name): item
            for item in declarations
        }
        edges = cls._public_dependency_edges(declarations)
        nodes = sorted({item.node_path for item in declarations})
        node_edges = {
            (consumer[0], provider[0])
            for consumer, provider, _ in edges
            if consumer[0] != provider[0]
        }
        node_order = cls._consumer_first_topological_order(nodes, node_edges)
        ordered: list[PublicApiDeclaration] = []
        for node_path in node_order:
            node_keys = sorted(
                key for key in by_key if key[0] == node_path
            )
            internal_edges = {
                (consumer, provider)
                for consumer, provider, _ in edges
                if consumer[0] == node_path and provider[0] == node_path
            }
            ordered.extend(
                by_key[key]
                for key in cls._consumer_first_topological_order(
                    node_keys,
                    internal_edges,
                )
            )
        return ordered

    @classmethod
    def _public_dependency_edges(
        cls,
        declarations: list[PublicApiDeclaration],
    ) -> list[
        tuple[tuple[str, str], tuple[str, str], Literal["Statement", "Proof"]]
    ]:
        by_label = {
            f"current repo:{item.node_path}.{item.name}": (
                item.node_path,
                item.name,
            )
            for item in declarations
        }
        edges: set[
            tuple[
                tuple[str, str],
                tuple[str, str],
                Literal["Statement", "Proof"],
            ]
        ] = set()
        statement_pairs: set[
            tuple[tuple[str, str], tuple[str, str]]
        ] = set()
        for declaration in declarations:
            consumer = (declaration.node_path, declaration.name)
            for dependency in declaration.statement_dependencies:
                provider = by_label.get(dependency)
                if provider is None or provider == consumer:
                    continue
                edges.add((consumer, provider, "Statement"))
                statement_pairs.add((consumer, provider))
            for dependency in declaration.proof_dependencies:
                provider = by_label.get(dependency)
                if (
                    provider is None
                    or provider == consumer
                    or (consumer, provider) in statement_pairs
                ):
                    continue
                edges.add((consumer, provider, "Proof"))
        return sorted(edges)

    @staticmethod
    def _consumer_first_topological_order(
        values: list[object],
        edges: set[tuple[object, object]],
    ) -> list[object]:
        remaining = set(values)
        outgoing = {value: set() for value in values}
        incoming_count = {value: 0 for value in values}
        for consumer, provider in edges:
            if (
                consumer not in remaining
                or provider not in remaining
                or provider in outgoing[consumer]
            ):
                continue
            outgoing[consumer].add(provider)
            incoming_count[provider] += 1
        ready = sorted(
            (value for value in values if incoming_count[value] == 0),
            key=str,
        )
        ordered: list[object] = []
        while ready:
            current = ready.pop(0)
            if current not in remaining:
                continue
            remaining.remove(current)
            ordered.append(current)
            for provider in sorted(outgoing[current], key=str):
                incoming_count[provider] -= 1
                if incoming_count[provider] == 0:
                    ready.append(provider)
                    ready.sort(key=str)
        ordered.extend(sorted(remaining, key=str))
        return ordered

    @classmethod
    def _transitively_reduced_public_dependency_edges(
        cls,
        declarations: list[PublicApiDeclaration],
    ) -> list[
        tuple[tuple[str, str], tuple[str, str], Literal["Statement", "Proof"]]
    ]:
        edges = cls._public_dependency_edges(declarations)
        pairs = {(consumer, provider) for consumer, provider, _ in edges}
        outgoing: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for consumer, provider in pairs:
            outgoing.setdefault(consumer, set()).add(provider)

        def has_alternate_path(
            start: tuple[str, str],
            target: tuple[str, str],
            *,
            excluded: tuple[tuple[str, str], tuple[str, str]],
        ) -> bool:
            pending = [
                value
                for value in outgoing.get(start, set())
                if (start, value) != excluded
            ]
            visited: set[tuple[str, str]] = set()
            while pending:
                current = pending.pop()
                if current == target:
                    return True
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(outgoing.get(current, set()) - visited)
            return False

        return [
            edge
            for edge in edges
            if not has_alternate_path(
                edge[0],
                edge[1],
                excluded=(edge[0], edge[1]),
            )
        ]

    @classmethod
    def _public_api_slug(cls, declaration: PublicApiDeclaration) -> str:
        return cls._public_api_slug_from_values(
            node_path=declaration.node_path,
            name=declaration.name,
        )

    @staticmethod
    def _public_api_slug_from_values(*, node_path: str, name: str) -> str:
        value = f"{node_path}--{name}".lower()
        return re.sub(r"[^a-z0-9]+", "-", value).strip("-")

    @classmethod
    def _stale_declaration_pages(
        cls,
        *,
        declarations_root: Path,
        current: list[PublicApiDeclaration],
    ) -> list[Path]:
        current_names = {
            f"{cls._public_api_slug(item)}.md"
            for item in current
        }
        return [
            path
            for path in declarations_root.glob("*.md")
            if path.name not in current_names
        ]

    @classmethod
    def _render_readme_block(
        cls,
        repo_root: Path,
        *,
        api: PublicApiDocument,
        presentation: RepoPublicationPresentation,
    ) -> str:
        title = presentation.title or repo_root.name
        description = presentation.description or (
            "A Lean 4 formalization project generated with Lean Constellation."
        )
        declaration_count = len(api.declarations)
        node_count = len({item.node_path for item in api.declarations})
        declaration_noun = "declaration" if declaration_count == 1 else "declarations"
        node_noun = "node" if node_count == 1 else "nodes"
        lines = [
            _README_BEGIN,
            (
                f'<h1><img src="{_PUBLICATION_MARK_PATH}" '
                'alt="Lean Constellation mark" width="42" '
                f'align="absmiddle"> {html.escape(title)}</h1>'
            ),
            "",
            cls._render_badges(repo_root, api=api, presentation=presentation),
            "",
            description,
            "",
            "## Project status",
            "",
            "| Property | Value |",
            "| --- | --- |",
            f"| Completion | `{api.completion_mode}` |",
            f"| Proof availability | `{api.proof_availability}` |",
            f"| Repository format | `{api.repo_format}` |",
            "",
            "## Build",
            "",
            "```sh",
            "lake build",
            "```",
        ]
        if api.adapter_upstream is not None:
            upstream = api.adapter_upstream
            source = (
                f"[{upstream.git_url}]({upstream.git_url})"
                if upstream.git_url is not None
                else f"`{upstream.source_kind}`"
            )
            lines.extend(
                [
                    "",
                    "## Adapter upstream",
                    "",
                    "This repository is a reviewed Lean adapter over an immutable upstream package.",
                    "",
                    "| Property | Value |",
                    "| --- | --- |",
                    f"| Source | {source} |",
                    f"| Revision | `{upstream.revision or 'unversioned'}` |",
                    f"| Package | `{upstream.package_name}` |",
                    f"| Lake dependency | `{upstream.dependency_name}` |",
                    f"| Visible modules | {', '.join(f'`{item}`' for item in upstream.visible_modules) or '—'} |",
                ]
            )
        if api.dependencies:
            lines.extend(
                [
                    "",
                    "## Repository dependencies",
                    "",
                    "| Repository | Required proofs | Provider completion | Release | Source |",
                    "| --- | --- | --- | --- | --- |",
                    *[
                        "| "
                        + " | ".join(
                            [
                                cls._markdown_cell(item.repo),
                                f"`{item.required_proof_availability}`",
                                (
                                    f"`{item.provider_completion_mode}`"
                                    if item.provider_completion_mode
                                    else "unknown"
                                ),
                                (
                                    f"`{item.provider_release_id}`"
                                    if item.provider_release_id
                                    else "unreleased"
                                ),
                                cls._dependency_source_markdown(item),
                            ]
                        )
                        + " |"
                        for item in api.dependencies
                    ],
                ]
            )
        lines.extend(
            [
                "",
                "## Public API",
                "",
                (
                    f"This repository exports **{declaration_count} public "
                    f"{declaration_noun}** across **{node_count} {node_noun}**."
                ),
                "",
                "Browse the [Public API index](docs/lean-constellation/PUBLIC_API.md) "
                "for the dependency graph, declaration index, final Lean code, "
                "dependencies, and sources. The "
                "[public boundary catalog](docs/lean-constellation/PUBLIC_BOUNDARIES.md) "
                + (
                    "documents the flat committed Adapter Main boundary."
                    if api.repo_format == RepoFormat.ADAPTER.value
                    else "documents internal Content-public declarations and Scope propagation."
                ),
            ]
        )
        lines.extend(
            [
                "",
                "## About this formalization",
                "",
                presentation.about_markdown
                or (
                    "<!-- Add mathematical provenance and formalization credit in "
                    ".lean_constellation/publication/presentation.json. -->"
                ),
                "",
                "## Citation",
                "",
                presentation.citation_markdown
                or (
                    "Citation metadata has not been supplied yet. Complete "
                    "`docs/lean-constellation/CITATION_TEMPLATE.cff` and publish it "
                    "as `CITATION.cff`."
                ),
                "",
                "## Licensing",
                "",
                presentation.licensing_markdown
                or (
                    "Licensing metadata has not been supplied yet. Complete "
                    "`docs/lean-constellation/LICENSING_TEMPLATE.md` and add the "
                    "corresponding license files before public distribution."
                ),
                "",
                '<p align="center">',
                (
                    f'  <img src="{_PUBLICATION_MARK_PATH}" '
                    'alt="Lean Constellation" width="72">'
                ),
                "  <br>",
                "  <sub>Generated with <strong>Lean Constellation</strong></sub>",
                "</p>",
                _README_END,
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    @classmethod
    def _render_badges(
        cls,
        repo_root: Path,
        *,
        api: PublicApiDocument,
        presentation: RepoPublicationPresentation,
    ) -> str:
        status_color = {
            ProofAvailability.DECLARED.value: "2563eb",
            ProofAvailability.PROVED.value: "0f8f88",
        }.get(api.proof_availability, "6b7280")
        badges = [
            ("status", api.proof_availability, status_color, None),
        ]
        toolchain_path = repo_root / "lean-toolchain"
        if toolchain_path.exists():
            toolchain = toolchain_path.read_text(encoding="utf-8").strip()
            lean_version = toolchain.rsplit(":", maxsplit=1)[-1].removeprefix("v")
            badges.append(("Lean", lean_version, "6b4fbb", None))
        badges.extend(
            (item.label, item.message, item.color, item.link)
            for item in presentation.badges
        )
        badges.extend(
            (
                (
                    "LC",
                    "Lean Constellation",
                    "092745",
                    _LEAN_CONSTELLATION_REPOSITORY_URL,
                ),
                (
                    "MCP",
                    "Lean Toolkit",
                    "e45132",
                    _LEAN_MCP_TOOLKIT_REPOSITORY_URL,
                ),
            )
        )
        rendered = []
        for label, message, color, link in badges:
            image = "https://img.shields.io/static/v1?" + urlencode(
                {
                    "label": label,
                    "message": message,
                    "color": color,
                    "style": "flat-square",
                }
            )
            markdown = f"![{label}: {message}]({image})"
            rendered.append(f"[{markdown}]({link})" if link else markdown)
        return " ".join(rendered)

    @staticmethod
    def _markdown_cell(value: str) -> str:
        return value.replace("|", "\\|")

    @classmethod
    def _dependency_source_markdown(
        cls,
        dependency: RepoPublicationDependency,
    ) -> str:
        source = dependency.source
        if source is None:
            return "not recorded"
        if source.startswith(("https://", "http://")):
            return f"[remote]({source})"
        return cls._markdown_cell(source)

    @staticmethod
    def _replace_managed_block(
        current: str,
        *,
        begin: str,
        end: str,
        replacement: str,
    ) -> str:
        normalized = current.rstrip()
        replacement = replacement.rstrip()
        start = normalized.find(begin)
        finish = normalized.find(end)
        if start >= 0 and finish >= start:
            finish += len(end)
            updated = normalized[:start].rstrip()
            suffix = normalized[finish:].strip()
            pieces = [piece for piece in (updated, replacement, suffix) if piece]
            return "\n\n".join(pieces) + "\n"
        return (
            f"{normalized}\n\n{replacement}\n"
            if normalized
            else f"{replacement}\n"
        )

    @staticmethod
    def _excluded_directory_reason(relpath: str) -> str | None:
        path = PurePosixPath(relpath)
        if path.parts[0] in _EXCLUDED_TOP_LEVEL:
            return "runtime_or_git_state"
        if (
            len(path.parts) >= 2
            and path.parts[0] == ".lean_constellation"
            and path.parts[1] in _EXCLUDED_CONSTELLATION_DIRS
        ):
            return "local_checkpoint_or_lock"
        if path.parts[:3] == (
            ".lean_constellation",
            "publication",
            "remote_receipts",
        ):
            return "local_remote_publication_receipt"
        return None

    @classmethod
    def _exclusion_reason(cls, relpath: str) -> str | None:
        path = PurePosixPath(relpath)
        directory_reason = cls._excluded_directory_reason(relpath)
        if directory_reason is not None:
            return directory_reason
        if path.parts[:3] == (
            ".lean_constellation",
            "publication",
            "manifest.json",
        ):
            return "publication_manifest_self"
        if path.name == ".env" or (
            path.name.startswith(".env.") and path.name != ".env.example"
        ):
            return "local_environment"
        if path.name in {"auth.json"}:
            return "local_authentication"
        return None

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()


__all__ = [
    "AdapterUpstreamPublication",
    "PublicApiDeclaration",
    "PublicApiDocument",
    "PublicBoundariesDocument",
    "PublicBoundaryDeclaration",
    "PublicationDirectoryExclusion",
    "PublicationFileEntry",
    "RepoProvenanceDocument",
    "RepoPublicationComponent",
    "RepoPublicationManifest",
    "RepoPublicationPreparationView",
]
