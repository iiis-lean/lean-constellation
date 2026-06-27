"""MaterialService composition and public wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from lean_constellation.services.external_clients import ExternalClientService
from lean_constellation.services.foundation import FoundationService, GateReport, ServiceResult
from lean_constellation.services.material.material_read import MaterialRangeView, MaterialReadComponent, MaterialSearchView
from lean_constellation.services.material.resource_curation import (
    ResourceCurationComponent,
    ResourceCurationFlowInputView,
)
from lean_constellation.services.material.resource_library import (
    ResourceDuplicateView,
    ResourceLibraryComponent,
    ResourceMetadataInput,
    ResourceTargetView,
    ResourceView,
)
from lean_constellation.services.material.source_corpus import (
    SourceAcquisitionView,
    SourceCorpusBlockedSubmitView,
    SourceCorpusComponent,
    SourceCorpusManifestView,
    SourceCorpusPreparedView,
    SourceExtractionView,
)
from lean_constellation.services.material.source_index import (
    SourceBlockView,
    SourceFileIndexView,
    SourceIndexComponent,
    SourceIndexCoverageView,
    SourceIndexView,
    SourceLinkView,
    SubmissionView,
)


class MaterialService:
    """Composition root for source corpus, source index, and resource services."""

    def __init__(
        self,
        *,
        foundation: FoundationService | None = None,
        external: ExternalClientService | None = None,
        source_corpus: SourceCorpusComponent | None = None,
        resource_library: ResourceLibraryComponent | None = None,
        material_read: MaterialReadComponent | None = None,
        source_index: SourceIndexComponent | None = None,
        resource_curation: ResourceCurationComponent | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.external = external or ExternalClientService()
        self.source_corpus = source_corpus or SourceCorpusComponent(self.foundation, self.external)
        self.resource_library = resource_library or ResourceLibraryComponent(self.foundation, self.external)
        self.material_read = material_read or MaterialReadComponent(
            self.foundation,
            source_corpus=self.source_corpus,
            resource_library=self.resource_library,
        )
        self.source_index = source_index or SourceIndexComponent(self.foundation, self.source_corpus)
        self.resource_curation = resource_curation or ResourceCurationComponent(
            self.foundation,
            self.external,
            self.resource_library,
            self.source_corpus,
        )

    def prepare_existing_source_corpus(
        self,
        repo_root: Path,
        *,
        relpath: str = ".lean_constellation/source",
    ) -> ServiceResult[SourceCorpusManifestView]:
        gate = self.source_corpus.check_source_corpus_draft(repo_root, relpath=relpath)
        if not gate.ok or gate.value is None:
            return self.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.foundation.fail(gate.value.issues)
        return self.source_corpus.scan_source_corpus(repo_root, relpath=relpath, created_from_mode="existing")

    def scan_source_corpus(
        self,
        repo_root: Path,
        *,
        relpath: str = ".lean_constellation/source",
    ) -> ServiceResult[SourceCorpusManifestView]:
        return self.source_corpus.scan_source_corpus(repo_root, relpath=relpath)

    def acquire_source_material(
        self,
        repo_root: Path,
        *,
        target: str,
        preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        return self.source_corpus.acquire_source_material(repo_root, target=target, preferred_kind=preferred_kind)

    def extract_source_artifact(
        self,
        repo_root: Path,
        *,
        artifact_ref: str,
        extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"] | None = None,
    ) -> ServiceResult[SourceExtractionView]:
        return self.source_corpus.extract_source_artifact(repo_root, artifact_ref=artifact_ref, extraction_kind=extraction_kind)

    def import_source_material(
        self,
        repo_root: Path,
        *,
        source_path: str,
        as_name: str | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        return self.source_corpus.import_source_material(repo_root, source_path=source_path, as_name=as_name)

    def normalize_source_text_material(self, repo_root: Path, *, material_ref: str) -> ServiceResult[SourceExtractionView]:
        return self.source_corpus.normalize_source_text_material(repo_root, material_ref=material_ref)

    def check_source_corpus_draft(
        self,
        repo_root: Path,
        *,
        relpath: str = ".lean_constellation/source",
        entry_path: str | None = None,
    ) -> ServiceResult[GateReport]:
        return self.source_corpus.check_source_corpus_draft(repo_root, relpath=relpath, entry_path=entry_path)

    def submit_source_corpus_prepared(
        self,
        repo_root: Path,
        *,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        relpath: str = ".lean_constellation/source",
        ctx: object | None = None,
    ) -> ServiceResult[SourceCorpusPreparedView]:
        del ctx
        return self.source_corpus.submit_source_corpus_prepared(
            repo_root,
            entry_path=entry_path,
            overview=overview,
            preparation_summary=preparation_summary,
            relpath=relpath,
        )

    def submit_source_corpus_blocked(
        self,
        repo_root: Path,
        *,
        reason: str,
        attempted_targets: list[str] | None = None,
        missing_materials: list[str] | None = None,
        suggested_next_action: str | None = None,
        ctx: object | None = None,
    ) -> ServiceResult[SourceCorpusBlockedSubmitView]:
        del ctx
        return self.source_corpus.submit_source_corpus_blocked(
            repo_root,
            reason=reason,
            attempted_targets=attempted_targets,
            missing_materials=missing_materials,
            suggested_next_action=suggested_next_action,
        )

    def submit_resource_request(
        self,
        ctx: Any,
        *,
        target_kind: Literal["web", "arxiv", "local_file", "local_dir"],
        target: str,
        arxiv_version: str | None = None,
    ) -> ServiceResult[ResourceCurationFlowInputView]:
        # First-round implementation returns the exact child-flow input that a
        # ToolFacade submit wrapper will later persist as a dispatch submission.
        return self.resource_curation.build_resource_curation_flow_input(
            ctx,
            target_kind=target_kind,
            target=target,
            arxiv_version=arxiv_version,
        )

    def normalize_resource_target(self, target: str) -> ServiceResult[ResourceTargetView]:
        return self.resource_library.normalize_resource_target(target)

    def find_duplicate_resource(self, repo_root: Path, *, target: ResourceTargetView) -> ServiceResult[ResourceDuplicateView]:
        return self.resource_library.find_duplicate_resource(repo_root, target=target)

    def register_local_resource(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        temp_dir: Path,
        metadata: ResourceMetadataInput,
    ) -> ServiceResult[ResourceView]:
        return self.resource_library.register_local_resource(repo_root, target=target, temp_dir=temp_dir, metadata=metadata)

    def search_material(self, repo_root: Path, *, query: str, scope: str = "all") -> ServiceResult[MaterialSearchView]:
        return self.material_read.search_material_text(repo_root, query=query, scope=scope)

    def read_material_ref(self, repo_root: Path, *, ref: Any) -> ServiceResult[MaterialRangeView]:
        preview = self.material_read.preview_material_ref(repo_root, ref=ref)
        if not preview.ok or preview.value is None:
            return self.foundation.fail(preview.issues)
        return self.foundation.ok(preview.value.preview)

    def read_source_range(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int,
        end_line: int,
        context_lines: int = 2,
    ) -> ServiceResult[MaterialRangeView]:
        return self.material_read.read_source_range(repo_root, path=path, start_line=start_line, end_line=end_line, context_lines=context_lines)

    def read_resource_range(
        self,
        repo_root: Path,
        *,
        resource_key: str,
        start_line: int,
        end_line: int,
        context_lines: int = 2,
    ) -> ServiceResult[MaterialRangeView]:
        return self.material_read.read_resource_range(repo_root, resource_key=resource_key, start_line=start_line, end_line=end_line, context_lines=context_lines)

    def search_material_text(
        self,
        repo_root: Path,
        *,
        query: str,
        scope: str = "all",
        regex: bool = False,
        limit: int = 20,
    ) -> ServiceResult[MaterialSearchView]:
        return self.material_read.search_material_text(repo_root, query=query, scope=scope, regex=regex, limit=limit)

    def create_draft_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        return self.source_index.create_draft_source_index(repo_root)

    def get_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        return self.source_index.get_source_index(repo_root)

    def set_source_index_overview(self, repo_root: Path, *, overview: str) -> ServiceResult[SourceIndexView]:
        return self.source_index.set_source_index_overview(repo_root, overview=overview)

    def create_source_block(
        self,
        repo_root: Path,
        *,
        parent_id: str,
        kind: str,
        subtype: str | None = None,
        title: str,
        summary: str,
    ) -> ServiceResult[SourceBlockView]:
        return self.source_index.create_source_block(
            repo_root,
            parent_id=parent_id,
            kind=kind,
            subtype=subtype,
            title=title,
            summary=summary,
        )

    def update_source_block(self, repo_root: Path, *, block_id: str, **kwargs: Any) -> ServiceResult[SourceBlockView]:
        return self.source_index.update_source_block(repo_root, block_id=block_id, **kwargs)

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
        return self.source_index.add_source_block_ref(repo_root, block_id=block_id, path=path, start_line=start_line, end_line=end_line, role=role)

    def remove_source_block_ref(self, repo_root: Path, *, block_id: str, ref_id: str) -> ServiceResult[SourceBlockView]:
        return self.source_index.remove_source_block_ref(repo_root, block_id=block_id, ref_id=ref_id)

    def mark_block_refs_done(self, repo_root: Path, *, block_id: str) -> ServiceResult[GateReport]:
        return self.source_index.mark_block_refs_done(repo_root, block_id=block_id)

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
        return self.source_index.create_source_link(
            repo_root,
            source_block_id=source_block_id,
            target_block_id=target_block_id,
            target_hint=target_hint,
            link_kind=link_kind,
            evidence_ref_ids=evidence_ref_ids,
        )

    def mark_block_links_done(self, repo_root: Path, *, block_id: str) -> ServiceResult[GateReport]:
        return self.source_index.mark_block_links_done(repo_root, block_id=block_id)

    def mark_block_completed(self, repo_root: Path, *, block_id: str) -> ServiceResult[GateReport]:
        return self.source_index.mark_block_completed(repo_root, block_id=block_id)

    def set_file_survey_status(
        self,
        repo_root: Path,
        *,
        path: str,
        status: Literal["pending", "surveyed", "skipped"],
        summary: str | None = None,
    ) -> ServiceResult[SourceFileIndexView]:
        return self.source_index.set_file_survey_status(repo_root, path=path, status=status, summary=summary)

    def set_file_indexing_status(
        self,
        repo_root: Path,
        *,
        path: str,
        status: Literal["pending", "indexed", "skipped"],
    ) -> ServiceResult[SourceFileIndexView]:
        return self.source_index.set_file_indexing_status(repo_root, path=path, status=status)

    def validate_source_index(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.source_index.validate_source_index(repo_root)

    def get_source_index_coverage(self, repo_root: Path) -> ServiceResult[SourceIndexCoverageView]:
        return self.source_index.get_source_index_coverage(repo_root)

    def submit_source_index_builder_round(self, repo_root: Path, *, summary: str, ctx: object | None = None) -> ServiceResult[SubmissionView]:
        return self.source_index.submit_source_index_builder_round(repo_root, summary=summary, ctx=ctx)

    def submit_source_index_review_round(
        self,
        repo_root: Path,
        *,
        approved: bool,
        summary: str,
        feedback: str | None = None,
        ctx: object | None = None,
    ) -> ServiceResult[SubmissionView]:
        return self.source_index.submit_source_index_review_round(repo_root, approved=approved, summary=summary, feedback=feedback, ctx=ctx)

    def commit_source_index(self, repo_root: Path) -> ServiceResult[GateReport]:
        return self.source_index.commit_source_index(repo_root)
