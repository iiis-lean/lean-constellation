"""Source index truth, lifecycle gates, and submit views."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.refs import MaterialRef, SourceRef
from lean_constellation.services.foundation import FoundationContext, GateReport, ServiceResult
from lean_constellation.services.material.source_corpus import SourceCorpusComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


BlockLifecycleStatus = Literal["draft", "refs_done", "links_done", "completed"]
SourceIndexStatus = Literal["draft", "committed"]


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
    line_count: int = 0
    readable_text: bool = False
    survey_status: Literal["pending", "surveyed", "skipped"] = "pending"
    indexing_status: Literal["pending", "indexed", "skipped"] = "pending"
    summary: str | None = None


class SourceIndex(StrictModel):
    schema_version: int = 2
    status: SourceIndexStatus = "draft"
    overview: str | None = None
    root_block_id: str = "root"
    blocks: dict[str, SourceBlock] = Field(default_factory=dict)
    links: dict[str, SourceLink] = Field(default_factory=dict)
    files: dict[str, SourceFileIndex] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    committed_at: str | None = None
    summary: str = "Source index draft."


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
    line_count: int = 0
    readable_text: bool = False
    survey_status: Literal["pending", "surveyed", "skipped"] = "pending"
    indexing_status: Literal["pending", "indexed", "skipped"] = "pending"
    summary: str | None = None


class SourceIndexView(StrictModel):
    repo_root: str
    status: SourceIndexStatus = "draft"
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

    def create_draft_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        path = self._index_path(repo_root)
        if path.exists():
            return self.get_source_index(repo_root)
        manifest = self.source_corpus.get_source_corpus_manifest(repo_root)
        if not manifest.ok or manifest.value is None:
            return self.runtime.foundation.fail(manifest.issues)
        root_block = SourceBlock(
            block_id="root",
            kind="root",
            title="Source corpus",
            summary=manifest.value.overview or "Root block for the source corpus.",
            lifecycle_status="draft",
        )
        files = {
            item.path: SourceFileIndex(
                path=item.path,
                line_count=item.line_count,
                readable_text=item.readable_text,
            )
            for item in manifest.value.files
        }
        index = SourceIndex(
            overview=manifest.value.overview,
            blocks={"root": root_block},
            files=files,
            summary="Created draft source index from source corpus manifest.",
        )
        write = self.runtime.foundation.store.write_json_atomic(path, index)
        if not write.ok:
            return self.runtime.foundation.fail(write.issues)
        return self.runtime.foundation.ok(self._to_index_view(repo_root, index))

    def get_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        return self.runtime.foundation.ok(self._to_index_view(repo_root, index.value))

    def get_source_index_model(self, repo_root: Path) -> ServiceResult[SourceIndex]:
        path = self._index_path(repo_root)
        if not path.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("source_index_missing", f"Source index does not exist: {path}")
            )
        return self.runtime.foundation.store.read_json(path, SourceIndex)

    def set_source_index_overview(self, repo_root: Path, *, overview: str) -> ServiceResult[SourceIndexView]:
        index = self._load_mutable(repo_root)
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
    ) -> ServiceResult[SourceBlockView]:
        index = self._load_mutable(repo_root)
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
    ) -> ServiceResult[SourceBlockView]:
        index = self._load_mutable(repo_root)
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
    ) -> ServiceResult[SourceBlockView]:
        index = self._load_mutable(repo_root)
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

    def remove_source_block_ref(self, repo_root: Path, *, block_id: str, ref_id: str) -> ServiceResult[SourceBlockView]:
        index = self._load_mutable(repo_root)
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

    def mark_block_refs_done(self, repo_root: Path, *, block_id: str) -> ServiceResult[GateReport]:
        index = self._load_mutable(repo_root)
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
    ) -> ServiceResult[SourceLinkView]:
        index = self._load_mutable(repo_root)
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

    def mark_block_links_done(self, repo_root: Path, *, block_id: str) -> ServiceResult[GateReport]:
        index = self._load_mutable(repo_root)
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

    def mark_block_completed(self, repo_root: Path, *, block_id: str) -> ServiceResult[GateReport]:
        index = self._load_mutable(repo_root)
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
    ) -> ServiceResult[SourceFileIndexView]:
        index = self._load_mutable(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        file = self._file_or_issue(index.value, path)
        if not file.ok or file.value is None:
            return self.runtime.foundation.fail(file.issues)
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
    ) -> ServiceResult[SourceFileIndexView]:
        index = self._load_mutable(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        file = self._file_or_issue(index.value, path)
        if not file.ok or file.value is None:
            return self.runtime.foundation.fail(file.issues)
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
        issues = self._validate_index(repo_root, index.value, require_completed=True)
        if issues:
            return self.runtime.foundation.ok(self.runtime.foundation.gate_failed("source_index", issues, summary=f"{len(issues)} source index checks failed."))
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("source_index", summary="Source index is valid."))

    def get_source_index_coverage(self, repo_root: Path) -> ServiceResult[SourceIndexCoverageView]:
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        blocks = [block for block in index.value.blocks.values() if block.active and block.block_id != index.value.root_block_id]
        files = list(index.value.files.values())
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
            link_count=len(index.value.links),
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
        if index.value.status != "draft":
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_index_not_draft", "Builder can only submit a draft SourceIndex."))
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
        if index.value.status != "draft":
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_index_not_draft", "Reviewer can only submit a draft SourceIndex."))
        return self.runtime.foundation.ok(
            SubmissionView(
                submission_kind="source_index_review_round",
                accepted=True,
                approved=approved,
                summary=summary.strip(),
                feedback=feedback.strip() if feedback else None,
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
        index.value.status = "committed"
        index.value.committed_at = utc_now_iso()
        self._touch(index.value, "Committed source index.")
        saved = self._save_model(repo_root, index.value)
        if not saved.ok:
            return self.runtime.foundation.fail(saved.issues)
        return self.runtime.foundation.ok(self.runtime.foundation.gate_passed("source_index_commit", summary="Source index committed."))

    def _index_path(self, repo_root: Path) -> Path:
        ctx = FoundationContext(repo_root=Path(repo_root))
        return self.runtime.foundation.layout.constellation_root(ctx) / "source_index" / "index.json"

    def _load_mutable(self, repo_root: Path) -> ServiceResult[SourceIndex]:
        index = self.get_source_index_model(repo_root)
        if not index.ok or index.value is None:
            return self.runtime.foundation.fail(index.issues)
        if index.value.status == "committed":
            return self.runtime.foundation.fail(self.runtime.foundation.issue("source_index_committed", "Committed SourceIndex is read-only."))
        return index

    def _save_model(self, repo_root: Path, index: SourceIndex) -> ServiceResult[SourceIndex]:
        write = self.runtime.foundation.store.write_json_atomic(self._index_path(repo_root), index)
        if not write.ok:
            return self.runtime.foundation.fail(write.issues)
        return self.runtime.foundation.ok(index)

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

    def _validate_index(self, repo_root: Path, index: SourceIndex, *, require_completed: bool) -> list[object]:
        issues = []
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

    def _to_index_view(self, repo_root: Path, index: SourceIndex) -> SourceIndexView:
        return SourceIndexView(
            repo_root=str(Path(repo_root)),
            status=index.status,
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
            line_count=file.line_count,
            readable_text=file.readable_text,
            survey_status=file.survey_status,
            indexing_status=file.indexing_status,
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
