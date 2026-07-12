"""Source index truth, lifecycle gates, and submit views."""

from __future__ import annotations

import hashlib
import json
import os
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.refs import MaterialRef, SourceRef
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceIssue, ServiceResult
from lean_constellation.services.material.source_corpus import SourceCorpusComponent, SourceCorpusManifestView

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


BlockLifecycleStatus = Literal["draft", "refs_done", "links_done", "completed"]
SourceIndexStatus = Literal["draft", "updating", "committed"]


class SourceBlockRef(StrictModel):
    ref_id: str
    material_ref: MaterialRef
    role: str


class SourceLink(StrictModel):
    link_id: str
    source_block_id: str
    target_block_id: str | None = None
    target_hint: str | None = None
    link_kind: str
    evidence_refs: list[MaterialRef] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def _validate_target(self) -> "SourceLink":
        if not self.target_block_id and not (self.target_hint and self.target_hint.strip()):
            raise ValueError("source link requires target_block_id or target_hint")
        return self


class SourceBlock(StrictModel):
    block_id: str
    parent_id: str | None = None
    kind: str
    subtype: str | None = None
    title: str
    summary: str
    lifecycle_status: BlockLifecycleStatus = "draft"
    refs: list[SourceBlockRef] = Field(default_factory=list)
    link_ids: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class SourceFileIndex(StrictModel):
    path: str
    source_sha256: str | None = None
    line_count: int = 0
    readable_text: bool = False
    survey_status: Literal["pending", "surveyed", "skipped"] = "pending"
    indexing_status: Literal["pending", "indexed", "skipped"] = "pending"
    committed: bool = False
    summary: str | None = None


class SourceIndex(StrictModel):
    schema_version: int = 3
    status: SourceIndexStatus = "draft"
    active_update_id: str | None = None
    active_file_scope: list[str] = Field(default_factory=list)
    overview: str | None = None
    root_block_id: str = "root"
    blocks: dict[str, SourceBlock] = Field(default_factory=dict)
    links: dict[str, SourceLink] = Field(default_factory=dict)
    files: dict[str, SourceFileIndex] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    committed_at: str | None = None
    summary: str = "Source index draft."

    @model_validator(mode="after")
    def _validate_update_state(self) -> "SourceIndex":
        if self.schema_version not in {2, 3}:
            raise ValueError("SourceIndex schema_version must be 2 or 3")
        if self.status == "committed" and (self.active_update_id is not None or self.active_file_scope):
            raise ValueError("committed SourceIndex cannot retain active update ownership")
        if self.status == "updating" and (self.active_update_id is None or not self.active_file_scope):
            raise ValueError("updating SourceIndex requires an active update id and file scope")
        if (self.active_update_id is None) != (not self.active_file_scope):
            raise ValueError("SourceIndex active update id and file scope must be set or cleared together")
        if any(file.committed and not file.source_sha256 for file in self.files.values()):
            raise ValueError("committed SourceIndex files require source_sha256")
        return self


class SourceBlockRefView(StrictModel):
    ref_id: str
    material_kind: Literal["source"] = "source"
    path: str
    start_line: int
    end_line: int
    role: str


class SourceLinkView(StrictModel):
    link_id: str
    source_block_id: str
    target_block_id: str | None = None
    target_hint: str | None = None
    link_kind: str
    evidence_ref_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[MaterialRef] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)

    @model_validator(mode="after")
    def _validate_target(self) -> "SourceLinkView":
        if not self.target_block_id and not (self.target_hint and self.target_hint.strip()):
            raise ValueError("source link requires target_block_id or target_hint")
        return self


class SourceBlockView(StrictModel):
    block_id: str
    parent_id: str | None = None
    kind: str
    subtype: str | None = None
    title: str
    summary: str
    lifecycle_status: BlockLifecycleStatus = "draft"
    refs: list[SourceBlockRefView] = Field(default_factory=list)
    link_ids: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    active: bool = True
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


class SourceFileIndexView(StrictModel):
    path: str
    source_sha256: str | None = None
    line_count: int = 0
    readable_text: bool = False
    survey_status: Literal["pending", "surveyed", "skipped"] = "pending"
    indexing_status: Literal["pending", "indexed", "skipped"] = "pending"
    committed: bool = False
    summary: str | None = None


class SourceIndexView(StrictModel):
    repo_root: str
    schema_version: int = 3
    stored_schema_version: int = 3
    migration_required: bool = False
    status: SourceIndexStatus = "draft"
    active_file_scope: list[str] = Field(default_factory=list)
    overview: str | None = None
    root_block_id: str = "root"
    blocks: dict[str, SourceBlockView] = Field(default_factory=dict)
    links: dict[str, SourceLinkView] = Field(default_factory=dict)
    files: dict[str, SourceFileIndexView] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    committed_at: str | None = None
    summary: str = "Source index draft."


class SourceIndexCoverageView(StrictModel):
    file_count: int
    surveyed_file_count: int
    indexed_file_count: int
    block_count: int
    completed_block_count: int
    ref_count: int
    link_count: int
    unfinished_block_ids: list[str] = Field(default_factory=list)
    pending_file_paths: list[str] = Field(default_factory=list)
    summary: str


class ResolvedSourceScopeView(StrictModel):
    mode: Literal["none", "selected", "all"]
    selectors: list[str] = Field(default_factory=list)
    resolved_file_paths: list[str] = Field(default_factory=list)
    readable_file_paths: list[str] = Field(default_factory=list)
    artifact_file_paths: list[str] = Field(default_factory=list)
    manifest_digest: str
    summary: str


class SourceIndexOpenUpdateView(StrictModel):
    outcome: Literal["opened", "already_open", "no_op"]
    previous_status: Literal["missing", "draft", "updating", "committed"]
    active_update_id: str | None = None
    active_file_scope: list[str] = Field(default_factory=list)
    new_file_paths: list[str] = Field(default_factory=list)
    already_committed_file_paths: list[str] = Field(default_factory=list)
    uncommitted_file_paths: list[str] = Field(default_factory=list)
    baseline_digest: str | None = None
    summary: str


class SourceIndexUpdateContextView(StrictModel):
    status: SourceIndexStatus
    active_file_scope: list[str] = Field(default_factory=list)
    files: dict[str, SourceFileIndexView] = Field(default_factory=dict)
    new_file_paths: list[str] = Field(default_factory=list)
    committed_file_paths: list[str] = Field(default_factory=list)
    uncommitted_file_paths: list[str] = Field(default_factory=list)
    coverage: SourceIndexCoverageView
    summary: str


class SourceIndexUpdateGateView(StrictModel):
    gate: GateReport
    expected_baseline_digest: str
    current_index_digest: str
    resolved_file_scope: list[str] = Field(default_factory=list)
    new_block_ids: list[str] = Field(default_factory=list)
    new_link_ids: list[str] = Field(default_factory=list)
    new_ref_ids: list[str] = Field(default_factory=list)
    newly_committed_file_candidates: list[str] = Field(default_factory=list)

    @property
    def gate_issue_kinds(self) -> list[str]:
        return [issue.kind for issue in self.gate.issues]


class SourceIndexCommitView(StrictModel):
    outcome: Literal["committed"] = "committed"
    newly_committed_file_paths: list[str] = Field(default_factory=list)
    appended_block_ids: list[str] = Field(default_factory=list)
    appended_link_ids: list[str] = Field(default_factory=list)
    appended_ref_ids: list[str] = Field(default_factory=list)
    coverage: SourceIndexCoverageView
    summary: str


class SourceIndexSchemaCompatibilityView(StrictModel):
    stored_schema_version: int
    effective_schema_version: int = 3
    migration_required: bool
    source_index_status: str
    file_findings: list[ServiceIssue] = Field(default_factory=list)
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    current_digest: str
    summary: str


class SubmissionView(StrictModel):
    submission_kind: str
    accepted: bool
    summary: str
    approved: bool | None = None
    feedback: str | None = None
    validation: GateReport | None = None
    coverage: SourceIndexCoverageView | None = None


class SourceIndexComponent:
    """Manage `.lean_constellation/source_index/index.json`."""

    def __init__(self, runtime: LeanRuntimeServices, source_corpus: SourceCorpusComponent) -> None:
        self.runtime = runtime
        self.source_corpus = source_corpus
        self._validated_updates: dict[tuple[Path, str], tuple[str, SourceIndexUpdateGateView]] = {}

    def resolve_source_scope(
        self,
        repo_root: Path,
        *,
        source_scope: SourceScope,
    ) -> ServiceResult[ResolvedSourceScopeView]:
        refreshed = self.source_corpus.refresh_source_corpus_manifest(repo_root)
        if not refreshed.ok or refreshed.value is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_corpus_manifest_refresh_failed",
                    "; ".join(issue.message for issue in refreshed.issues),
                )
            )
        manifest = refreshed.value
        paths = sorted(item.path for item in manifest.files)
        if source_scope.mode == "none":
            resolved: list[str] = []
        elif source_scope.mode == "all":
            resolved = paths
        else:
            resolved_set: set[str] = set()
            for selector in source_scope.selectors:
                unsafe = self._unsafe_scope_selector(selector)
                if unsafe is not None:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "source_scope_selector_unsafe",
                            unsafe,
                            object_ref=selector,
                        )
                    )
                matches = self._match_scope_selector(paths, selector)
                if not matches:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "source_scope_selector_unmatched",
                            "Source scope selector did not match any corpus file.",
                            object_ref=selector,
                        )
                    )
                resolved_set.update(matches)
            resolved = sorted(resolved_set)
        by_path = {item.path: item for item in manifest.files}
        readable = [path for path in resolved if by_path[path].readable_text]
        artifacts = [path for path in resolved if not by_path[path].readable_text]
        return self.runtime.foundation.ok(
            ResolvedSourceScopeView(
                mode=source_scope.mode,
                selectors=list(source_scope.selectors),
                resolved_file_paths=resolved,
                readable_file_paths=readable,
                artifact_file_paths=artifacts,
                manifest_digest=self._manifest_digest(manifest),
                summary=f"Resolved {len(resolved)} source files ({len(readable)} readable, {len(artifacts)} artifacts).",
            )
        )

    def open_source_index_update(
        self,
        repo_root: Path,
        *,
        update_id: str,
        resolved_scope: ResolvedSourceScopeView,
        index_policy: Literal["auto", "update", "reuse"],
        expected_baseline_digest: str | None = None,
        retry_baseline_index: SourceIndex | None = None,
    ) -> ServiceResult[SourceIndexOpenUpdateView]:
        update_id = update_id.strip()
        if not update_id:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_update_id_required", "SourceIndex update id must be non-empty.")
            )
        if index_policy not in {"auto", "update", "reuse"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_policy_invalid", f"Unsupported SourceIndex policy: {index_policy}")
            )
        manifest = self.source_corpus.refresh_source_corpus_manifest(repo_root)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        if self._manifest_digest(manifest.value) != resolved_scope.manifest_digest:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_scope_manifest_drift",
                    "Source corpus manifest changed after source scope resolution.",
                )
            )
        manifest_by_path = {item.path: item for item in manifest.value.files}
        if any(path not in manifest_by_path for path in resolved_scope.resolved_file_paths):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_scope_manifest_drift", "Resolved SourceIndex scope is not present in the current manifest.")
            )

        index_path = self._index_path(repo_root)
        if index_path.exists():
            inspected = self.inspect_source_index_schema(repo_root)
            if not inspected.ok or inspected.value is None:
                return self.runtime.foundation.fail(inspected.issues)
            if inspected.value.migration_required:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_index_schema_migration_required",
                        "Persisted SourceIndex must be explicitly migrated before opening an update.",
                    )
                )
            loaded = self.get_source_index_model(repo_root)
            if not loaded.ok or loaded.value is None:
                return self.runtime.foundation.fail(loaded.issues)
            index = loaded.value
            previous_status: Literal["missing", "draft", "updating", "committed"] = index.status
            baseline_digest = self.canonical_source_index_digest(index)
            pre_open_file_paths = set(index.files)
        else:
            index = self._new_index_from_manifest(manifest.value)
            previous_status = "missing"
            baseline_digest = self.missing_source_index_digest()
            pre_open_file_paths = set()

        if index.status in {"draft", "updating"} and index.active_update_id is not None:
            if index.active_update_id != update_id:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_index_update_owner_mismatch",
                        "Another SourceIndex update owns the current mutable truth.",
                        current=index.active_update_id,
                        expected=update_id,
                    )
                )
            if index.active_file_scope != resolved_scope.resolved_file_paths:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_index_update_already_open",
                        "The existing SourceIndex update has a different active file scope.",
                    )
                )
            if expected_baseline_digest is not None:
                retry_baseline_digest = (
                    self.missing_source_index_digest()
                    if retry_baseline_index is None
                    else self.canonical_source_index_digest(retry_baseline_index)
                )
                if retry_baseline_digest != expected_baseline_digest:
                    return self.runtime.foundation.fail(
                        self.runtime.foundation.issue(
                            "source_index_baseline_digest_mismatch",
                            "The caller-supplied retry baseline does not match its expected digest.",
                            current=retry_baseline_digest,
                            expected=expected_baseline_digest,
                        )
                    )
            return self.runtime.foundation.ok(
                self._open_update_view(
                    index,
                    previous_status=previous_status,
                    baseline_digest=None,
                    outcome="already_open",
                    new_file_paths=[path for path in index.active_file_scope if not index.files[path].committed],
                )
            )

        if expected_baseline_digest is not None and baseline_digest != expected_baseline_digest:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_baseline_digest_mismatch",
                    "The SourceIndex pre-open baseline differs from the caller's expected digest.",
                    current=baseline_digest,
                    expected=expected_baseline_digest,
                )
            )

        committed_paths = {path for path, item in index.files.items() if item.committed}
        for path in sorted(committed_paths):
            current = manifest_by_path.get(path)
            if current is None:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "committed_source_file_missing",
                        "A committed SourceIndex file is missing from the fresh source corpus manifest.",
                        object_ref=path,
                    )
                )
            stored = index.files[path]
            if stored.source_sha256 != current.sha256:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "committed_source_file_changed",
                        "A committed SourceIndex file changed and cannot be silently re-indexed.",
                        object_ref=path,
                        current=current.sha256,
                        expected=stored.source_sha256,
                    )
                )

        selected = list(resolved_scope.resolved_file_paths)
        selected_uncommitted = [path for path in selected if path not in committed_paths]
        selected_committed = [path for path in selected if path in committed_paths]
        if index_policy == "reuse":
            semantic_uncommitted = [path for path in selected_uncommitted if manifest_by_path[path].readable_text]
            if semantic_uncommitted:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        "source_index_scope_not_reusable",
                        "Selected readable source files have not been committed.",
                        current=", ".join(semantic_uncommitted),
                    )
                )
            return self.runtime.foundation.ok(
                SourceIndexOpenUpdateView(
                    outcome="no_op",
                    previous_status=previous_status,
                    active_file_scope=[],
                    already_committed_file_paths=selected_committed,
                    uncommitted_file_paths=selected_uncommitted,
                    baseline_digest=baseline_digest,
                    summary="Selected SourceIndex scope is reusable without mutation.",
                )
            )
        if not selected or (index_policy == "auto" and not selected_uncommitted):
            return self.runtime.foundation.ok(
                SourceIndexOpenUpdateView(
                    outcome="no_op",
                    previous_status=previous_status,
                    active_file_scope=[],
                    already_committed_file_paths=selected_committed,
                    baseline_digest=baseline_digest,
                    summary="SourceIndex update is a no-op for the selected scope.",
                )
            )

        for path, item in manifest_by_path.items():
            existing = index.files.get(path)
            if existing is None:
                index.files[path] = self._file_from_manifest(item)
            elif not existing.committed:
                index.files[path] = self._file_from_manifest(item)
        index.status = "draft" if previous_status in {"missing", "draft"} else "updating"
        index.active_update_id = update_id
        index.active_file_scope = selected
        self._touch(index, "Opened scoped SourceIndex update.")
        saved = self._save_model(repo_root, index)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(
            self._open_update_view(
                saved.value,
                previous_status=previous_status,
                baseline_digest=baseline_digest,
                outcome="opened",
                new_file_paths=sorted(path for path in selected if path not in pre_open_file_paths),
            )
        )

    def get_source_index_update_context(self, repo_root: Path) -> ServiceResult[SourceIndexUpdateContextView]:
        loaded = self.get_source_index_model(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        coverage = self._source_index_coverage(loaded.value, scope=loaded.value.active_file_scope or None)
        if not coverage.ok or coverage.value is None:
            return self.runtime.foundation.fail(coverage.issues)
        files = loaded.value.files
        active = loaded.value.active_file_scope
        context_paths = active or sorted(files)
        return self.runtime.foundation.ok(
            SourceIndexUpdateContextView(
                status=loaded.value.status,
                active_file_scope=list(active),
                files={path: self._to_file_view(files[path]) for path in context_paths if path in files},
                new_file_paths=[path for path in active if path in files and not files[path].committed],
                committed_file_paths=sorted(path for path, item in files.items() if item.committed),
                uncommitted_file_paths=sorted(path for path, item in files.items() if not item.committed),
                coverage=coverage.value,
                summary=f"SourceIndex {loaded.value.status} with {len(active)} active files.",
            )
        )

    def inspect_source_index_schema(self, repo_root: Path) -> ServiceResult[SourceIndexSchemaCompatibilityView]:
        payload = self._read_stored_payload(repo_root)
        if not payload.ok or payload.value is None:
            return self.runtime.foundation.fail(payload.issues)
        return self._inspect_source_index_payload(repo_root, payload.value)

    def _inspect_source_index_payload(
        self, repo_root: Path, payload: dict[str, object]
    ) -> ServiceResult[SourceIndexSchemaCompatibilityView]:
        try:
            stored_version = int(payload.get("schema_version", 2))
        except (TypeError, ValueError):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_schema_unsupported",
                    "SourceIndex schema_version must be an integer.",
                )
            )
        status = str(payload.get("status", "draft"))
        findings: list[ServiceIssue] = []
        if stored_version not in {2, 3}:
            findings.append(
                self.runtime.foundation.issue(
                    "source_index_schema_unsupported",
                    f"Unsupported SourceIndex schema version: {stored_version}",
                )
            )
        elif stored_version == 2:
            if status == "draft":
                findings.append(
                    self.runtime.foundation.issue(
                        "source_index_draft_migration_ambiguous",
                        "A v2 draft has no trustworthy update owner or active scope and requires lifecycle-level review.",
                    )
                )
            mapped = self._map_v2_payload(repo_root, payload)
            if not mapped.ok:
                findings.extend(mapped.issues)
            elif mapped.value is not None:
                findings.extend(
                    self._validate_index(
                        repo_root,
                        mapped.value,
                        require_completed=status == "committed",
                    )
                )
        blockers = [issue.kind for issue in findings]
        return self.runtime.foundation.ok(
            SourceIndexSchemaCompatibilityView(
                stored_schema_version=stored_version,
                migration_required=stored_version != 3,
                source_index_status=status,
                file_findings=findings,
                blocking_issue_kinds=blockers,
                current_digest=self._payload_digest(payload),
                summary=(
                    "SourceIndex schema is current."
                    if stored_version == 3
                    else f"SourceIndex schema v{stored_version} requires explicit migration."
                ),
            )
        )

    def migrate_source_index_schema(
        self,
        repo_root: Path,
        *,
        expected_source_index_digest: str,
    ) -> ServiceResult[SourceIndexView]:
        path = self._index_path(repo_root)
        try:
            backup_bytes = path.read_bytes()
            payload = json.loads(backup_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_migration_backup_failed", str(exc))
            )
        if not isinstance(payload, dict):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_invalid", "SourceIndex JSON must be an object.")
            )
        authoritative_digest = self._payload_digest(payload)
        if authoritative_digest != expected_source_index_digest:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_migration_digest_mismatch",
                    "SourceIndex changed after schema inspection.",
                    current=authoritative_digest,
                    expected=expected_source_index_digest,
                )
            )
        inspected = self._inspect_source_index_payload(repo_root, payload)
        if not inspected.ok or inspected.value is None:
            return self.runtime.foundation.fail(inspected.issues)
        if inspected.value.stored_schema_version == 3:
            return self.get_source_index(repo_root)
        if inspected.value.blocking_issue_kinds:
            return self.runtime.foundation.fail(inspected.value.file_findings)
        mapped = self._map_v2_payload(repo_root, payload)
        if not mapped.ok or mapped.value is None:
            return self.runtime.foundation.fail(mapped.issues)
        written = self._save_model(repo_root, mapped.value)
        if not written.ok:
            restored = self._restore_bytes_atomic(path, backup_bytes)
            if restored is not None:
                return self.runtime.foundation.fail([*written.issues, restored])
            return self.runtime.foundation.fail(written.issues)
        verified = self._verify_migrated_source_index(repo_root)
        if not verified.ok:
            restored = self._restore_bytes_atomic(path, backup_bytes)
            if restored is not None:
                return self.runtime.foundation.fail([*verified.issues, restored])
            return self.runtime.foundation.fail(verified.issues)
        return self.get_source_index(repo_root)

    def create_draft_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        path = self._index_path(repo_root)
        if path.exists():
            return self.get_source_index(repo_root)
        manifest = self.source_corpus.get_source_corpus_manifest(repo_root)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        index = self._new_index_from_manifest(manifest.value)
        write = self.runtime.foundation.store.write_json_atomic(path, index)
        if not write.ok:
            return self.runtime.foundation.fail(write.issues)
        return self.runtime.foundation.ok(self._to_index_view(repo_root, index, stored_schema_version=3))

    def get_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        loaded = self._load_effective_index(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        index, stored_version = loaded.value
        return self.runtime.foundation.ok(self._to_index_view(repo_root, index, stored_schema_version=stored_version))

    def get_committed_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        loaded = self._load_effective_index(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        index, stored_version = loaded.value
        if index.status != "committed" or index.active_update_id is not None or index.active_file_scope:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_not_committed",
                    "Committed SourceIndex read requires a committed SourceIndex.",
                )
            )
        return self.runtime.foundation.ok(self._to_index_view(repo_root, index, stored_schema_version=stored_version))

    def get_source_index_model(self, repo_root: Path) -> ServiceResult[SourceIndex]:
        loaded = self._load_effective_index(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        return self.runtime.foundation.ok(loaded.value[0])

    def set_source_index_overview(
        self, repo_root: Path, *, overview: str, expected_update_id: str | None = None
    ) -> ServiceResult[SourceIndexView]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        overview = overview.strip()
        if not overview:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_index_overview_empty", "SourceIndex overview must be non-empty."))
        index.value.overview = overview
        index.value.updated_at = utc_now_iso()
        index.value.summary = "Updated source index overview."
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._to_index_view(repo_root, saved.value))

    def create_source_block(
        self,
        repo_root: Path,
        *,
        parent_id: str,
        kind: str,
        subtype: str | None,
        title: str,
        summary: str,
        expected_update_id: str | None = None,
    ) -> ServiceResult[SourceBlockView]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        if parent_id not in index.value.blocks:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_block_parent_missing", f"Parent block not found: {parent_id}"))
        kind = kind.strip()
        title = title.strip()
        summary = summary.strip()
        field_issue = self._required_field_issue(
            [
                ("kind", kind),
                ("title", title),
                ("summary", summary),
            ]
        )
        if field_issue is not None:
            return self.runtime.foundation.fail(field_issue)
        block_id = self._next_id("b", index.value.blocks)
        block = SourceBlock(
            block_id=block_id,
            parent_id=parent_id,
            kind=kind,
            subtype=subtype.strip() if subtype else None,
            title=title,
            summary=summary,
        )
        index.value.blocks[block_id] = block
        parent = index.value.blocks[parent_id]
        parent.child_ids.append(block_id)
        parent.updated_at = utc_now_iso()
        parent.lifecycle_status = "draft"
        self._touch(index.value, "Created source block.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._to_block_view(saved.value, block))

    def update_source_block(
        self,
        repo_root: Path,
        *,
        block_id: str,
        title: str | None = None,
        summary: str | None = None,
        kind: str | None = None,
        subtype: str | None = None,
        expected_update_id: str | None = None,
    ) -> ServiceResult[SourceBlockView]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        block = self._block_or_issue(index.value, block_id)
        if not block.ok or block.value is None:
            return self.runtime.foundation.fail(block.issues)
        current = block.value
        if title is not None:
            title = title.strip()
            if not title:
                return self.runtime.foundation.fail(self.runtime.foundation.issue("source_block_field_empty", "Source block title must be non-empty.", object_ref=block_id, field="title"))
            current.title = title
        if summary is not None:
            summary = summary.strip()
            if not summary:
                return self.runtime.foundation.fail(self.runtime.foundation.issue("source_block_field_empty", "Source block summary must be non-empty.", object_ref=block_id, field="summary"))
            current.summary = summary
        if kind is not None:
            kind = kind.strip()
            if not kind:
                return self.runtime.foundation.fail(self.runtime.foundation.issue("source_block_field_empty", "Source block kind must be non-empty.", object_ref=block_id, field="kind"))
            current.kind = kind
        if subtype is not None:
            current.subtype = subtype.strip() or None
        current.lifecycle_status = "draft"
        current.updated_at = utc_now_iso()
        self._touch(index.value, "Updated source block.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._to_block_view(saved.value, current))

    def add_source_block_ref(
        self,
        repo_root: Path,
        *,
        block_id: str,
        path: str,
        start_line: int,
        end_line: int,
        role: str,
        expected_update_id: str | None = None,
    ) -> ServiceResult[SourceBlockView]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        block = self._block_or_issue(index.value, block_id)
        if not block.ok or block.value is None:
            return self.runtime.foundation.fail(block.issues)
        validation = self.source_corpus.validate_source_ref(
            repo_root,
            path=path,
            start_line=start_line,
            end_line=end_line,
        )
        if not validation.ok or validation.value is None:
            return self.runtime.foundation.fail(validation.issues)
        if not validation.value.valid:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    validation.value.issue_code or "source_ref_invalid",
                    validation.value.summary,
                    object_ref=path,
                )
            )
        if index.value.active_update_id is not None and validation.value.path not in index.value.active_file_scope:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_ref_outside_active_scope",
                    "New SourceIndex refs must point into the active file scope.",
                    object_ref=validation.value.path,
                )
            )
        role = role.strip()
        if not role:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_ref_role_empty", "Source ref role must be non-empty.", object_ref=block_id, field="role"))
        ref = SourceBlockRef(
            ref_id=self._next_ref_id(index.value),
            material_ref=MaterialRef(
                kind="source",
                ref=SourceRef(path=validation.value.path, start_line=start_line, end_line=end_line),
            ),
            role=role,
        )
        block.value.refs.append(ref)
        block.value.lifecycle_status = "draft"
        block.value.updated_at = utc_now_iso()
        self._touch(index.value, "Added source block ref.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._to_block_view(saved.value, block.value))

    def remove_source_block_ref(
        self, repo_root: Path, *, block_id: str, ref_id: str, expected_update_id: str | None = None
    ) -> ServiceResult[SourceBlockView]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        block = self._block_or_issue(index.value, block_id)
        if not block.ok or block.value is None:
            return self.runtime.foundation.fail(block.issues)
        removed_refs = [ref.material_ref for ref in block.value.refs if ref.ref_id == ref_id]
        original = len(block.value.refs)
        block.value.refs = [ref for ref in block.value.refs if ref.ref_id != ref_id]
        if len(block.value.refs) == original:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_ref_missing", f"Source ref not found: {ref_id}"))
        for link in index.value.links.values():
            link.evidence_refs = [item for item in link.evidence_refs if item not in removed_refs]
        block.value.lifecycle_status = "draft"
        block.value.updated_at = utc_now_iso()
        self._touch(index.value, "Removed source block ref.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._to_block_view(saved.value, block.value))

    def mark_block_refs_done(
        self, repo_root: Path, *, block_id: str, expected_update_id: str | None = None
    ) -> ServiceResult[GateReport]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        block = self._block_or_issue(index.value, block_id)
        if not block.ok or block.value is None:
            return self.runtime.foundation.fail(block.issues)
        issues = []
        if block_id != index.value.root_block_id and not block.value.refs and not self._allows_no_direct_refs(block.value.summary):
            issues.append(
                self.runtime.foundation.issue(
                    "source_block_refs_missing",
                    "Non-root source block needs at least one source ref or an explicit no-direct-ref summary.",
                    object_ref=block_id,
                )
            )
        for ref in block.value.refs:
            source_ref = self._source_ref_or_issue(ref.material_ref, object_ref=ref.ref_id)
            if not source_ref.ok or source_ref.value is None:
                issues.extend(source_ref.issues)
                continue
            valid = self.source_corpus.validate_source_ref(
                repo_root,
                path=source_ref.value.path,
                start_line=source_ref.value.start_line or 1,
                end_line=source_ref.value.end_line or source_ref.value.start_line or 1,
            )
            if not valid.ok or valid.value is None:
                issues.extend(valid.issues)
            elif not valid.value.valid:
                issues.append(
                    self.runtime.foundation.issue(
                        valid.value.issue_code or "source_ref_invalid",
                        valid.value.summary,
                        object_ref=ref.ref_id,
                    )
                )
        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("source_block_refs_done", issues, summary="Source block refs are not ready."))
        block.value.lifecycle_status = "refs_done"
        block.value.updated_at = utc_now_iso()
        self._touch(index.value, "Marked source block refs done.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("source_block_refs_done", summary="Source block refs are ready."))

    def create_source_link(
        self,
        repo_root: Path,
        *,
        source_block_id: str,
        target_block_id: str | None,
        target_hint: str | None,
        link_kind: str,
        evidence_ref_ids: list[str],
        expected_update_id: str | None = None,
    ) -> ServiceResult[SourceLinkView]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        source = self._block_or_issue(index.value, source_block_id)
        if not source.ok or source.value is None:
            return self.runtime.foundation.fail(source.issues)
        if target_block_id and target_block_id not in index.value.blocks:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_link_target_missing", f"Target block not found: {target_block_id}"))
        link_kind = link_kind.strip()
        if not link_kind:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_link_kind_empty", "Source link kind must be non-empty.", object_ref=source_block_id, field="link_kind"))
        if not evidence_ref_ids:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_link_evidence_empty", "Source link needs at least one evidence ref.", object_ref=source_block_id))
        source_ref_by_id = {ref.ref_id: ref for ref in source.value.refs}
        missing_refs = [ref_id for ref_id in evidence_ref_ids if ref_id not in source_ref_by_id]
        if missing_refs:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_link_evidence_missing",
                    "Evidence refs must belong to the source block.",
                    current=", ".join(missing_refs),
                )
            )
        link_id = self._next_id("link", index.value.links)
        try:
            link = SourceLink(
                link_id=link_id,
                source_block_id=source_block_id,
                target_block_id=target_block_id,
                target_hint=target_hint.strip() if target_hint else None,
                link_kind=link_kind,
                evidence_refs=[source_ref_by_id[ref_id].material_ref for ref_id in evidence_ref_ids],
            )
        except Exception as exc:  # noqa: BLE001 - normalize pydantic validation into ServiceResult.
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_link_invalid", str(exc)))
        index.value.links[link_id] = link
        source.value.link_ids.append(link_id)
        source.value.lifecycle_status = "refs_done"
        source.value.updated_at = utc_now_iso()
        self._touch(index.value, "Created source link.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._to_link_view(saved.value, link))

    def mark_block_links_done(
        self, repo_root: Path, *, block_id: str, expected_update_id: str | None = None
    ) -> ServiceResult[GateReport]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        block = self._block_or_issue(index.value, block_id)
        if not block.ok or block.value is None:
            return self.runtime.foundation.fail(block.issues)
        if block.value.lifecycle_status not in {"refs_done", "links_done", "completed"}:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "source_block_links_done",
                    self.runtime.foundation.issue("source_block_refs_not_done", "Refs must be marked done before links.", object_ref=block_id),
                    summary="Source block links are not ready.",
                )
            )
        issues = []
        for link_id in block.value.link_ids:
            link = index.value.links.get(link_id)
            if link is None:
                issues.append(self.runtime.foundation.issue("source_link_missing", f"Link missing: {link_id}", object_ref=block_id))
            elif not link.target_block_id and not (link.target_hint and link.target_hint.strip()):
                issues.append(self.runtime.foundation.issue("source_link_target_missing", "Unresolved link needs target_hint.", object_ref=link_id))
            elif not link.evidence_refs:
                issues.append(self.runtime.foundation.issue("source_link_evidence_empty", "Source link needs at least one evidence ref.", object_ref=link_id))
            else:
                ref_by_material = {ref.material_ref.model_dump_json(): ref.ref_id for ref in block.value.refs}
                missing_refs = [ref.model_dump_json() for ref in link.evidence_refs if ref.model_dump_json() not in ref_by_material]
                if missing_refs:
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_link_evidence_missing",
                            "Link evidence refs do not belong to source block.",
                            object_ref=link_id,
                            current=", ".join(missing_refs),
                        )
                    )
        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("source_block_links_done", issues, summary="Source block links are not ready."))
        block.value.lifecycle_status = "links_done"
        block.value.updated_at = utc_now_iso()
        self._touch(index.value, "Marked source block links done.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("source_block_links_done", summary="Source block links are ready."))

    def mark_block_completed(
        self, repo_root: Path, *, block_id: str, expected_update_id: str | None = None
    ) -> ServiceResult[GateReport]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        block = self._block_or_issue(index.value, block_id)
        if not block.ok or block.value is None:
            return self.runtime.foundation.fail(block.issues)
        if block.value.lifecycle_status not in {"links_done", "completed"}:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "source_block_completed",
                    self.runtime.foundation.issue("source_block_links_not_done", "Links must be marked done before completion.", object_ref=block_id),
                    summary="Source block is not complete.",
                )
            )
        child_incomplete = [
            child_id
            for child_id in block.value.child_ids
            if index.value.blocks[child_id].active and index.value.blocks[child_id].lifecycle_status != "completed"
        ]
        if child_incomplete:
            return self.runtime.foundation.ok(
                self.runtime.foundation.gate_failed(
                    "source_block_completed",
                    self.runtime.foundation.issue("source_block_children_incomplete", "Child blocks must be completed first.", current=", ".join(child_incomplete)),
                    summary="Source block has incomplete children.",
                )
            )
        block.value.lifecycle_status = "completed"
        block.value.updated_at = utc_now_iso()
        self._touch(index.value, "Marked source block completed.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("source_block_completed", summary="Source block is complete."))

    def set_file_survey_status(
        self,
        repo_root: Path,
        *,
        path: str,
        status: Literal["pending", "surveyed", "skipped"],
        summary: str | None = None,
        expected_update_id: str | None = None,
    ) -> ServiceResult[SourceFileIndexView]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        file = self._file_or_issue(index.value, path)
        if not file.ok or file.value is None:
            return self.runtime.foundation.fail(file.issues)
        if index.value.active_update_id is not None and path not in index.value.active_file_scope:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_file_outside_active_scope",
                    "Source file status can only be changed inside the active SourceIndex scope.",
                    object_ref=path,
                )
            )
        if status not in {"pending", "surveyed", "skipped"}:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_source_file_survey_status", f"Invalid survey status: {status}", object_ref=path))
        file.value.survey_status = status
        file.value.summary = summary
        self._touch(index.value, "Updated source file survey status.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._to_file_view(file.value))

    def set_file_indexing_status(
        self,
        repo_root: Path,
        *,
        path: str,
        status: Literal["pending", "indexed", "skipped"],
        expected_update_id: str | None = None,
    ) -> ServiceResult[SourceFileIndexView]:
        index = self._load_mutable(repo_root, expected_update_id=expected_update_id)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        file = self._file_or_issue(index.value, path)
        if not file.ok or file.value is None:
            return self.runtime.foundation.fail(file.issues)
        if index.value.active_update_id is not None and path not in index.value.active_file_scope:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_file_outside_active_scope",
                    "Source file status can only be changed inside the active SourceIndex scope.",
                    object_ref=path,
                )
            )
        if status not in {"pending", "indexed", "skipped"}:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("invalid_source_file_indexing_status", f"Invalid indexing status: {status}", object_ref=path))
        file.value.indexing_status = status
        self._touch(index.value, "Updated source file indexing status.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self._to_file_view(file.value))

    def validate_source_index(self, repo_root: Path) -> ServiceResult[GateReport]:
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        if index.value.active_file_scope:
            file_scope: set[str] | None = set(index.value.active_file_scope)
        elif index.value.status == "committed":
            file_scope = {path for path, item in index.value.files.items() if item.committed}
        else:
            file_scope = None
        issues = self._validate_index(repo_root, index.value, require_completed=True, file_scope=file_scope)
        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("source_index", issues, summary=f"{len(issues)} source index checks failed."))
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("source_index", summary="Source index is valid."))

    def get_source_index_coverage(self, repo_root: Path) -> ServiceResult[SourceIndexCoverageView]:
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        return self._source_index_coverage(index.value, scope=index.value.active_file_scope or None)

    def get_committed_source_index_coverage(self, repo_root: Path) -> ServiceResult[SourceIndexCoverageView]:
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        if index.value.status != "committed" or index.value.active_update_id is not None or index.value.active_file_scope:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_not_committed",
                    "Committed SourceIndex coverage read requires a committed SourceIndex.",
                )
            )
        return self._source_index_coverage(index.value)

    def _source_index_coverage(
        self, index: SourceIndex, *, scope: list[str] | None = None
    ) -> ServiceResult[SourceIndexCoverageView]:
        blocks = [block for block in index.blocks.values() if block.active and block.block_id != index.root_block_id]
        files = [item for path, item in index.files.items() if scope is None or path in scope]
        unfinished = [block.block_id for block in blocks if block.lifecycle_status != "completed"]
        pending = [
            file.path
            for file in files
            if file.readable_text and (file.survey_status == "pending" or file.indexing_status == "pending")
        ]
        coverage = SourceIndexCoverageView(
            file_count=len(files),
            surveyed_file_count=sum(1 for file in files if file.survey_status in {"surveyed", "skipped"}),
            indexed_file_count=sum(1 for file in files if file.indexing_status in {"indexed", "skipped"}),
            block_count=len(blocks),
            completed_block_count=sum(1 for block in blocks if block.lifecycle_status == "completed"),
            ref_count=sum(len(block.refs) for block in blocks),
            link_count=len(index.links),
            unfinished_block_ids=unfinished,
            pending_file_paths=pending,
            summary=f"{len(blocks) - len(unfinished)}/{len(blocks)} blocks completed; {len(files) - len(pending)}/{len(files)} files non-pending.",
        )
        return self.runtime.foundation.ok(coverage)

    def submit_source_index_builder_round(self, repo_root: Path, *, summary: str, ctx: object | None = None) -> ServiceResult[SubmissionView]:
        del ctx
        if not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_submission_summary", "Builder submission requires a summary."))
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        if index.value.status not in {"draft", "updating"}:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_index_not_draft", "Builder can only submit a mutable SourceIndex."))
        validation = self.validate_source_index(repo_root)
        if not validation.ok or validation.value is None:
            return self.runtime.foundation.fail(validation.issues)
        coverage = self.get_source_index_coverage(repo_root)
        if not coverage.ok or coverage.value is None:
            return self.runtime.foundation.fail(coverage.issues)
        if not validation.value.passed:
            return self.runtime.foundation.fail(validation.value.issues)
        return self.runtime.foundation.ok(
            SubmissionView(
                submission_kind="source_index_builder_round",
                accepted=True,
                summary=summary.strip(),
                validation=validation.value,
                coverage=coverage.value,
            )
        )

    def submit_source_index_review_round(
        self,
        repo_root: Path,
        *,
        approved: bool,
        summary: str,
        feedback: str | None = None,
        ctx: object | None = None,
    ) -> ServiceResult[SubmissionView]:
        del ctx
        if not summary.strip():
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_review_summary", "Reviewer submission requires a summary."))
        if not approved and not (feedback and feedback.strip()):
            return self.runtime.foundation.fail(self.runtime.foundation.issue("missing_review_feedback", "Rejected review requires feedback."))
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        if index.value.status not in {"draft", "updating"}:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_index_not_draft", "Reviewer can only submit a mutable SourceIndex."))
        return self.runtime.foundation.ok(
            SubmissionView(
                submission_kind="source_index_review_round",
                accepted=True,
                approved=approved,
                summary=summary.strip(),
                feedback=feedback.strip() if feedback else None,
            )
        )

    def validate_source_index_update(
        self,
        repo_root: Path,
        *,
        update_id: str,
        baseline_index: SourceIndex | None,
        expected_baseline_digest: str,
        resolved_scope: list[str],
        require_completed: bool,
    ) -> ServiceResult[SourceIndexUpdateGateView]:
        current_result = self.get_source_index_model(repo_root)
        if not current_result.ok or current_result.value is None:
            return self.runtime.foundation.fail(current_result.issues)
        current = current_result.value
        owner = self._assert_update_owner(current, update_id)
        if owner is not None:
            return self.runtime.foundation.fail(owner)
        issues: list[ServiceIssue] = []
        if current.active_file_scope != resolved_scope:
            issues.append(
                self.runtime.foundation.issue(
                    "source_index_active_scope_mismatch",
                    "Persisted active SourceIndex scope differs from the Flow-resolved scope.",
                    current=", ".join(current.active_file_scope),
                    expected=", ".join(resolved_scope),
                )
            )
        source_hash_issues = self._current_source_hash_issues(
            repo_root,
            current,
            paths={
                path
                for path, file in current.files.items()
                if file.committed or path in resolved_scope
            },
        )
        if not source_hash_issues.ok or source_hash_issues.value is None:
            return self.runtime.foundation.fail(source_hash_issues.issues)
        issues.extend(source_hash_issues.value)

        actual_baseline_digest = (
            self.missing_source_index_digest()
            if baseline_index is None
            else self.canonical_source_index_digest(baseline_index)
        )
        if actual_baseline_digest != expected_baseline_digest:
            issues.append(
                self.runtime.foundation.issue(
                    "source_index_baseline_digest_mismatch",
                    "SourceIndex baseline digest differs from the expected pre-update checkpoint digest.",
                    current=actual_baseline_digest,
                    expected=expected_baseline_digest,
                )
            )

        issues.extend(
            self._validate_index(
                repo_root,
                current,
                require_completed=require_completed,
                file_scope=set(resolved_scope),
            )
        )
        baseline_blocks = baseline_index.blocks if baseline_index is not None else {}
        baseline_links = baseline_index.links if baseline_index is not None else {}
        baseline_ref_models = {
            ref.ref_id: ref
            for block in baseline_blocks.values()
            for ref in block.refs
        }
        baseline_refs = set(baseline_ref_models)

        if baseline_index is not None:
            for block_id, old in baseline_index.blocks.items():
                new = current.blocks.get(block_id)
                if new is None:
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_block_missing",
                            "A baseline SourceIndex block was removed.",
                            object_ref=block_id,
                        )
                    )
                    continue
                old_scalar = self._block_baseline_scalar(old)
                new_scalar = self._block_baseline_scalar(new)
                if old_scalar != new_scalar:
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_block_changed",
                            "A baseline SourceIndex block semantic payload changed.",
                            object_ref=block_id,
                        )
                    )
                old_ref_ids = [ref.ref_id for ref in old.refs]
                new_ref_ids = [ref.ref_id for ref in new.refs]
                if new_ref_ids != old_ref_ids:
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_ref_adjacency_changed",
                            "Baseline block refs must retain exactly the same identities and order.",
                            object_ref=block_id,
                        )
                    )
                new_refs_by_id = {ref.ref_id: ref for ref in new.refs}
                for ref_id, old_ref in ((ref.ref_id, ref) for ref in old.refs):
                    new_ref = new_refs_by_id.get(ref_id)
                    if new_ref is None:
                        issues.append(
                            self.runtime.foundation.issue(
                                "source_index_baseline_ref_missing",
                                "A baseline SourceIndex ref was removed.",
                                object_ref=ref_id,
                            )
                        )
                    elif new_ref != old_ref:
                        issues.append(
                            self.runtime.foundation.issue(
                                "source_index_baseline_ref_changed",
                                "A baseline SourceIndex ref semantic payload changed.",
                                object_ref=ref_id,
                            )
                        )
                if new.child_ids[: len(old.child_ids)] != old.child_ids or len(new.child_ids) < len(old.child_ids):
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_adjacency_changed",
                            "Baseline child adjacency may only be extended by appending new identities.",
                            object_ref=block_id,
                        )
                    )
                if new.link_ids[: len(old.link_ids)] != old.link_ids or len(new.link_ids) < len(old.link_ids):
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_adjacency_changed",
                            "Baseline link adjacency may only be extended by appending new identities.",
                            object_ref=block_id,
                        )
                    )
            for link_id, old in baseline_index.links.items():
                new = current.links.get(link_id)
                if new is None:
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_link_missing",
                            "A baseline SourceIndex link was removed.",
                            object_ref=link_id,
                        )
                    )
                elif self._link_semantic_payload(old) != self._link_semantic_payload(new):
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_link_changed",
                            "A baseline SourceIndex link semantic payload changed.",
                            object_ref=link_id,
                        )
                    )
            for path, old in baseline_index.files.items():
                if not old.committed:
                    continue
                new = current.files.get(path)
                if new is None:
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_file_missing",
                            "A committed baseline SourceIndex file was removed.",
                            object_ref=path,
                        )
                    )
                elif old != new:
                    issues.append(
                        self.runtime.foundation.issue(
                            "source_index_baseline_file_changed",
                            "A committed baseline SourceIndex file payload changed.",
                            object_ref=path,
                        )
                    )

        new_block_ids = sorted(set(current.blocks) - set(baseline_blocks) - {current.root_block_id})
        new_link_ids = sorted(set(current.links) - set(baseline_links))
        current_refs = {ref.ref_id for block in current.blocks.values() for ref in block.refs}
        new_ref_ids = sorted(current_refs - baseline_refs)
        for block_id in new_block_ids:
            block = current.blocks[block_id]
            source_paths = []
            for ref in block.refs:
                if ref.material_ref.kind == "source" and isinstance(ref.material_ref.ref, SourceRef):
                    source_paths.append(ref.material_ref.ref.path)
            if not source_paths or any(path not in resolved_scope for path in source_paths):
                issues.append(
                    self.runtime.foundation.issue(
                        "source_index_new_block_outside_active_scope",
                        "Every new SourceIndex block must have direct source evidence inside the active scope.",
                        object_ref=block_id,
                    )
                )

        candidates = sorted(
            path
            for path in resolved_scope
            if path in current.files and not current.files[path].committed
        )
        current_digest = self.canonical_source_index_digest(current)
        gate = (
            self.runtime.foundation.gate_failed(
                "source_index_update",
                issues,
                summary=f"{len(issues)} scoped SourceIndex update checks failed.",
            )
            if issues
            else self.runtime.foundation.gate_passed(
                "source_index_update",
                summary="Scoped SourceIndex update is append-only and ready to commit.",
            )
        )
        view = SourceIndexUpdateGateView(
            gate=gate,
            expected_baseline_digest=expected_baseline_digest,
            current_index_digest=current_digest,
            resolved_file_scope=list(resolved_scope),
            new_block_ids=new_block_ids,
            new_link_ids=new_link_ids,
            new_ref_ids=new_ref_ids,
            newly_committed_file_candidates=candidates,
        )
        key = (self._repo_key(repo_root), update_id)
        if gate.passed:
            self._validated_updates[key] = (current_digest, view)
        else:
            self._validated_updates.pop(key, None)
        return self.runtime.foundation.ok(view)

    def commit_source_index_update(
        self,
        repo_root: Path,
        *,
        update_id: str,
        validated: SourceIndexUpdateGateView,
    ) -> ServiceResult[SourceIndexCommitView]:
        if not validated.gate.passed:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_update_not_validated",
                    "A failed SourceIndex update gate cannot be committed.",
                )
            )
        current_result = self.get_source_index_model(repo_root)
        if not current_result.ok or current_result.value is None:
            return self.runtime.foundation.fail(current_result.issues)
        current = current_result.value
        owner = self._assert_update_owner(current, update_id)
        if owner is not None:
            return self.runtime.foundation.fail(owner)
        source_hash_issues = self._current_source_hash_issues(
            repo_root,
            current,
            paths={
                path
                for path, file in current.files.items()
                if file.committed or path in current.active_file_scope
            },
        )
        if not source_hash_issues.ok or source_hash_issues.value is None:
            return self.runtime.foundation.fail(source_hash_issues.issues)
        if source_hash_issues.value:
            return self.runtime.foundation.fail(source_hash_issues.value)
        key = (self._repo_key(repo_root), update_id)
        recorded = self._validated_updates.get(key)
        current_digest = self.canonical_source_index_digest(current)
        if (
            recorded is None
            or recorded[0] != current_digest
            or recorded[1].model_dump(mode="json") != validated.model_dump(mode="json")
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_index_validation_stale",
                    "SourceIndex changed after validation or the validation result was not issued by this Service instance.",
                )
            )
        for path in validated.newly_committed_file_candidates:
            current.files[path].committed = True
        current.status = "committed"
        current.active_update_id = None
        current.active_file_scope = []
        current.committed_at = utc_now_iso()
        self._touch(current, "Committed scoped SourceIndex update.")
        saved = self._save_model(repo_root, current)
        if not saved.ok or saved.value is None:
            return self.runtime.foundation.fail(saved.issues)
        self._validated_updates.pop(key, None)
        coverage = self._source_index_coverage(saved.value)
        if not coverage.ok or coverage.value is None:
            return self.runtime.foundation.fail(coverage.issues)
        return self.runtime.foundation.ok(
            SourceIndexCommitView(
                newly_committed_file_paths=list(validated.newly_committed_file_candidates),
                appended_block_ids=list(validated.new_block_ids),
                appended_link_ids=list(validated.new_link_ids),
                appended_ref_ids=list(validated.new_ref_ids),
                coverage=coverage.value,
                summary="Scoped SourceIndex update committed.",
            )
        )

    def commit_source_index(self, repo_root: Path) -> ServiceResult[GateReport]:
        index = self._load_mutable(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        validation = self.validate_source_index(repo_root)
        if not validation.ok or validation.value is None:
            return self.runtime.foundation.fail(validation.issues)
        if not validation.value.passed:
            return self.runtime.foundation.ok(validation.value)
        for file in index.value.files.values():
            file.committed = True
        index.value.status = "committed"
        index.value.active_update_id = None
        index.value.active_file_scope = []
        index.value.committed_at = utc_now_iso()
        self._touch(index.value, "Committed source index.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("source_index_commit", summary="Source index committed."))

    def _index_path(self, repo_root: Path) -> Path:
        ctx = FoundationContext(repo_root=Path(repo_root))
        return self.runtime.foundation.layout.constellation_root(ctx) / "source_index" / "index.json"

    def _load_mutable(
        self, repo_root: Path, *, expected_update_id: str | None = None
    ) -> ServiceResult[SourceIndex]:
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        if index.value.status == "committed":
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_index_committed", "Committed SourceIndex is read-only."))
        if index.value.active_update_id is not None:
            owner = self._assert_update_owner(index.value, expected_update_id)
            if owner is not None:
                return self.runtime.foundation.fail(owner)
            return index
        if (
            expected_update_id is None
            and self._stored_schema_version(repo_root) == 3
            and index.value.status == "draft"
            and not index.value.active_file_scope
            and index.value.committed_at is None
            and not any(item.committed for item in index.value.files.values())
        ):
            return index
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "source_index_update_context_required",
                "Mutable SourceIndex operations require the active system update id.",
            )
        )

    def _stored_schema_version(self, repo_root: Path) -> int | None:
        payload = self._read_stored_payload(repo_root)
        if not payload.ok or payload.value is None:
            return None
        try:
            return int(payload.value.get("schema_version", 2))
        except (TypeError, ValueError):
            return None

    def _save_model(self, repo_root: Path, index: SourceIndex) -> ServiceResult[SourceIndex]:
        write = self.runtime.foundation.store.write_json_atomic(self._index_path(repo_root), index)
        if not write.ok:
            return self.runtime.foundation.fail(write.issues)
        return self.runtime.foundation.ok(index)

    def _load_effective_index(self, repo_root: Path) -> ServiceResult[tuple[SourceIndex, int]]:
        payload = self._read_stored_payload(repo_root)
        if not payload.ok or payload.value is None:
            return self.runtime.foundation.fail(payload.issues)
        stored_version = int(payload.value.get("schema_version", 2))
        if stored_version == 3:
            try:
                return self.runtime.foundation.ok((SourceIndex.model_validate(payload.value), 3))
            except Exception as exc:  # noqa: BLE001 - normalize persisted model failures.
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue("source_index_invalid", str(exc))
                )
        if stored_version == 2:
            mapped = self._map_v2_payload(repo_root, payload.value)
            if not mapped.ok or mapped.value is None:
                return self.runtime.foundation.fail(mapped.issues)
            return self.runtime.foundation.ok((mapped.value, 2))
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "source_index_schema_unsupported",
                f"Unsupported SourceIndex schema version: {stored_version}",
            )
        )

    def _read_stored_payload(self, repo_root: Path) -> ServiceResult[dict[str, object]]:
        path = self._index_path(repo_root)
        if not path.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_missing", f"Source index does not exist: {path}")
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_invalid", str(exc), object_ref=str(path))
            )
        if not isinstance(payload, dict):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_invalid", "SourceIndex JSON must be an object.")
            )
        return self.runtime.foundation.ok(payload)

    def _map_v2_payload(
        self, repo_root: Path, payload: dict[str, object]
    ) -> ServiceResult[SourceIndex]:
        try:
            legacy = SourceIndex.model_validate(payload)
        except Exception as exc:  # noqa: BLE001
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_v2_invalid", str(exc))
            )
        manifest = self.source_corpus.scan_source_corpus(repo_root)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        by_path = {item.path: item for item in manifest.value.files}
        issues: list[ServiceIssue] = []
        if set(legacy.files) != set(by_path):
            issues.append(
                self.runtime.foundation.issue(
                    "source_index_v2_manifest_mismatch",
                    "v2 SourceIndex file paths differ from the fresh source corpus manifest.",
                    current=", ".join(sorted(legacy.files)),
                    expected=", ".join(sorted(by_path)),
                )
            )
        mapped_files: dict[str, SourceFileIndex] = {}
        for path, old in legacy.files.items():
            fresh = by_path.get(path)
            if fresh is None:
                continue
            if old.line_count != fresh.line_count or old.readable_text != fresh.readable_text:
                issues.append(
                    self.runtime.foundation.issue(
                        "source_index_v2_file_metadata_mismatch",
                        "v2 SourceIndex file metadata differs from the fresh corpus manifest.",
                        object_ref=path,
                    )
                )
            if (
                legacy.status == "committed"
                and fresh.readable_text
                and (old.survey_status == "pending" or old.indexing_status == "pending")
            ):
                issues.append(
                    self.runtime.foundation.issue(
                        "source_index_v2_committed_file_pending",
                        "A readable v2 committed SourceIndex file is still pending.",
                        object_ref=path,
                    )
                )
            mapped_files[path] = SourceFileIndex(
                path=path,
                source_sha256=fresh.sha256,
                line_count=fresh.line_count,
                readable_text=fresh.readable_text,
                survey_status=old.survey_status,
                indexing_status=old.indexing_status,
                committed=(
                    legacy.status == "committed"
                    and fresh.readable_text
                    and old.survey_status != "pending"
                    and old.indexing_status != "pending"
                ),
                summary=old.summary,
            )
        if issues:
            return self.runtime.foundation.fail(issues)
        return self.runtime.foundation.ok(
            legacy.model_copy(
                update={
                    "schema_version": 3,
                    "active_update_id": None,
                    "active_file_scope": [],
                    "files": mapped_files,
                },
                deep=True,
            )
        )

    def _verify_migrated_source_index(self, repo_root: Path) -> ServiceResult[GateReport]:
        inspected = self.inspect_source_index_schema(repo_root)
        if not inspected.ok or inspected.value is None:
            return self.runtime.foundation.fail(inspected.issues)
        if inspected.value.stored_schema_version != 3:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_migration_verify_failed", "Migrated SourceIndex is not schema v3.")
            )
        loaded = self.get_source_index_model(repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        source_hash_issues = self._current_source_hash_issues(
            repo_root,
            loaded.value,
            paths=set(loaded.value.files),
        )
        if not source_hash_issues.ok or source_hash_issues.value is None:
            return self.runtime.foundation.fail(source_hash_issues.issues)
        if source_hash_issues.value:
            return self.runtime.foundation.fail(source_hash_issues.value)
        gate = self.validate_source_index(repo_root)
        if not gate.ok or gate.value is None:
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        return gate

    def _restore_bytes_atomic(self, path: Path, payload: bytes) -> ServiceIssue | None:
        temp = path.with_name(f".{path.name}.migration-restore-{os.getpid()}")
        try:
            with temp.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            temp.unlink(missing_ok=True)
            return self.runtime.foundation.issue("source_index_migration_restore_failed", str(exc))
        return None

    def _new_index_from_manifest(self, manifest: SourceCorpusManifestView) -> SourceIndex:
        root_block = SourceBlock(
            block_id="root",
            kind="root",
            title="Source corpus",
            summary=manifest.overview or "Root block for the source corpus.",
            lifecycle_status="draft",
        )
        return SourceIndex(
            overview=manifest.overview,
            blocks={"root": root_block},
            files={item.path: self._file_from_manifest(item) for item in manifest.files},
            summary="Created draft source index from source corpus manifest.",
        )

    @staticmethod
    def _file_from_manifest(item: object) -> SourceFileIndex:
        return SourceFileIndex(
            path=str(getattr(item, "path")),
            source_sha256=getattr(item, "sha256"),
            line_count=int(getattr(item, "line_count")),
            readable_text=bool(getattr(item, "readable_text")),
        )

    def _open_update_view(
        self,
        index: SourceIndex,
        *,
        previous_status: Literal["missing", "draft", "updating", "committed"],
        baseline_digest: str | None,
        outcome: Literal["opened", "already_open", "no_op"],
        new_file_paths: list[str] | None = None,
    ) -> SourceIndexOpenUpdateView:
        active = list(index.active_file_scope)
        return SourceIndexOpenUpdateView(
            outcome=outcome,
            previous_status=previous_status,
            active_update_id=index.active_update_id,
            active_file_scope=active,
            new_file_paths=sorted(new_file_paths or []),
            already_committed_file_paths=sorted(path for path in active if path in index.files and index.files[path].committed),
            uncommitted_file_paths=sorted(path for path in active if path in index.files and not index.files[path].committed),
            baseline_digest=baseline_digest,
            summary=(
                "Scoped SourceIndex update is already open; reuse the baseline saved by the opening caller."
                if outcome == "already_open"
                else "Scoped SourceIndex update opened."
            ),
        )

    def _current_source_hash_issues(
        self,
        repo_root: Path,
        index: SourceIndex,
        *,
        paths: set[str],
    ) -> ServiceResult[list[ServiceIssue]]:
        scanned = self.source_corpus.scan_source_corpus(repo_root)
        if not scanned.ok or scanned.value is None:
            return self.runtime.foundation.fail(scanned.issues)
        current = {item.path: item for item in scanned.value.files}
        issues: list[ServiceIssue] = []
        for path in sorted(paths):
            stored = index.files.get(path)
            fresh = current.get(path)
            if stored is None or fresh is None:
                issues.append(
                    self.runtime.foundation.issue(
                        "source_index_source_file_missing",
                        "A SourceIndex update file is missing from the fresh source corpus scan.",
                        object_ref=path,
                    )
                )
            elif stored.source_sha256 != fresh.sha256:
                issues.append(
                    self.runtime.foundation.issue(
                        "source_index_source_hash_changed",
                        "Source corpus content changed while the SourceIndex update was active.",
                        object_ref=path,
                        current=fresh.sha256,
                        expected=stored.source_sha256,
                    )
                )
        return self.runtime.foundation.ok(issues)

    def _assert_update_owner(self, index: SourceIndex, update_id: str | None) -> ServiceIssue | None:
        if index.active_update_id is None:
            return self.runtime.foundation.issue(
                "source_index_update_context_required",
                "SourceIndex has no active update owner.",
            )
        if update_id is None:
            return self.runtime.foundation.issue(
                "source_index_update_context_required",
                "Mutable SourceIndex operations require the active system update id.",
            )
        if index.active_update_id != update_id:
            return self.runtime.foundation.issue(
                "source_index_update_owner_mismatch",
                "SourceIndex update id does not match the active owner.",
                current=index.active_update_id,
                expected=update_id,
            )
        return None

    @staticmethod
    def _repo_key(repo_root: Path) -> Path:
        return Path(repo_root).resolve(strict=False)

    @staticmethod
    def missing_source_index_digest() -> str:
        return hashlib.sha256(b"lean-constellation:source-index:missing").hexdigest()

    @staticmethod
    def canonical_source_index_digest(index: SourceIndex) -> str:
        payload = index.model_dump(mode="json")
        return SourceIndexComponent._payload_digest(payload)

    @staticmethod
    def _payload_digest(payload: object) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _manifest_digest(manifest: SourceCorpusManifestView) -> str:
        payload = {
            "relpath": manifest.relpath,
            "files": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "readable_text": item.readable_text,
                    "line_count": item.line_count,
                    "sha256": item.sha256,
                }
                for item in sorted(manifest.files, key=lambda value: value.path)
            ],
        }
        return SourceIndexComponent._payload_digest(payload)

    @staticmethod
    def _unsafe_scope_selector(selector: str) -> str | None:
        if "\\" in selector or "\x00" in selector:
            return "Source scope selectors must use safe POSIX-relative syntax."
        path = PurePosixPath(selector)
        if path.is_absolute() or selector.startswith("/") or any(part == ".." for part in path.parts):
            return "Source scope selectors cannot be absolute or traverse outside the corpus."
        if selector in {"", "."}:
            return "Source scope selector must identify a file, directory, or glob."
        return None

    @staticmethod
    def _match_scope_selector(paths: list[str], selector: str) -> list[str]:
        normalized = selector.rstrip("/")
        has_glob = any(character in selector for character in "*?[")
        if has_glob:
            pattern_parts = tuple(PurePosixPath(selector).parts)
            return sorted(
                path
                for path in paths
                if SourceIndexComponent._match_posix_glob(tuple(PurePosixPath(path).parts), pattern_parts)
            )
        return sorted(
            path
            for path in paths
            if path == normalized or path.startswith(f"{normalized}/")
        )

    @staticmethod
    def _match_posix_glob(path_parts: tuple[str, ...], pattern_parts: tuple[str, ...]) -> bool:
        """Match an anchored POSIX glob without allowing `*` or `?` to cross `/`."""

        if not pattern_parts:
            return not path_parts
        pattern_head = pattern_parts[0]
        if pattern_head == "**":
            return SourceIndexComponent._match_posix_glob(
                path_parts, pattern_parts[1:]
            ) or bool(path_parts) and SourceIndexComponent._match_posix_glob(
                path_parts[1:], pattern_parts
            )
        return (
            bool(path_parts)
            and fnmatchcase(path_parts[0], pattern_head)
            and SourceIndexComponent._match_posix_glob(path_parts[1:], pattern_parts[1:])
        )

    @staticmethod
    def _block_baseline_scalar(block: SourceBlock) -> dict[str, object]:
        return {
            "block_id": block.block_id,
            "parent_id": block.parent_id,
            "kind": block.kind,
            "subtype": block.subtype,
            "title": block.title,
            "summary": block.summary,
            "lifecycle_status": block.lifecycle_status,
            "active": block.active,
        }

    @staticmethod
    def _link_semantic_payload(link: SourceLink) -> dict[str, object]:
        return {
            "link_id": link.link_id,
            "source_block_id": link.source_block_id,
            "target_block_id": link.target_block_id,
            "target_hint": link.target_hint,
            "link_kind": link.link_kind,
            "evidence_refs": [item.model_dump(mode="json") for item in link.evidence_refs],
        }

    def _block_or_issue(self, index: SourceIndex, block_id: str) -> ServiceResult[SourceBlock]:
        block = index.blocks.get(block_id)
        if block is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_block_missing", f"Source block not found: {block_id}"))
        return self.runtime.foundation.ok(block)

    def _file_or_issue(self, index: SourceIndex, path: str) -> ServiceResult[SourceFileIndex]:
        file = index.files.get(path)
        if file is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_file_missing", f"Source file not indexed: {path}"))
        return self.runtime.foundation.ok(file)

    def _validate_index(
        self,
        repo_root: Path,
        index: SourceIndex,
        *,
        require_completed: bool,
        file_scope: set[str] | None = None,
    ) -> list[ServiceIssue]:
        issues: list[ServiceIssue] = []
        if index.root_block_id not in index.blocks:
            issues.append(self.runtime.foundation.issue("source_index_root_missing", "Root source block is missing."))
        non_root_blocks = [block for block in index.blocks.values() if block.active and block.block_id != index.root_block_id]
        if not non_root_blocks:
            issues.append(self.runtime.foundation.issue("source_index_no_blocks", "SourceIndex needs at least one non-root source block."))
        for block in index.blocks.values():
            if block.parent_id and block.parent_id not in index.blocks:
                issues.append(self.runtime.foundation.issue("source_block_parent_missing", f"Parent block not found: {block.parent_id}", object_ref=block.block_id))
            for child_id in block.child_ids:
                child = index.blocks.get(child_id)
                if child is None:
                    issues.append(self.runtime.foundation.issue("source_block_child_missing", f"Child block not found: {child_id}", object_ref=block.block_id))
                elif child.parent_id != block.block_id:
                    issues.append(self.runtime.foundation.issue("source_block_parent_mismatch", "Child parent_id does not match parent child_ids.", object_ref=child_id))
            if require_completed and block.block_id != index.root_block_id and block.active and block.lifecycle_status != "completed":
                issues.append(self.runtime.foundation.issue("source_block_incomplete", "Active source block is not completed.", object_ref=block.block_id))
            for ref in block.refs:
                source_ref = self._source_ref_or_issue(ref.material_ref, object_ref=ref.ref_id)
                if not source_ref.ok or source_ref.value is None:
                    issues.extend(source_ref.issues)
                    continue
                valid = self.source_corpus.validate_source_ref(
                    repo_root,
                    path=source_ref.value.path,
                    start_line=source_ref.value.start_line or 1,
                    end_line=source_ref.value.end_line or source_ref.value.start_line or 1,
                )
                if not valid.ok or valid.value is None:
                    issues.extend(valid.issues)
                elif not valid.value.valid:
                    issues.append(self.runtime.foundation.issue(valid.value.issue_code or "source_ref_invalid", valid.value.summary, object_ref=ref.ref_id))
        for link_id, link in index.links.items():
            source = index.blocks.get(link.source_block_id)
            if source is None:
                issues.append(self.runtime.foundation.issue("source_link_source_missing", "Source block missing for link.", object_ref=link_id))
                continue
            if link_id not in source.link_ids:
                issues.append(self.runtime.foundation.issue("source_link_not_bound", "Link is not listed on source block.", object_ref=link_id))
            if link.target_block_id and link.target_block_id not in index.blocks:
                issues.append(self.runtime.foundation.issue("source_link_target_missing", "Target block missing for link.", object_ref=link_id))
            if not link.evidence_refs:
                issues.append(self.runtime.foundation.issue("source_link_evidence_empty", "Source link needs at least one evidence ref.", object_ref=link_id))
            source_ref_keys = {ref.material_ref.model_dump_json() for ref in source.refs}
            missing = [ref.model_dump_json() for ref in link.evidence_refs if ref.model_dump_json() not in source_ref_keys]
            if missing:
                issues.append(self.runtime.foundation.issue("source_link_evidence_missing", "Link evidence refs do not belong to source block.", object_ref=link_id, current=", ".join(missing)))
        for file in index.files.values():
            if file_scope is not None and file.path not in file_scope:
                continue
            if file.readable_text and (file.survey_status == "pending" or file.indexing_status == "pending"):
                issues.append(
                    self.runtime.foundation.issue(
                        "source_file_pending",
                        "Readable source file must have survey and indexing status resolved.",
                        object_ref=file.path,
                        current=f"survey={file.survey_status}, indexing={file.indexing_status}",
                        expected="surveyed/skipped and indexed/skipped",
                    )
                )
        if self._has_cycle(index):
            issues.append(self.runtime.foundation.issue("source_block_cycle", "Source block tree contains a cycle."))
        return issues

    def _has_cycle(self, index: SourceIndex) -> bool:
        root_id = index.root_block_id
        if root_id not in index.blocks:
            return False
        visited: set[str] = set()
        active: set[str] = set()

        def visit(block_id: str) -> bool:
            if block_id in active:
                return True
            if block_id in visited:
                return False
            active.add(block_id)
            for child_id in index.blocks[block_id].child_ids:
                if child_id in index.blocks and visit(child_id):
                    return True
            active.remove(block_id)
            visited.add(block_id)
            return False

        return visit(root_id)

    @staticmethod
    def _next_id(prefix: str, existing: object) -> str:
        if isinstance(existing, dict):
            keys = set(existing)
        else:
            keys = set(existing)
        number = 1
        while f"{prefix}_{number:04d}" in keys:
            number += 1
        return f"{prefix}_{number:04d}"

    @staticmethod
    def _next_ref_id(index: SourceIndex) -> str:
        refs = {ref.ref_id for block in index.blocks.values() for ref in block.refs}
        return SourceIndexComponent._next_id("ref", refs)

    @staticmethod
    def _allows_no_direct_refs(summary: str) -> bool:
        normalized = summary.lower()
        return "no direct source" in normalized or "structural" in normalized or "overview" in normalized

    def _required_field_issue(self, fields: list[tuple[str, str]]) -> object | None:
        for field, value in fields:
            if not value:
                return self.runtime.foundation.issue("source_block_field_empty", f"Source block {field} must be non-empty.", field=field)
        return None

    @staticmethod
    def _touch(index: SourceIndex, summary: str) -> None:
        index.updated_at = utc_now_iso()
        index.summary = summary

    def _to_index_view(
        self, repo_root: Path, index: SourceIndex, *, stored_schema_version: int = 3
    ) -> SourceIndexView:
        return SourceIndexView(
            repo_root=str(Path(repo_root)),
            schema_version=3,
            stored_schema_version=stored_schema_version,
            migration_required=stored_schema_version != 3,
            status=index.status,
            active_file_scope=list(index.active_file_scope),
            overview=index.overview,
            root_block_id=index.root_block_id,
            blocks={block_id: self._to_block_view(index, block) for block_id, block in index.blocks.items()},
            links={link_id: self._to_link_view(index, link) for link_id, link in index.links.items()},
            files={path: self._to_file_view(file) for path, file in index.files.items()},
            created_at=index.created_at,
            updated_at=index.updated_at,
            committed_at=index.committed_at,
            summary=index.summary,
        )

    def _to_block_view(self, index: SourceIndex, block: SourceBlock) -> SourceBlockView:
        return SourceBlockView(
            block_id=block.block_id,
            parent_id=block.parent_id,
            kind=block.kind,
            subtype=block.subtype,
            title=block.title,
            summary=block.summary,
            lifecycle_status=block.lifecycle_status,
            refs=[self._to_block_ref_view(ref) for ref in block.refs],
            link_ids=list(block.link_ids),
            child_ids=list(block.child_ids),
            active=block.active,
            created_at=block.created_at,
            updated_at=block.updated_at,
        )

    def _to_block_ref_view(self, ref: SourceBlockRef) -> SourceBlockRefView:
        source_ref = ref.material_ref.ref
        if ref.material_ref.kind != "source" or not isinstance(source_ref, SourceRef):
            raise ValueError(f"SourceBlockRef {ref.ref_id} does not contain a source MaterialRef.")
        return SourceBlockRefView(
            ref_id=ref.ref_id,
            path=source_ref.path,
            start_line=source_ref.start_line or 1,
            end_line=source_ref.end_line or source_ref.start_line or 1,
            role=ref.role,
        )

    def _to_link_view(self, index: SourceIndex, link: SourceLink) -> SourceLinkView:
        return SourceLinkView(
            link_id=link.link_id,
            source_block_id=link.source_block_id,
            target_block_id=link.target_block_id,
            target_hint=link.target_hint,
            link_kind=link.link_kind,
            evidence_ref_ids=[
                ref_id
                for ref_id in (self._ref_id_for_material_ref(index, link.source_block_id, material_ref) for material_ref in link.evidence_refs)
                if ref_id is not None
            ],
            evidence_refs=list(link.evidence_refs),
            created_at=link.created_at,
        )

    @staticmethod
    def _to_file_view(file: SourceFileIndex) -> SourceFileIndexView:
        return SourceFileIndexView(
            path=file.path,
            source_sha256=file.source_sha256,
            line_count=file.line_count,
            readable_text=file.readable_text,
            survey_status=file.survey_status,
            indexing_status=file.indexing_status,
            committed=file.committed,
            summary=file.summary,
        )

    def _source_ref_or_issue(self, material_ref: MaterialRef, *, object_ref: str) -> ServiceResult[SourceRef]:
        if material_ref.kind != "source" or not isinstance(material_ref.ref, SourceRef):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "source_material_ref_invalid",
                    "SourceIndex block refs must use MaterialRef(kind='source').",
                    object_ref=object_ref,
                    current=material_ref.model_dump_json(),
                )
            )
        return self.runtime.foundation.ok(material_ref.ref)

    @staticmethod
    def _ref_id_for_material_ref(index: SourceIndex, block_id: str, material_ref: MaterialRef) -> str | None:
        block = index.blocks.get(block_id)
        if block is None:
            return None
        material_key = material_ref.model_dump_json()
        for ref in block.refs:
            if ref.material_ref.model_dump_json() == material_key:
                return ref.ref_id
        return None
