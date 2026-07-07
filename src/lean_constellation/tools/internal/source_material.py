"""Source corpus, SourceIndex, and material read tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolExecutionContext, ToolSpec
from lean_constellation.tools.args import (
    DraftIdArgs,
    DraftIdReasonArgs,
    FileIndexingStatusArgs,
    FileStatusArgs,
    MaterialSearchArgs,
    SourceArtifactExtractArgs,
    NoArgs,
    ResourceDraftTargetArgs,
    ResourceKeyArgs,
    ResourceListArgs,
    ResourceRangeArgs,
    SourceBlockCreateArgs,
    SourceBlockIdArgs,
    SourceBlockRefArgs,
    SourceBlockRefRemoveArgs,
    SourceBlockUpdateArgs,
    SourceCorpusCheckArgs,
    SourceCorpusScanArgs,
    SourceIndexOverviewArgs,
    SourceLinkCreateArgs,
    SourceMaterialAcquireArgs,
    SourceMaterialImportArgs,
    SourceMaterialNormalizeArgs,
    SourceRangeArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.keys import ApplicationToolViewKey as AppView
from lean_constellation.tools.specs import direct_tool, handler_tool


_COMMITTED_SOURCE_INDEX_VIEWS = {
    AppView.ROOT_INTERFACE_PREPARE.value,
    AppView.NATIVE_REPO_COORDINATOR.value,
    AppView.CONTENT_PLAN.value,
    AppView.RESOURCE_RECON.value,
}


def _requires_committed_source_index(ctx: ToolExecutionContext) -> bool:
    return ctx.expected_view_key in _COMMITTED_SOURCE_INDEX_VIEWS


def _get_source_index(runtime, ctx: ToolExecutionContext, _args: NoArgs):
    if _requires_committed_source_index(ctx):
        return runtime.material.get_committed_source_index(ctx.repo_root)
    return runtime.material.get_source_index(ctx.repo_root)


def _get_source_index_coverage(runtime, ctx: ToolExecutionContext, _args: NoArgs):
    if _requires_committed_source_index(ctx):
        return runtime.material.get_committed_source_index_coverage(ctx.repo_root)
    return runtime.material.get_source_index_coverage(ctx.repo_root)


def build_source_index_tool_specs() -> list[ToolSpec]:
    builder_roles = {"worker", "admin"}
    read_roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    return [
        direct_tool(
            name="create_draft_source_index",
            description="Create or load the draft SourceIndex for the current repo.",
            args_model=NoArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="create_draft_source_index",
            result_view="source_index",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        handler_tool(
            name="get_source_index",
            description="Read the SourceIndex view for the repo. Draft SourceIndex views return the draft; committed SourceIndex views require committed state.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="source_index",
            groups={AppGroup.SOURCE_INDEX_DRAFT_READ, AppGroup.SOURCE_INDEX_COMMITTED_READ},
            roles=read_roles,
            handler=_get_source_index,
        ),
        direct_tool(
            name="set_source_index_overview",
            description="Set the draft SourceIndex overview.",
            args_model=SourceIndexOverviewArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="set_source_index_overview",
            result_view="source_index",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="create_source_block",
            description="Create a semantic block in the draft SourceIndex.",
            args_model=SourceBlockCreateArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="create_source_block",
            result_view="source_block",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="update_source_block",
            description="Update title, summary, kind, or subtype of a draft SourceIndex block.",
            args_model=SourceBlockUpdateArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="update_source_block",
            result_view="source_block",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="add_source_block_ref",
            description="Attach a source range to a draft SourceIndex block.",
            args_model=SourceBlockRefArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="add_source_block_ref",
            result_view="source_block",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="remove_source_block_ref",
            description="Remove a draft-local source range ref from a SourceIndex block.",
            args_model=SourceBlockRefRemoveArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="remove_source_block_ref",
            result_view="source_block",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="mark_block_refs_done",
            description="Run the refs-done gate for a draft SourceIndex block.",
            args_model=SourceBlockIdArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="mark_block_refs_done",
            result_view="gate_report",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="create_source_link",
            description="Create a semantic relationship between SourceIndex blocks.",
            args_model=SourceLinkCreateArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="create_source_link",
            result_view="source_link",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="mark_block_links_done",
            description="Run the links-done gate for a draft SourceIndex block.",
            args_model=SourceBlockIdArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="mark_block_links_done",
            result_view="gate_report",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="mark_block_completed",
            description="Run the completed gate for a draft SourceIndex block.",
            args_model=SourceBlockIdArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="mark_block_completed",
            result_view="gate_report",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="set_file_survey_status",
            description="Set the SourceIndex survey status for a source corpus file.",
            args_model=FileStatusArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="set_file_survey_status",
            result_view="source_file_index",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="set_file_indexing_status",
            description="Set the SourceIndex indexing status for a source corpus file.",
            args_model=FileIndexingStatusArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="set_file_indexing_status",
            result_view="source_file_index",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
        ),
        direct_tool(
            name="validate_source_index",
            description="Validate the draft SourceIndex without submitting it.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="validate_source_index",
            result_view="gate_report",
            groups={AppGroup.SOURCE_INDEX_DRAFT_READ},
            roles=read_roles,
        ),
        handler_tool(
            name="get_source_index_coverage",
            description="Read SourceIndex coverage status for source files and semantic blocks. Committed SourceIndex views require committed state.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="source_index_coverage",
            groups={AppGroup.SOURCE_INDEX_DRAFT_READ, AppGroup.SOURCE_INDEX_COMMITTED_READ},
            roles=read_roles,
            handler=_get_source_index_coverage,
        ),
    ]


def build_material_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    return [
        direct_tool(
            name="search_material_text",
            description="Search source corpus and resource text in the current repo.",
            args_model=MaterialSearchArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="search_material_text",
            result_view="material_search",
            groups={AppGroup.SOURCE_MATERIAL_TEXT_READ},
            roles=roles,
        ),
        direct_tool(
            name="read_source_range",
            description="Read a line range from source corpus text.",
            args_model=SourceRangeArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="read_source_range",
            result_view="material_range",
            groups={AppGroup.SOURCE_MATERIAL_TEXT_READ},
            roles=roles,
        ),
    ]


def build_source_corpus_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "worker", "admin"}
    return [
        direct_tool(
            name="scan_source_corpus",
            description="Scan the source corpus and return its manifest view.",
            args_model=SourceCorpusScanArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="scan_source_corpus",
            result_view="source_corpus_manifest",
            groups={AppGroup.SOURCE_CORPUS_READ},
            roles=roles,
        ),
        direct_tool(
            name="check_source_corpus_draft",
            description="Validate source corpus draft structure and entry file.",
            args_model=SourceCorpusCheckArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="check_source_corpus_draft",
            result_view="gate_report",
            groups={AppGroup.SOURCE_CORPUS_READ},
            roles=roles,
        ),
        direct_tool(
            name="acquire_source_material",
            description="Acquire raw source material into the source draft area.",
            args_model=SourceMaterialAcquireArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="acquire_source_material",
            result_view="source_acquisition",
            groups={AppGroup.SOURCE_ACQUISITION, AppGroup.MATERIAL_ACQUISITION},
            roles={"worker", "admin"},
        ),
        direct_tool(
            name="extract_source_artifact",
            description="Extract readable text from an acquired source artifact.",
            args_model=SourceArtifactExtractArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="extract_source_artifact",
            result_view="source_extraction",
            groups={AppGroup.SOURCE_ACQUISITION, AppGroup.MATERIAL_ACQUISITION},
            roles={"worker", "admin"},
        ),
        direct_tool(
            name="import_source_material",
            description="Import a local source file or directory into the source draft area.",
            args_model=SourceMaterialImportArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="import_source_material",
            result_view="source_acquisition",
            groups={AppGroup.SOURCE_ACQUISITION, AppGroup.MATERIAL_ACQUISITION},
            roles={"worker", "admin"},
        ),
        direct_tool(
            name="normalize_source_text_material",
            description="Normalize a source draft material reference into readable text.",
            args_model=SourceMaterialNormalizeArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="normalize_source_text_material",
            result_view="source_extraction",
            groups={AppGroup.SOURCE_ACQUISITION, AppGroup.MATERIAL_ACQUISITION},
            roles={"worker", "admin"},
        ),
    ]


def build_tool_specs() -> list[ToolSpec]:
    return [*build_source_index_tool_specs(), *build_source_corpus_tool_specs(), *build_material_tool_specs()]
