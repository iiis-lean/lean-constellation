"""Repo-level resource library."""

from __future__ import annotations

import hashlib
import re
import shutil
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceIssue, ServiceResult, WriteMode
from lean_constellation.services.material.tex_tree import find_literal_tex_include_problems

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
    canonical_entry: str
    created_at: str = Field(default_factory=utc_now_iso)
    content_hash: str | None = None


class ResourceDraftStatus(StrEnum):
    ALLOCATED = "allocated"
    CHECKED = "checked"
    FINALIZED = "finalized"
    ABANDONED = "abandoned"


class ResourceDraft(StrictModel):
    schema_version: Literal[2] = 2
    draft_id: str
    status: ResourceDraftStatus = ResourceDraftStatus.ALLOCATED
    target: ResourceTarget
    resource_kind: str | None = None
    title_hint: str | None = None
    requested_use: Literal["supporting_material", "formal_dependency", "unknown"] | None = None
    consumer_need: str | None = None
    caller_kind: str | None = None
    purpose_hint: str | None = None
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
    work_dir: str
    summary: str


class ResourceMaterialFileView(StrictModel):
    path: str
    category: Literal["content", "asset", "supplementary"]
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    resolved_kind: Literal[
        "pdf",
        "html",
        "tex_source_archive",
        "plain_text",
        "directory",
        "unknown_binary",
    ]
    readable_kind: Literal["plain_text", "markdown", "tex_source"] | None = None


class ResourceMaterialManifest(StrictModel):
    manifest_kind: Literal["resource_material_manifest"] = "resource_material_manifest"
    schema_version: Literal[2] = 2
    readme_path: Literal["README.md"] = "README.md"
    canonical_entry: str
    files: list[ResourceMaterialFileView] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_canonical_entry(self):
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("resource material manifest file paths must be unique")
        matching = [item for item in self.files if item.path == self.canonical_entry]
        if len(matching) != 1:
            raise ValueError("canonical_entry must identify exactly one manifest file")
        canonical = matching[0]
        if canonical.resolved_kind != "plain_text" or canonical.readable_kind is None:
            raise ValueError("canonical_entry must identify validated readable text")
        return self


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
        requested_use: Literal["supporting_material", "formal_dependency", "unknown"] | None = None,
        consumer_need: str | None = None,
        caller_kind: str | None = None,
        purpose_hint: str | None = None,
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
            requested_use=requested_use,
            consumer_need=consumer_need.strip() if consumer_need and consumer_need.strip() else None,
            caller_kind=caller_kind.strip() if caller_kind and caller_kind.strip() else None,
            purpose_hint=purpose_hint.strip() if purpose_hint and purpose_hint.strip() else None,
            summary=f"Allocated resource draft for {normalized.value.canonical_locator}.",
        )
        draft_root = drafts_root / draft.draft_id
        try:
            self.runtime.foundation.layout.assert_within(drafts_root, draft_root)
        except ValueError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_draft_path_escape", str(exc), object_ref=draft.draft_id))
        for directory in (draft_root, draft_root / "_work"):
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
        refreshed = self.refresh_resource_draft_manifest(repo_root, draft_id=draft_id)
        if not refreshed.ok or refreshed.value is None:
            issues = [*refreshed.issues, *self._draft_gate_issues(repo_root, draft)]
            deduplicated = []
            seen = set()
            for issue in issues:
                key = (issue.kind, issue.object_ref, issue.field)
                if key not in seen:
                    seen.add(key)
                    deduplicated.append(issue)
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "resource_draft_check",
                    deduplicated,
                    summary=f"{len(deduplicated)} resource draft checks failed.",
                )
            )
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

    def refresh_resource_draft_manifest(
        self,
        repo_root: Path,
        *,
        draft_id: str,
        canonical_entry: str | None = None,
    ) -> ServiceResult[ResourceMaterialManifest]:
        loaded = self._load_draft(repo_root, draft_id=draft_id)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self._refresh_material_manifest(
            self._draft_root(repo_root, draft_id),
            canonical_entry=canonical_entry,
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
        manifest = self._load_material_manifest(draft_root)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        entry = draft_root / manifest.value.canonical_entry
        resource_key = self._resource_key(draft.target)
        ctx = FoundationContext(repo_root=Path(repo_root))
        dest = self.runtime.foundation.layout.resource_dir(ctx, resource_key)
        if dest.exists():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("resource_duplicate", f"Resource key already exists: {resource_key}", object_ref=resource_key))
        draft.status = ResourceDraftStatus.FINALIZED
        draft.finalized_at = utc_now_iso()
        draft.resource_key = resource_key
        draft.summary = summary.strip()
        canonical_file = next(
            item
            for item in manifest.value.files
            if item.path == manifest.value.canonical_entry
        )
        if self._hash_file(entry) != canonical_file.sha256:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_manifest_content_changed",
                    "Canonical entry bytes changed after the draft gate.",
                    object_ref=manifest.value.canonical_entry,
                )
            )
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.mkdir(parents=True, exist_ok=False)
            for item in draft_root.iterdir():
                if item.name in {"_work", "draft.json", "manifest.json", "resource.json"}:
                    continue
                destination = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, destination, copy_function=shutil.copy2)
                else:
                    shutil.copy2(item, destination)
            manifest_write = self.runtime.foundation.store.write_json_atomic(
                dest / "manifest.json",
                manifest.value,
                mode=WriteMode.CREATE_ONLY,
            )
            if not manifest_write.ok:
                raise OSError(manifest_write.issues[0].message if manifest_write.issues else "manifest write failed")
        except OSError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_finalize_copy_failed",
                    f"Failed to copy resource draft to final library item: {exc}",
                    object_ref=draft.draft_id,
                    details={"draft_root": str(draft_root), "resource_root": str(dest)},
                )
            )
        dest_entry = dest / entry.relative_to(draft_root)
        if self._hash_file(dest_entry) != canonical_file.sha256:
            shutil.rmtree(dest, ignore_errors=True)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_manifest_content_changed",
                    "Canonical entry bytes changed after the draft gate.",
                    object_ref=manifest.value.canonical_entry,
                )
            )
        resource = ResourceMetadata(
            resource_key=resource_key,
            target=draft.target,
            title=draft.title_hint,
            source_url=draft.target.target if draft.target.kind == "web_url" else None,
            notes=summary.strip(),
            canonical_entry=dest_entry.relative_to(dest).as_posix(),
            content_hash=canonical_file.sha256,
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

    def get_resource_draft_canonical_entry(
        self,
        repo_root: Path,
        *,
        draft_id: str,
    ) -> ServiceResult[str]:
        loaded = self._load_draft(repo_root, draft_id=draft_id)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        manifest = self._load_material_manifest(self._draft_root(repo_root, draft_id))
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        return self.runtime.foundation.ok(manifest.value.canonical_entry)

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
        manifest = self._refresh_material_manifest(
            temp_dir,
            missing_entry_issue_kind="resource_not_readable",
        )
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        entry = temp_dir / manifest.value.canonical_entry
        resource_key = self._resource_key(target_model)
        ctx = FoundationContext(repo_root=Path(repo_root))
        dest = self.runtime.foundation.layout.resource_dir(ctx, resource_key)
        if dest.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("resource_duplicate", f"Resource key already exists: {resource_key}")
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.mkdir(parents=True, exist_ok=False)
        try:
            for item in temp_dir.iterdir():
                if item.name in {"_work", "draft.json", "manifest.json", "resource.json"}:
                    continue
                destination = dest / item.name
                if item.is_dir():
                    shutil.copytree(item, destination, copy_function=shutil.copy2)
                else:
                    shutil.copy2(item, destination)
            manifest_write = self.runtime.foundation.store.write_json_atomic(
                dest / "manifest.json",
                manifest.value,
                mode=WriteMode.CREATE_ONLY,
            )
            if not manifest_write.ok:
                raise OSError(manifest_write.issues[0].message if manifest_write.issues else "manifest write failed")
        except OSError as exc:
            shutil.rmtree(dest, ignore_errors=True)
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_finalize_copy_failed",
                    f"Failed to copy resource candidate to final library item: {exc}",
                    details={"candidate_root": str(temp_dir), "resource_root": str(dest)},
                )
            )
        dest_entry = dest / entry.relative_to(temp_dir)
        resource = ResourceMetadata(
            resource_key=resource_key,
            target=target_model,
            title=metadata.title,
            source_url=metadata.source_url,
            notes=metadata.notes,
            canonical_entry=dest_entry.relative_to(dest).as_posix(),
            content_hash=next(
                item.sha256
                for item in manifest.value.files
                if item.path == manifest.value.canonical_entry
            ),
        )
        write = self.runtime.foundation.store.write_json_atomic(
            self.runtime.foundation.layout.resource_metadata_path(ctx, resource_key),
            resource,
            mode=WriteMode.CREATE_ONLY,
        )
        if not write.ok:
            shutil.rmtree(dest, ignore_errors=True)
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

        entry = self.canonical_entry_path(repo_root, resource_key)
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
        path = Path(resource.value.resource_root) / resource.value.resource.canonical_entry
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

    def canonical_entry_path(self, repo_root: Path, resource_key: str) -> ServiceResult[Path]:
        resource = self.get_resource(repo_root, resource_key=resource_key)
        if not resource.ok or resource.value is None:
            return self.runtime.foundation.fail(resource.issues)
        return self.runtime.foundation.ok(Path(resource.value.resource_root) / resource.value.resource.canonical_entry)

    def get_resource_material_manifest(
        self,
        repo_root: Path,
        *,
        resource_key: str,
    ) -> ServiceResult[ResourceMaterialManifest]:
        resource = self.get_resource(repo_root, resource_key=resource_key)
        if not resource.ok or resource.value is None:
            return self.runtime.foundation.fail(resource.issues)
        return self._load_material_manifest(Path(resource.value.resource_root))

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
        schema_issues = [
            issue
            for issue in loaded.issues
            if issue.kind in {"schema_version_missing", "schema_version_mismatch"}
        ]
        if schema_issues:
            return self.runtime.foundation.fail([self._as_schema_error(issue) for issue in schema_issues])
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
            work_dir=str(draft_root / "_work"),
            summary=draft.summary,
        )

    def _load_material_manifest(self, root: Path) -> ServiceResult[ResourceMaterialManifest]:
        path = root / "manifest.json"
        if not path.is_file():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_manifest_missing",
                    "Resource material manifest is missing.",
                    object_ref=str(path),
                )
            )
        loaded = self.runtime.foundation.store.read_json(path, ResourceMaterialManifest)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        schema_issues = [
            issue
            for issue in loaded.issues
            if issue.kind in {"schema_version_missing", "schema_version_mismatch"}
        ]
        if schema_issues:
            return self.runtime.foundation.fail([self._as_schema_error(issue) for issue in schema_issues])
        return self.runtime.foundation.ok(loaded.value)

    def _refresh_material_manifest(
        self,
        root: Path,
        *,
        canonical_entry: str | None = None,
        missing_entry_issue_kind: str = "resource_draft_canonical_entry_missing",
    ) -> ServiceResult[ResourceMaterialManifest]:
        root = Path(root).expanduser().resolve(strict=False)
        if not root.is_dir():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_material_root_missing",
                    "Resource material root is missing.",
                    object_ref=str(root),
                )
            )
        existing: ResourceMaterialManifest | None = None
        manifest_path = root / "manifest.json"
        if manifest_path.exists():
            loaded = self._load_material_manifest(root)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            existing = loaded.value

        file_views: list[ResourceMaterialFileView] = []
        readable_candidates: list[str] = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name in {"draft.json", "manifest.json", "resource.json"}:
                continue
            relative_path = path.relative_to(root)
            if relative_path.parts and relative_path.parts[0] == "_work":
                continue
            if path.is_symlink():
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "resource_draft_symlink_forbidden",
                        "Resource material must not contain symlinks.",
                        object_ref=str(path),
                    )
                )
            relative = relative_path.as_posix()
            resolution = self.runtime.external.material.resolve_artifact_kind(path)
            category = self._material_category(relative, resolved_kind=resolution.kind)
            readable_kind = None
            if resolution.kind == "plain_text":
                validation = self.runtime.external.material.validate_readable_text(path)
                if validation.ok:
                    readable_kind = self._readable_kind(path)
                    if relative != "README.md":
                        readable_candidates.append(relative)
            file_views.append(
                ResourceMaterialFileView(
                    path=relative,
                    category=category,
                    size_bytes=path.stat().st_size,
                    sha256=self._hash_file(path),
                    resolved_kind=resolution.kind,
                    readable_kind=readable_kind,
                )
            )

        canonical = canonical_entry or (
            existing.canonical_entry if existing is not None else None
        )
        if canonical is not None:
            try:
                canonical = self.runtime.foundation.layout.ensure_relative_path(canonical)
            except ValueError as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "resource_manifest_canonical_entry_invalid",
                        str(exc),
                        object_ref=canonical,
                    )
                )
            if canonical not in readable_candidates:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "resource_manifest_canonical_entry_invalid",
                        "Canonical entry must identify validated readable candidate text outside _work.",
                        object_ref=canonical,
                    )
                )
        elif len(readable_candidates) == 1:
            canonical = readable_candidates[0]
        elif not readable_candidates:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    missing_entry_issue_kind,
                    "Resource material requires validated readable candidate text outside _work.",
                    object_ref=str(root),
                )
            )
        else:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "resource_manifest_canonical_entry_ambiguous",
                    "Multiple readable candidate files require an explicit canonical_entry.",
                    object_ref=str(root),
                    details={"candidates": readable_candidates},
                )
            )
        manifest = ResourceMaterialManifest(
            canonical_entry=canonical,
            files=sorted(file_views, key=lambda item: item.path),
        )
        mode = WriteMode.UPDATE_EXISTING if manifest_path.exists() else WriteMode.CREATE_ONLY
        written = self.runtime.foundation.store.write_json_atomic(manifest_path, manifest, mode=mode)
        if not written.ok:
            return self.runtime.foundation.fail(written.issues)
        return self.runtime.foundation.ok(manifest)

    def _draft_gate_issues(self, repo_root: Path, draft: ResourceDraft) -> list[ServiceIssue]:
        issues: list[ServiceIssue] = []
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
            relative = path.relative_to(draft_root)
            if self._is_forbidden_draft_artifact(relative):
                issues.append(
                    self.runtime.foundation.issue(
                        "resource_draft_artifact_forbidden",
                        "Resource drafts must not retain runtime, cache, credential, or interpreter artifacts.",
                        object_ref=relative.as_posix(),
                    )
                )
        if not (draft_root / "README.md").is_file():
            issues.append(
                self.runtime.foundation.issue(
                    "resource_draft_readme_missing",
                    "Resource draft requires README.md.",
                    object_ref=str(draft_root),
                )
            )
        manifest = self._load_material_manifest(draft_root)
        if not manifest.ok or manifest.value is None:
            issues.extend(manifest.issues)
            return issues
        canonical = draft_root / manifest.value.canonical_entry
        try:
            self.runtime.foundation.layout.assert_within(draft_root, canonical)
        except ValueError as exc:
            issues.append(
                self.runtime.foundation.issue(
                    "resource_manifest_canonical_entry_invalid",
                    str(exc),
                    object_ref=manifest.value.canonical_entry,
                )
            )
            return issues
        validation = self.runtime.external.material.validate_readable_text(canonical)
        if not validation.ok:
            issues.append(
                self.runtime.foundation.issue(
                    validation.issue_code or "resource_manifest_canonical_entry_unreadable",
                    validation.summary,
                    object_ref=manifest.value.canonical_entry,
                )
            )
        for item in manifest.value.files:
            path = draft_root / item.path
            if not path.is_file() or self._hash_file(path) != item.sha256:
                issues.append(
                    self.runtime.foundation.issue(
                        "resource_manifest_file_mismatch",
                        "Resource manifest file metadata does not match current bytes.",
                        object_ref=item.path,
                    )
                )
            if item.category == "content" and item.resolved_kind in {"pdf", "html", "tex_source_archive", "unknown_binary"}:
                issues.append(
                    self.runtime.foundation.issue(
                        "resource_raw_container_outside_work",
                        "Raw textual containers and unknown binaries must stay under _work unless organized as an explicit asset.",
                        object_ref=item.path,
                    )
                )
        for problem in find_literal_tex_include_problems(
            draft_root,
            [item.path for item in manifest.value.files if item.readable_kind is not None],
        ):
            issues.append(
                self.runtime.foundation.issue(
                    "resource_tex_include_invalid",
                    "Final-facing Resource TeX contains a missing or escaping literal local include.",
                    object_ref=problem.source_path,
                    details={
                        "line_number": str(problem.line_number),
                        "reason": problem.reason,
                        "target": problem.target,
                    },
                )
            )
        issues.extend(self._resource_readme_issues(draft_root))
        return issues

    def _resource_readme_issues(
        self,
        draft_root: Path,
    ) -> list[ServiceIssue]:
        readme_path = draft_root / "README.md"
        validation = self.runtime.external.material.validate_readable_text(readme_path)
        if not validation.ok:
            return [
                self.runtime.foundation.issue(
                    validation.issue_code or "resource_draft_readme_unreadable",
                    validation.summary,
                    object_ref="README.md",
                )
            ]
        return []

    @staticmethod
    def _is_forbidden_draft_artifact(relative: Path) -> bool:
        forbidden_parts = {".git", ".agent_runtime", ".codex", ".cache", "__pycache__"}
        if any(part in forbidden_parts for part in relative.parts):
            return True
        name = relative.name.lower()
        return name == "auth.json" or name == ".env" or name.startswith(".env.") or relative.suffix.lower() == ".pyc"

    def _as_schema_error(self, issue: ServiceIssue) -> ServiceIssue:
        return self.runtime.foundation.issue(
            issue.kind,
            issue.message,
            object_ref=issue.object_ref,
            field=issue.field,
            current=issue.current,
            expected=issue.expected,
            suggested_action=issue.suggested_action,
            details=issue.details,
        )

    @staticmethod
    def _canonical_url(value: str) -> str:
        parsed = urlparse(value)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        return f"{scheme}://{netloc}{path}"

    @staticmethod
    def _material_category(
        path: str,
        *,
        resolved_kind: Literal[
            "pdf",
            "html",
            "tex_source_archive",
            "plain_text",
            "directory",
            "unknown_binary",
        ],
    ) -> Literal["content", "asset", "supplementary"]:
        first = Path(path).parts[0] if Path(path).parts else ""
        if first == "supplementary":
            return "supplementary"
        if first in {"assets", "asset"} or resolved_kind == "unknown_binary":
            return "asset"
        return "content"

    @staticmethod
    def _readable_kind(path: Path) -> Literal["plain_text", "markdown", "tex_source"]:
        suffix = path.suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "markdown"
        if suffix == ".tex":
            return "tex_source"
        return "plain_text"

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
