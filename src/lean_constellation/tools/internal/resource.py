"""Resource curation, resource library, and material acquisition tools."""

from __future__ import annotations

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.material.resource_library import ResourceDraftStatus, ResourceTarget
from lean_constellation.services.tool_facade import ToolCapability, ToolExecutionContext, ToolSpec
from lean_constellation.tools.args import (
    DraftIdArgs,
    DraftIdReasonArgs,
    MaterialContextArgs,
    ResourceArtifactExtractArgs,
    ResourceDraftTargetArgs,
    ResourceKeyArgs,
    ResourceListArgs,
    ResourceMaterialAcquireArgs,
    ResourceMaterialImportArgs,
    ResourceMaterialNormalizeArgs,
    ResourceRangeArgs,
    ResourceTargetArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import current_node_path, direct_tool, handler_tool


_COMMITTED_MATERIAL_CONTEXT_VIEWS = {
    "resource_curator",
    "native_repo_coordinator",
    "content_plan",
    "resource_recon",
}


class ResourceAgentView(StrictModel):
    resource_key: str
    title: str | None = None
    kind: str
    canonical_locator: str
    normalized_entry: str
    source_url: str | None = None
    notes: str | None = None
    summary: str


class ResourceDraftAgentView(StrictModel):
    draft_id: str
    status: ResourceDraftStatus
    target: ResourceTarget
    resource_kind: str | None = None
    title_hint: str | None = None
    resource_key: str | None = None
    logical_files: list[str] = Field(
        default_factory=lambda: ["README.md", "original/", "normalized/"]
    )
    summary: str


class ResourceAcquisitionAgentView(StrictModel):
    ok: bool
    target: str
    artifact_refs: list[str] = Field(default_factory=list)
    primary_artifact_ref: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str
    issue_code: str | None = None


class ResourceExtractionAgentView(StrictModel):
    ok: bool
    artifact_ref: str
    material_refs: list[str] = Field(default_factory=list)
    primary_material_ref: str | None = None
    preview: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str
    issue_code: str | None = None


def _material_context(runtime, ctx, args: MaterialContextArgs):
    node_path = current_node_path(ctx) if ctx.node is not None else None
    return runtime.material.get_material_context_view(
        ctx.repo_root,
        node_path=node_path,
        query=args.query,
        scope=args.scope,
        require_committed_source_index=ctx.expected_view_key in _COMMITTED_MATERIAL_CONTEXT_VIEWS,
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


def _agent_metadata(metadata: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"source_path", "cache_path", "output_root", "temp_root"}
        and not value.startswith("/")
    }


def _resource_draft_agent_view(value) -> ResourceDraftAgentView:
    draft = value.draft
    return ResourceDraftAgentView(
        draft_id=draft.draft_id,
        status=draft.status,
        target=draft.target,
        resource_kind=draft.resource_kind,
        title_hint=draft.title_hint,
        resource_key=draft.resource_key,
        summary=draft.summary,
    )


def _resource_acquisition_agent_view(value) -> ResourceAcquisitionAgentView:
    return ResourceAcquisitionAgentView(
        ok=value.ok,
        target=value.target,
        artifact_refs=value.artifact_refs,
        primary_artifact_ref=value.primary_artifact_ref,
        metadata=_agent_metadata(value.metadata),
        summary=value.summary,
        issue_code=value.issue_code,
    )


def _resource_extraction_agent_view(value) -> ResourceExtractionAgentView:
    return ResourceExtractionAgentView(
        ok=value.ok,
        artifact_ref=value.artifact_ref,
        material_refs=value.material_refs,
        primary_material_ref=value.primary_material_ref,
        preview=value.preview,
        metadata=_agent_metadata(value.metadata),
        summary=value.summary,
        issue_code=value.issue_code,
    )


def _get_resource(runtime, ctx: ToolExecutionContext, args: ResourceKeyArgs):
    loaded = runtime.material.resource_library.get_resource(
        ctx.repo_root, resource_key=args.resource_key
    )
    if not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues)
    resource = loaded.value.resource
    return runtime.foundation.ok(
        ResourceAgentView(
            resource_key=resource.resource_key,
            title=resource.title,
            kind=resource.target.kind,
            canonical_locator=resource.target.canonical_locator,
            normalized_entry=resource.normalized_entry,
            source_url=resource.source_url,
            notes=resource.notes,
            summary=loaded.value.summary,
        ),
        warnings=loaded.issues,
    )


def _allocate_resource_draft(
    runtime, ctx: ToolExecutionContext, args: ResourceDraftTargetArgs
):
    allocated = runtime.material.allocate_resource_draft(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not allocated.ok or allocated.value is None:
        return runtime.foundation.fail(allocated.issues)
    return runtime.foundation.ok(
        _resource_draft_agent_view(allocated.value), warnings=allocated.issues
    )


def _get_resource_draft(runtime, ctx: ToolExecutionContext, args: DraftIdArgs):
    loaded = runtime.material.get_resource_draft(ctx.repo_root, draft_id=args.draft_id)
    if not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues)
    return runtime.foundation.ok(
        _resource_draft_agent_view(loaded.value), warnings=loaded.issues
    )


def _abandon_resource_draft(
    runtime, ctx: ToolExecutionContext, args: DraftIdReasonArgs
):
    abandoned = runtime.material.abandon_resource_draft(
        ctx.repo_root, draft_id=args.draft_id, reason=args.reason
    )
    if not abandoned.ok or abandoned.value is None:
        return runtime.foundation.fail(abandoned.issues)
    return runtime.foundation.ok(
        _resource_draft_agent_view(abandoned.value), warnings=abandoned.issues
    )


def _acquire_resource_material(runtime, ctx: ToolExecutionContext, args: ResourceMaterialAcquireArgs):
    draft_id = _active_resource_draft_id(runtime, ctx)
    if not draft_id.ok or draft_id.value is None:
        return draft_id
    acquired = runtime.material.acquire_resource_material(
        ctx.repo_root,
        draft_id=draft_id.value,
        target=args.target,
        preferred_kind=args.preferred_kind,
    )
    if not acquired.ok or acquired.value is None:
        return runtime.foundation.fail(acquired.issues)
    return runtime.foundation.ok(
        _resource_acquisition_agent_view(acquired.value), warnings=acquired.issues
    )


def _extract_resource_artifact(runtime, ctx: ToolExecutionContext, args: ResourceArtifactExtractArgs):
    draft_id = _active_resource_draft_id(runtime, ctx)
    if not draft_id.ok or draft_id.value is None:
        return draft_id
    extracted = runtime.material.extract_resource_artifact(
        ctx.repo_root,
        draft_id=draft_id.value,
        artifact_ref=args.artifact_ref,
        extraction_kind=args.extraction_kind,
    )
    if not extracted.ok or extracted.value is None:
        return runtime.foundation.fail(extracted.issues)
    return runtime.foundation.ok(
        _resource_extraction_agent_view(extracted.value), warnings=extracted.issues
    )


def _import_resource_material(runtime, ctx: ToolExecutionContext, args: ResourceMaterialImportArgs):
    draft_id = _active_resource_draft_id(runtime, ctx)
    if not draft_id.ok or draft_id.value is None:
        return draft_id
    imported = runtime.material.import_resource_material(
        ctx.repo_root,
        draft_id=draft_id.value,
        source_path=args.source_path,
        as_name=args.as_name,
    )
    if not imported.ok or imported.value is None:
        return runtime.foundation.fail(imported.issues)
    return runtime.foundation.ok(
        _resource_acquisition_agent_view(imported.value), warnings=imported.issues
    )


def _normalize_resource_text_material(runtime, ctx: ToolExecutionContext, args: ResourceMaterialNormalizeArgs):
    draft_id = _active_resource_draft_id(runtime, ctx)
    if not draft_id.ok or draft_id.value is None:
        return draft_id
    normalized = runtime.material.normalize_resource_text_material(
        ctx.repo_root,
        draft_id=draft_id.value,
        material_ref=args.material_ref,
    )
    if not normalized.ok or normalized.value is None:
        return runtime.foundation.fail(normalized.issues)
    return runtime.foundation.ok(
        _resource_extraction_agent_view(normalized.value), warnings=normalized.issues
    )


def build_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    curator_roles = {"worker", "admin"}
    return [
        handler_tool(
            name="get_material_context",
            description=(
                "Return the complete compact material context for the current node by default. "
                "This does not enumerate Lean declaration providers. Set scope or query only "
                "when searching beyond assigned node evidence; omit limit to receive all compact matches."
            ),
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
            args_model=ResourceMaterialAcquireArgs,
            capability=ToolCapability.WRITE,
            result_view="resource_acquisition_handles",
            groups={AppGroup.RESOURCE_ACQUISITION},
            roles=curator_roles,
            handler=_acquire_resource_material,
        ),
        handler_tool(
            name="extract_resource_artifact",
            description="Extract readable text or project files from an active resource draft artifact into the resource draft normalized area.",
            args_model=ResourceArtifactExtractArgs,
            capability=ToolCapability.WRITE,
            result_view="resource_extraction_handles",
            groups={AppGroup.RESOURCE_ACQUISITION},
            roles=curator_roles,
            handler=_extract_resource_artifact,
        ),
        handler_tool(
            name="import_resource_material",
            description="Import a local file into the current active resource draft original material area.",
            args_model=ResourceMaterialImportArgs,
            capability=ToolCapability.WRITE,
            result_view="resource_acquisition_handles",
            groups={AppGroup.RESOURCE_ACQUISITION},
            roles=curator_roles,
            handler=_import_resource_material,
        ),
        handler_tool(
            name="normalize_resource_text_material",
            description="Normalize a resource draft material reference into readable text for README summaries, resource notes, and downstream line-range reads.",
            args_model=ResourceMaterialNormalizeArgs,
            capability=ToolCapability.WRITE,
            result_view="resource_extraction_handles",
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
        handler_tool(
            name="get_resource",
            description="Inspect metadata, normalized text availability, and notes for one registered resource by resource key.",
            args_model=ResourceKeyArgs,
            capability=ToolCapability.READ,
            result_view="resource_detail",
            groups={AppGroup.RESOURCE_LIBRARY_READ},
            roles=roles,
            handler=_get_resource,
        ),
        handler_tool(
            name="allocate_resource_draft",
            description="Allocate a resource draft and return its logical files; the active draft is the current working directory.",
            args_model=ResourceDraftTargetArgs,
            capability=ToolCapability.WRITE,
            result_view="resource_draft_detail",
            groups={AppGroup.RESOURCE_DRAFT_LIFECYCLE_WRITE},
            roles=curator_roles,
            handler=_allocate_resource_draft,
        ),
        handler_tool(
            name="get_resource_draft",
            description="Inspect target metadata, status, and logical files for one resource draft.",
            args_model=DraftIdArgs,
            capability=ToolCapability.READ,
            result_view="resource_draft_detail",
            groups={AppGroup.RESOURCE_DRAFT_CURRENT_READ},
            roles=curator_roles,
            handler=_get_resource_draft,
        ),
        direct_tool(
            name="check_resource_draft",
            description="Validate that a resource draft has coherent metadata, readable normalized material, and required README content before local-resource submit.",
            args_model=DraftIdArgs,
            capability=ToolCapability.READ,
            backing_service="material",
            backing_method="check_resource_draft",
            result_view="gate_report",
            groups={AppGroup.RESOURCE_DRAFT_CURRENT_READ},
            roles=curator_roles,
        ),
        handler_tool(
            name="abandon_resource_draft",
            description="Mark a resource draft as abandoned with a reason when it should not be finalized into the resource library.",
            args_model=DraftIdReasonArgs,
            capability=ToolCapability.WRITE,
            result_view="resource_draft_detail",
            groups={AppGroup.RESOURCE_DRAFT_LIFECYCLE_WRITE},
            roles=curator_roles,
            handler=_abandon_resource_draft,
        ),
    ]
