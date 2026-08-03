"""Repo-level resource library."""

from __future__ import annotations

import hashlib
import shutil
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceIssue, ServiceResult, WriteMode

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class ResourceTargetView(StrictModel):
    kind: str
    target: str
    canonical_locator: str
    version: str | None = None
    summary: str


class ResourceTarget(StrictModel):
    kind: Literal["arxiv", "web_url", "local_file", "local_dir"]
    target: str
    canonical_locator: str
    version: str | None = None


class ResourceMetadataInput(StrictModel):
    title: str | None = None
    source_url: str | None = None
    notes: str | None = None


class ResourceMetadata(StrictModel):
    resource_key: str
    target: ResourceTarget
    title: str | None = None
    source_url: str | None = None
    notes: str | None = None
    normalized_entry: str
    created_at: str = Field(default_factory=utc_now_iso)
    content_hash: str | None = None


class ResourceDraftStatus(StrEnum):
    ALLOCATED = "allocated"
    CHECKED = "checked"
    FINALIZED = "finalized"
    ABANDONED = "abandoned"


class ResourceDraft(StrictModel):
    draft_id: str
    status: ResourceDraftStatus = ResourceDraftStatus.ALLOCATED
    target: ResourceTarget
    resource_kind: str | None = None
    title_hint: str | None = None
    allocated_at: str = Field(default_factory=utc_now_iso)
    checked_at: str | None = None
    finalized_at: str | None = None
    abandoned_at: str | None = None
    resource_key: str | None = None
    summary: str


class ResourceView(StrictModel):
    repo_root: str
    resource: ResourceMetadata
    resource_root: str
    summary: str


class ResourceDraftView(StrictModel):
    repo_root: str
    draft: ResourceDraft
    draft_root: str
    metadata_path: str
    readme_path: str
    manifest_path: str
    original_dir: str
    normalized_dir: str
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

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def normalize_resource_target_model(self, target: str) -> ServiceResult[ResourceTarget]:
        try:
            normalized = self.runtime.external.material.normalize_target(target)
        except Exception as exc:  # noqa: BLE001
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_target", str(exc)))
        if normalized.kind == "arxiv":
            locator = f"arxiv:{normalized.value}{normalized.version or ''}"
        elif normalized.kind == "web_url":
            locator = self._canonical_url(normalized.value)
        else:
            locator = f"{normalized.kind}:{Path(normalized.value).expanduser().resolve(strict=False)}"
        return self.runtime.foundation.ok(
            ResourceTarget(
                kind=normalized.kind,
                target=normalized.value,
                canonical_locator=locator,
                version=normalized.version,
            )
        )

    def normalize_resource_target(self, target: str) -> ServiceResult[ResourceTargetView]:
        normalized = self.normalize_resource_target_model(target)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        return self.runtime.foundation.ok(self._target_view(normalized.value))

    def find_duplicate_resource(
        self,
        repo_root: Path,
        *,
        target: ResourceTarget | ResourceTargetView,
    ) -> ServiceResult[ResourceDuplicateView]:
        normalized_target = self._coerce_target_model(target)
        if not normalized_target.ok or normalized_target.value is None:
            return self.runtime.foundation.fail(normalized_target.issues)
        target_model = normalized_target.value
        target_view = self._target_view(target_model)
        listed = self.list_resources(repo_root)
        if not listed.ok or listed.value is None:
            return self.runtime.foundation.fail(listed.issues)
        for item in listed.value:
            if item.canonical_locator == target_model.canonical_locator:
                return self.runtime.foundation.ok(
                    ResourceDuplicateView(
                        duplicate=True,
                        target=target_view,
                        resource_key=item.resource_key,
                        summary=f"Duplicate resource found: {item.resource_key}.",
                    )
                )
            resource = self.get_resource(repo_root, resource_key=item.resource_key)
            if resource.ok and resource.value and self._resource_metadata_matches_target(resource.value.resource, target_model):
                return self.runtime.foundation.ok(
                    ResourceDuplicateView(
                        duplicate=True,
                        target=target_view,
                        resource_key=item.resource_key,
                        summary=f"Duplicate resource metadata matched target: {item.resource_key}.",
                    )
                )
        return self.runtime.foundation.ok(ResourceDuplicateView(duplicate=False, target=target_view, summary="No duplicate resource found."))

    def allocate_resource_draft(
        self,
        repo_root: Path,
        *,
        target: str | ResourceTarget | ResourceTargetView,
        resource_kind: str | None = None,
        title_hint: str | None = None,
        allow_duplicate: bool = False,
    ) -> ServiceResult[ResourceDraftView]:
        normalized = self._coerce_target_model(target)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        duplicate = self.find_duplicate_resource(repo_root, target=normalized.value)
        if not duplicate.ok or duplicate.value is None:
            return self.runtime.foundation.fail(duplicate.issues)
        if duplicate.value.duplicate and not allow_duplicate:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_duplicate",
                    duplicate.value.summary,
                    object_ref=duplicate.value.resource_key,
                )
            )
        drafts_root = self._drafts_root(repo_root)
        allocated = self.runtime.foundation.store.allocate_uuid(
            lambda candidate: self._draft_root(repo_root, candidate).exists(),
            prefix="draft",
        )
        if not allocated.ok or allocated.value is None:
            return self.runtime.foundation.fail(allocated.issues)
        draft = ResourceDraft(
            draft_id=allocated.value,
            target=normalized.value,
            resource_kind=resource_kind.strip() if resource_kind else normalized.value.kind,
            title_hint=title_hint.strip() if title_hint else None,
            summary=f"Allocated resource draft for {normalized.value.canonical_locator}.",
        )
        draft_root = drafts_root / draft.draft_id
        try:
            self.runtime.foundation.layout.assert_within(drafts_root, draft_root)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_draft_path_escape", str(exc), object_ref=draft.draft_id))
        for directory in (draft_root, draft_root / "original", draft_root / "normalized"):
            ensured = self.runtime.foundation.store.ensure_dir(directory)
            if not ensured.ok:
                return self.runtime.foundation.fail(ensured.issues)
        written = self.runtime.foundation.store.write_json_atomic(
            self._draft_metadata_path(repo_root, draft.draft_id),
            draft,
            mode=WriteMode.CREATE_ONLY,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(self._draft_view(repo_root, draft))

    def check_resource_draft(self, repo_root: Path, *, draft_id: str, update_status: bool = True) -> ServiceResult[GateReport]:
        loaded = self._load_draft(repo_root, draft_id=draft_id)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        draft = loaded.value
        issues = self._draft_gate_issues(repo_root, draft)
        if issues:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "resource_draft_check",
                    issues,
                    summary=f"{len(issues)} resource draft checks failed.",
                )
            )
        if update_status and draft.status == ResourceDraftStatus.ALLOCATED:
            draft.status = ResourceDraftStatus.CHECKED
            draft.checked_at = utc_now_iso()
            draft.summary = "Resource draft passed checks."
            written = self.runtime.foundation.store.write_json_atomic(
                self._draft_metadata_path(repo_root, draft.draft_id),
                draft,
                mode=WriteMode.UPDATE_EXISTING,
            )
            if not written.ok:
                return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("resource_draft_check", summary="Resource draft checks passed.")
        )

    def resource_key_for_target(self, target: ResourceTarget | ResourceTargetView | str) -> ServiceResult[str]:
        normalized = self._coerce_target_model(target)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        return self.runtime.foundation.ok(self._resource_key(normalized.value))

    def finalize_resource_draft(self, repo_root: Path, *, draft_id: str, summary: str) -> ServiceResult[ResourceView]:
        if not summary or not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_finalize_summary_required", "Finalize summary is required.", field="summary"))
        loaded = self._load_draft(repo_root, draft_id=draft_id)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        draft = loaded.value
        gate = self.check_resource_draft(repo_root, draft_id=draft_id)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        duplicate = self.find_duplicate_resource(repo_root, target=draft.target)
        if not duplicate.ok or duplicate.value is None:
            return self.runtime.foundation.fail(duplicate.issues)
        if duplicate.value.duplicate:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("resource_duplicate", duplicate.value.summary, object_ref=duplicate.value.resource_key)
            )
        draft_root = self._draft_root(repo_root, draft.draft_id)
        entry = self._choose_normalized_entry(draft_root / "normalized")
        if entry is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("resource_not_readable", "Resource draft has no readable normalized text.", object_ref=str(draft_root))
            )
        resource_key = self._resource_key(draft.target)
        ctx = FoundationContext(repo_root=Path(repo_root))
        dest = self.runtime.foundation.layout.resource_dir(ctx, resource_key)
        if dest.exists():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_duplicate", f"Resource key already exists: {resource_key}", object_ref=resource_key))
        draft.status = ResourceDraftStatus.FINALIZED
        draft.finalized_at = utc_now_iso()
        draft.resource_key = resource_key
        draft.summary = summary.strip()
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(draft_root, dest)
        except OSError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_finalize_copy_failed",
                    f"Failed to copy resource draft to final library item: {exc}",
                    object_ref=draft.draft_id,
                    details={"draft_root": str(draft_root), "resource_root": str(dest)},
                )
            )
        dest_entry = dest / entry.relative_to(draft_root)
        resource = ResourceMetadata(
            resource_key=resource_key,
            target=draft.target,
            title=draft.title_hint,
            source_url=draft.target.target if draft.target.kind == "web_url" else None,
            notes=summary.strip(),
            normalized_entry=dest_entry.relative_to(dest).as_posix(),
            content_hash=self._hash_file(dest_entry),
        )
        resource_write = self.runtime.foundation.store.write_json_atomic(
            self.runtime.foundation.layout.resource_metadata_path(ctx, resource_key),
            resource,
            mode=WriteMode.CREATE_ONLY,
        )
        if not resource_write.ok:
            shutil.rmtree(dest, ignore_errors=True)
            return self.runtime.foundation.fail(resource_write.issues)
        draft_write = self.runtime.foundation.store.write_json_atomic(
            self._draft_metadata_path(repo_root, draft.draft_id),
            draft,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not draft_write.ok:
            return self.runtime.foundation.fail(draft_write.issues)
        return self.runtime.foundation.ok(
            ResourceView(
                repo_root=str(Path(repo_root)),
                resource=resource,
                resource_root=str(dest),
                summary=f"Finalized resource draft {draft.draft_id} as {resource_key}.",
            )
        )

    def abandon_resource_draft(self, repo_root: Path, *, draft_id: str, reason: str) -> ServiceResult[ResourceDraftView]:
        if not reason or not reason.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_abandon_reason_required", "Abandon reason is required.", field="reason"))
        loaded = self._load_draft(repo_root, draft_id=draft_id)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        draft = loaded.value
        if draft.status == ResourceDraftStatus.FINALIZED:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("resource_draft_already_finalized", "Finalized drafts cannot be abandoned.", object_ref=draft_id)
            )
        draft.status = ResourceDraftStatus.ABANDONED
        draft.abandoned_at = utc_now_iso()
        draft.summary = reason.strip()
        written = self.runtime.foundation.store.write_json_atomic(
            self._draft_metadata_path(repo_root, draft_id),
            draft,
            mode=WriteMode.UPDATE_EXISTING,
        )
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(self._draft_view(repo_root, draft))

    def get_resource_draft(self, repo_root: Path, *, draft_id: str) -> ServiceResult[ResourceDraftView]:
        loaded = self._load_draft(repo_root, draft_id=draft_id)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self.runtime.foundation.ok(self._draft_view(repo_root, loaded.value))

    def get_resource_draft_normalized_entry(
        self,
        repo_root: Path,
        *,
        draft_id: str,
    ) -> ServiceResult[str]:
        loaded = self._load_draft(repo_root, draft_id=draft_id)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        draft_root = self._draft_root(repo_root, draft_id)
        entry = self._choose_normalized_entry(draft_root / "normalized")
        if entry is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_not_readable",
                    "Resource draft has no readable normalized text.",
                    object_ref=str(draft_root),
                )
            )
        return self.runtime.foundation.ok(entry.relative_to(draft_root).as_posix())

    def register_local_resource(
        self,
        repo_root: Path,
        *,
        target: ResourceTarget | ResourceTargetView,
        temp_dir: Path,
        metadata: ResourceMetadataInput,
    ) -> ServiceResult[ResourceView]:
        normalized = self._coerce_target_model(target)
        if not normalized.ok or normalized.value is None:
            return self.runtime.foundation.fail(normalized.issues)
        target_model = normalized.value
        duplicate = self.find_duplicate_resource(repo_root, target=target_model)
        if duplicate.ok and duplicate.value and duplicate.value.duplicate:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_duplicate",
                    duplicate.value.summary,
                    object_ref=duplicate.value.resource_key,
                )
            )
        temp_dir = Path(temp_dir)
        normalized_root = temp_dir / "normalized"
        entry = self._choose_normalized_entry(normalized_root if normalized_root.exists() else temp_dir)
        if entry is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_not_readable",
                    "Resource temp directory has no readable normalized text.",
                    object_ref=str(temp_dir),
                )
            )
        resource_key = self._resource_key(target_model)
        ctx = FoundationContext(repo_root=Path(repo_root))
        dest = self.runtime.foundation.layout.resource_dir(ctx, resource_key)
        if dest.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("resource_duplicate", f"Resource key already exists: {resource_key}")
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(temp_dir, dest)
        dest_entry = dest / entry.relative_to(temp_dir)
        resource = ResourceMetadata(
            resource_key=resource_key,
            target=target_model,
            title=metadata.title,
            source_url=metadata.source_url,
            notes=metadata.notes,
            normalized_entry=dest_entry.relative_to(dest).as_posix(),
            content_hash=self._hash_file(dest_entry),
        )
        write = self.runtime.foundation.store.write_json_atomic(
            self.runtime.foundation.layout.resource_metadata_path(ctx, resource_key),
            resource,
            mode=WriteMode.CREATE_ONLY,
        )
        if not write.ok:
            return self.runtime.foundation.fail(write.issues)
        return self.runtime.foundation.ok(
            ResourceView(
                repo_root=str(Path(repo_root)),
                resource=resource,
                resource_root=str(dest),
                summary=f"Registered local resource {resource_key}.",
            )
        )

    def get_resource(self, repo_root: Path, *, resource_key: str) -> ServiceResult[ResourceView]:
        try:
            resource_key = self.runtime.foundation.layout.ensure_safe_key(resource_key)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_key", str(exc), object_ref=str(resource_key)))
        ctx = FoundationContext(repo_root=Path(repo_root))
        path = self.runtime.foundation.layout.resource_metadata_path(ctx, resource_key)
        loaded = self.runtime.foundation.store.read_json(path, ResourceMetadata)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("resource_not_found", f"Resource not found: {resource_key}", object_ref=str(path))
            )
        return self.runtime.foundation.ok(
            ResourceView(
                repo_root=str(Path(repo_root)),
                resource=loaded.value,
                resource_root=str(self.runtime.foundation.layout.resource_dir(ctx, resource_key)),
                summary=f"Loaded resource {resource_key}.",
            )
        )

    def list_resources(self, repo_root: Path, *, query: str | None = None) -> ServiceResult[list[ResourceSummaryView]]:
        root = self.runtime.foundation.layout.resources_root(FoundationContext(repo_root=Path(repo_root))) / "items"
        if not root.exists():
            return self.runtime.foundation.ok([])
        values = []
        for path in sorted(root.glob("*/resource.json")):
            loaded = self.runtime.foundation.store.read_json(path, ResourceMetadata)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
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
        return self.runtime.foundation.ok(values)

    def preview_resource(self, repo_root: Path, *, resource_key: str):
        from lean_constellation.services.material.material_read import MaterialReadComponent

        entry = self.normalized_entry_path(repo_root, resource_key)
        if not entry.ok or entry.value is None:
            return self.runtime.foundation.fail(entry.issues)
        line_count = max(1, self._line_count(entry.value))
        return MaterialReadComponent(self.runtime, source_corpus=None, resource_library=self).read_resource_range(
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
            return self.runtime.foundation.fail(resource.issues)
        path = Path(resource.value.resource_root) / resource.value.resource.normalized_entry
        line_count = self._line_count(path)
        valid = 1 <= start_line <= end_line <= line_count
        return self.runtime.foundation.ok(
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
            return self.runtime.foundation.fail(resource.issues)
        return self.runtime.foundation.ok(Path(resource.value.resource_root) / resource.value.resource.normalized_entry)

    def _coerce_target_model(self, target: str | ResourceTarget | ResourceTargetView) -> ServiceResult[ResourceTarget]:
        if isinstance(target, ResourceTarget):
            return self.runtime.foundation.ok(target)
        if isinstance(target, ResourceTargetView):
            return self.runtime.foundation.ok(
                ResourceTarget(
                    kind=target.kind,
                    target=target.target,
                    canonical_locator=target.canonical_locator,
                    version=target.version,
                )
            )
        return self.normalize_resource_target_model(target)

    @staticmethod
    def _target_view(target: ResourceTarget) -> ResourceTargetView:
        return ResourceTargetView(
            kind=target.kind,
            target=target.target,
            canonical_locator=target.canonical_locator,
            version=target.version,
            summary=f"Normalized resource target as {target.kind}.",
        )

    def _drafts_root(self, repo_root: Path) -> Path:
        return self.runtime.foundation.layout.resources_root(FoundationContext(repo_root=Path(repo_root))) / ".drafts"

    def _draft_root(self, repo_root: Path, draft_id: str) -> Path:
        return self._drafts_root(repo_root) / self.runtime.foundation.layout.ensure_safe_key(draft_id)

    def _draft_metadata_path(self, repo_root: Path, draft_id: str) -> Path:
        return self._draft_root(repo_root, draft_id) / "draft.json"

    def _load_draft(self, repo_root: Path, *, draft_id: str) -> ServiceResult[ResourceDraft]:
        try:
            path = self._draft_metadata_path(repo_root, draft_id)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_resource_draft_id", str(exc), object_ref=draft_id))
        loaded = self.runtime.foundation.store.read_json(path, ResourceDraft)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self.runtime.foundation.ok(loaded.value)

    def _draft_view(self, repo_root: Path, draft: ResourceDraft) -> ResourceDraftView:
        draft_root = self._draft_root(repo_root, draft.draft_id)
        return ResourceDraftView(
            repo_root=str(Path(repo_root)),
            draft=draft,
            draft_root=str(draft_root),
            metadata_path=str(draft_root / "draft.json"),
            readme_path=str(draft_root / "README.md"),
            manifest_path=str(draft_root / "manifest.json"),
            original_dir=str(draft_root / "original"),
            normalized_dir=str(draft_root / "normalized"),
            summary=draft.summary,
        )

    def _draft_gate_issues(self, repo_root: Path, draft: ResourceDraft) -> list[ServiceIssue]:
        issues = []
        draft_root = self._draft_root(repo_root, draft.draft_id)
        if draft.status == ResourceDraftStatus.FINALIZED:
            issues.append(
                self.runtime.foundation.issue("resource_draft_already_finalized", "Resource draft is already finalized.", object_ref=draft.draft_id)
            )
        if draft.status == ResourceDraftStatus.ABANDONED:
            issues.append(self.runtime.foundation.issue("resource_draft_abandoned", "Resource draft is abandoned.", object_ref=draft.draft_id))
        if not draft_root.exists() or not draft_root.is_dir():
            issues.append(self.runtime.foundation.issue("resource_draft_missing", "Resource draft directory is missing.", object_ref=str(draft_root)))
            return issues
        try:
            self.runtime.foundation.layout.assert_within(self._drafts_root(repo_root), draft_root)
        except ValueError as exc:
            issues.append(self.runtime.foundation.issue("resource_draft_path_escape", str(exc), object_ref=draft.draft_id))
        for path in draft_root.rglob("*"):
            try:
                self.runtime.foundation.layout.assert_within(draft_root, path)
            except ValueError as exc:
                issues.append(self.runtime.foundation.issue("resource_draft_path_escape", str(exc), object_ref=str(path)))
            if path.is_symlink():
                issues.append(self.runtime.foundation.issue("resource_draft_symlink_forbidden", "Resource draft must not contain symlinks.", object_ref=str(path)))
        if not (draft_root / "README.md").is_file() and not (draft_root / "manifest.json").is_file():
            issues.append(
                self.runtime.foundation.issue(
                    "resource_draft_readme_or_manifest_missing",
                    "Resource draft requires README.md or manifest.json.",
                    object_ref=str(draft_root),
                )
            )
        normalized_entry = self._choose_normalized_entry(draft_root / "normalized")
        if normalized_entry is None:
            issues.append(
                self.runtime.foundation.issue(
                    "resource_draft_normalized_artifact_missing",
                    "Resource draft requires at least one readable non-empty normalized text artifact.",
                    object_ref=str(draft_root / "normalized"),
                )
            )
        return issues

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
    def _resource_key(target: ResourceTarget) -> str:
        digest = hashlib.sha256(target.canonical_locator.encode("utf-8")).hexdigest()[:16]
        return f"r_{digest}"

    def _resource_metadata_matches_target(self, resource: ResourceMetadata, target: ResourceTarget) -> bool:
        if resource.source_url:
            normalized = self.normalize_resource_target(resource.source_url)
            if normalized.ok and normalized.value and normalized.value.canonical_locator == target.canonical_locator:
                return True
        return False
