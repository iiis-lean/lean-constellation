"""Repo-level resource library."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.external_clients import ExternalClientService
from lean_constellation.services.foundation import FoundationContext, FoundationService, ServiceResult, WriteMode


class ResourceTargetView(StrictModel):
    kind: str
    target: str
    canonical_locator: str
    version: str | None = None
    summary: str


class ResourceMetadataInput(StrictModel):
    title: str | None = None
    source_url: str | None = None
    notes: str | None = None


class ResourceMetadata(StrictModel):
    resource_key: str
    target: ResourceTargetView
    title: str | None = None
    source_url: str | None = None
    notes: str | None = None
    normalized_entry: str
    created_at: str = Field(default_factory=utc_now_iso)
    content_hash: str | None = None


class ResourceView(StrictModel):
    repo_root: str
    resource: ResourceMetadata
    resource_root: str
    summary: str


class ResourceSummaryView(StrictModel):
    resource_key: str
    title: str | None = None
    kind: str
    canonical_locator: str
    summary: str


class ResourceDuplicateView(StrictModel):
    duplicate: bool
    target: ResourceTargetView
    resource_key: str | None = None
    summary: str


class ResourceLibraryComponent:
    """Register and query normalized local resources."""

    def __init__(self, foundation: FoundationService, external: ExternalClientService) -> None:
        self.foundation = foundation
        self.external = external

    def normalize_resource_target(self, target: str) -> ServiceResult[ResourceTargetView]:
        try:
            normalized = self.external.material.normalize_target(target)
        except Exception as exc:  # noqa: BLE001
            return self.foundation.fail(self.foundation.issue("invalid_resource_target", str(exc)))
        if normalized.kind == "arxiv":
            locator = f"arxiv:{normalized.value}{normalized.version or ''}"
        elif normalized.kind == "web_url":
            locator = self._canonical_url(normalized.value)
        else:
            locator = f"{normalized.kind}:{Path(normalized.value).expanduser().resolve(strict=False)}"
        return self.foundation.ok(
            ResourceTargetView(
                kind=normalized.kind,
                target=normalized.value,
                canonical_locator=locator,
                version=normalized.version,
                summary=f"Normalized resource target as {normalized.kind}.",
            )
        )

    def find_duplicate_resource(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
    ) -> ServiceResult[ResourceDuplicateView]:
        listed = self.list_resources(repo_root)
        if not listed.ok or listed.value is None:
            return self.foundation.fail(listed.issues)
        for item in listed.value:
            if item.canonical_locator == target.canonical_locator:
                return self.foundation.ok(
                    ResourceDuplicateView(
                        duplicate=True,
                        target=target,
                        resource_key=item.resource_key,
                        summary=f"Duplicate resource found: {item.resource_key}.",
                    )
                )
            resource = self.get_resource(repo_root, resource_key=item.resource_key)
            if resource.ok and resource.value and self._resource_metadata_matches_target(resource.value.resource, target):
                return self.foundation.ok(
                    ResourceDuplicateView(
                        duplicate=True,
                        target=target,
                        resource_key=item.resource_key,
                        summary=f"Duplicate resource metadata matched target: {item.resource_key}.",
                    )
                )
        return self.foundation.ok(ResourceDuplicateView(duplicate=False, target=target, summary="No duplicate resource found."))

    def register_local_resource(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        temp_dir: Path,
        metadata: ResourceMetadataInput,
    ) -> ServiceResult[ResourceView]:
        duplicate = self.find_duplicate_resource(repo_root, target=target)
        if duplicate.ok and duplicate.value and duplicate.value.duplicate:
            return self.foundation.fail(
                self.foundation.issue(
                    "resource_duplicate",
                    duplicate.value.summary,
                    object_ref=duplicate.value.resource_key,
                )
            )
        temp_dir = Path(temp_dir)
        normalized_root = temp_dir / "normalized"
        entry = self._choose_normalized_entry(normalized_root if normalized_root.exists() else temp_dir)
        if entry is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "resource_not_readable",
                    "Resource temp directory has no readable normalized text.",
                    object_ref=str(temp_dir),
                )
            )
        resource_key = self._resource_key(target)
        ctx = FoundationContext(repo_root=Path(repo_root))
        dest = self.foundation.layout.resource_dir(ctx, resource_key)
        if dest.exists():
            return self.foundation.fail(
                self.foundation.issue("resource_duplicate", f"Resource key already exists: {resource_key}")
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(temp_dir, dest)
        dest_entry = dest / entry.relative_to(temp_dir)
        resource = ResourceMetadata(
            resource_key=resource_key,
            target=target,
            title=metadata.title,
            source_url=metadata.source_url,
            notes=metadata.notes,
            normalized_entry=dest_entry.relative_to(dest).as_posix(),
            content_hash=self._hash_file(dest_entry),
        )
        write = self.foundation.store.write_json_atomic(
            self.foundation.layout.resource_metadata_path(ctx, resource_key),
            resource,
            mode=WriteMode.CREATE_ONLY,
        )
        if not write.ok:
            return self.foundation.fail(write.issues)
        return self.foundation.ok(
            ResourceView(
                repo_root=str(Path(repo_root)),
                resource=resource,
                resource_root=str(dest),
                summary=f"Registered local resource {resource_key}.",
            )
        )

    def get_resource(self, repo_root: Path, *, resource_key: str) -> ServiceResult[ResourceView]:
        try:
            resource_key = self.foundation.layout.ensure_safe_key(resource_key)
        except ValueError as exc:
            return self.foundation.fail(self.foundation.issue("invalid_resource_key", str(exc), object_ref=str(resource_key)))
        ctx = FoundationContext(repo_root=Path(repo_root))
        path = self.foundation.layout.resource_metadata_path(ctx, resource_key)
        loaded = self.foundation.store.read_json(path, ResourceMetadata)
        if not loaded.ok or loaded.value is None:
            return self.foundation.fail(
                self.foundation.issue("resource_not_found", f"Resource not found: {resource_key}", object_ref=str(path))
            )
        return self.foundation.ok(
            ResourceView(
                repo_root=str(Path(repo_root)),
                resource=loaded.value,
                resource_root=str(self.foundation.layout.resource_dir(ctx, resource_key)),
                summary=f"Loaded resource {resource_key}.",
            )
        )

    def list_resources(self, repo_root: Path, *, query: str | None = None) -> ServiceResult[list[ResourceSummaryView]]:
        root = self.foundation.layout.resources_root(FoundationContext(repo_root=Path(repo_root))) / "items"
        if not root.exists():
            return self.foundation.ok([])
        values = []
        for path in sorted(root.glob("*/resource.json")):
            loaded = self.foundation.store.read_json(path, ResourceMetadata)
            if not loaded.ok or loaded.value is None:
                return self.foundation.fail(loaded.issues)
            resource = loaded.value
            haystack = " ".join(
                [
                    resource.resource_key,
                    resource.title or "",
                    resource.target.canonical_locator,
                    resource.notes or "",
                ]
            )
            if query and query.lower() not in haystack.lower():
                continue
            values.append(
                ResourceSummaryView(
                    resource_key=resource.resource_key,
                    title=resource.title,
                    kind=resource.target.kind,
                    canonical_locator=resource.target.canonical_locator,
                    summary=resource.title or resource.target.canonical_locator,
                )
            )
        return self.foundation.ok(values)

    def preview_resource(self, repo_root: Path, *, resource_key: str):
        from lean_constellation.services.material.material_read import MaterialReadComponent

        entry = self.normalized_entry_path(repo_root, resource_key)
        if not entry.ok or entry.value is None:
            return self.foundation.fail(entry.issues)
        line_count = max(1, self._line_count(entry.value))
        return MaterialReadComponent(self.foundation, source_corpus=None, resource_library=self).read_resource_range(
            repo_root,
            resource_key=resource_key,
            start_line=1,
            end_line=min(20, line_count),
            context_lines=0,
        )

    def validate_resource_ref(
        self,
        repo_root: Path,
        *,
        resource_key: str,
        start_line: int,
        end_line: int,
    ):
        resource = self.get_resource(repo_root, resource_key=resource_key)
        if not resource.ok or resource.value is None:
            return self.foundation.fail(resource.issues)
        path = Path(resource.value.resource_root) / resource.value.resource.normalized_entry
        line_count = self._line_count(path)
        valid = 1 <= start_line <= end_line <= line_count
        return self.foundation.ok(
            {
                "valid": valid,
                "resource_key": resource_key,
                "start_line": start_line,
                "end_line": end_line,
                "line_count": line_count,
                "summary": "Resource ref is valid." if valid else "Resource ref line range is invalid.",
                "issue_code": None if valid else "resource_ref_range_invalid",
            }
        )

    def normalized_entry_path(self, repo_root: Path, resource_key: str) -> ServiceResult[Path]:
        resource = self.get_resource(repo_root, resource_key=resource_key)
        if not resource.ok or resource.value is None:
            return self.foundation.fail(resource.issues)
        return self.foundation.ok(Path(resource.value.resource_root) / resource.value.resource.normalized_entry)

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlparse(value)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        return f"{scheme}://{netloc}{path}"

    @staticmethod
    def _choose_normalized_entry(root: Path) -> Path | None:
        candidates = [path for path in sorted(root.rglob("*")) if path.is_file()]
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if text.strip():
                return path
        return None

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _line_count(path: Path) -> int:
        try:
            return len(path.read_text(encoding="utf-8").splitlines())
        except UnicodeDecodeError:
            return 0

    @staticmethod
    def _resource_key(target: ResourceTargetView) -> str:
        digest = hashlib.sha256(target.canonical_locator.encode("utf-8")).hexdigest()[:16]
        return f"r_{digest}"

    def _resource_metadata_matches_target(self, resource: ResourceMetadata, target: ResourceTargetView) -> bool:
        if resource.source_url:
            normalized = self.normalize_resource_target(resource.source_url)
            if normalized.ok and normalized.value and normalized.value.canonical_locator == target.canonical_locator:
                return True
        return False
