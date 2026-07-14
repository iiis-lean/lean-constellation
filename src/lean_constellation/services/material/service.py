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
    SourceCorpusPreparedView,
    SourceExtractionView,
)
from lean_constellation.services.material.source_index import (
    ResolvedSourceScopeView,
    SourceBlockView,
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
    ref_scope: Literal["source_index", "node_owned", "node_context"]
    material_kind: Literal["source", "resource"]
    path: str | None = None
    resource_key: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    role: str | None = None
    reason: str | None = None
    added_by: str | None = None
    valid: bool | None = None
    preview_summary: str | None = None
    reusable_ref_fields: dict[str, str | int] = Field(default_factory=dict)


class MaterialContextSourceBlockView(StrictModel):
    kind: str
    subtype: str | None = None
    title: str
    summary: str
    lifecycle_status: str
    refs: list[MaterialContextCitationView] = Field(default_factory=list)


class MaterialContextView(StrictModel):
    repo_root: str
    node_path: str | None = None
    query: str | None = None
    include_source: bool
    include_resources: bool
    source_files: list[MaterialFileEntry] = Field(default_factory=list)
    source_index_overview: str | None = None
    source_blocks: list[MaterialContextSourceBlockView] = Field(default_factory=list)
    resources: list[ResourceSummaryView] = Field(default_factory=list)
    node_owned_refs: list[MaterialContextCitationView] = Field(default_factory=list)
    node_context_refs: list[MaterialContextCitationView] = Field(default_factory=list)
    search_hits: list[MaterialSearchHit] = Field(default_factory=list)
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
    ) -> ServiceResult[SourceExtractionView]:
        return self.resource_curation.extract_resource_artifact(repo_root, draft_id=draft_id, artifact_ref=artifact_ref, extraction_kind=extraction_kind)

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
        include_source: bool = True,
        include_resources: bool = True,
        require_committed_source_index: bool = False,
        regex: bool = False,
        limit: int = 20,
    ) -> ServiceResult[MaterialContextView]:
        if not include_source and not include_resources:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("material_context_empty_scope", "include_source and include_resources cannot both be false.")
            )

        source_files: list[MaterialFileEntry] = []
        source_blocks: list[MaterialContextSourceBlockView] = []
        source_index_overview: str | None = None
        resources: list[ResourceSummaryView] = []
        search_hits: list[MaterialSearchHit] = []
        node_owned_refs: list[MaterialContextCitationView] = []
        node_context_refs: list[MaterialContextCitationView] = []

        if include_source:
            listed_source = self.material_read.list_material_files(repo_root, material_kind="source")
            if not listed_source.ok or listed_source.value is None:
                return self.runtime.foundation.fail(listed_source.issues)
            source_files = listed_source.value.files
            source_index = (
                self.source_index.get_committed_source_index(repo_root)
                if require_committed_source_index
                else self.source_index.get_source_index(repo_root)
            )
            if source_index.ok and source_index.value is not None:
                source_index_overview = source_index.value.overview
                source_blocks = [
                    self._source_block_context(block)
                    for block in source_index.value.blocks.values()
                    if block.active and block.block_id != source_index.value.root_block_id
                ]
                source_blocks.sort(key=lambda item: (item.kind, item.title))
            elif not any(
                issue.kind in {"source_index_missing", "source_index_not_committed"}
                for issue in source_index.issues
            ):
                return self.runtime.foundation.fail(source_index.issues)

        if include_resources:
            listed_resources = self.resource_library.list_resources(repo_root, query=query if query and query.strip() else None)
            if not listed_resources.ok or listed_resources.value is None:
                return self.runtime.foundation.fail(listed_resources.issues)
            resources = listed_resources.value

        if query and query.strip():
            if include_source and include_resources:
                scope = "all"
            elif include_source:
                scope = "source"
            else:
                scope = "resource"
            search = self.material_read.search_material_text(repo_root, query=query, scope=scope, regex=regex, limit=limit)
            if not search.ok or search.value is None:
                return self.runtime.foundation.fail(search.issues)
            search_hits = search.value.hits

        if node_path:
            node_service = self.runtime.app.node
            if node_service is None:
                return self.runtime.foundation.fail(self.runtime.foundation.issue("node_service_unavailable", "NodeService is not initialized."))
            node_refs = node_service.material_ref.list_node_material_refs(repo_root, node_path=node_path)
            if not node_refs.ok or node_refs.value is None:
                return self.runtime.foundation.fail(node_refs.issues)
            node_owned_refs = [self._node_ref_context("node_owned", item) for item in node_refs.value.owned_refs]
            node_context_refs = [self._node_ref_context("node_context", item) for item in node_refs.value.context_refs]
            invalid_refs = [item for item in [*node_owned_refs, *node_context_refs] if item.valid is False]
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

        return self.runtime.foundation.ok(
            MaterialContextView(
                repo_root=str(Path(repo_root)),
                node_path=node_path,
                query=query.strip() if query and query.strip() else None,
                include_source=include_source,
                include_resources=include_resources,
                source_files=source_files,
                source_index_overview=source_index_overview,
                source_blocks=source_blocks,
                resources=resources,
                node_owned_refs=node_owned_refs,
                node_context_refs=node_context_refs,
                search_hits=search_hits,
                summary=(
                    f"Material context: {len(source_files)} source files, {len(source_blocks)} source blocks, "
                    f"{len(resources)} resources, {len(node_owned_refs)} owned refs, "
                    f"{len(node_context_refs)} context refs, {len(search_hits)} search hits."
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
        allow_duplicate: bool = False,
    ) -> ServiceResult[ResourceDraftView]:
        return self.resource_library.allocate_resource_draft(
            repo_root,
            target=target,
            resource_kind=resource_kind,
            title_hint=title_hint,
            allow_duplicate=allow_duplicate,
        )

    def check_resource_draft(self, repo_root: Path, *, draft_id: str) -> ServiceResult[GateReport]:
        return self.resource_library.check_resource_draft(repo_root, draft_id=draft_id)

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
    ) -> ServiceResult[ResourceCurationResultView]:
        return self.resource_curation.submit_local_resource_created(
            repo_root,
            target=target,
            draft_id=draft_id,
            summary=summary,
        )

    def check_local_resource_created(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        draft_id: str,
        summary: str,
    ) -> ServiceResult[ResourceCurationResultView]:
        return self.resource_curation.check_local_resource_created(
            repo_root,
            target=target,
            draft_id=draft_id,
            summary=summary,
        )

    def submit_external_repo_required(
        self,
        repo_root: Path,
        *,
        target: ResourceTargetView,
        reason: str,
        source_description: str,
        suggested_repo_name: str | None = None,
        required_interfaces_hint: str | None = None,
    ) -> ServiceResult[ResourceCurationResultView]:
        return self.resource_curation.submit_external_repo_required(
            repo_root,
            target=target,
            reason=reason,
            source_description=source_description,
            suggested_repo_name=suggested_repo_name,
            required_interfaces_hint=required_interfaces_hint,
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

    def _source_block_context(self, block: SourceBlockView) -> MaterialContextSourceBlockView:
        refs = [
            MaterialContextCitationView(
                ref_scope="source_index",
                material_kind="source",
                path=ref.path,
                start_line=ref.start_line,
                end_line=ref.end_line,
                role=ref.role,
                reusable_ref_fields={"path": ref.path, "start_line": ref.start_line, "end_line": ref.end_line},
            )
            for ref in block.refs
        ]
        return MaterialContextSourceBlockView(
            kind=block.kind,
            subtype=block.subtype,
            title=block.title,
            summary=block.summary,
            lifecycle_status=block.lifecycle_status,
            refs=refs,
        )

    @staticmethod
    def _node_ref_context(ref_scope: Literal["node_owned", "node_context"], item: object) -> MaterialContextCitationView:
        path = getattr(item, "path", None)
        resource_key = getattr(item, "resource_key", None)
        start_line = getattr(item, "start_line", None)
        end_line = getattr(item, "end_line", None)
        reusable: dict[str, str | int] = {}
        if path:
            reusable["path"] = path
        if resource_key:
            reusable["resource_key"] = resource_key
        if start_line is not None:
            reusable["start_line"] = start_line
        if end_line is not None:
            reusable["end_line"] = end_line
        added_by = getattr(getattr(item, "added_by", None), "value", getattr(item, "added_by", None))
        return MaterialContextCitationView(
            ref_scope=ref_scope,
            material_kind=getattr(item, "material_kind"),
            path=path,
            resource_key=resource_key,
            start_line=start_line,
            end_line=end_line,
            reason=getattr(item, "reason", None),
            added_by=added_by,
            valid=getattr(item, "valid", None),
            preview_summary=getattr(item, "preview_summary", None),
            reusable_ref_fields=reusable,
        )

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
