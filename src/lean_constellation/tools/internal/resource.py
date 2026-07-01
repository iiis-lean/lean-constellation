"""Resource curation, resource library, and material acquisition tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
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


def build_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    curator_roles = {"worker", "admin"}
    return [
        handler_tool(
            name="get_material_context",
            description="Read source/resource material context for the current node and optional query.",
            args_model=MaterialContextArgs,
            capability=ToolCapability.READ,
            result_view="material_context",
            groups={AppGroup.RESOURCE_CURATION_CONTEXT_READ, AppGroup.EXTERNAL_RESOURCE_DISCOVERY},
            roles=roles,
            handler=_material_context,
        ),
        handler_tool(
            name="normalize_resource_target",
            description="Normalize a resource target into canonical resource-target fields.",
            args_model=ResourceTargetArgs,
            capability=ToolCapability.READ,
            result_view="resource_target",
            groups={AppGroup.RESOURCE_CURATION_CONTEXT_READ, AppGroup.EXTERNAL_RESOURCE_DISCOVERY},
            roles=roles,
            handler=_normalize_resource_target,
            required_context=set(),
        ),
        handler_tool(
            name="find_duplicate_resource",
            description="Check whether a target duplicates an existing source or resource in the current repo.",
            args_model=ResourceTargetArgs,
            capability=ToolCapability.READ,
            result_view="resource_duplicate",
            groups={AppGroup.RESOURCE_CURATION_CONTEXT_READ, AppGroup.EXTERNAL_RESOURCE_DISCOVERY},
            roles=roles,
            handler=_find_duplicate_resource,
        ),
        direct_tool(
            name="acquire_material_resource",
            description="Acquire raw material for source/resource preparation into the draft area.",
            args_model=SourceMaterialAcquireArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="acquire_source_material",
            result_view="source_acquisition",
            groups={AppGroup.MATERIAL_ACQUISITION},
            roles=curator_roles,
        ),
        direct_tool(
            name="extract_material_artifact",
            description="Extract readable text from an acquired material artifact.",
            args_model=SourceArtifactExtractArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="extract_source_artifact",
            result_view="source_extraction",
            groups={AppGroup.MATERIAL_ACQUISITION},
            roles=curator_roles,
        ),
        direct_tool(
            name="import_material_file",
            description="Import a local file or directory into the material draft area.",
            args_model=SourceMaterialImportArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="import_source_material",
            result_view="source_acquisition",
            groups={AppGroup.MATERIAL_ACQUISITION},
            roles=curator_roles,
        ),
        direct_tool(
            name="normalize_material_text",
            description="Normalize a draft material reference into readable text.",
            args_model=SourceMaterialNormalizeArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="normalize_source_text_material",
            result_view="source_extraction",
            groups={AppGroup.MATERIAL_ACQUISITION},
            roles=curator_roles,
        ),
        direct_tool(
            name="read_resource_range",
            description="Read a line range from a normalized resource.",
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
            description="List resources registered in the current repo resource library.",
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
            description="Inspect a registered resource by resource key.",
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
            description="Allocate a resource draft directory for curator work.",
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
            description="Inspect a resource draft by draft id.",
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
            description="Validate resource draft structure before submit.",
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
            description="Abandon a resource draft that should not be finalized.",
            args_model=DraftIdReasonArgs,
            capability=ToolCapability.WRITE,
            backing_service="material",
            backing_method="abandon_resource_draft",
            result_view="resource_draft",
            groups={AppGroup.RESOURCE_DRAFT_WRITE},
            roles=curator_roles,
        ),
    ]
