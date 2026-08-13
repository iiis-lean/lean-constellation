"""MaterialService composition and public wrappers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.repo_run import SourceScope
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.material.material_read import (
    MaterialFileEntry,
    MaterialRangeView,
    MaterialReadComponent,
    MaterialSearchHit,
    MaterialSearchView,
)
from lean_constellation.services.material.resource_curation import (
    ResourceCurationComponent,
    ResourceCurationResultView,
)
from lean_constellation.services.material.resource_library import (
    ResourceDraftView,
    ResourceDuplicateView,
    ResourceLibraryComponent,
    ResourceMetadataInput,
    ResourceMaterialManifest,
    ResourceSummaryView,
    ResourceTarget,
    ResourceTargetView,
    ResourceView,
)
from lean_constellation.services.material.source_corpus import (
    SourceAcquisitionView,
    SourceCorpusBlockedSubmitView,
    SourceCorpusComponent,
    SourceCorpusManifestView,
    SourceCorpusImportView,
    SourceCorpusPreparedView,
    SourceExtractionView,
    SourcePdfPagePreviewView,
)
from lean_constellation.services.material.source_index import (
    ResolvedSourceScopeView,
    SourceBlockDetailView,
    SourceBlockListItemView,
    SourceBlockListView,
    SourceBlockView,
    SourceIndexFileListView,
    SourceIndexOverviewMutationReceipt,
    SourceIndexOverviewView,
    SourceFileIndexView,
    SourceIndex,
    SourceIndexCommitView,
    SourceIndexComponent,
    SourceIndexCoverageView,
    SourceIndexOpenUpdateView,
    SourceIndexUpdateContextView,
    SourceIndexUpdateGateView,
    SourceIndexView,
    SourceLinkView,
    SubmissionView,
)

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class MaterialContextCitationView(StrictModel):
    material_kind: Literal["source", "resource"]
    path: str | None = None
    resource_key: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    role: str | None = None
    reason: str | None = None
    source_block_id: str | None = None


class MaterialContextView(StrictModel):
    node_path: str | None = None
    query: str | None = None
    scope: Literal["current_node", "source", "resource", "all"]
    source_index: SourceIndexOverviewView | None = None
    source_files: list[MaterialFileEntry] = Field(default_factory=list)
    source_blocks: list[SourceBlockListItemView] = Field(default_factory=list)
    resources: list[ResourceSummaryView] = Field(default_factory=list)
    owned_refs: list[MaterialContextCitationView] = Field(default_factory=list)
    context_refs: list[MaterialContextCitationView] = Field(default_factory=list)
    matches: list[MaterialSearchHit] = Field(default_factory=list)
    returned_count: int
    total_matching_count: int
    truncated: bool = False
    summary: str


class MaterialService:
    """Composition root for source corpus, source index, and resource services."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        source_corpus: SourceCorpusComponent | None = None,
        resource_library: ResourceLibraryComponent | None = None,
        material_read: MaterialReadComponent | None = None,
        source_index: SourceIndexComponent | None = None,
        resource_curation: ResourceCurationComponent | None = None,
    ) -> None:
        self.runtime = runtime
        self.source_corpus = source_corpus or SourceCorpusComponent(runtime)
        self.resource_library = resource_library or ResourceLibraryComponent(runtime)
        self.material_read = material_read or MaterialReadComponent(
            runtime,
            source_corpus=self.source_corpus,
            resource_library=self.resource_library,
        )
        self.source_index = source_index or SourceIndexComponent(runtime, self.source_corpus)
        self.resource_curation = resource_curation or ResourceCurationComponent(
            runtime,
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
            return self.runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return self.runtime.foundation.fail(gate.value.issues)
        return self.source_corpus.scan_source_corpus(repo_root, relpath=relpath, created_from_mode="existing")

    def import_local_source_corpus(
        self,
        repo_root: Path,
        *,
        source_dir: Path,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        replace_existing: bool = False,
        expected_manifest_digest: str | None = None,
    ) -> ServiceResult[SourceCorpusImportView]:
        return self.source_corpus.import_local_source_corpus(
            repo_root,
            source_dir=source_dir,
            entry_path=entry_path,
            overview=overview,
            preparation_summary=preparation_summary,
            replace_existing=replace_existing,
            expected_manifest_digest=expected_manifest_digest,
        )

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
        acquisition_kind: str | None = None,
        mime_type: str | None = None,
    ) -> ServiceResult[SourceExtractionView]:
        return self.source_corpus.extract_source_artifact(
            repo_root,
            artifact_ref=artifact_ref,
            extraction_kind=extraction_kind,
            acquisition_kind=acquisition_kind,
            mime_type=mime_type,
        )

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

    def acquire_resource_material(
        self,
        repo_root: Path,
        *,
        draft_id: str,
        target: str,
        preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        return self.resource_curation.acquire_resource_material(repo_root, draft_id=draft_id, target=target, preferred_kind=preferred_kind)

    def import_resource_material(
        self,
        repo_root: Path,
        *,
        draft_id: str,
        source_path: str,
        as_name: str | None = None,
    ) -> ServiceResult[SourceAcquisitionView]:
        return self.resource_curation.import_resource_material(repo_root, draft_id=draft_id, source_path=source_path, as_name=as_name)

    def extract_resource_artifact(
        self,
        repo_root: Path,
        *,
        draft_id: str,
        artifact_ref: str,
        extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"] | None = None,
        acquisition_kind: str | None = None,
        mime_type: str | None = None,
    ) -> ServiceResult[SourceExtractionView]:
        return self.resource_curation.extract_resource_artifact(
            repo_root,
            draft_id=draft_id,
            artifact_ref=artifact_ref,
            extraction_kind=extraction_kind,
            acquisition_kind=acquisition_kind,
            mime_type=mime_type,
        )

    def normalize_resource_text_material(self, repo_root: Path, *, draft_id: str, material_ref: str) -> ServiceResult[SourceExtractionView]:
        return self.resource_curation.normalize_resource_text_material(repo_root, draft_id=draft_id, material_ref=material_ref)

    def check_source_corpus_draft(
        self,
        repo_root: Path,
        *,
        relpath: str = ".lean_constellation/source",
        entry_path: str | None = None,
    ) -> ServiceResult[GateReport]:
        return self.source_corpus.check_source_corpus_draft(repo_root, relpath=relpath, entry_path=entry_path)

    def render_source_pdf_page(
        self,
        repo_root: Path,
        *,
        path: str,
        page_number: int,
        dpi: int = 160,
    ) -> ServiceResult[SourcePdfPagePreviewView]:
        return self.source_corpus.render_source_pdf_page(
            repo_root,
            path=path,
            page_number=page_number,
            dpi=dpi,
        )

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

    def check_source_corpus_prepared(
        self,
        repo_root: Path,
        *,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        relpath: str = ".lean_constellation/source",
    ) -> ServiceResult[SourceCorpusPreparedView]:
        return self.source_corpus.check_source_corpus_prepared(
            repo_root,
            entry_path=entry_path,
            overview=overview,
            preparation_summary=preparation_summary,
            relpath=relpath,
        )

    def finalize_source_corpus_prepared(
        self,
        repo_root: Path,
        *,
        entry_path: str,
        overview: str,
        preparation_summary: str,
        relpath: str = ".lean_constellation/source",
    ) -> ServiceResult[SourceCorpusPreparedView]:
        return self.source_corpus.finalize_source_corpus_prepared(
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

    def prepare_resource_target(
        self,
        *,
        target_kind: Literal["web", "arxiv", "local_file", "local_dir"],
        target: str,
        arxiv_version: str | None = None,
    ) -> ServiceResult[ResourceTargetView]:
        return self.resource_curation.prepare_resource_target(
            target_kind=target_kind,
            target=target,
            arxiv_version=arxiv_version,
        )

    def normalize_resource_target(self, target: str) -> ServiceResult[ResourceTargetView]:
        return self.resource_library.normalize_resource_target(target)

    def find_duplicate_resource(self, repo_root: Path, *, target: ResourceTarget | ResourceTargetView) -> ServiceResult[ResourceDuplicateView]:
        return self.resource_library.find_duplicate_resource(repo_root, target=target)

    def get_material_context_view(
        self,
        repo_root: Path,
        *,
        node_path: str | None = None,
        query: str | None = None,
        scope: Literal["current_node", "source", "resource", "all"] = "current_node",
        require_committed_source_index: bool = False,
        regex: bool = False,
        limit: int | None = None,
    ) -> ServiceResult[MaterialContextView]:
        if limit is not None and limit < 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "invalid_search_limit", "Material context limit must be >= 1."
                )
            )
        source_files: list[MaterialFileEntry] = []
        source_blocks: list[SourceBlockListItemView] = []
        source_index_overview: SourceIndexOverviewView | None = None
        resources: list[ResourceSummaryView] = []
        search_hits: list[MaterialSearchHit] = []
        node_owned_refs: list[MaterialContextCitationView] = []
        node_context_refs: list[MaterialContextCitationView] = []

        if scope in {"source", "all"} and not (query and query.strip()):
            listed_source = self.material_read.list_material_files(repo_root, material_kind="source")
            if not listed_source.ok or listed_source.value is None:
                return self.runtime.foundation.fail(listed_source.issues)
            source_files = listed_source.value.files
            listed_blocks = self.source_index.list_source_blocks(
                repo_root,
                require_committed=require_committed_source_index,
            )
            if listed_blocks.ok and listed_blocks.value is not None:
                source_blocks = listed_blocks.value.blocks
            elif not any(
                issue.kind in {"source_index_missing", "source_index_not_committed"}
                for issue in listed_blocks.issues
            ):
                return self.runtime.foundation.fail(listed_blocks.issues)

        if scope in {"resource", "all"} and not (query and query.strip()):
            listed_resources = self.resource_library.list_resources(repo_root)
            if not listed_resources.ok or listed_resources.value is None:
                return self.runtime.foundation.fail(listed_resources.issues)
            resources = listed_resources.value

        if scope == "current_node" and not node_path:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "material_context_node_required",
                    "current_node material context requires a node-scoped tool call.",
                )
            )

        if node_path and scope == "current_node":
            node_service = self.runtime.app.node
            if node_service is None:
                return self.runtime.foundation.fail(self.runtime.foundation.issue("node_service_unavailable", "NodeService is not initialized."))
            node_refs = node_service.material_ref.list_node_material_refs(repo_root, node_path=node_path)
            if not node_refs.ok or node_refs.value is None:
                return self.runtime.foundation.fail(node_refs.issues)
            invalid_refs = [
                item
                for item in [*node_refs.value.owned_refs, *node_refs.value.context_refs]
                if item.valid is False
            ]
            if invalid_refs:
                return self.runtime.foundation.fail(
                    [
                        self.runtime.foundation.issue(
                            "node_material_ref_invalid",
                            item.preview_summary or "Node material ref is invalid.",
                            object_ref=node_path,
                            details=item.model_dump(mode="json"),
                        )
                        for item in invalid_refs
                    ]
                )
            node_owned_refs = [
                self._node_ref_context(repo_root, item) for item in node_refs.value.owned_refs
            ]
            node_context_refs = [
                self._node_ref_context(repo_root, item) for item in node_refs.value.context_refs
            ]

        if scope in {"current_node", "source", "all"}:
            overview = self.source_index.get_source_index_overview(
                repo_root,
                require_committed=require_committed_source_index,
            )
            if overview.ok and overview.value is not None:
                source_index_overview = overview.value
            elif not any(
                issue.kind in {"source_index_missing", "source_index_not_committed"}
                for issue in overview.issues
            ):
                return self.runtime.foundation.fail(overview.issues)

        total_matching_count = 0
        truncated = False
        normalized_query = query.strip() if query and query.strip() else None
        if normalized_query:
            search_scope = "all" if scope == "current_node" else scope
            search = self.material_read.search_material_text(
                repo_root,
                query=normalized_query,
                scope=search_scope,
                regex=regex,
                limit=None,
            )
            if not search.ok or search.value is None:
                return self.runtime.foundation.fail(search.issues)
            search_hits = search.value.hits
            if scope == "current_node":
                refs = [*node_owned_refs, *node_context_refs]
                search_hits = [
                    hit for hit in search_hits if self._hit_matches_any_ref(hit, refs)
                ]
            total_matching_count = len(search_hits)
            if limit is not None:
                search_hits = search_hits[:limit]
            truncated = len(search_hits) < total_matching_count

        if normalized_query:
            returned_count = len(search_hits)
        elif scope == "current_node":
            returned_count = len(node_owned_refs) + len(node_context_refs)
            total_matching_count = returned_count
        else:
            returned_count = len(source_files) + len(source_blocks) + len(resources)
            total_matching_count = returned_count

        return self.runtime.foundation.ok(
            MaterialContextView(
                node_path=node_path,
                query=normalized_query,
                scope=scope,
                source_index=source_index_overview,
                source_files=source_files,
                source_blocks=source_blocks,
                resources=resources,
                owned_refs=node_owned_refs,
                context_refs=node_context_refs,
                matches=search_hits,
                returned_count=returned_count,
                total_matching_count=total_matching_count,
                truncated=truncated,
                summary=(
                    f"Material context ({scope}): returned {returned_count} of "
                    f"{total_matching_count} compact entries."
                ),
            )
        )

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
        return self.resource_library.allocate_resource_draft(
            repo_root,
            target=target,
            resource_kind=resource_kind,
            title_hint=title_hint,
            requested_use=requested_use,
            consumer_need=consumer_need,
            caller_kind=caller_kind,
            purpose_hint=purpose_hint,
            allow_duplicate=allow_duplicate,
        )

    def check_resource_draft(self, repo_root: Path, *, draft_id: str) -> ServiceResult[GateReport]:
        return self.resource_library.check_resource_draft(repo_root, draft_id=draft_id)

    def refresh_resource_draft_manifest(
        self,
        repo_root: Path,
        *,
        draft_id: str,
        canonical_entry: str | None = None,
    ) -> ServiceResult[ResourceMaterialManifest]:
        return self.resource_library.refresh_resource_draft_manifest(
            repo_root,
            draft_id=draft_id,
            canonical_entry=canonical_entry,
        )

    def get_resource_draft(self, repo_root: Path, *, draft_id: str) -> ServiceResult[ResourceDraftView]:
        return self.resource_library.get_resource_draft(repo_root, draft_id=draft_id)

    def finalize_resource_draft(self, repo_root: Path, *, draft_id: str, summary: str) -> ServiceResult[ResourceView]:
        return self.resource_library.finalize_resource_draft(repo_root, draft_id=draft_id, summary=summary)

    def abandon_resource_draft(self, repo_root: Path, *, draft_id: str, reason: str) -> ServiceResult[ResourceDraftView]:
        return self.resource_library.abandon_resource_draft(repo_root, draft_id=draft_id, reason=reason)

    def submit_resource_duplicate(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        existing_kind: Literal["resource", "source"],
        duplicate_reason: str,
        existing_resource_key: str | None = None,
        existing_source_path: str | None = None,
        preview: str | None = None,
    ) -> ServiceResult[ResourceCurationResultView]:
        return self.resource_curation.submit_resource_duplicate(
            repo_root,
            target=target,
            existing_kind=existing_kind,
            duplicate_reason=duplicate_reason,
            existing_resource_key=existing_resource_key,
            existing_source_path=existing_source_path,
            preview=preview,
        )

    def submit_local_resource_created(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        draft_id: str,
        summary: str,
        classification_reason: str,
        resource_role: str,
        consumer_formalization_scope: str,
    ) -> ServiceResult[ResourceCurationResultView]:
        return self.resource_curation.submit_local_resource_created(
            repo_root,
            target=target,
            draft_id=draft_id,
            summary=summary,
            classification_reason=classification_reason,
            resource_role=resource_role,
            consumer_formalization_scope=consumer_formalization_scope,
        )

    def check_local_resource_created(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        draft_id: str,
        summary: str,
        classification_reason: str,
        resource_role: str,
        consumer_formalization_scope: str,
    ) -> ServiceResult[ResourceCurationResultView]:
        return self.resource_curation.check_local_resource_created(
            repo_root,
            target=target,
            draft_id=draft_id,
            summary=summary,
            classification_reason=classification_reason,
            resource_role=resource_role,
            consumer_formalization_scope=consumer_formalization_scope,
        )

    def submit_external_repo_required(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        reason: str,
        source_description: str,
        classification_reason: str,
        relation_to_current_repo_or_node: str,
        consumer_need: str,
        provider_scope: str,
        suggested_repo_name: str | None = None,
        required_interfaces_hint: str | None = None,
        existing_lean_repo_signal: str | None = None,
    ) -> ServiceResult[ResourceCurationResultView]:
        return self.resource_curation.submit_external_repo_required(
            repo_root,
            target=target,
            reason=reason,
            source_description=source_description,
            classification_reason=classification_reason,
            relation_to_current_repo_or_node=relation_to_current_repo_or_node,
            consumer_need=consumer_need,
            provider_scope=provider_scope,
            suggested_repo_name=suggested_repo_name,
            required_interfaces_hint=required_interfaces_hint,
            existing_lean_repo_signal=existing_lean_repo_signal,
        )

    def submit_resource_rejected(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        reason: str,
    ) -> ServiceResult[ResourceCurationResultView]:
        return self.resource_curation.submit_resource_rejected(repo_root, target=target, reason=reason)

    def register_local_resource(
        self,
        repo_root: Path,
        *,
        target: ResourceTarget | ResourceTargetView,
        temp_dir: Path,
        metadata: ResourceMetadataInput,
    ) -> ServiceResult[ResourceView]:
        return self.resource_library.register_local_resource(repo_root, target=target, temp_dir=temp_dir, metadata=metadata)

    def search_material(self, repo_root: Path, *, query: str, scope: str = "all") -> ServiceResult[MaterialSearchView]:
        return self.material_read.search_material_text(repo_root, query=query, scope=scope)

    def read_material_ref(self, repo_root: Path, *, ref: Any) -> ServiceResult[MaterialRangeView]:
        preview = self.material_read.preview_material_ref(repo_root, ref=ref)
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        return self.runtime.foundation.ok(preview.value.preview)

    def validate_source_range(self, repo_root: Path, *, path: str, start_line: int, end_line: int) -> ServiceResult[object]:
        return self.material_read.validate_source_range(repo_root, path=path, start_line=start_line, end_line=end_line)

    def validate_resource_range(self, repo_root: Path, *, resource_key: str, start_line: int, end_line: int) -> ServiceResult[object]:
        return self.material_read.validate_resource_range(repo_root, resource_key=resource_key, start_line=start_line, end_line=end_line)

    def preview_source_ref(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int,
        end_line: int,
        context_lines: int = 2,
    ):
        return self.material_read.preview_source_ref(
            repo_root,
            path=path,
            start_line=start_line,
            end_line=end_line,
            context_lines=context_lines,
        )

    def preview_resource_ref(
        self,
        repo_root: Path,
        *,
        resource_key: str,
        start_line: int,
        end_line: int,
        context_lines: int = 2,
    ):
        return self.material_read.preview_resource_ref(
            repo_root,
            resource_key=resource_key,
            start_line=start_line,
            end_line=end_line,
            context_lines=context_lines,
        )

    def read_source_range(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int,
        end_line: int,
        context_lines: int = 0,
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

    def resolve_source_scope(
        self, repo_root: Path, *, source_scope: SourceScope
    ) -> ServiceResult[ResolvedSourceScopeView]:
        return self.source_index.resolve_source_scope(repo_root, source_scope=source_scope)

    def open_source_index_update(
        self,
        repo_root: Path,
        *,
        resolved_scope: ResolvedSourceScopeView,
        index_policy: Literal["auto", "update", "reuse"],
        expected_baseline_digest: str | None = None,
        retry_baseline_index: SourceIndex | None = None,
    ) -> ServiceResult[SourceIndexOpenUpdateView]:
        return self.source_index.open_source_index_update(
            repo_root,
            resolved_scope=resolved_scope,
            index_policy=index_policy,
            expected_baseline_digest=expected_baseline_digest,
            retry_baseline_index=retry_baseline_index,
        )

    def get_source_index_update_context(
        self, repo_root: Path
    ) -> ServiceResult[SourceIndexUpdateContextView]:
        return self.source_index.get_source_index_update_context(repo_root)

    def validate_source_index_update(
        self,
        repo_root: Path,
        *,
        baseline_index: SourceIndex | None,
        expected_baseline_digest: str,
        resolved_scope: list[str],
        require_completed: bool,
    ) -> ServiceResult[SourceIndexUpdateGateView]:
        return self.source_index.validate_source_index_update(
            repo_root,
            baseline_index=baseline_index,
            expected_baseline_digest=expected_baseline_digest,
            resolved_scope=resolved_scope,
            require_completed=require_completed,
        )

    def commit_source_index_update(
        self,
        repo_root: Path,
        *,
        validated: SourceIndexUpdateGateView,
    ) -> ServiceResult[SourceIndexCommitView]:
        return self.source_index.commit_source_index_update(repo_root, validated=validated)

    def get_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        return self.source_index.get_source_index(repo_root)

    def get_committed_source_index(self, repo_root: Path) -> ServiceResult[SourceIndexView]:
        return self.source_index.get_committed_source_index(repo_root)

    def get_source_index_overview(
        self, repo_root: Path, *, require_committed: bool = False
    ) -> ServiceResult[SourceIndexOverviewView]:
        return self.source_index.get_source_index_overview(
            repo_root, require_committed=require_committed
        )

    def list_source_index_files(
        self,
        repo_root: Path,
        *,
        status: Literal["pending", "surveyed", "indexed", "skipped", "committed"] | None = None,
        require_committed: bool = False,
    ) -> ServiceResult[SourceIndexFileListView]:
        return self.source_index.list_source_index_files(
            repo_root,
            status=status,
            require_committed=require_committed,
        )

    def list_source_blocks(
        self,
        repo_root: Path,
        *,
        query: str | None = None,
        kind: str | None = None,
        subtype: str | None = None,
        path: str | None = None,
        limit: int | None = None,
        require_committed: bool = False,
    ) -> ServiceResult[SourceBlockListView]:
        return self.source_index.list_source_blocks(
            repo_root,
            query=query,
            kind=kind,
            subtype=subtype,
            path=path,
            limit=limit,
            require_committed=require_committed,
        )

    def get_source_block(
        self,
        repo_root: Path,
        *,
        block_id: str,
        require_committed: bool = False,
    ) -> ServiceResult[SourceBlockDetailView]:
        return self.source_index.get_source_block(
            repo_root,
            block_id=block_id,
            require_committed=require_committed,
        )

    def set_source_index_overview(
        self, repo_root: Path, *, overview: str
    ) -> ServiceResult[SourceIndexOverviewMutationReceipt]:
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
        return self.source_index.update_source_block(
            repo_root,
            block_id=block_id,
            title=title,
            summary=summary,
            kind=kind,
            subtype=subtype,
        )

    def _node_ref_context(
        self, repo_root: Path, item: object
    ) -> MaterialContextCitationView:
        path = getattr(item, "path", None)
        resource_key = getattr(item, "resource_key", None)
        start_line = getattr(item, "start_line", None)
        end_line = getattr(item, "end_line", None)
        source_block_id = (
            self._source_block_id_for_range(
                repo_root,
                path=path,
                start_line=start_line,
                end_line=end_line,
            )
            if path
            else None
        )
        return MaterialContextCitationView(
            material_kind=getattr(item, "material_kind"),
            path=path,
            resource_key=resource_key,
            start_line=start_line,
            end_line=end_line,
            reason=getattr(item, "reason", None),
            source_block_id=source_block_id,
        )

    def _source_block_id_for_range(
        self,
        repo_root: Path,
        *,
        path: str,
        start_line: int | None,
        end_line: int | None,
    ) -> str | None:
        loaded = self.source_index.get_source_index_model(repo_root)
        if not loaded.ok or loaded.value is None:
            return None
        for block in self.source_index._active_blocks(loaded.value):
            for ref in block.refs:
                source_ref = ref.material_ref.ref
                if (
                    ref.material_ref.kind == "source"
                    and getattr(source_ref, "path", None) == path
                    and (
                        start_line is None
                        or getattr(source_ref, "start_line", None) is None
                        or getattr(source_ref, "start_line", None) <= start_line
                    )
                    and (
                        end_line is None
                        or getattr(source_ref, "end_line", None) is None
                        or getattr(source_ref, "end_line", None) >= end_line
                    )
                ):
                    return block.block_id
        return None

    @staticmethod
    def _hit_matches_any_ref(
        hit: MaterialSearchHit, refs: list[MaterialContextCitationView]
    ) -> bool:
        for ref in refs:
            if hit.material_kind != ref.material_kind:
                continue
            if hit.material_kind == "source" and hit.path != ref.path:
                continue
            if hit.material_kind == "resource" and hit.resource_key != ref.resource_key:
                continue
            if ref.start_line is not None and hit.line_number < ref.start_line:
                continue
            if ref.end_line is not None and hit.line_number > ref.end_line:
                continue
            return True
        return False

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
        return self.source_index.add_source_block_ref(
            repo_root,
            block_id=block_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            role=role,
        )

    def remove_source_block_ref(self, repo_root: Path, *, block_id: str, ref_id: str) -> ServiceResult[SourceBlockView]:
        return self.source_index.remove_source_block_ref(repo_root, block_id=block_id, ref_id=ref_id)

    def update_source_block_ref(
        self,
        repo_root: Path,
        *,
        block_id: str,
        ref_id: str,
        path: str,
        start_line: int,
        end_line: int,
        role: str,
    ) -> ServiceResult[SourceBlockView]:
        return self.source_index.update_source_block_ref(
            repo_root,
            block_id=block_id,
            ref_id=ref_id,
            path=path,
            start_line=start_line,
            end_line=end_line,
            role=role,
        )

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

    def update_source_link(
        self,
        repo_root: Path,
        *,
        link_id: str,
        target_block_id: str | None,
        target_hint: str | None,
        link_kind: str,
        evidence_ref_ids: list[str],
    ) -> ServiceResult[SourceLinkView]:
        return self.source_index.update_source_link(
            repo_root,
            link_id=link_id,
            target_block_id=target_block_id,
            target_hint=target_hint,
            link_kind=link_kind,
            evidence_ref_ids=evidence_ref_ids,
        )

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
        return self.source_index.set_file_survey_status(
            repo_root,
            path=path,
            status=status,
            summary=summary,
        )

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

    def get_committed_source_index_coverage(self, repo_root: Path) -> ServiceResult[SourceIndexCoverageView]:
        return self.source_index.get_committed_source_index_coverage(repo_root)

    def submit_source_index_builder_round(
        self,
        repo_root: Path,
        *,
        summary: str,
        ctx: object | None = None,
    ) -> ServiceResult[SubmissionView]:
        return self.source_index.submit_source_index_builder_round(
            repo_root,
            summary=summary,
            ctx=ctx,
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
        return self.source_index.submit_source_index_review_round(
            repo_root,
            approved=approved,
            summary=summary,
            feedback=feedback,
            ctx=ctx,
        )
