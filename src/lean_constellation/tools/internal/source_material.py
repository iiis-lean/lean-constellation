"""Source corpus, SourceIndex, and material read tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.material import SourceBlockAdjacentLinkView
from lean_constellation.services.material.source_index import SourceBlockRefView
from lean_constellation.services.tool_facade import ToolCapability, ToolExecutionContext, ToolSpec
from lean_constellation.tools.args import (
    FileIndexingStatusArgs,
    FileStatusArgs,
    SourceArtifactExtractArgs,
    NoArgs,
    SourceBlockCreateArgs,
    SourceBlockIdArgs,
    SourceBlockListArgs,
    SourceBlockRefArgs,
    SourceBlockRefRemoveArgs,
    SourceBlockUpdateArgs,
    SourceCorpusCheckArgs,
    SourceCorpusScanArgs,
    SourceIndexOverviewArgs,
    SourceIndexFileListArgs,
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
from lean_constellation.tools.source_index_ownership import authorize_source_index_flow_context


_COMMITTED_SOURCE_INDEX_VIEWS = {
    AppView.ROOT_INTERFACE_PREPARE.value,
    AppView.NATIVE_REPO_COORDINATOR.value,
    AppView.RESOURCE_CURATOR.value,
    AppView.RESOURCE_RECON.value,
    AppView.CONTENT_PLAN.value,
    AppView.STATEMENT_NL_WORKER.value,
    AppView.STATEMENT_NL_REVIEWER.value,
    AppView.STATEMENT_FORMAL_WORKER.value,
    AppView.STATEMENT_FORMAL_REVIEWER.value,
    AppView.PROOF_NL_WORKER.value,
    AppView.PROOF_NL_REVIEWER.value,
    AppView.PROOF_FORMAL_WORKER.value,
    AppView.PROOF_FORMAL_REVIEWER.value,
}


class SourceBlockAgentView(StrictModel):
    block_id: str
    parent_id: str | None = None
    kind: str
    subtype: str | None = None
    title: str
    summary: str
    lifecycle_status: str
    refs: list[SourceBlockRefView] = Field(default_factory=list)
    link_ids: list[str] = Field(default_factory=list)
    child_ids: list[str] = Field(default_factory=list)
    active: bool = True


class SourceLinkAgentView(StrictModel):
    link_id: str
    source_block_id: str
    target_block_id: str | None = None
    target_hint: str | None = None
    link_kind: str
    evidence_ref_ids: list[str] = Field(default_factory=list)


class SourceFileAgentView(StrictModel):
    path: str
    line_count: int
    readable_text: bool
    survey_status: str
    indexing_status: str
    committed: bool
    summary: str | None = None


class SourceIndexAuditAgentView(StrictModel):
    schema_version: int
    status: str
    active_file_scope: list[str] = Field(default_factory=list)
    overview: str | None = None
    root_block_id: str
    blocks: dict[str, SourceBlockAgentView] = Field(default_factory=dict)
    links: dict[str, SourceLinkAgentView] = Field(default_factory=dict)
    files: dict[str, SourceFileAgentView] = Field(default_factory=dict)
    summary: str


class SourceBlockDetailAgentView(StrictModel):
    block: SourceBlockAgentView
    adjacent_links: list[SourceBlockAdjacentLinkView] = Field(default_factory=list)
    summary: str


class SourceBlockMutationReceipt(StrictModel):
    operation: Literal["create", "update"]
    block_id: str
    changed_fields: list[str] = Field(default_factory=list)
    lifecycle_status: str
    parent_id: str | None = None
    summary: str


class SourceBlockRefMutationReceipt(StrictModel):
    operation: Literal["add", "remove"]
    block_id: str
    ref_id: str
    path: str
    start_line: int
    end_line: int
    role: str
    summary: str


class SourceLinkMutationReceipt(StrictModel):
    operation: Literal["create"]
    link_id: str
    source_block_id: str
    target_block_id: str | None = None
    target_hint: str | None = None
    link_kind: str
    evidence_ref_ids: list[str] = Field(default_factory=list)
    summary: str


class SourceFileStatusMutationReceipt(StrictModel):
    operation: Literal["set_survey_status", "set_indexing_status"]
    path: str
    survey_status: str
    indexing_status: str
    summary: str


class SourceCorpusFileAgentView(StrictModel):
    path: str
    readable_text: bool
    line_count: int


class SourceCorpusManifestAgentView(StrictModel):
    logical_corpus_path: str
    overview: str | None = None
    entry_path: str | None = None
    files: list[SourceCorpusFileAgentView] = Field(default_factory=list)
    summary: str


class SourceAcquisitionAgentView(StrictModel):
    ok: bool
    target: str
    artifact_refs: list[str] = Field(default_factory=list)
    primary_artifact_ref: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str
    issue_code: str | None = None


class SourceExtractionAgentView(StrictModel):
    ok: bool
    artifact_ref: str
    material_refs: list[str] = Field(default_factory=list)
    primary_material_ref: str | None = None
    preview: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    summary: str
    issue_code: str | None = None


def _requires_committed_source_index(ctx: ToolExecutionContext) -> bool:
    return ctx.expected_view_key in _COMMITTED_SOURCE_INDEX_VIEWS


def _source_block_agent_view(block) -> SourceBlockAgentView:
    return SourceBlockAgentView(
        block_id=block.block_id,
        parent_id=block.parent_id,
        kind=block.kind,
        subtype=block.subtype,
        title=block.title,
        summary=block.summary,
        lifecycle_status=block.lifecycle_status,
        refs=block.refs,
        link_ids=block.link_ids,
        child_ids=block.child_ids,
        active=block.active,
    )


def _get_source_index(runtime, ctx: ToolExecutionContext, _args: NoArgs):
    loaded = (
        runtime.material.get_committed_source_index(ctx.repo_root)
        if _requires_committed_source_index(ctx)
        else runtime.material.get_source_index(ctx.repo_root)
    )
    if not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues)
    value = loaded.value
    return runtime.foundation.ok(
        SourceIndexAuditAgentView(
            schema_version=value.schema_version,
            status=value.status,
            active_file_scope=value.active_file_scope,
            overview=value.overview,
            root_block_id=value.root_block_id,
            blocks={key: _source_block_agent_view(item) for key, item in value.blocks.items()},
            links={
                key: SourceLinkAgentView(
                    link_id=item.link_id,
                    source_block_id=item.source_block_id,
                    target_block_id=item.target_block_id,
                    target_hint=item.target_hint,
                    link_kind=item.link_kind,
                    evidence_ref_ids=item.evidence_ref_ids,
                )
                for key, item in value.links.items()
            },
            files={
                key: SourceFileAgentView(
                    path=item.path,
                    line_count=item.line_count,
                    readable_text=item.readable_text,
                    survey_status=item.survey_status,
                    indexing_status=item.indexing_status,
                    committed=item.committed,
                    summary=item.summary,
                )
                for key, item in value.files.items()
            },
            summary=value.summary,
        ),
        warnings=loaded.issues,
    )


def _get_source_index_coverage(runtime, ctx: ToolExecutionContext, _args: NoArgs):
    if _requires_committed_source_index(ctx):
        return runtime.material.get_committed_source_index_coverage(ctx.repo_root)
    return runtime.material.get_source_index_coverage(ctx.repo_root)


def _get_source_index_overview(runtime, ctx: ToolExecutionContext, _args: NoArgs):
    return runtime.material.get_source_index_overview(
        ctx.repo_root,
        require_committed=_requires_committed_source_index(ctx),
    )


def _list_source_index_files(
    runtime, ctx: ToolExecutionContext, args: SourceIndexFileListArgs
):
    return runtime.material.list_source_index_files(
        ctx.repo_root,
        status=args.status,
        require_committed=_requires_committed_source_index(ctx),
    )


def _list_source_blocks(runtime, ctx: ToolExecutionContext, args: SourceBlockListArgs):
    return runtime.material.list_source_blocks(
        ctx.repo_root,
        query=args.query,
        kind=args.kind,
        subtype=args.subtype,
        path=args.path,
        limit=args.limit,
        require_committed=_requires_committed_source_index(ctx),
    )


def _get_source_block(runtime, ctx: ToolExecutionContext, args: SourceBlockIdArgs):
    loaded = runtime.material.get_source_block(
        ctx.repo_root,
        block_id=args.block_id,
        require_committed=_requires_committed_source_index(ctx),
    )
    if not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues)
    return runtime.foundation.ok(
        SourceBlockDetailAgentView(
            block=_source_block_agent_view(loaded.value.block),
            adjacent_links=loaded.value.adjacent_links,
            summary=loaded.value.summary,
        ),
        warnings=loaded.issues,
    )


def _get_source_index_update_context(runtime, ctx: ToolExecutionContext, _args: NoArgs):
    authorized = authorize_source_index_flow_context(
        runtime,
        ctx,
        allowed_step_types={"source_index_builder_agent_step", "source_index_reviewer_agent_step"},
        allowed_actor_roles={"worker", "reviewer", "admin"},
    )
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    flow = runtime.get_flow(ctx.runtime.flow_id)
    flow_input = getattr(flow, "input", None)
    if getattr(flow_input, "repo_root", None) is not None and str(flow_input.repo_root) != str(ctx.repo_root):
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_repo_context_mismatch",
                "The owning SourceIndex Flow is bound to a different repository.",
            )
        )
    context = runtime.material.get_source_index_update_context(ctx.repo_root)
    if not context.ok or context.value is None:
        return runtime.foundation.fail(context.issues)
    state = getattr(flow, "state", None)
    return runtime.foundation.ok(
        {
            "run_objective": getattr(flow_input, "run_objective", None),
            "target_proof_availability": getattr(getattr(flow_input, "target_proof_availability", None), "value", getattr(flow_input, "target_proof_availability", None)),
            "work_mode": getattr(getattr(flow_input, "work_mode", None), "value", getattr(flow_input, "work_mode", None)),
            "source_scope": (
                flow_input.source_scope.model_dump(mode="json")
                if getattr(flow_input, "source_scope", None) is not None
                else None
            ),
            "start_reason": getattr(flow_input, "start_reason", None),
            "review_round": getattr(state, "review_round", 0),
            "reviewer_feedback": getattr(state, "latest_reviewer_feedback", None),
            **context.value.model_dump(mode="json"),
        }
    )


def _source_index_write_handler(method_name: str):
    def handler(runtime, ctx: ToolExecutionContext, args):  # noqa: ANN001
        authorized = authorize_source_index_flow_context(
            runtime,
            ctx,
            allowed_step_types={"source_index_builder_agent_step"},
            allowed_actor_roles={"worker", "admin"},
        )
        if not authorized.ok:
            return runtime.foundation.fail(authorized.issues)
        kwargs = args.model_dump(exclude_unset=True)
        return getattr(runtime.material, method_name)(ctx.repo_root, **kwargs)

    return handler


def _authorize_source_index_write(runtime, ctx):
    return authorize_source_index_flow_context(
        runtime,
        ctx,
        allowed_step_types={"source_index_builder_agent_step"},
        allowed_actor_roles={"worker", "admin"},
    )


def _create_source_block(runtime, ctx: ToolExecutionContext, args: SourceBlockCreateArgs):
    authorized = _authorize_source_index_write(runtime, ctx)
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    created = runtime.material.create_source_block(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not created.ok or created.value is None:
        return runtime.foundation.fail(created.issues)
    return runtime.foundation.ok(
        SourceBlockMutationReceipt(
            operation="create",
            block_id=created.value.block_id,
            changed_fields=["parent_id", "kind", "subtype", "title", "summary"],
            lifecycle_status=created.value.lifecycle_status,
            parent_id=created.value.parent_id,
            summary="Created SourceIndex block.",
        ),
        warnings=created.issues,
    )


def _update_source_block(runtime, ctx: ToolExecutionContext, args: SourceBlockUpdateArgs):
    authorized = _authorize_source_index_write(runtime, ctx)
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    kwargs = args.model_dump(exclude_unset=True)
    updated = runtime.material.update_source_block(ctx.repo_root, **kwargs)
    if not updated.ok or updated.value is None:
        return runtime.foundation.fail(updated.issues)
    changed_fields = sorted(
        key for key, value in kwargs.items() if key != "block_id" and value is not None
    )
    return runtime.foundation.ok(
        SourceBlockMutationReceipt(
            operation="update",
            block_id=updated.value.block_id,
            changed_fields=changed_fields,
            lifecycle_status=updated.value.lifecycle_status,
            parent_id=updated.value.parent_id,
            summary="Updated SourceIndex block fields.",
        ),
        warnings=updated.issues,
    )


def _add_source_block_ref(runtime, ctx: ToolExecutionContext, args: SourceBlockRefArgs):
    authorized = _authorize_source_index_write(runtime, ctx)
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    updated = runtime.material.add_source_block_ref(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not updated.ok or updated.value is None:
        return runtime.foundation.fail(updated.issues)
    ref = next(
        (
            item
            for item in reversed(updated.value.refs)
            if item.path == args.path
            and item.start_line == args.start_line
            and item.end_line == args.end_line
            and item.role == args.role
        ),
        None,
    )
    if ref is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_ref_receipt_missing",
                "Added SourceIndex ref was not present in the updated block.",
                object_ref=args.block_id,
            )
        )
    return runtime.foundation.ok(
        SourceBlockRefMutationReceipt(
            operation="add",
            block_id=args.block_id,
            ref_id=ref.ref_id,
            path=ref.path,
            start_line=ref.start_line,
            end_line=ref.end_line,
            role=ref.role,
            summary="Added SourceIndex block ref.",
        ),
        warnings=updated.issues,
    )


def _remove_source_block_ref(runtime, ctx: ToolExecutionContext, args: SourceBlockRefRemoveArgs):
    authorized = _authorize_source_index_write(runtime, ctx)
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    before = runtime.material.get_source_block(
        ctx.repo_root, block_id=args.block_id, require_committed=False
    )
    if not before.ok or before.value is None:
        return runtime.foundation.fail(before.issues)
    ref = next((item for item in before.value.block.refs if item.ref_id == args.ref_id), None)
    if ref is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_ref_missing",
                f"Source ref not found: {args.ref_id}",
                object_ref=args.block_id,
            )
        )
    updated = runtime.material.remove_source_block_ref(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not updated.ok:
        return runtime.foundation.fail(updated.issues)
    return runtime.foundation.ok(
        SourceBlockRefMutationReceipt(
            operation="remove",
            block_id=args.block_id,
            ref_id=ref.ref_id,
            path=ref.path,
            start_line=ref.start_line,
            end_line=ref.end_line,
            role=ref.role,
            summary="Removed SourceIndex block ref.",
        ),
        warnings=[*before.issues, *updated.issues],
    )


def _create_source_link(runtime, ctx: ToolExecutionContext, args: SourceLinkCreateArgs):
    authorized = _authorize_source_index_write(runtime, ctx)
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    created = runtime.material.create_source_link(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not created.ok or created.value is None:
        return runtime.foundation.fail(created.issues)
    return runtime.foundation.ok(
        SourceLinkMutationReceipt(
            operation="create",
            link_id=created.value.link_id,
            source_block_id=created.value.source_block_id,
            target_block_id=created.value.target_block_id,
            target_hint=created.value.target_hint,
            link_kind=created.value.link_kind,
            evidence_ref_ids=created.value.evidence_ref_ids,
            summary="Created SourceIndex link.",
        ),
        warnings=created.issues,
    )


def _set_file_survey_status(runtime, ctx: ToolExecutionContext, args: FileStatusArgs):
    authorized = _authorize_source_index_write(runtime, ctx)
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    updated = runtime.material.set_file_survey_status(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not updated.ok or updated.value is None:
        return runtime.foundation.fail(updated.issues)
    return runtime.foundation.ok(
        SourceFileStatusMutationReceipt(
            operation="set_survey_status",
            path=updated.value.path,
            survey_status=updated.value.survey_status,
            indexing_status=updated.value.indexing_status,
            summary="Updated SourceIndex file survey status.",
        ),
        warnings=updated.issues,
    )


def _set_file_indexing_status(
    runtime, ctx: ToolExecutionContext, args: FileIndexingStatusArgs
):
    authorized = _authorize_source_index_write(runtime, ctx)
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    updated = runtime.material.set_file_indexing_status(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not updated.ok or updated.value is None:
        return runtime.foundation.fail(updated.issues)
    return runtime.foundation.ok(
        SourceFileStatusMutationReceipt(
            operation="set_indexing_status",
            path=updated.value.path,
            survey_status=updated.value.survey_status,
            indexing_status=updated.value.indexing_status,
            summary="Updated SourceIndex file indexing status.",
        ),
        warnings=updated.issues,
    )


def _validate_source_index(runtime, ctx: ToolExecutionContext, _args: NoArgs):
    authorized = authorize_source_index_flow_context(
        runtime,
        ctx,
        allowed_step_types={"source_index_builder_agent_step", "source_index_reviewer_agent_step"},
        allowed_actor_roles={"worker", "reviewer", "admin"},
    )
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    return runtime.material.validate_source_index(ctx.repo_root)


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
            groups={AppGroup.SOURCE_INDEX_FULL_AUDIT_READ},
            roles=read_roles,
            handler=_get_source_index,
        ),
        handler_tool(
            name="get_source_index_overview",
            description="Read compact SourceIndex status, overview, and aggregate counts.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="source_index_overview",
            groups={AppGroup.SOURCE_INDEX_NAVIGATION_READ},
            roles=read_roles,
            handler=_get_source_index_overview,
        ),
        handler_tool(
            name="list_source_index_files",
            description="List compact SourceIndex file lifecycle entries, optionally filtered by status.",
            args_model=SourceIndexFileListArgs,
            capability=ToolCapability.READ,
            result_view="source_index_file_list",
            groups={AppGroup.SOURCE_INDEX_NAVIGATION_READ},
            roles=read_roles,
            handler=_list_source_index_files,
        ),
        handler_tool(
            name="list_source_blocks",
            description="List compact SourceIndex semantic blocks. Use get_source_block for one block's refs and links.",
            args_model=SourceBlockListArgs,
            capability=ToolCapability.READ,
            result_view="source_block_list",
            groups={AppGroup.SOURCE_INDEX_NAVIGATION_READ},
            roles=read_roles,
            handler=_list_source_blocks,
        ),
        handler_tool(
            name="get_source_block",
            description="Read one SourceIndex block with its source refs and adjacent semantic links.",
            args_model=SourceBlockIdArgs,
            capability=ToolCapability.READ,
            result_view="source_block_detail",
            groups={AppGroup.SOURCE_INDEX_NAVIGATION_READ},
            roles=read_roles,
            handler=_get_source_block,
        ),
        handler_tool(
            name="set_source_index_overview",
            description="Set the draft SourceIndex overview.",
            args_model=SourceIndexOverviewArgs,
            capability=ToolCapability.WRITE,
            result_view="source_index_overview_mutation_receipt",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_source_index_write_handler("set_source_index_overview"),
        ),
        handler_tool(
            name="create_source_block",
            description="Create a semantic block in the draft SourceIndex.",
            args_model=SourceBlockCreateArgs,
            capability=ToolCapability.WRITE,
            result_view="source_block_mutation_receipt",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_create_source_block,
        ),
        handler_tool(
            name="update_source_block",
            description="Update title, summary, kind, or subtype of a draft SourceIndex block.",
            args_model=SourceBlockUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="source_block_mutation_receipt",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_update_source_block,
        ),
        handler_tool(
            name="add_source_block_ref",
            description="Attach a source range to a draft SourceIndex block.",
            args_model=SourceBlockRefArgs,
            capability=ToolCapability.WRITE,
            result_view="source_block_ref_mutation_receipt",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_add_source_block_ref,
        ),
        handler_tool(
            name="remove_source_block_ref",
            description="Remove a draft-local source range ref from a SourceIndex block.",
            args_model=SourceBlockRefRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="source_block_ref_mutation_receipt",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_remove_source_block_ref,
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
            result_view="source_link_mutation_receipt",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_create_source_link,
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
            result_view="source_file_status_mutation_receipt",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_set_file_survey_status,
        ),
        handler_tool(
            name="set_file_indexing_status",
            description="Set the SourceIndex indexing status for a source corpus file.",
            args_model=FileIndexingStatusArgs,
            capability=ToolCapability.WRITE,
            result_view="source_file_status_mutation_receipt",
            groups={AppGroup.SOURCE_INDEX_DRAFT_WRITE},
            roles=builder_roles,
            handler=_set_file_indexing_status,
        ),
        handler_tool(
            name="get_source_index_update_context",
            description="Read the active file scope, committed baseline split, and current delta for the owning SourceIndex build Flow.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="source_index_update_context",
            groups={AppGroup.SOURCE_INDEX_DRAFT_CONTEXT_READ},
            roles={"worker", "reviewer", "admin"},
            handler=_get_source_index_update_context,
        ),
        handler_tool(
            name="validate_source_index",
            description="Validate the draft SourceIndex without submitting it.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={AppGroup.SOURCE_INDEX_DRAFT_CONTEXT_READ},
            roles=read_roles,
            handler=_validate_source_index,
        ),
        handler_tool(
            name="get_source_index_coverage",
            description="Read SourceIndex coverage status for source files and semantic blocks. Committed SourceIndex views require committed state.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="source_index_coverage",
            groups={AppGroup.SOURCE_INDEX_NAVIGATION_READ},
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


def _scan_source_corpus(runtime, ctx: ToolExecutionContext, args: SourceCorpusScanArgs):
    scanned = runtime.material.scan_source_corpus(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not scanned.ok or scanned.value is None:
        return runtime.foundation.fail(scanned.issues)
    return runtime.foundation.ok(
        SourceCorpusManifestAgentView(
            logical_corpus_path=scanned.value.relpath,
            overview=scanned.value.overview,
            entry_path=scanned.value.entry_path,
            files=[
                SourceCorpusFileAgentView(
                    path=item.path,
                    readable_text=item.readable_text,
                    line_count=item.line_count,
                )
                for item in scanned.value.files
            ],
            summary=scanned.value.summary,
        ),
        warnings=scanned.issues,
    )


def _agent_metadata(metadata: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"source_path", "cache_path", "output_root", "temp_root"}
        and not value.startswith("/")
    }


def _source_acquisition_agent_view(value) -> SourceAcquisitionAgentView:
    return SourceAcquisitionAgentView(
        ok=value.ok,
        target=value.target,
        artifact_refs=value.artifact_refs,
        primary_artifact_ref=value.primary_artifact_ref,
        metadata=_agent_metadata(value.metadata),
        summary=value.summary,
        issue_code=value.issue_code,
    )


def _source_extraction_agent_view(value) -> SourceExtractionAgentView:
    return SourceExtractionAgentView(
        ok=value.ok,
        artifact_ref=value.artifact_ref,
        material_refs=value.material_refs,
        primary_material_ref=value.primary_material_ref,
        preview=value.preview,
        metadata=_agent_metadata(value.metadata),
        summary=value.summary,
        issue_code=value.issue_code,
    )


def _acquire_source_material(runtime, ctx, args: SourceMaterialAcquireArgs):
    acquired = runtime.material.acquire_source_material(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not acquired.ok or acquired.value is None:
        return runtime.foundation.fail(acquired.issues)
    return runtime.foundation.ok(
        _source_acquisition_agent_view(acquired.value),
        warnings=acquired.issues,
    )


def _import_source_material(runtime, ctx, args: SourceMaterialImportArgs):
    imported = runtime.material.import_source_material(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not imported.ok or imported.value is None:
        return runtime.foundation.fail(imported.issues)
    return runtime.foundation.ok(
        _source_acquisition_agent_view(imported.value),
        warnings=imported.issues,
    )


def _extract_source_artifact(runtime, ctx, args: SourceArtifactExtractArgs):
    extracted = runtime.material.extract_source_artifact(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not extracted.ok or extracted.value is None:
        return runtime.foundation.fail(extracted.issues)
    return runtime.foundation.ok(
        _source_extraction_agent_view(extracted.value),
        warnings=extracted.issues,
    )


def _normalize_source_text_material(runtime, ctx, args: SourceMaterialNormalizeArgs):
    normalized = runtime.material.normalize_source_text_material(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not normalized.ok or normalized.value is None:
        return runtime.foundation.fail(normalized.issues)
    return runtime.foundation.ok(
        _source_extraction_agent_view(normalized.value),
        warnings=normalized.issues,
    )


def build_source_corpus_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "worker", "reviewer", "admin"}
    return [
        handler_tool(
            name="scan_source_corpus",
            description="Scan the current source corpus and return relative paths plus readability and line counts.",
            args_model=SourceCorpusScanArgs,
            capability=ToolCapability.READ,
            result_view="source_corpus_manifest_overview",
            groups={AppGroup.SOURCE_CORPUS_READ},
            roles=roles,
            handler=_scan_source_corpus,
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
        handler_tool(
            name="acquire_source_material",
            description="Acquire raw source material into the source draft area.",
            args_model=SourceMaterialAcquireArgs,
            capability=ToolCapability.WRITE,
            result_view="source_acquisition_handles",
            groups={AppGroup.SOURCE_ACQUISITION},
            roles={"worker", "admin"},
            handler=_acquire_source_material,
        ),
        handler_tool(
            name="extract_source_artifact",
            description="Extract readable text from an acquired source artifact.",
            args_model=SourceArtifactExtractArgs,
            capability=ToolCapability.WRITE,
            result_view="source_extraction_handles",
            groups={AppGroup.SOURCE_ACQUISITION},
            roles={"worker", "admin"},
            handler=_extract_source_artifact,
        ),
        handler_tool(
            name="import_source_material",
            description="Import a local source file or directory into the source draft area.",
            args_model=SourceMaterialImportArgs,
            capability=ToolCapability.WRITE,
            result_view="source_acquisition_handles",
            groups={AppGroup.SOURCE_ACQUISITION},
            roles={"worker", "admin"},
            handler=_import_source_material,
        ),
        handler_tool(
            name="normalize_source_text_material",
            description="Normalize a source draft material reference into readable text.",
            args_model=SourceMaterialNormalizeArgs,
            capability=ToolCapability.WRITE,
            result_view="source_extraction_handles",
            groups={AppGroup.SOURCE_ACQUISITION},
            roles={"worker", "admin"},
            handler=_normalize_source_text_material,
        ),
    ]


def build_tool_specs() -> list[ToolSpec]:
    return [*build_source_index_tool_specs(), *build_source_corpus_tool_specs(), *build_material_tool_specs()]
