"""Portable repository publication manifests, policy, and derived documents."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.publication import (
    EffectiveRepoPublicationPolicy,
    RepoPortability,
    RepoPublicationOverride,
    RepoPublicationPolicy,
    WorkspacePublicationPolicy,
)
from lean_constellation.services.foundation import ServiceResult

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
_EXCLUDED_TOP_LEVEL = {".git", ".lake", ".agent_runtime", ".runtime"}
_EXCLUDED_CONSTELLATION_DIRS = {
    ".locks",
    "checkpoints",
    "locks",
    "snapshots",
    "staging",
}


class PublicationFileEntry(StrictModel):
    path: str
    disposition: Literal["include", "exclude", "warning"]
    reason: str
    size_bytes: int = 0
    sha256: str | None = None


class RepoPublicationManifest(StrictModel):
    schema_version: Literal[1] = 1
    repo_key: str
    generated_at: str | None = None
    entries: list[PublicationFileEntry] = Field(default_factory=list)
    included_file_count: int
    included_size_bytes: int
    manifest_digest: str
    summary: str


class PublicApiDeclaration(StrictModel):
    name: str
    kind: str
    node_path: str
    module: str
    lean_full_name: str | None = None
    statement: str | None = None
    statement_dependencies: list[str] = Field(default_factory=list)
    source_origins: list[str] = Field(default_factory=list)
    summary: str | None = None


class PublicApiDocument(StrictModel):
    schema_version: Literal[1] = 1
    repo_key: str
    release_id: str | None = None
    declarations: list[PublicApiDeclaration] = Field(default_factory=list)
    summary: str


class RepoProvenanceDocument(StrictModel):
    schema_version: Literal[1] = 1
    repo_key: str
    release_id: str | None = None
    semantic_manifest_digest: str | None = None
    lean_toolchain: str | None = None
    source_manifest_digest: str | None = None
    source_files: dict[str, str] = Field(default_factory=dict)
    build_command: str = "lake build"
    generated_at: str | None = None
    summary: str


class RepoPublicationPreparationView(StrictModel):
    repo_key: str
    manifest_path: str
    readme_path: str
    public_api_markdown_path: str
    public_api_json_path: str
    provenance_path: str
    gitignore_path: str
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
        entries: list[PublicationFileEntry] = []
        included_size = 0
        for path in sorted(repo_root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relpath = path.relative_to(repo_root).as_posix()
            excluded_reason = self._exclusion_reason(relpath)
            if (
                relpath == "lake-manifest.json"
                and not policy.value.policy.include_lake_manifest
            ):
                excluded_reason = "publication_policy_excludes_lake_manifest"
            if excluded_reason is not None:
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
        repo_root = Path(repo_root)
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        tree = self.runtime.node.node_tree.get_node_tree(repo_root)
        if not tree.ok or tree.value is None:
            return self.runtime.foundation.fail(tree.issues)
        declarations: list[PublicApiDeclaration] = []
        seen: set[tuple[str, str, int]] = set()
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
        for public in public_values:
            if not public.ready:
                continue
            ref = public.ref
            resolved_revision = public.resolved_revision or ref.revision
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
                    kind=str(decl.value.kind),
                    node_path=ref.node,
                    module=decl.value.module or ref.node,
                    lean_full_name=revision.value.lean_decl_name,
                    statement=(
                        revision.value.statement.formal.code
                        if revision.value.statement.formal is not None
                        else None
                    ),
                    statement_dependencies=statement_dependencies,
                    source_origins=source_origins,
                    summary=decl.value.summary,
                )
            )
        return self.runtime.foundation.ok(
            PublicApiDocument(
                repo_key=repo_root.name,
                release_id=(
                    release_id
                    if release_id is not None
                    else publication.value.publication.latest_release_id
                ),
                declarations=sorted(
                    declarations, key=lambda item: (item.node_path, item.name)
                ),
                summary=f"Exported {len(declarations)} public declarations.",
            )
        )

    def prepare_publication(
        self,
        repo_root: Path,
        *,
        title: str | None = None,
        release_id: str | None = None,
        semantic_manifest_digest: str | None = None,
        generated_at: str | None = None,
    ) -> ServiceResult[RepoPublicationPreparationView]:
        repo_root = Path(repo_root).resolve()
        gitignore = self.refresh_managed_gitignore(repo_root)
        if not gitignore.ok:
            return self.runtime.foundation.fail(gitignore.issues)
        api = self.render_public_api(repo_root, release_id=release_id)
        if not api.ok or api.value is None:
            return self.runtime.foundation.fail(api.issues)
        provenance = self._build_provenance(
            repo_root,
            release_id=release_id,
            semantic_manifest_digest=semantic_manifest_digest,
            generated_at=generated_at,
        )
        if not provenance.ok or provenance.value is None:
            return self.runtime.foundation.fail(provenance.issues)
        metadata = self.runtime.repo_workspace.metadata.get_repo_model(repo_root)
        if not metadata.ok or metadata.value is None:
            return self.runtime.foundation.fail(metadata.issues)
        readme_text = self._render_readme_block(
            repo_root,
            api=api.value,
            title=title or repo_root.name,
            objective=metadata.value.summary,
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
        if updated_readme != existing:
            readme_path.write_text(updated_readme, encoding="utf-8")
            written.append("README.md")
        docs_root = repo_root / "docs" / "lean-constellation"
        docs_root.mkdir(parents=True, exist_ok=True)
        api_json_path = docs_root / "public-api.json"
        api_md_path = docs_root / "PUBLIC_API.md"
        provenance_path = docs_root / "provenance.json"
        for path, value in (
            (api_json_path, api.value),
            (provenance_path, provenance.value),
        ):
            result = self.runtime.foundation.store.write_json_atomic(path, value)
            if not result.ok:
                return self.runtime.foundation.fail(result.issues)
            written.append(path.relative_to(repo_root).as_posix())
        api_md_path.write_text(self._render_public_api_markdown(api.value), encoding="utf-8")
        written.append(api_md_path.relative_to(repo_root).as_posix())
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
                readme_path="README.md",
                public_api_markdown_path=api_md_path.relative_to(repo_root).as_posix(),
                public_api_json_path=api_json_path.relative_to(repo_root).as_posix(),
                provenance_path=provenance_path.relative_to(repo_root).as_posix(),
                gitignore_path=".gitignore",
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
                summary="Portable Lean Constellation provenance.",
            )
        )

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

    @staticmethod
    def _render_public_api_markdown(value: PublicApiDocument) -> str:
        lines = ["# Public API", ""]
        for decl in value.declarations:
            lines.extend(
                [
                    f"## `{decl.name}`",
                    "",
                    f"- Kind: `{decl.kind}`",
                    f"- Node: `{decl.node_path}`",
                    f"- Module: `{decl.module}`",
                ]
            )
            if decl.statement:
                lines.extend(["", "```lean", decl.statement.rstrip(), "```"])
            if decl.statement_dependencies:
                lines.extend(
                    [
                        "",
                        "Statement dependencies:",
                        *[f"- `{item}`" for item in decl.statement_dependencies],
                    ]
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _render_readme_block(
        repo_root: Path,
        *,
        api: PublicApiDocument,
        title: str,
        objective: str | None,
    ) -> str:
        declarations = (
            "\n".join(f"- `{item.name}` — {item.kind}" for item in api.declarations)
            or "- No public declarations have been exported."
        )
        objective_text = objective or "Lean formalization project."
        release = api.release_id or "not released"
        return (
            f"{_README_BEGIN}\n"
            f"# {title}\n\n"
            f"{objective_text}\n\n"
            "## Build\n\n"
            "```sh\nlake build\n```\n\n"
            f"Current Lean Constellation Release: `{release}`.\n\n"
            "## Public declarations\n\n"
            f"{declarations}\n\n"
            "Generated metadata is available in `docs/lean-constellation/`.\n"
            f"{_README_END}"
        )

    @staticmethod
    def _replace_managed_block(
        current: str,
        *,
        begin: str,
        end: str,
        replacement: str,
    ) -> str:
        normalized = current.rstrip()
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
    def _exclusion_reason(relpath: str) -> str | None:
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
    "PublicApiDeclaration",
    "PublicApiDocument",
    "PublicationFileEntry",
    "RepoProvenanceDocument",
    "RepoPublicationComponent",
    "RepoPublicationManifest",
    "RepoPublicationPreparationView",
]
