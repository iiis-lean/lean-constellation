"""Submit tool handlers that build typed ARK submissions after service gates."""

from __future__ import annotations

from urllib.parse import urlparse
from typing import Any

from agent_runtime_kit.flow.models import BaseSubmission, ChildFlowDispatchSubmission, FlowRequest

from lean_constellation.flows.common.flow_requests import (
    build_content_node_task_request,
    build_decl_round_request,
    build_preparation_recon_request,
    build_resource_curation_request,
    node_scope_id,
    repo_scope_id,
)
from lean_constellation.flows.common.submissions import new_submission_id, submission_agent_id
from lean_constellation.flows.content_node_task.decl_round.submissions import (
    DeclRoundDispatchSubmission,
    DeclStageReviewSubmittedSubmission,
    DeclStageWorkerBlockedSubmission,
    DeclStageWorkerCompletedSubmission,
)
from lean_constellation.flows.content_node_task.preparation.mathlib_recon.submissions import MathlibReconCompletedSubmission
from lean_constellation.flows.content_node_task.preparation.node_dir_recon.submissions import NodeDirDependencyReconCompletedSubmission
from lean_constellation.flows.content_node_task.preparation.resource_recon.submissions import (
    ResourceReconBlockedSubmission,
    ResourceReconCompletedSubmission,
    ResourceReconRequestResourceSubmission,
)
from lean_constellation.flows.content_node_task.submissions import (
    ContentNodeBlockedSubmission,
    ContentNodeFailedSubmission,
    ContentNodeReadySubmission,
    ContentPreparationDispatchSubmission,
    ContentResourceRequestSubmission,
)
from lean_constellation.flows.coordinator.submissions import (
    CoordinatorContentTasksSubmission,
    CoordinatorRepoReadySubmission,
    CoordinatorRepoRequirementSubmission,
    CoordinatorResourceRequestSubmission,
)
from lean_constellation.flows.repo_lifecycle.submissions import (
    AdapterCatalogBlockedSubmission,
    AdapterCatalogReadySubmission,
    RepoFormatAdapterChoiceSubmission,
    RepoFormatNativeChoiceSubmission,
    RootInterfacePrepareReadySubmission,
    SourceCorpusBlockedSubmission,
    SourceCorpusPreparedSubmission,
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)
from lean_constellation.flows.resource_request.submissions import (
    ExternalRepoRequiredSubmission,
    LocalResourceCreatedSubmission,
    ResourceDuplicateSubmission,
    ResourceRejectedSubmission,
)
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.tool_facade import PreparedSubmissionView, ToolExecutionContext
from lean_constellation.tools.submit_args import (
    SubmitAdapterCatalogBlockedArgs,
    SubmitAdapterCatalogReadyArgs,
    SubmitAdapterRepoChoiceArgs,
    SubmitContentNodeBlockedArgs,
    SubmitContentNodeFailedArgs,
    SubmitContentNodeReadyArgs,
    SubmitContentNodeTasksArgs,
    SubmitContentPreparationReconArgs,
    SubmitCurrentDeclRoundArgs,
    SubmitExternalRepoRequiredArgs,
    SubmitLocalResourceCreatedArgs,
    SubmitMathlibReconCompletedArgs,
    SubmitNativeRepoChoiceArgs,
    SubmitNodeDirDependencyReconCompletedArgs,
    SubmitRepoReadyArgs,
    SubmitRepoRequirementArgs,
    SubmitResourceDuplicateArgs,
    SubmitResourceReconBlockedArgs,
    SubmitResourceReconCompletedArgs,
    SubmitResourceRejectedArgs,
    SubmitResourceRequestArgs,
    SubmitRootInterfacePrepareReadyArgs,
    SubmitSourceCorpusBlockedArgs,
    SubmitSourceCorpusPreparedArgs,
    SubmitSourceIndexBuilderRoundArgs,
    SubmitSourceIndexReviewRoundArgs,
    SubmitStageReviewArgs,
    SubmitStageWorkerBlockedArgs,
    SubmitStageWorkerCompletedArgs,
)


def _prepared(runtime: Any, submission: BaseSubmission, *, agent_view: dict[str, Any] | None = None) -> ServiceResult[PreparedSubmissionView]:
    return runtime.foundation.ok(
        PreparedSubmissionView(
            submission=submission,
            summary=submission.summary or f"{submission.tool_name} accepted.",
            agent_view=agent_view or {},
        )
    )


def _fail(runtime: Any, kind: str, message: str, *, field: str | None = None) -> ServiceResult[Any]:
    return runtime.foundation.fail(runtime.foundation.issue(kind, message, field=field))


def _summary(text: str | None, *, fallback: str) -> str:
    stripped = (text or "").strip()
    return stripped or fallback


def _base_kwargs(ctx: ToolExecutionContext, *, tool_name: str, summary: str | None = None) -> dict[str, Any]:
    return {
        "submission_id": new_submission_id(),
        "tool_name": tool_name,
        "submitted_by_agent_id": submission_agent_id(ctx),
        "summary": summary,
        "repo_key": ctx.repo.repo_key,
        "node_path": ctx.node.node_path if ctx.node else None,
    }


def _dispatch_kwargs(
    ctx: ToolExecutionContext,
    *,
    tool_name: str,
    requests: list[FlowRequest],
    summary: str | None = None,
    continuation: str = "wait_for_callback",
) -> dict[str, Any]:
    return {
        **_base_kwargs(ctx, tool_name=tool_name, summary=summary),
        "requests": requests,
        "continuation": continuation,
    }


def _preparation_already_used(runtime: Any, ctx: ToolExecutionContext, recon_kind: str) -> ServiceResult[None]:
    flow_id = ctx.runtime.flow_id
    if not flow_id:
        return runtime.foundation.ok(None)
    flow_service = getattr(runtime.ark, "flow_service", None)
    if flow_service is None:
        return runtime.foundation.ok(None)
    try:
        flow = flow_service.get_flow(flow_id)
    except Exception:
        return runtime.foundation.ok(None)
    if getattr(flow, "flow_type", None) != "content_node_task":
        return runtime.foundation.ok(None)
    used = set(getattr(getattr(flow, "state", None), "used_preparation_kinds", []) or [])
    if recon_kind in used:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "preparation_recon_already_used",
                f"{recon_kind} preparation recon has already been dispatched in this content node task.",
                field="recon_kind",
                current=recon_kind,
            )
        )
    return runtime.foundation.ok(None)


def _require_node(runtime: Any, ctx: ToolExecutionContext) -> ServiceResult[str]:
    if ctx.node is None:
        return _fail(runtime, "node_context_missing", "This submit tool requires a current Content node.")
    return runtime.foundation.ok(ctx.node.node_path)


def _require_stage(runtime: Any, ctx: ToolExecutionContext) -> ServiceResult[tuple[str, str]]:
    if ctx.decl_stage is None or not ctx.decl_stage.stage or not ctx.decl_stage.round_id:
        return _fail(runtime, "decl_stage_context_missing", "This submit tool requires current decl stage and round context.")
    return runtime.foundation.ok((ctx.decl_stage.stage, ctx.decl_stage.round_id))


def _gate_or_fail(runtime: Any, gate: GateReport | None) -> ServiceResult[None]:
    if gate is None:
        return _fail(runtime, "gate_missing", "Required submit gate did not return a report.")
    if not gate.passed:
        return runtime.foundation.fail(gate.issues)
    return runtime.foundation.ok(None)


def _is_github_repo_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == "github.com":
        return bool(parsed.path.strip("/"))
    if value.startswith("git@github.com:"):
        return bool(value.removeprefix("git@github.com:").strip("/"))
    return False


def _resource_request(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitResourceRequestArgs,
    *,
    submission_cls: type[ChildFlowDispatchSubmission],
    tool_name: str = "submit_resource_request",
) -> ServiceResult[PreparedSubmissionView]:
    flow_input = runtime.material.submit_resource_request(
        ctx,
        target_kind=args.target_kind,
        target=args.target,
        arxiv_version=args.arxiv_version,
    )
    if not flow_input.ok or flow_input.value is None:
        return runtime.foundation.fail(flow_input.issues)
    request = build_resource_curation_request(
        scope_id=repo_scope_id(ctx.repo.repo_key, ctx.runtime.scope_id),
        target_kind=args.target_kind,
        target=args.target,
        arxiv_version=args.arxiv_version,
        requested_by=ctx.actor.role,
        context_summary=args.context_summary,
        repo_key=ctx.repo.repo_key,
        repo_root=str(ctx.repo_root),
        node_path=ctx.node.node_path if ctx.node else None,
    )
    submission = submission_cls(
        **_dispatch_kwargs(ctx, tool_name=tool_name, requests=[request], summary=args.summary),
        target_kind=args.target_kind,
        target=args.target,
        arxiv_version=args.arxiv_version,
        context_summary=args.context_summary,
    )
    return _prepared(runtime, submission, agent_view={"child_flow": flow_input.value.model_dump(mode="json")})


def submit_adapter_repo_choice(runtime: Any, ctx: ToolExecutionContext, args: SubmitAdapterRepoChoiceArgs) -> ServiceResult[PreparedSubmissionView]:
    upstream_url = args.upstream_github_url.strip()
    if not upstream_url:
        return _fail(runtime, "upstream_github_url_required", "Adapter repo choice requires upstream_github_url.", field="upstream_github_url")
    if not _is_github_repo_url(upstream_url):
        return _fail(runtime, "upstream_github_url_invalid", "Adapter repo choice requires a GitHub repository URL.", field="upstream_github_url")
    return _prepared(
        runtime,
        RepoFormatAdapterChoiceSubmission(
            **_base_kwargs(ctx, tool_name="submit_adapter_repo_choice", summary=args.summary),
            upstream_github_url=upstream_url,
            upstream_revision=args.upstream_revision,
            upstream_subdir=args.upstream_subdir,
            adapter_repo_name=args.adapter_repo_name,
        ),
    )


def submit_native_repo_choice(runtime: Any, ctx: ToolExecutionContext, args: SubmitNativeRepoChoiceArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        RepoFormatNativeChoiceSubmission(
            **_base_kwargs(ctx, tool_name="submit_native_repo_choice", summary=args.summary),
            native_repo_name=args.native_repo_name,
            source_corpus_mode=args.source_corpus_mode,
        ),
    )


def submit_source_corpus_prepared(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceCorpusPreparedArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.material.submit_source_corpus_prepared(
        ctx.repo_root,
        entry_path=args.entry_path,
        overview=args.overview,
        preparation_summary=args.preparation_summary,
        relpath=args.relpath,
        ctx=ctx,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        SourceCorpusPreparedSubmission(
            **_base_kwargs(ctx, tool_name="submit_source_corpus_prepared", summary=args.summary),
            relpath=args.relpath,
            entry_path=args.entry_path,
            overview=args.overview,
            preparation_summary=args.preparation_summary,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_source_corpus_blocked(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceCorpusBlockedArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.material.submit_source_corpus_blocked(
        ctx.repo_root,
        reason=args.reason,
        attempted_targets=args.attempted_targets,
        missing_materials=args.missing_materials,
        suggested_next_action=args.suggested_next_action,
        ctx=ctx,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        SourceCorpusBlockedSubmission(
            **_base_kwargs(ctx, tool_name="submit_source_corpus_blocked", summary=gate.value.summary),
            reason=args.reason,
            attempted_targets=args.attempted_targets,
            missing_materials=args.missing_materials,
            suggested_next_action=args.suggested_next_action,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_source_index_builder_round(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceIndexBuilderRoundArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.material.submit_source_index_builder_round(ctx.repo_root, summary=args.summary, ctx=ctx)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        SourceIndexBuilderRoundSubmission(
            **_base_kwargs(ctx, tool_name="submit_source_index_builder_round", summary=args.summary),
            validation_summary=getattr(getattr(gate.value, "validation", None), "summary", None),
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_source_index_review_round(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceIndexReviewRoundArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.material.submit_source_index_review_round(ctx.repo_root, approved=args.approved, summary=args.summary, feedback=args.feedback, ctx=ctx)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        SourceIndexReviewerRoundSubmission(
            **_base_kwargs(ctx, tool_name="submit_source_index_review_round", summary=args.summary),
            approved=args.approved,
            feedback=args.feedback,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_root_interface_prepare_ready(runtime: Any, ctx: ToolExecutionContext, args: SubmitRootInterfacePrepareReadyArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.node.interface.submit_root_interface_prepare_ready(ctx.repo_root, summary=args.summary, ctx=ctx)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        RootInterfacePrepareReadySubmission(**_base_kwargs(ctx, tool_name="submit_root_interface_prepare_ready", summary=args.summary)),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_adapter_catalog_ready(runtime: Any, ctx: ToolExecutionContext, args: SubmitAdapterCatalogReadyArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.adapter.submit_adapter_catalog_ready(ctx.repo_root, summary=args.summary, ctx=ctx)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        AdapterCatalogReadySubmission(**_base_kwargs(ctx, tool_name="submit_adapter_catalog_ready", summary=args.summary)),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_adapter_catalog_blocked(runtime: Any, ctx: ToolExecutionContext, args: SubmitAdapterCatalogBlockedArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.adapter.submit_adapter_catalog_blocked(
        ctx.repo_root,
        reason=args.reason,
        missing_interfaces=args.missing_interfaces,
        evidence_summary=args.evidence_summary,
        suggested_next_action=args.suggested_next_action,
        ctx=ctx,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        AdapterCatalogBlockedSubmission(
            **_base_kwargs(ctx, tool_name="submit_adapter_catalog_blocked", summary=gate.value.summary),
            reason=args.reason,
            missing_interfaces=args.missing_interfaces,
            evidence_summary=args.evidence_summary,
            suggested_next_action=args.suggested_next_action,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_resource_request(runtime: Any, ctx: ToolExecutionContext, args: SubmitResourceRequestArgs) -> ServiceResult[PreparedSubmissionView]:
    if ctx.actor.role == "coordinator" or (ctx.actor.agent_type and "Coordinator" in ctx.actor.agent_type):
        cls = CoordinatorResourceRequestSubmission
    elif ctx.actor.agent_type in {"ResourceReconAgent", "resource_recon"} or ctx.endpoint_view_key == "resource_recon":
        cls = ResourceReconRequestResourceSubmission
    else:
        cls = ContentResourceRequestSubmission
    return _resource_request(runtime, ctx, args, submission_cls=cls)


def _resource_flow_input(runtime: Any, ctx: ToolExecutionContext, target_kind: str, target: str, arxiv_version: str | None) -> ServiceResult[Any]:
    return runtime.material.submit_resource_request(ctx, target_kind=target_kind, target=target, arxiv_version=arxiv_version)


def submit_resource_duplicate(runtime: Any, ctx: ToolExecutionContext, args: SubmitResourceDuplicateArgs) -> ServiceResult[PreparedSubmissionView]:
    flow_input = _resource_flow_input(runtime, ctx, args.target_kind, args.target, args.arxiv_version)
    if not flow_input.ok or flow_input.value is None:
        return runtime.foundation.fail(flow_input.issues)
    gate = runtime.material.submit_resource_duplicate(
        ctx.repo_root,
        flow_input=flow_input.value,
        existing_kind=args.existing_kind,
        duplicate_reason=args.duplicate_reason,
        existing_resource_key=args.existing_resource_key,
        existing_source_path=args.existing_source_path,
        preview=args.preview,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        ResourceDuplicateSubmission(
            **_base_kwargs(ctx, tool_name="submit_resource_duplicate", summary=gate.value.summary),
            target_kind=args.target_kind,
            target=args.target,
            arxiv_version=args.arxiv_version,
            existing_kind=args.existing_kind,
            duplicate_reason=args.duplicate_reason,
            existing_resource_key=args.existing_resource_key,
            existing_source_path=args.existing_source_path,
            preview=args.preview,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_local_resource_created(runtime: Any, ctx: ToolExecutionContext, args: SubmitLocalResourceCreatedArgs) -> ServiceResult[PreparedSubmissionView]:
    flow_input = _resource_flow_input(runtime, ctx, args.target_kind, args.target, args.arxiv_version)
    if not flow_input.ok or flow_input.value is None:
        return runtime.foundation.fail(flow_input.issues)
    gate = runtime.material.submit_local_resource_created(ctx.repo_root, flow_input=flow_input.value, draft_id=args.draft_id, summary=args.summary)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        LocalResourceCreatedSubmission(
            **_base_kwargs(ctx, tool_name="submit_local_resource_created", summary=args.summary),
            target_kind=args.target_kind,
            target=args.target,
            arxiv_version=args.arxiv_version,
            draft_id=args.draft_id,
            resource_key=gate.value.resource_key,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_external_repo_required(runtime: Any, ctx: ToolExecutionContext, args: SubmitExternalRepoRequiredArgs) -> ServiceResult[PreparedSubmissionView]:
    flow_input = _resource_flow_input(runtime, ctx, args.target_kind, args.target, args.arxiv_version)
    if not flow_input.ok or flow_input.value is None:
        return runtime.foundation.fail(flow_input.issues)
    gate = runtime.material.submit_external_repo_required(
        ctx.repo_root,
        flow_input=flow_input.value,
        reason=args.reason,
        source_description=args.source_description,
        suggested_repo_name=args.suggested_repo_name,
        required_interfaces_hint=args.required_interfaces_hint,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        ExternalRepoRequiredSubmission(
            **_base_kwargs(ctx, tool_name="submit_external_repo_required", summary=gate.value.summary),
            target_kind=args.target_kind,
            target=args.target,
            arxiv_version=args.arxiv_version,
            reason=args.reason,
            source_description=args.source_description,
            suggested_repo_name=args.suggested_repo_name,
            required_interfaces_hint=args.required_interfaces_hint,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_resource_rejected(runtime: Any, ctx: ToolExecutionContext, args: SubmitResourceRejectedArgs) -> ServiceResult[PreparedSubmissionView]:
    flow_input = _resource_flow_input(runtime, ctx, args.target_kind, args.target, args.arxiv_version)
    if not flow_input.ok or flow_input.value is None:
        return runtime.foundation.fail(flow_input.issues)
    gate = runtime.material.submit_resource_rejected(ctx.repo_root, flow_input=flow_input.value, reason=args.reason)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        ResourceRejectedSubmission(
            **_base_kwargs(ctx, tool_name="submit_resource_rejected", summary=gate.value.summary),
            target_kind=args.target_kind,
            target=args.target,
            arxiv_version=args.arxiv_version,
            reason=args.reason,
            details=args.details,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_content_node_tasks(runtime: Any, ctx: ToolExecutionContext, args: SubmitContentNodeTasksArgs) -> ServiceResult[PreparedSubmissionView]:
    if not args.node_paths:
        return _fail(runtime, "content_task_nodes_required", "submit_content_node_tasks requires at least one node path.", field="node_paths")
    gate_result = runtime.node.submit_content_node_batch_preflight(ctx.repo_root, node_paths=args.node_paths)
    if not gate_result.ok or gate_result.value is None:
        return runtime.foundation.fail(gate_result.issues)
    passed = _gate_or_fail(runtime, gate_result.value)
    if not passed.ok:
        return runtime.foundation.fail(passed.issues)
    requests = [
        build_content_node_task_request(
            repo_key=ctx.repo.repo_key,
            node_path=node_path,
            scope_id=node_scope_id(ctx.repo.repo_key, node_path),
            repo_path=str(ctx.repo_root),
            task_mode=args.task_mode,
        )
        for node_path in args.node_paths
    ]
    return _prepared(
        runtime,
        CoordinatorContentTasksSubmission(
            **_dispatch_kwargs(ctx, tool_name="submit_content_node_tasks", requests=requests, summary=args.summary),
            node_paths=args.node_paths,
            task_mode=args.task_mode,
        ),
        agent_view={"gate": gate_result.value.model_dump(mode="json")},
    )


def submit_repo_requirement(runtime: Any, ctx: ToolExecutionContext, args: SubmitRepoRequirementArgs) -> ServiceResult[PreparedSubmissionView]:
    interfaces = [item.model_dump(exclude_none=True) for item in args.interfaces]
    created = runtime.repo_workspace.create_requirement_with_interfaces(
        ctx.repo_root,
        name=args.name,
        target_repo=args.target_repo,
        source_description=args.source_description,
        reason=args.reason,
        interfaces=interfaces,
    )
    if not created.ok or created.value is None:
        return runtime.foundation.fail(created.issues)
    return _prepared(
        runtime,
        CoordinatorRepoRequirementSubmission(
            **_base_kwargs(ctx, tool_name="submit_repo_requirement", summary=args.summary),
            requirement_name=args.name,
            target_repo=args.target_repo,
            source_description=args.source_description,
            reason=args.reason,
            interfaces=interfaces,
        ),
        agent_view=created.value.model_dump(mode="json") if hasattr(created.value, "model_dump") else {},
    )


def submit_repo_ready(runtime: Any, ctx: ToolExecutionContext, args: SubmitRepoReadyArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.validation_snapshot.check_repo_ready(ctx.repo_root, summary=args.summary)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    passed = _gate_or_fail(runtime, gate.value)
    if not passed.ok:
        return runtime.foundation.fail(passed.issues)
    return _prepared(
        runtime,
        CoordinatorRepoReadySubmission(**_base_kwargs(ctx, tool_name="submit_repo_ready", summary=args.summary)),
        agent_view={"gate": gate.value.model_dump(mode="json")},
    )


def submit_content_preparation_recon(runtime: Any, ctx: ToolExecutionContext, args: SubmitContentPreparationReconArgs) -> ServiceResult[PreparedSubmissionView]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    if not args.objective or not args.objective.strip():
        return _fail(runtime, "preparation_objective_required", "Preparation recon dispatch requires a non-empty objective.", field="objective")
    duplicate = _preparation_already_used(runtime, ctx, args.recon_kind)
    if not duplicate.ok:
        return runtime.foundation.fail(duplicate.issues)
    request = build_preparation_recon_request(
        recon_kind=args.recon_kind,
        repo_key=ctx.repo.repo_key,
        node_path=node.value,
        scope_id=node_scope_id(ctx.repo.repo_key, node.value, ctx.runtime.scope_id),
        repo_path=str(ctx.repo_root),
        contract_version=ctx.node.contract_version if ctx.node else None,
        objective=args.objective,
        context_summary=args.context_summary,
    )
    return _prepared(
        runtime,
        ContentPreparationDispatchSubmission(
            **_dispatch_kwargs(ctx, tool_name="submit_content_preparation_recon", requests=[request], summary=args.summary),
            recon_kind=args.recon_kind,
            objective=args.objective,
            context_summary=args.context_summary,
        ),
    )


def submit_current_decl_round(runtime: Any, ctx: ToolExecutionContext, args: SubmitCurrentDeclRoundArgs) -> ServiceResult[PreparedSubmissionView]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    gate = runtime.decl_graph.validate_round_draft(ctx.repo_root, node_path=node.value, round_id=args.round_id)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    if isinstance(gate.value, GateReport) and not gate.value.passed:
        return runtime.foundation.fail(gate.value.issues)
    request = build_decl_round_request(
        repo_key=ctx.repo.repo_key,
        node_path=node.value,
        scope_id=node_scope_id(ctx.repo.repo_key, node.value, ctx.runtime.scope_id),
        strategy_id=args.strategy_id,
        round_id=args.round_id,
        repo_path=str(ctx.repo_root),
        contract_version=ctx.node.contract_version if ctx.node else None,
        round_index=args.round_index,
        summary=args.summary,
    )
    return _prepared(
        runtime,
        DeclRoundDispatchSubmission(
            **_dispatch_kwargs(ctx, tool_name="submit_current_decl_round", requests=[request], summary=args.summary),
            strategy_id=args.strategy_id,
            round_id=args.round_id,
            round_index=args.round_index,
        ),
        agent_view=gate.value.model_dump(mode="json") if hasattr(gate.value, "model_dump") else {},
    )


def submit_content_node_ready(runtime: Any, ctx: ToolExecutionContext, args: SubmitContentNodeReadyArgs) -> ServiceResult[PreparedSubmissionView]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    gate = runtime.validation_snapshot.check_content_node_ready(ctx.repo_root, node_path=node.value)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    passed = _gate_or_fail(runtime, gate.value)
    if not passed.ok:
        return runtime.foundation.fail(passed.issues)
    return _prepared(
        runtime,
        ContentNodeReadySubmission(**_base_kwargs(ctx, tool_name="submit_content_node_ready", summary=args.summary)),
        agent_view={"gate": gate.value.model_dump(mode="json")},
    )


def submit_content_node_blocked(runtime: Any, ctx: ToolExecutionContext, args: SubmitContentNodeBlockedArgs) -> ServiceResult[PreparedSubmissionView]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    gate = runtime.validation_snapshot.readiness_gate.check_content_node_blocked_submit(ctx.repo_root, node_path=node.value, reason=args.reason)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    passed = _gate_or_fail(runtime, gate.value)
    if not passed.ok:
        return runtime.foundation.fail(passed.issues)
    return _prepared(
        runtime,
        ContentNodeBlockedSubmission(**_base_kwargs(ctx, tool_name="submit_content_node_blocked", summary=args.reason), reason=args.reason),
        agent_view={"gate": gate.value.model_dump(mode="json")},
    )


def submit_content_node_failed(runtime: Any, ctx: ToolExecutionContext, args: SubmitContentNodeFailedArgs) -> ServiceResult[PreparedSubmissionView]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    if not args.reason.strip():
        return _fail(runtime, "content_failed_reason_required", "Content failed submit requires a reason.", field="reason")
    return _prepared(
        runtime,
        ContentNodeFailedSubmission(**_base_kwargs(ctx, tool_name="submit_content_node_failed", summary=args.reason), reason=args.reason),
    )


def submit_node_dir_dependency_recon_completed(runtime: Any, ctx: ToolExecutionContext, args: SubmitNodeDirDependencyReconCompletedArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        NodeDirDependencyReconCompletedSubmission(
            **_base_kwargs(ctx, tool_name="submit_node_dir_dependency_recon_completed", summary=args.summary),
            added_node_deps=args.added_node_deps,
            removed_node_deps=args.removed_node_deps,
        ),
    )


def submit_mathlib_recon_completed(runtime: Any, ctx: ToolExecutionContext, args: SubmitMathlibReconCompletedArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        MathlibReconCompletedSubmission(
            **_base_kwargs(ctx, tool_name="submit_mathlib_recon_completed", summary=args.summary),
            added_modules=args.added_modules,
            added_decls=args.added_decls,
        ),
    )


def submit_resource_recon_completed(runtime: Any, ctx: ToolExecutionContext, args: SubmitResourceReconCompletedArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        ResourceReconCompletedSubmission(
            **_base_kwargs(ctx, tool_name="submit_resource_recon_completed", summary=args.summary),
            added_owned_refs=args.added_owned_refs,
            added_context_refs=args.added_context_refs,
        ),
    )


def submit_resource_recon_blocked(runtime: Any, ctx: ToolExecutionContext, args: SubmitResourceReconBlockedArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        ResourceReconBlockedSubmission(
            **_base_kwargs(ctx, tool_name="submit_resource_recon_blocked", summary=args.reason),
            reason=args.reason,
            missing_targets=args.missing_targets,
        ),
    )


def submit_stage_worker_completed(runtime: Any, ctx: ToolExecutionContext, args: SubmitStageWorkerCompletedArgs) -> ServiceResult[PreparedSubmissionView]:
    stage = _require_stage(runtime, ctx)
    if not stage.ok or stage.value is None:
        return runtime.foundation.fail(stage.issues)
    stage_name, round_id = stage.value
    return _prepared(
        runtime,
        DeclStageWorkerCompletedSubmission(
            **_base_kwargs(ctx, tool_name="submit_stage_worker_completed", summary=args.summary),
            stage=stage_name,
            round_id=round_id,
            completed_decl_names=args.completed_decl_names,
        ),
    )


def submit_stage_worker_blocked(runtime: Any, ctx: ToolExecutionContext, args: SubmitStageWorkerBlockedArgs) -> ServiceResult[PreparedSubmissionView]:
    stage = _require_stage(runtime, ctx)
    if not stage.ok or stage.value is None:
        return runtime.foundation.fail(stage.issues)
    stage_name, round_id = stage.value
    return _prepared(
        runtime,
        DeclStageWorkerBlockedSubmission(
            **_base_kwargs(ctx, tool_name="submit_stage_worker_blocked", summary=args.reason),
            stage=stage_name,
            round_id=round_id,
            reason=args.reason,
            affected_decl_names=args.affected_decl_names,
        ),
    )


def submit_stage_review(runtime: Any, ctx: ToolExecutionContext, args: SubmitStageReviewArgs) -> ServiceResult[PreparedSubmissionView]:
    stage = _require_stage(runtime, ctx)
    if not stage.ok or stage.value is None:
        return runtime.foundation.fail(stage.issues)
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    stage_name, round_id = stage.value
    review = runtime.decl_graph.submit_stage_review(ctx.repo_root, node_path=node.value, round_id=round_id, stage=stage_name, summary=args.summary)
    if not review.ok or review.value is None:
        return runtime.foundation.fail(review.issues)
    accepted = bool(getattr(review.value, "passed", False))
    retry_required = not accepted
    return _prepared(
        runtime,
        DeclStageReviewSubmittedSubmission(
            **_base_kwargs(ctx, tool_name="submit_stage_review", summary=args.summary),
            stage=stage_name,
            round_id=round_id,
            accepted=accepted,
            retry_required=retry_required,
        ),
        agent_view=review.value.model_dump(mode="json"),
    )
