"""Source corpus, SourceIndex, and material read tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolExecutionContext, ToolSpec
from lean_constellation.tools.args import (
    DraftIdArgs,
    DraftIdReasonArgs,
    FileIndexingStatusArgs,
    FileStatusArgs,
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
    SourceRangeValidateArgs,
    TextSearchArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.keys import ApplicationToolViewKey as AppView
from lean_constellation.tools.specs import direct_tool, handler_tool


_COMMITTED_SOURCE_INDEX_VIEWS = {
    AppView.ROOT_INTERFACE_PREPARE.value,
    AppView.NATIVE_REPO_COORDINATOR.value,
    AppView.RESOURCE_CURATOR.value,
    AppView.RESOURCE_RECON.value,
    AppView.STATEMENT_NL_WORKER.value,
    AppView.STATEMENT_NL_REVIEWER.value,
    AppView.STATEMENT_FORMAL_REVIEWER.value,
    AppView.PROOF_NL_WORKER.value,
    AppView.PROOF_NL_REVIEWER.value,
    AppView.PROOF_FORMAL_WORKER.value,
    AppView.PROOF_FORMAL_REVIEWER.value,
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


def _get_source_index_update_context(runtime, ctx: ToolExecutionContext, _args: NoArgs):
    owner = _source_index_update_owner(runtime, ctx, require_builder=False)
    if not owner.ok or owner.value is None:
        return runtime.foundation.fail(owner.issues)
    return runtime.material.get_source_index_update_context(ctx.repo_root)


def _source_index_update_owner(runtime, ctx: ToolExecutionContext, *, require_builder: bool = True):
    flow_id = ctx.runtime.flow_id
    step_id = ctx.runtime.step_id
    if not flow_id or not step_id:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_flow_context_required",
                "SourceIndex mutation requires the current SourceIndexBuildFlow context.",
            )
        )
    try:
        flow = runtime.get_flow(flow_id)
        step = runtime.get_step(step_id)
    except Exception as exc:  # noqa: BLE001 - normalize runtime identity failures.
        return runtime.foundation.fail(
            runtime.foundation.issue("source_index_flow_context_invalid", str(exc), object_ref=flow_id)
        )
    update_id = getattr(getattr(flow, "state", None), "active_update_id", None)
    if getattr(flow, "flow_type", None) != "source_index_build" or not update_id:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_update_owner_mismatch",
                "Current Flow does not own the active SourceIndex update.",
                object_ref=flow_id,
            )
        )
    allowed_step_types = (
        {"source_index_builder_agent_step"}
        if require_builder
        else {"source_index_builder_agent_step", "source_index_reviewer_agent_step"}
    )
    if getattr(step, "flow_id", None) != flow_id or getattr(step, "step_type", None) not in allowed_step_types:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_update_owner_mismatch",
                (
                    "Only the Builder step of the owning SourceIndexBuildFlow may mutate the draft."
                    if require_builder
                    else "Only Builder and Reviewer steps of the owning SourceIndexBuildFlow may read update context."
                ),
                object_ref=step_id,
            )
        )
    return runtime.foundation.ok(str(update_id))


def _source_index_write_handler(method_name: str):
    def handler(runtime, ctx: ToolExecutionContext, args):  # noqa: ANN001
        owner = _source_index_update_owner(runtime, ctx)
        if not owner.ok or owner.value is None:
            return runtime.foundation.fail(owner.issues)
        kwargs = args.model_dump(exclude_unset=True)
        kwargs["expected_update_id"] = owner.value
        return getattr(runtime.material, method_name)(ctx.repo_root, **kwargs)

    return handler


def build_source_index_tool_specs() -> list[ToolSpec]:
    builder_roles = {"worker", "admin"}
    read_roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    return [
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
        handler_tool(
            name="set_source_index_overview",
            description="Set the draft SourceIndex overview.",
            args_model=SourceIndexOverviewArgs,
            capability=ToolCapability.WRITE,
            result_view="source_index",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("set_source_index_overview"),
        ),
        handler_tool(
            name="create_source_block",
            description="Create a semantic block in the draft SourceIndex.",
            args_model=SourceBlockCreateArgs,
            capability=ToolCapability.WRITE,
            result_view="source_block",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("create_source_block"),
        ),
        handler_tool(
            name="update_source_block",
            description="Update title, summary, kind, or subtype of a draft SourceIndex block.",
            args_model=SourceBlockUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="source_block",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("update_source_block"),
        ),
        handler_tool(
            name="add_source_block_ref",
            description="Attach a source range to a draft SourceIndex block.",
            args_model=SourceBlockRefArgs,
            capability=ToolCapability.WRITE,
            result_view="source_block",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("add_source_block_ref"),
        ),
        handler_tool(
            name="remove_source_block_ref",
            description="Remove a draft-local source range ref from a SourceIndex block.",
            args_model=SourceBlockRefRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="source_block",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("remove_source_block_ref"),
        ),
        handler_tool(
            name="mark_block_refs_done",
            description="Run the refs-done gate for a draft SourceIndex block.",
            args_model=SourceBlockIdArgs,
            capability=ToolCapability.WRITE,
            result_view="gate_report",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("mark_block_refs_done"),
        ),
        handler_tool(
            name="create_source_link",
            description="Create a semantic relationship between SourceIndex blocks.",
            args_model=SourceLinkCreateArgs,
            capability=ToolCapability.WRITE,
            result_view="source_link",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("create_source_link"),
        ),
        handler_tool(
            name="mark_block_links_done",
            description="Run the links-done gate for a draft SourceIndex block.",
            args_model=SourceBlockIdArgs,
            capability=ToolCapability.WRITE,
            result_view="gate_report",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("mark_block_links_done"),
        ),
        handler_tool(
            name="mark_block_completed",
            description="Run the completed gate for a draft SourceIndex block.",
            args_model=SourceBlockIdArgs,
            capability=ToolCapability.WRITE,
            result_view="gate_report",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("mark_block_completed"),
        ),
        handler_tool(
            name="set_file_survey_status",
            description="Set the SourceIndex survey status for a source corpus file.",
            args_model=FileStatusArgs,
            capability=ToolCapability.WRITE,
            result_view="source_file_index",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("set_file_survey_status"),
        ),
        handler_tool(
            name="set_file_indexing_status",
            description="Set the SourceIndex indexing status for a source corpus file.",
            args_model=FileIndexingStatusArgs,
            capability=ToolCapability.WRITE,
            result_view="source_file_index",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("set_file_indexing_status"),
        ),
        handler_tool(
            name="get_source_index_update_context",
            description="Read the active file scope, committed baseline split, and current delta for the owning SourceIndex build Flow.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="source_index_update_context",
            groups={AppGroup.SOURCE_INDEX_DRAFT_READ},
            roles=read_roles,
            handler=_get_source_index_update_context,
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
        handler_tool(
            name="search_source_text",
            description="Search source corpus text in the current repo.",
            args_model=TextSearchArgs,
            capability=ToolCapability.READ,
            result_view="material_search",
            groups={AppGroup.SOURCE_MATERIAL_TEXT_READ},
            roles=roles,
            handler=_search_source_text,
        ),
        handler_tool(
            name="search_resource_text",
            description="Search registered resource library text in the current repo.",
            args_model=TextSearchArgs,
            capability=ToolCapability.READ,
            result_view="material_search",
            groups={AppGroup.RESOURCE_LIBRARY_READ},
            roles=roles,
            handler=_search_resource_text,
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
        direct_tool(
            name="validate_source_range",
            description="Validate that a source corpus line range exists and is well-formed.",
            args_model=SourceRangeValidateArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="validate_source_range",
            result_view="source_range_validation",
            groups={AppGroup.SOURCE_MATERIAL_TEXT_READ},
            roles=roles,
        ),
        direct_tool(
            name="preview_source_ref",
            description="Preview a source corpus range with nearby context before using it as a SourceIndex ref.",
            args_model=SourceRangeArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="preview_source_ref",
            result_view="material_ref_preview",
            groups={AppGroup.SOURCE_MATERIAL_TEXT_READ},
            roles=roles,
        ),
    ]


def _search_source_text(runtime, ctx: ToolExecutionContext, args: TextSearchArgs):
    return runtime.material.search_material_text(
        ctx.repo_root,
        query=args.query,
        scope="source",
        regex=args.regex,
        limit=args.limit,
    )


def _search_resource_text(runtime, ctx: ToolExecutionContext, args: TextSearchArgs):
    return runtime.material.search_material_text(
        ctx.repo_root,
        query=args.query,
        scope="resource",
        regex=args.regex,
        limit=args.limit,
    )


def build_source_corpus_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "worker", "reviewer", "admin"}
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
            groups={AppGroup.SOURCE_ACQUISITION},
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
            groups={AppGroup.SOURCE_ACQUISITION},
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
            groups={AppGroup.SOURCE_ACQUISITION},
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
            groups={AppGroup.SOURCE_ACQUISITION},
            roles={"worker", "admin"},
        ),
    ]


def build_tool_specs() -> list[ToolSpec]:
    return [*build_source_index_tool_specs(), *build_source_corpus_tool_specs(), *build_material_tool_specs()]
