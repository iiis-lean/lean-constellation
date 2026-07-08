"""Resource curation, resource library, and material acquisition tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolExecutionContext, ToolSpec
from lean_constellation.tools.args import (
    DraftIdArgs,
    DraftIdReasonArgs,
    MaterialContextArgs,
    ResourceDraftTargetArgs,
    ResourceKeyArgs,
    ResourceListArgs,
    ResourceRangeArgs,
    ResourceTargetArgs,
    SourceArtifactExtractArgs,
    SourceMaterialAcquireArgs,
    SourceMaterialImportArgs,
    SourceMaterialNormalizeArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import current_node_path, direct_tool, handler_tool


def _material_context(runtime, ctx, args: MaterialContextArgs):
    node_path = current_node_path(ctx) if ctx.node is not None else None
    return runtime.material.get_material_context_view(
        ctx.repo_root,
        node_path=node_path,
        query=args.query,
        include_source=args.include_source,
        include_resources=args.include_resources,
        regex=args.regex,
        limit=args.limit,
    )


def _normalize_resource_target(runtime, ctx, args: ResourceTargetArgs):
    del ctx
    return runtime.material.normalize_resource_target(args.target)


def _find_duplicate_resource(runtime, ctx, args: ResourceTargetArgs):
    normalized = runtime.material.normalize_resource_target(args.target)
    if not normalized.ok or normalized.value is None:
        return runtime.foundation.fail(normalized.issues)
    return runtime.material.find_duplicate_resource(ctx.repo_root, target=normalized.value)


def _active_resource_draft_id(runtime, ctx: ToolExecutionContext):
    if not ctx.runtime.flow_id:
        return runtime.foundation.fail(
            runtime.foundation.issue("resource_curation_context_missing", "Resource acquisition requires current ResourceCurationFlow context.")
        )
    flow = runtime.get_flow(ctx.runtime.flow_id)
    if getattr(flow, "flow_type", None) != "resource_curation":
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "resource_curation_context_missing",
                "Resource acquisition tools are only available inside ResourceCurationFlow.",
                current=getattr(flow, "flow_type", None),
                expected="resource_curation",
            )
        )
    draft_id = getattr(getattr(flow, "state", None), "active_resource_draft_key", None)
    if not draft_id:
        return runtime.foundation.fail(
            runtime.foundation.issue("resource_active_draft_missing", "Resource acquisition requires an active resource draft.")
        )
    return runtime.foundation.ok(draft_id)


def _acquire_resource_material(runtime, ctx: ToolExecutionContext, args: SourceMaterialAcquireArgs):
    draft_id = _active_resource_draft_id(runtime, ctx)
    if not draft_id.ok or draft_id.value is None:
        return draft_id
    return runtime.material.acquire_resource_material(
        ctx.repo_root,
        draft_id=draft_id.value,
        target=args.target,
        preferred_kind=args.preferred_kind,
    )


def _extract_resource_artifact(runtime, ctx: ToolExecutionContext, args: SourceArtifactExtractArgs):
    draft_id = _active_resource_draft_id(runtime, ctx)
    if not draft_id.ok or draft_id.value is None:
        return draft_id
    return runtime.material.extract_resource_artifact(
        ctx.repo_root,
        draft_id=draft_id.value,
        artifact_ref=args.artifact_ref,
        extraction_kind=args.extraction_kind,
    )


def _import_resource_material(runtime, ctx: ToolExecutionContext, args: SourceMaterialImportArgs):
    draft_id = _active_resource_draft_id(runtime, ctx)
    if not draft_id.ok or draft_id.value is None:
        return draft_id
    return runtime.material.import_resource_material(
        ctx.repo_root,
        draft_id=draft_id.value,
        source_path=args.source_path,
        as_name=args.as_name,
    )


def _normalize_resource_text_material(runtime, ctx: ToolExecutionContext, args: SourceMaterialNormalizeArgs):
    draft_id = _active_resource_draft_id(runtime, ctx)
    if not draft_id.ok or draft_id.value is None:
        return draft_id
    return runtime.material.normalize_resource_text_material(
        ctx.repo_root,
        draft_id=draft_id.value,
        material_ref=args.material_ref,
    )


def build_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    curator_roles = {"worker", "admin"}
    return [
        handler_tool(
            name="get_material_context",
            description="Read source corpus, source index, and resource-library material context for the current node, optionally narrowed by a text or regex query.",
            args_model=MaterialContextArgs,
            capability=ToolCapability.READ,
            result_view="material_context",
            groups={AppGroup.MATERIAL_CONTEXT_READ, AppGroup.RESOURCE_CURATION_CONTEXT_READ},
            roles=roles,
            handler=_material_context,
        ),
        handler_tool(
            name="normalize_resource_target",
            description="Normalize a resource target such as a URL, arXiv id, DOI, or local path into canonical fields for duplicate checks and draft allocation.",
            args_model=ResourceTargetArgs,
            capability=ToolCapability.READ,
            result_view="resource_target",
            groups={AppGroup.RESOURCE_TARGET_PREFLIGHT_READ, AppGroup.RESOURCE_CURATION_CONTEXT_READ},
            roles=roles,
            handler=_normalize_resource_target,
            required_context=set(),
        ),
        handler_tool(
            name="find_duplicate_resource",
            description="Check whether a normalized resource target duplicates a registered resource in the current repo resource library.",
            args_model=ResourceTargetArgs,
            capability=ToolCapability.READ,
            result_view="resource_duplicate",
            groups={AppGroup.RESOURCE_TARGET_PREFLIGHT_READ, AppGroup.RESOURCE_CURATION_CONTEXT_READ},
            roles=roles,
            handler=_find_duplicate_resource,
        ),
        handler_tool(
            name="acquire_resource_material",
            description="Acquire raw resource material such as arXiv source, PDF, web page, or local target into the current active resource draft acquisition area.",
            args_model=SourceMaterialAcquireArgs,
            capability=ToolCapability.WRITE,
            result_view="source_acquisition",
            groups={AppGroup.RESOURCE_ACQUISITION},
            roles=curator_roles,
            handler=_acquire_resource_material,
        ),
        handler_tool(
            name="extract_resource_artifact",
            description="Extract readable text or project files from an active resource draft artifact into the resource draft normalized area.",
            args_model=SourceArtifactExtractArgs,
            capability=ToolCapability.WRITE,
            result_view="source_extraction",
            groups={AppGroup.RESOURCE_ACQUISITION},
            roles=curator_roles,
            handler=_extract_resource_artifact,
        ),
        handler_tool(
            name="import_resource_material",
            description="Import a local file into the current active resource draft original material area.",
            args_model=SourceMaterialImportArgs,
            capability=ToolCapability.WRITE,
            result_view="source_acquisition",
            groups={AppGroup.RESOURCE_ACQUISITION},
            roles=curator_roles,
            handler=_import_resource_material,
        ),
        handler_tool(
            name="normalize_resource_text_material",
            description="Normalize a resource draft material reference into readable text for README summaries, resource notes, and downstream line-range reads.",
            args_model=SourceMaterialNormalizeArgs,
            capability=ToolCapability.WRITE,
            result_view="source_extraction",
            groups={AppGroup.RESOURCE_ACQUISITION},
            roles=curator_roles,
            handler=_normalize_resource_text_material,
        ),
        direct_tool(
            name="read_resource_range",
            description="Read a line range from a registered resource's normalized text by resource key.",
            args_model=ResourceRangeArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="read_resource_range",
            result_view="material_range",
            groups={AppGroup.RESOURCE_LIBRARY_READ},
            roles=roles,
        ),
        direct_tool(
            name="list_resources",
            description="List resources registered in the current repo resource library with optional text filtering.",
            args_model=ResourceListArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_component="resource_library",
            backing_method="list_resources",
            result_view="resource_list",
            groups={AppGroup.RESOURCE_LIBRARY_READ},
            roles=roles,
        ),
        direct_tool(
            name="get_resource",
            description="Inspect metadata, normalized text availability, and notes for one registered resource by resource key.",
            args_model=ResourceKeyArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_component="resource_library",
            backing_method="get_resource",
            result_view="resource",
            groups={AppGroup.RESOURCE_LIBRARY_READ},
            roles=roles,
        ),
        direct_tool(
            name="allocate_resource_draft",
            description="Allocate a resource draft directory for one canonical target and return the draft id and writable draft layout for curator work.",
            args_model=ResourceDraftTargetArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="allocate_resource_draft",
            result_view="resource_draft",
            groups={AppGroup.RESOURCE_DRAFT_WRITE},
            roles=curator_roles,
        ),
        direct_tool(
            name="get_resource_draft",
            description="Inspect a resource draft by draft id, including target metadata, draft paths, and current readiness state.",
            args_model=DraftIdArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="get_resource_draft",
            result_view="resource_draft",
            groups={AppGroup.RESOURCE_DRAFT_WRITE},
            roles=curator_roles,
        ),
        direct_tool(
            name="check_resource_draft",
            description="Validate that a resource draft has coherent metadata, readable normalized material, and required README content before local-resource submit.",
            args_model=DraftIdArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="check_resource_draft",
            result_view="gate_report",
            groups={AppGroup.RESOURCE_DRAFT_WRITE},
            roles=curator_roles,
        ),
        direct_tool(
            name="abandon_resource_draft",
            description="Mark a resource draft as abandoned with a reason when it should not be finalized into the resource library.",
            args_model=DraftIdReasonArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="abandon_resource_draft",
            result_view="resource_draft",
            groups={AppGroup.RESOURCE_DRAFT_WRITE},
            roles=curator_roles,
        ),
    ]
