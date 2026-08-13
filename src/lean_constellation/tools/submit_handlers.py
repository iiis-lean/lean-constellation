"""Submit tool handlers that build typed ARK submissions after service gates."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

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
from lean_constellation.domain.preparation import (
    AdapterProviderRoute,
    AutoProviderRoute,
    NativeProviderRoute,
    ProviderRoute,
)
from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
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
    CoordinatorRepoExplorationSubmission,
    CoordinatorRepoReadySubmission,
    CoordinatorRepoRequirementSubmission,
    CoordinatorResourceRequestSubmission,
    RepoExplorationSpec,
)
from lean_constellation.flows.repo_exploration.submissions import (
    RepoLeanProviderDiscoverySubmission,
    RepoMathlibReconSubmission,
    RepoResourceDiscoverySubmission,
)
from lean_constellation.flows.repo_lifecycle.submissions import (
    AdapterCatalogBlockedSubmission,
    AdapterCatalogReadySubmission,
    RepoFormatAdapterChoiceSubmission,
    RepoFormatNativeChoiceSubmission,
    RootInterfacePrepareReadySubmission,
    SourceCorpusBuilderBlockedSubmission,
    SourceCorpusBuilderReadySubmission,
    SourceCorpusReviewSubmission,
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
from lean_constellation.tools.source_index_ownership import authorize_source_index_flow_context
from lean_constellation.tools.submit_args import (
    SubmitAdapterCatalogBlockedArgs,
    SubmitAdapterCatalogReadyArgs,
    SubmitAdapterRepoRequirementArgs,
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
    SubmitNativeRepoRequirementArgs,
    SubmitNodeDirDependencyReconCompletedArgs,
    SubmitRepoReadyArgs,
    SubmitRepoExplorationArgs,
    SubmitRepoLeanProviderDiscoveryResultArgs,
    SubmitRepoMathlibReconResultArgs,
    SubmitRepoResourceDiscoveryResultArgs,
    SubmitRepoRequirementArgs,
    SubmitResourceDuplicateArgs,
    SubmitResourceReconBlockedArgs,
    SubmitResourceReconCompletedArgs,
    SubmitResourceRejectedArgs,
    SubmitResourceRequestArgs,
    SubmitRootInterfacePrepareReadyArgs,
    SubmitSourceCorpusBuilderBlockedArgs,
    SubmitSourceCorpusBuilderReadyArgs,
    SubmitSourceCorpusReviewArgs,
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


def _require_no_pending_decl_round(runtime: Any, ctx: ToolExecutionContext) -> ServiceResult[None]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    rounds = runtime.decl_graph.list_rounds(ctx.repo_root, node_path=node.value)
    if not rounds.ok or rounds.value is None:
        return runtime.foundation.fail(rounds.issues)
    pending = [
        item
        for item in rounds.value
        if item.status.value in {"draft", "running", "awaiting_closeout"}
        or (
            item.status.value == "committed"
            and item.plan_closeout_acknowledged_at is None
        )
    ]
    if pending:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_round_closeout_pending",
                "Close every declaration round before preparation or Content terminal submission.",
                object_ref=node.value,
                current=", ".join(
                    f"{item.round_id}:{item.status.value}" for item in pending
                ),
            )
        )
    return runtime.foundation.ok(None)


def _gate_or_fail(runtime: Any, gate: GateReport | None) -> ServiceResult[None]:
    if gate is None:
        return _fail(runtime, "gate_missing", "Required submit gate did not return a report.")
    if not gate.passed:
        return runtime.foundation.fail(gate.issues)
    return runtime.foundation.ok(None)


def _decl_stage_candidate_ready(runtime: Any, ctx: ToolExecutionContext, *, stage_name: str, decl_names: list[str]) -> ServiceResult[None]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    round_id = ctx.decl_stage.round_id if ctx.decl_stage is not None else None
    if round_id is None:
        return _fail(runtime, "decl_stage_context_missing", "This submit tool requires current decl stage and round context.")
    validation = runtime.decl_graph.validate_round_stage_candidates(
        ctx.repo_root,
        node_path=node.value,
        round_id=round_id,
        stage=stage_name,
        target_decl_names=decl_names,
    )
    if not validation.ok or validation.value is None:
        return runtime.foundation.fail(validation.issues)
    if not validation.value.passed:
        return runtime.foundation.fail(validation.value.issues)
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
    normalized_target = runtime.material.prepare_resource_target(
        target_kind=args.target_kind,
        target=args.target,
        arxiv_version=args.arxiv_version,
    )
    if not normalized_target.ok or normalized_target.value is None:
        return runtime.foundation.fail(normalized_target.issues)
    request = build_resource_curation_request(
        scope_id=repo_scope_id(ctx.repo.repo_key, ctx.runtime.scope_id),
        target_kind=args.target_kind,
        target=args.target,
        arxiv_version=args.arxiv_version,
        requested_use=args.requested_use,
        consumer_need=args.consumer_need,
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
        requested_use=args.requested_use,
        consumer_need=args.consumer_need,
        context_summary=args.context_summary,
    )
    return _prepared(
        runtime,
        submission,
        agent_view={"normalized_target": normalized_target.value.model_dump(mode="json")},
    )


def submit_adapter_repo_choice(runtime: Any, ctx: ToolExecutionContext, args: SubmitAdapterRepoChoiceArgs) -> ServiceResult[PreparedSubmissionView]:
    try:
        git_url = runtime.external.github_repo.normalize_github_url(args.git_url)
    except ValueError as exc:
        return _fail(runtime, "git_url_invalid", str(exc), field="git_url")
    try:
        route = AdapterProviderRoute(
            git_url=git_url,
            revision=args.revision,
            subdir=args.subdir,
            evidence_summary=args.evidence_summary,
            known_risks=args.known_risks,
        )
    except ValueError as exc:
        return _fail(runtime, "adapter_provider_route_invalid", str(exc), field="provider_route")
    verified = runtime.repo_workspace.verify_adapter_provider_route(route)
    if not verified.ok or verified.value is None:
        return runtime.foundation.fail(verified.issues)
    receipt = verified.value
    return _prepared(
        runtime,
        RepoFormatAdapterChoiceSubmission(
            **_base_kwargs(ctx, tool_name="submit_adapter_repo_choice", summary=route.evidence_summary),
            git_url=receipt.git_url,
            revision=receipt.revision,
            subdir=receipt.subdir,
            evidence_summary=route.evidence_summary,
            known_risks=route.known_risks,
            verified_route=receipt,
        ),
        agent_view={
            "verified_route": receipt.model_dump(mode="json"),
            "summary": "Adapter route passed exact remote compatibility verification.",
        },
    )


def submit_native_repo_choice(runtime: Any, ctx: ToolExecutionContext, args: SubmitNativeRepoChoiceArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        RepoFormatNativeChoiceSubmission(
            **_base_kwargs(ctx, tool_name="submit_native_repo_choice", summary=args.summary),
            searched_targets=args.searched_targets,
        ),
    )


def submit_source_corpus_builder_ready(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceCorpusBuilderReadyArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.material.check_source_corpus_draft(
        ctx.repo_root,
        relpath=".lean_constellation/source_draft",
        entry_path=args.entry_path,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    if not gate.value.passed:
        return runtime.foundation.fail(gate.value.issues)
    return _prepared(
        runtime,
        SourceCorpusBuilderReadySubmission(
            **_base_kwargs(ctx, tool_name="submit_source_corpus_builder_ready", summary=args.summary),
            relpath=expected_relpath,
            entry_path=args.entry_path,
            overview=args.overview.strip(),
            preparation_summary=args.preparation_summary.strip(),
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def _expected_source_corpus_relpath(runtime: Any, ctx: ToolExecutionContext) -> str:
    fallback = ".lean_constellation/source"
    try:
        preparation = runtime.repo_workspace.preparation.get_preparation_input(ctx.repo_root)
    except Exception:  # noqa: BLE001
        return fallback
    if preparation.ok and preparation.value is not None:
        return preparation.value.input.source_corpus_relpath or fallback
    return fallback


def submit_source_corpus_builder_blocked(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceCorpusBuilderBlockedArgs) -> ServiceResult[PreparedSubmissionView]:
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
        SourceCorpusBuilderBlockedSubmission(
            **_base_kwargs(ctx, tool_name="submit_source_corpus_builder_blocked", summary=gate.value.summary),
            reason=args.reason,
            attempted_targets=args.attempted_targets,
            missing_materials=args.missing_materials,
            suggested_next_action=args.suggested_next_action,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_source_corpus_review(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceCorpusReviewArgs) -> ServiceResult[PreparedSubmissionView]:
    if args.approved:
        if not [item for item in args.checked_materials if item.strip()]:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "source_corpus_review_checked_materials_missing",
                    "Approved SourceCorpus review requires at least one checked material locator.",
                )
            )
        entry_path = None
        flow_service = getattr(getattr(runtime, "ark", None), "flow_service", None)
        if flow_service is not None and ctx.runtime is not None and ctx.runtime.flow_id is not None:
            try:
                flow = flow_service.get_flow(ctx.runtime.flow_id)
            except Exception:  # noqa: BLE001
                flow = None
            candidate = getattr(getattr(flow, "state", None), "source_corpus_candidate", None)
            if candidate is not None:
                entry_path = candidate.entry_path
        if entry_path is None:
            manifest = runtime.material.source_corpus.get_source_corpus_manifest(ctx.repo_root)
            if manifest.ok and manifest.value is not None:
                entry_path = manifest.value.entry_path
        gate = runtime.material.check_source_corpus_draft(
            ctx.repo_root,
            relpath=".lean_constellation/source_draft",
            entry_path=entry_path,
        )
        if not gate.ok or gate.value is None:
            return runtime.foundation.fail(gate.issues)
        if not gate.value.passed:
            return runtime.foundation.fail(gate.value.issues)
    elif not (args.feedback or "").strip():
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_corpus_review_feedback_missing",
                "Rejected SourceCorpus review requires actionable feedback.",
            )
        )
    return _prepared(
        runtime,
        SourceCorpusReviewSubmission(
            **_base_kwargs(ctx, tool_name="submit_source_corpus_review", summary=args.summary),
            approved=args.approved,
            feedback=args.feedback.strip() if args.feedback else None,
            checked_materials=list(dict.fromkeys(item.strip() for item in args.checked_materials if item.strip())),
            unresolved_risks=list(dict.fromkeys(item.strip() for item in args.unresolved_risks if item.strip())),
        ),
    )


def submit_source_index_builder_round(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceIndexBuilderRoundArgs) -> ServiceResult[PreparedSubmissionView]:
    authorized = authorize_source_index_flow_context(
        runtime,
        ctx,
        allowed_step_types={"source_index_builder_agent_step"},
        allowed_actor_roles={"worker", "admin"},
    )
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    gate = runtime.material.submit_source_index_builder_round(
        ctx.repo_root,
        summary=args.summary,
        ctx=ctx,
    )
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
    authorized = authorize_source_index_flow_context(
        runtime,
        ctx,
        allowed_step_types={"source_index_reviewer_agent_step"},
        allowed_actor_roles={"reviewer", "admin"},
    )
    if not authorized.ok:
        return runtime.foundation.fail(authorized.issues)
    gate = runtime.material.submit_source_index_review_round(
        ctx.repo_root,
        approved=args.approved,
        summary=args.summary,
        feedback=args.feedback,
        ctx=ctx,
    )
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
    gate = runtime.node.interface.submit_root_interface_prepare_ready(ctx.repo_root, summary=args.summary)
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        RootInterfacePrepareReadySubmission(**_base_kwargs(ctx, tool_name="submit_root_interface_prepare_ready", summary=args.summary)),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_adapter_catalog_ready(runtime: Any, ctx: ToolExecutionContext, args: SubmitAdapterCatalogReadyArgs) -> ServiceResult[PreparedSubmissionView]:
    gate = runtime.adapter.submit_adapter_catalog_ready(ctx.repo_root, summary=args.summary)
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
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        AdapterCatalogBlockedSubmission(
            **_base_kwargs(ctx, tool_name="submit_adapter_catalog_blocked", summary=gate.value.summary),
            reason=gate.value.reason,
            missing_interfaces=gate.value.missing_interfaces,
            evidence_summary=gate.value.evidence_summary,
            suggested_next_action=gate.value.suggested_next_action,
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


def submit_repo_exploration(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitRepoExplorationArgs,
) -> ServiceResult[PreparedSubmissionView]:
    requested = (
        ("resource", args.resource_objective),
        ("lean_provider", args.lean_provider_objective),
        ("mathlib", args.mathlib_objective),
    )
    submission = CoordinatorRepoExplorationSubmission(
        **_base_kwargs(ctx, tool_name="submit_repo_exploration", summary=args.summary),
        explorations=[
            RepoExplorationSpec(
                kind=kind,
                objective=objective,
                context_summary=args.context_summary,
            )
            for kind, objective in requested
            if objective is not None
        ],
    )
    return _prepared(
        runtime,
        submission,
        agent_view={"accepted_kinds": [item.kind.value for item in submission.explorations]},
    )


def submit_repo_resource_discovery_result(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitRepoResourceDiscoveryResultArgs,
) -> ServiceResult[PreparedSubmissionView]:
    candidates: list[dict[str, Any]] = []
    seen_locators: set[str] = set()
    for index, requested in enumerate(args.candidates):
        inspected = runtime.external.resource_discovery.inspect(requested.target)
        if not inspected.ok or inspected.candidate is None:
            return _fail(
                runtime,
                inspected.issue_code or "external_resource_inspection_failed",
                inspected.summary,
                field=f"candidates[{index}].target",
            )
        canonical = inspected.candidate
        locator_key = canonical.canonical_locator.strip().casefold()
        if locator_key in seen_locators:
            return _fail(
                runtime,
                "repo_resource_candidate_duplicate",
                "Resource discovery candidates must resolve to distinct canonical resources.",
                field=f"candidates[{index}].target",
            )
        seen_locators.add(locator_key)
        source_urls = _unique_non_empty(canonical.source_urls)
        if requested.recommended_handling == "local_resource" and not source_urls:
            return _fail(
                runtime,
                "repo_resource_source_url_missing",
                "A local_resource candidate must expose at least one verified source URL.",
                field=f"candidates[{index}].target",
            )
        candidates.append(
            {
                "title": canonical.title,
                "authors": _unique_non_empty(canonical.authors),
                "resource_kind": canonical.resource_kind,
                "canonical_locator": canonical.canonical_locator,
                "version": canonical.version,
                "source_urls": source_urls,
                "support_summary": requested.support_summary,
                "risks_or_gaps": _unique_non_empty(requested.risks_or_gaps),
                "recommended_handling": requested.recommended_handling,
                "consumer_need": requested.consumer_need.strip() if requested.consumer_need else None,
                "provider_scope": requested.provider_scope.strip() if requested.provider_scope else None,
            }
        )
    return _prepared(
        runtime,
        RepoResourceDiscoverySubmission(
            **_base_kwargs(ctx, tool_name="submit_repo_resource_discovery_result", summary=args.summary),
            outcome=args.outcome,
            candidates=candidates,
        ),
        agent_view={
            "outcome": args.outcome,
            "candidate_count": len(candidates),
            "canonical_locators": [candidate["canonical_locator"] for candidate in candidates],
        },
    )


def submit_repo_lean_provider_discovery_result(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitRepoLeanProviderDiscoveryResultArgs,
) -> ServiceResult[PreparedSubmissionView]:
    candidates: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str | None]] = set()
    for index, requested in enumerate(args.candidates):
        try:
            probe = runtime.external.github_repo.probe_github_lean_repo_candidate(
                requested.git_url,
                revision=requested.revision,
                subdir=requested.subdir,
            )
        except ValueError as exc:
            return _fail(
                runtime,
                "github_lean_repo_probe_failed",
                str(exc),
                field=f"candidates[{index}].git_url",
            )
        if probe.is_mathlib_repository:
            return _fail(
                runtime,
                "mathlib_provider_candidate_forbidden",
                "Mathlib is the platform dependency and must be handled by RepoMathlibRecon.",
                field=f"candidates[{index}].git_url",
            )
        if not probe.is_lean_project or not probe.has_lean_files:
            return _fail(
                runtime,
                "lean_provider_candidate_unverified",
                "The exact GitHub probe did not verify a Lean repository with Lean source files.",
                field=f"candidates[{index}].git_url",
            )
        revision = (probe.resolved_revision or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) is None:
            return _fail(
                runtime,
                "lean_provider_revision_unresolved",
                "The exact GitHub probe did not resolve an immutable commit.",
                field=f"candidates[{index}].revision",
            )
        if requested.revision is not None and revision != requested.revision:
            return _fail(
                runtime,
                "lean_provider_revision_mismatch",
                "The exact GitHub probe did not preserve the requested immutable revision.",
                field=f"candidates[{index}].revision",
            )
        target_key = (probe.normalized_git_url.casefold(), probe.selected_subdir)
        if target_key in seen_targets:
            return _fail(
                runtime,
                "lean_provider_candidate_duplicate",
                "Lean provider candidates must resolve to distinct repository project roots.",
                field=f"candidates[{index}].git_url",
            )
        seen_targets.add(target_key)
        relevant_declarations = _unique_non_empty(requested.relevant_declarations)
        if requested.recommendation == "direct_adapter_requirement":
            missing_facts: list[str] = []
            if not probe.has_lakefile:
                missing_facts.append("Lakefile")
            if not probe.package_name:
                missing_facts.append("Lake package")
            if not probe.likely_import_modules:
                missing_facts.append("import module")
            if not relevant_declarations:
                missing_facts.append("relevant declaration")
            if requested.gaps:
                missing_facts.append("gap-free evidence")
            if missing_facts:
                return _fail(
                    runtime,
                    "direct_adapter_candidate_incomplete",
                    "Direct Adapter recommendation is missing: " + ", ".join(missing_facts) + ".",
                    field=f"candidates[{index}].recommendation",
                )
        lean_evidence = _unique_non_empty(
            [
                *probe.lean_signals,
                *(f"path:{path}" for path in probe.lakefile_paths),
                *(f"path:{path}" for path in probe.lean_toolchain_paths),
                *(f"path:{path}" for path in probe.lean_file_paths[:25]),
            ]
        )
        candidates.append(
            {
                "git_url": probe.normalized_git_url,
                "resolved_revision": revision,
                "subdir": probe.selected_subdir,
                "package_name": probe.package_name,
                "likely_import_modules": _unique_non_empty(probe.likely_import_modules),
                "lean_toolchain": probe.lean_toolchain,
                "has_lakefile": probe.has_lakefile,
                "has_lean_manifest": probe.has_lean_manifest,
                "has_lean_files": probe.has_lean_files,
                "capability_summary": requested.capability_summary,
                "relevant_declarations": relevant_declarations,
                "lean_evidence": lean_evidence,
                "gaps": _unique_non_empty(requested.gaps),
                "risks": _unique_non_empty([*requested.risks, *probe.known_risks]),
                "recommendation": requested.recommendation,
            }
        )
    return _prepared(
        runtime,
        RepoLeanProviderDiscoverySubmission(
            **_base_kwargs(ctx, tool_name="submit_repo_lean_provider_discovery_result", summary=args.summary),
            outcome=args.outcome,
            candidates=candidates,
        ),
        agent_view={"outcome": args.outcome, "candidate_count": len(candidates)},
    )


def _unique_non_empty(values: Iterable[object]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value).strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            normalized.append(item)
    return normalized


def submit_repo_mathlib_recon_result(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitRepoMathlibReconResultArgs,
) -> ServiceResult[PreparedSubmissionView]:
    relevant_modules: list[str] = []
    for index, module in enumerate(args.relevant_modules):
        loaded = runtime.mathlib.get_mathlib_module_entry(ctx.repo_root, module=module)
        if not loaded.ok or loaded.value is None:
            issue = loaded.issues[0] if loaded.issues else None
            return _fail(
                runtime,
                issue.kind if issue is not None else "mathlib_module_entry_missing",
                issue.message if issue is not None else "Mathlib module is not recorded in the current index.",
                field=f"relevant_modules[{index}]",
            )
        relevant_modules.append(loaded.value.module)
    relevant_declarations: list[str] = []
    for index, declaration in enumerate(args.relevant_declarations):
        loaded = runtime.mathlib.get_mathlib_decl_entry(ctx.repo_root, name=declaration)
        if not loaded.ok or loaded.value is None:
            issue = loaded.issues[0] if loaded.issues else None
            return _fail(
                runtime,
                issue.kind if issue is not None else "mathlib_decl_entry_missing",
                issue.message if issue is not None else "Mathlib declaration is not recorded in the current index.",
                field=f"relevant_declarations[{index}]",
            )
        relevant_declarations.append(loaded.value.name)
    return _prepared(
        runtime,
        RepoMathlibReconSubmission(
            **_base_kwargs(ctx, tool_name="submit_repo_mathlib_recon_result", summary=args.summary),
            outcome=args.outcome,
            relevant_modules=relevant_modules,
            relevant_declarations=relevant_declarations,
            unresolved=args.unresolved,
            usage_notes=args.usage_notes,
        ),
        agent_view={
            "outcome": args.outcome,
            "relevant_module_count": len(relevant_modules),
            "relevant_declaration_count": len(relevant_declarations),
        },
    )


def _resource_target(runtime: Any, target_kind: str, target: str, arxiv_version: str | None) -> ServiceResult[Any]:
    return runtime.material.prepare_resource_target(
        target_kind=target_kind,
        target=target,
        arxiv_version=arxiv_version,
    )


def _current_resource_target_context(
    runtime: Any,
    ctx: ToolExecutionContext,
    *,
    arxiv_version: str | None,
) -> ServiceResult[Any]:
    if not ctx.runtime.flow_id:
        return runtime.foundation.fail(runtime.foundation.issue("resource_curation_context_missing", "Resource curator submit requires current flow context."))
    flow = runtime.get_flow(ctx.runtime.flow_id)
    if getattr(flow, "flow_type", None) != "resource_curation":
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "resource_curation_context_missing",
                "Resource curator submit tools are only available inside ResourceCurationFlow.",
                current=getattr(flow, "flow_type", None),
                expected="resource_curation",
            )
        )
    input_model = getattr(flow, "input", None)
    request_target = getattr(input_model, "target", None)
    if request_target is None:
        return runtime.foundation.fail(runtime.foundation.issue("resource_curation_input_missing", "ResourceCurationFlow has no target input."))
    if arxiv_version is not None and arxiv_version != request_target.arxiv_version:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "resource_request_target_mismatch",
                "Submitted arxiv_version does not match the current resource curation request.",
                field="arxiv_version",
                current=arxiv_version,
                expected=request_target.arxiv_version,
            )
        )
    normalized = _resource_target(runtime, request_target.kind, request_target.target, request_target.arxiv_version)
    if not normalized.ok or normalized.value is None:
        return runtime.foundation.fail(normalized.issues)
    return runtime.foundation.ok((request_target, normalized.value))


def _active_resource_draft_id_for_submit(runtime: Any, ctx: ToolExecutionContext) -> ServiceResult[str]:
    if not ctx.runtime.flow_id:
        return runtime.foundation.fail(runtime.foundation.issue("resource_curation_context_missing", "Resource curator submit requires current flow context."))
    flow = runtime.get_flow(ctx.runtime.flow_id)
    draft_id = getattr(getattr(flow, "state", None), "active_resource_draft_key", None)
    if not draft_id:
        return runtime.foundation.fail(
            runtime.foundation.issue("resource_active_draft_missing", "Current ResourceCurationFlow has no active resource draft.")
        )
    return runtime.foundation.ok(draft_id)


def submit_resource_duplicate(runtime: Any, ctx: ToolExecutionContext, args: SubmitResourceDuplicateArgs) -> ServiceResult[PreparedSubmissionView]:
    target_context = _current_resource_target_context(runtime, ctx, arxiv_version=args.arxiv_version)
    if not target_context.ok or target_context.value is None:
        return runtime.foundation.fail(target_context.issues)
    request_target, normalized_target = target_context.value
    gate = runtime.material.submit_resource_duplicate(
        ctx.repo_root,
        target=normalized_target,
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
            **_base_kwargs(ctx, tool_name="submit_resource_duplicate", summary=args.summary or gate.value.summary),
            target_kind=request_target.kind,
            target=request_target.target,
            arxiv_version=request_target.arxiv_version,
            existing_kind=args.existing_kind,
            duplicate_reason=args.duplicate_reason,
            existing_resource_key=args.existing_resource_key,
            existing_source_path=args.existing_source_path,
            preview=args.preview,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_local_resource_created(runtime: Any, ctx: ToolExecutionContext, args: SubmitLocalResourceCreatedArgs) -> ServiceResult[PreparedSubmissionView]:
    target_context = _current_resource_target_context(runtime, ctx, arxiv_version=args.arxiv_version)
    if not target_context.ok or target_context.value is None:
        return runtime.foundation.fail(target_context.issues)
    request_target, normalized_target = target_context.value
    active_draft = _active_resource_draft_id_for_submit(runtime, ctx)
    if not active_draft.ok or active_draft.value is None:
        return runtime.foundation.fail(active_draft.issues)
    if args.draft_id != active_draft.value:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "resource_active_draft_mismatch",
                "Local resource submit draft_id must match the current active resource draft.",
                field="draft_id",
                current=args.draft_id,
                expected=active_draft.value,
            )
        )
    gate = runtime.material.check_local_resource_created(
        ctx.repo_root,
        target=normalized_target,
        draft_id=args.draft_id,
        summary=args.summary,
        classification_reason=args.classification_reason,
        resource_role=args.resource_role,
        consumer_formalization_scope=args.consumer_formalization_scope,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    if not gate.value.resource_key:
        return runtime.foundation.fail(
            runtime.foundation.issue("resource_key_missing", "Local resource submit succeeded without a finalized resource key.")
        )
    return _prepared(
        runtime,
        LocalResourceCreatedSubmission(
            **_base_kwargs(ctx, tool_name="submit_local_resource_created", summary=args.summary),
            target_kind=request_target.kind,
            target=request_target.target,
            arxiv_version=request_target.arxiv_version,
            draft_id=args.draft_id,
            resource_key=gate.value.resource_key,
            classification_reason=gate.value.classification_reason or args.classification_reason,
            resource_role=gate.value.resource_role or args.resource_role,
            consumer_formalization_scope=(
                gate.value.consumer_formalization_scope
                or args.consumer_formalization_scope
            ),
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_external_repo_required(runtime: Any, ctx: ToolExecutionContext, args: SubmitExternalRepoRequiredArgs) -> ServiceResult[PreparedSubmissionView]:
    target_context = _current_resource_target_context(runtime, ctx, arxiv_version=args.arxiv_version)
    if not target_context.ok or target_context.value is None:
        return runtime.foundation.fail(target_context.issues)
    request_target, normalized_target = target_context.value
    gate = runtime.material.submit_external_repo_required(
        ctx.repo_root,
        target=normalized_target,
        reason=args.reason,
        source_description=args.source_description,
        classification_reason=args.classification_reason,
        relation_to_current_repo_or_node=args.relation_to_current_repo_or_node,
        consumer_need=args.consumer_need,
        provider_scope=args.provider_scope,
        suggested_repo_name=args.suggested_repo_name,
        required_interfaces_hint=args.required_interfaces_hint,
        existing_lean_repo_signal=args.existing_lean_repo_signal,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        ExternalRepoRequiredSubmission(
            **_base_kwargs(ctx, tool_name="submit_external_repo_required", summary=gate.value.summary),
            target_kind=request_target.kind,
            target=request_target.target,
            arxiv_version=request_target.arxiv_version,
            reason=args.reason,
            source_description=args.source_description,
            classification_reason=gate.value.classification_reason or args.classification_reason,
            relation_to_current_repo_or_node=(
                gate.value.relation_to_current_repo_or_node
                or args.relation_to_current_repo_or_node
            ),
            consumer_need=gate.value.consumer_need or args.consumer_need,
            provider_scope=gate.value.provider_scope or args.provider_scope,
            suggested_repo_name=args.suggested_repo_name,
            required_interfaces_hint=args.required_interfaces_hint,
            existing_lean_repo_signal=gate.value.existing_lean_repo_signal,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_resource_rejected(runtime: Any, ctx: ToolExecutionContext, args: SubmitResourceRejectedArgs) -> ServiceResult[PreparedSubmissionView]:
    target_context = _current_resource_target_context(runtime, ctx, arxiv_version=args.arxiv_version)
    if not target_context.ok or target_context.value is None:
        return runtime.foundation.fail(target_context.issues)
    request_target, normalized_target = target_context.value
    gate = runtime.material.submit_resource_rejected(
        ctx.repo_root, target=normalized_target, reason=args.reason
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        ResourceRejectedSubmission(
            **_base_kwargs(ctx, tool_name="submit_resource_rejected", summary=gate.value.summary),
            target_kind=request_target.kind,
            target=request_target.target,
            arxiv_version=request_target.arxiv_version,
            reason=args.reason,
            details=args.details,
        ),
        agent_view=gate.value.model_dump(mode="json"),
    )


def submit_content_node_tasks(runtime: Any, ctx: ToolExecutionContext, args: SubmitContentNodeTasksArgs) -> ServiceResult[PreparedSubmissionView]:
    if not args.node_paths:
        return _fail(runtime, "content_task_nodes_required", "submit_content_node_tasks requires at least one node path.", field="node_paths")
    max_parallel = _current_coordinator_content_parallelism(runtime, ctx)
    if len(args.node_paths) > max_parallel:
        return _fail(
            runtime,
            "content_task_batch_parallelism_exceeded",
            f"Requested {len(args.node_paths)} content node tasks, but this run allows at most {max_parallel}.",
            field="node_paths",
        )
    gate_result = runtime.node.submit_content_node_batch_preflight(ctx.repo_root, node_paths=args.node_paths)
    if not gate_result.ok or gate_result.value is None:
        return runtime.foundation.fail(gate_result.issues)
    passed = _gate_or_fail(runtime, gate_result.value)
    if not passed.ok:
        return runtime.foundation.fail(passed.issues)
    requests = []
    material_previews: dict[str, list[dict[str, object]]] = {}
    for node_path in args.node_paths:
        node = runtime.node.node_tree.node_store.resolve_active_node(ctx.repo_root, path=node_path)
        if not node.ok or node.value is None:
            return runtime.foundation.fail(node.issues)
        contract = runtime.node.contract.get_open_contract(ctx.repo_root, node_path=node_path)
        if not contract.ok or contract.value is None:
            return runtime.foundation.fail(contract.issues)
        refs = runtime.node.material_ref.list_node_material_refs(ctx.repo_root, node_path=node_path)
        if not refs.ok or refs.value is None:
            return runtime.foundation.fail(refs.issues)
        invalid_refs = [item for item in [*refs.value.owned_refs, *refs.value.context_refs] if item.valid is not True]
        if invalid_refs:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "content_task_material_ref_invalid",
                    "Content task dispatch requires every current material reference to resolve to a non-empty preview.",
                    object_ref=node_path,
                    details={
                        "invalid_refs": ", ".join(
                            f"{item.material_kind}:{item.path or item.resource_key}:{item.start_line or ''}-{item.end_line or ''}"
                            for item in invalid_refs
                        )
                    },
                )
            )
        material_previews[node_path] = [
            {
                "scope": scope,
                "kind": item.material_kind,
                "locator": item.path or item.resource_key,
                "start_line": item.start_line,
                "end_line": item.end_line,
                "reason": item.reason,
                "preview_summary": item.preview_summary,
            }
            for scope, items in (("owned", refs.value.owned_refs), ("context", refs.value.context_refs))
            for item in items
        ]
        requests.append(build_content_node_task_request(
            repo_key=ctx.repo.repo_key,
            node_path=node_path,
            scope_id=node_scope_id(ctx.repo.repo_key, node.value.node_id),
            repo_path=str(ctx.repo_root),
            contract_version=contract.value.version,
            max_parallel_content_node_tasks=max_parallel,
        ))
    return _prepared(
        runtime,
        CoordinatorContentTasksSubmission(
            **_dispatch_kwargs(ctx, tool_name="submit_content_node_tasks", requests=requests, summary=args.summary),
            node_paths=args.node_paths,
        ),
        agent_view={
            "gate": gate_result.value.model_dump(mode="json"),
            "material_ref_previews": material_previews,
        },
    )


def _current_coordinator_content_parallelism(runtime: Any, ctx: ToolExecutionContext) -> int:
    flow_id = ctx.runtime.flow_id
    if not flow_id:
        return 1
    try:
        flow = runtime.get_flow(flow_id)
    except RuntimeError:
        return 1
    run_context = getattr(getattr(flow, "input", None), "run_context", None)
    run_spec = getattr(run_context, "run_spec", None)
    return int(getattr(run_spec, "max_parallel_content_node_tasks", 1))


def _submit_repo_requirement(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitRepoRequirementArgs,
    *,
    provider_route: ProviderRoute,
    tool_name: str,
) -> ServiceResult[PreparedSubmissionView]:
    if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*", args.name) is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "requirement_name_invalid",
                "Requirement name must be a consumer-local lower_snake_case identity.",
                field="name",
                current=args.name,
            )
        )
    try:
        requirement_name = runtime.foundation.layout.ensure_safe_key(args.name)
        target_repo = runtime.foundation.layout.ensure_safe_key(args.target_repo)
    except ValueError as exc:
        return runtime.foundation.fail(runtime.foundation.issue("requirement_key_invalid", str(exc)))
    invalid_target_name = (
        not target_repo[0].isupper()
        or not target_repo.isalnum()
        or target_repo.lower().endswith(("provider", "repo", "repository", "dependency"))
    )
    if invalid_target_name:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "requirement_target_repo_name_invalid",
                "Requirement target_repo must be an UpperCamelCase mathematical project name without Provider, Repo, Repository, or Dependency suffixes.",
                field="target_repo",
                current=target_repo,
                suggested_action="Use a reusable mathematical scope name such as WeightedSieve.",
            )
        )
    if not (args.source_description and args.source_description.strip()) and not (args.reason and args.reason.strip()):
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "requirement_missing_context",
                "Requirement needs at least source_description or reason.",
            )
        )
    existing = runtime.repo_workspace.requirement.get_requirement(ctx.repo_root, name=requirement_name)
    if existing.ok and existing.value is not None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "requirement_name_duplicate",
                f"Requirement already exists: {requirement_name}",
            )
        )
    interfaces = []
    interface_names: set[str] = set()
    for item in args.interfaces:
        if not item.name.strip() or not item.summary.strip():
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "requirement_interface_invalid",
                    "Requirement interfaces need non-empty name and summary.",
                    field="interfaces",
                )
            )
        interface_name = item.name.strip()
        if interface_name in interface_names:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "requirement_interface_name_duplicate",
                    "Requirement interface names must be unique within one requirement.",
                    field="interfaces.name",
                    current=interface_name,
                )
            )
        interface_names.add(interface_name)
        interfaces.append(
            {
                "name": interface_name,
                "kind": item.kind.value,
                "summary": item.summary.strip(),
                **({"statement_hint": item.statement_hint.strip()} if item.statement_hint and item.statement_hint.strip() else {}),
                **(
                    {"expected_statement_lean_code": item.expected_statement_lean_code.strip()}
                    if item.expected_statement_lean_code and item.expected_statement_lean_code.strip()
                    else {}
                ),
            }
        )
    required_proof_availability = runtime.repo_workspace.requirement_proof_availability_for_repo(ctx.repo_root)
    return _prepared(
        runtime,
        CoordinatorRepoRequirementSubmission(
            **_base_kwargs(ctx, tool_name=tool_name, summary=args.summary),
            requirement_name=requirement_name,
            target_repo=target_repo,
            provider_route=provider_route,
            required_proof_availability=required_proof_availability,
            source_description=args.source_description.strip() if args.source_description else None,
            reason=args.reason.strip() if args.reason else None,
            interfaces=interfaces,
        ),
        agent_view={
            "requirement_name": requirement_name,
            "target_repo": target_repo,
            "provider_route": provider_route.model_dump(mode="json"),
            "required_proof_availability": str(required_proof_availability),
            "interfaces": interfaces,
            "summary": "Requirement submission validated; waiting state will be recorded after the submission is accepted.",
        },
    )


def submit_repo_requirement(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitRepoRequirementArgs,
) -> ServiceResult[PreparedSubmissionView]:
    return _submit_repo_requirement(
        runtime,
        ctx,
        args,
        provider_route=AutoProviderRoute(),
        tool_name="submit_repo_requirement",
    )


def submit_adapter_repo_requirement(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitAdapterRepoRequirementArgs,
) -> ServiceResult[PreparedSubmissionView]:
    try:
        requested_route = AdapterProviderRoute(
            git_url=runtime.external.github_repo.normalize_github_url(args.git_url),
            revision=args.revision,
            subdir=args.subdir,
            evidence_summary=args.evidence_summary,
            known_risks=args.known_risks,
        )
    except ValueError as exc:
        return _fail(
            runtime,
            "adapter_provider_route_invalid",
            str(exc),
            field="provider_route",
        )
    verified = runtime.repo_workspace.verify_adapter_provider_route(requested_route)
    if not verified.ok or verified.value is None:
        return runtime.foundation.fail(verified.issues)
    receipt = verified.value
    route = AdapterProviderRoute(
        git_url=receipt.git_url,
        revision=receipt.revision,
        subdir=receipt.subdir,
        package_name=receipt.package_name,
        likely_import_module=receipt.likely_import_module,
        evidence_summary=args.evidence_summary,
        known_risks=args.known_risks,
    )
    return _submit_repo_requirement(
        runtime,
        ctx,
        args,
        provider_route=route,
        tool_name="submit_adapter_repo_requirement",
    )


def submit_native_repo_requirement(
    runtime: Any,
    ctx: ToolExecutionContext,
    args: SubmitNativeRepoRequirementArgs,
) -> ServiceResult[PreparedSubmissionView]:
    try:
        route = NativeProviderRoute(
            evidence_summary=args.evidence_summary,
            searched_targets=args.searched_targets,
            rejected_candidates=[],
        )
    except ValueError as exc:
        return _fail(
            runtime,
            "native_provider_route_invalid",
            str(exc),
            field="provider_route",
        )
    return _submit_repo_requirement(
        runtime,
        ctx,
        args,
        provider_route=route,
        tool_name="submit_native_repo_requirement",
    )


def submit_repo_ready(runtime: Any, ctx: ToolExecutionContext, args: SubmitRepoReadyArgs) -> ServiceResult[PreparedSubmissionView]:
    if not ctx.runtime.flow_id:
        return _fail(runtime, "coordinator_flow_context_required", "Repository-ready submission requires the current Coordinator Flow.")
    flow = runtime.get_flow(ctx.runtime.flow_id)
    flow_input = getattr(flow, "input", None)
    if getattr(flow, "flow_type", None) != "native_repo_coordinator" or str(
        getattr(flow_input, "repo_root", "")
    ) != str(ctx.repo_root):
        return _fail(runtime, "coordinator_flow_context_invalid", "Current Flow does not own this repository-ready submission.")
    from lean_constellation.flows.coordinator.release_runtime import check_repo_release_runtime_closeout

    runtime_closeout = check_repo_release_runtime_closeout(
        runtime,
        ctx.repo_root,
        owner_flow_id=ctx.runtime.flow_id,
        phase="submission_preview",
        allowed_agent_id=ctx.runtime.agent_id,
    )
    if not runtime_closeout.ok or runtime_closeout.value is None:
        return runtime.foundation.fail(runtime_closeout.issues)
    passed = _gate_or_fail(runtime, runtime_closeout.value)
    if not passed.ok:
        return runtime.foundation.fail(passed.issues)
    return _prepared(
        runtime,
        CoordinatorRepoReadySubmission(**_base_kwargs(ctx, tool_name="submit_repo_ready", summary=args.summary)),
        agent_view={
            "audit_status": "pending",
            "summary": "Repo-ready intent accepted; the deterministic Coordinator Step owns the authoritative audit.",
        },
    )


def submit_content_preparation_recon(runtime: Any, ctx: ToolExecutionContext, args: SubmitContentPreparationReconArgs) -> ServiceResult[PreparedSubmissionView]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    round_gate = _require_no_pending_decl_round(runtime, ctx)
    if not round_gate.ok:
        return runtime.foundation.fail(round_gate.issues)
    if not args.objective or not args.objective.strip():
        return _fail(runtime, "preparation_objective_required", "Preparation recon dispatch requires a non-empty objective.", field="objective")
    duplicate = _preparation_already_used(runtime, ctx, args.recon_kind)
    if not duplicate.ok:
        return runtime.foundation.fail(duplicate.issues)
    request = build_preparation_recon_request(
        recon_kind=args.recon_kind,
        repo_key=ctx.repo.repo_key,
        node_path=node.value,
        scope_id=ctx.runtime.scope_id,
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
        scope_id=ctx.runtime.scope_id,
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
    round_gate = _require_no_pending_decl_round(runtime, ctx)
    if not round_gate.ok:
        return runtime.foundation.fail(round_gate.issues)
    return _prepared(
        runtime,
        ContentNodeReadySubmission(**_base_kwargs(ctx, tool_name="submit_content_node_ready", summary=args.summary)),
        agent_view={
            "completion_intent": "accepted",
            "next_step": "deterministic_content_completion_audit",
        },
    )


def submit_content_node_blocked(runtime: Any, ctx: ToolExecutionContext, args: SubmitContentNodeBlockedArgs) -> ServiceResult[PreparedSubmissionView]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    round_gate = _require_no_pending_decl_round(runtime, ctx)
    if not round_gate.ok:
        return runtime.foundation.fail(round_gate.issues)
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
    round_gate = _require_no_pending_decl_round(runtime, ctx)
    if not round_gate.ok:
        return runtime.foundation.fail(round_gate.issues)
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
            dependency_change_summary=args.dependency_change_summary,
            checked_boundary_summary=args.checked_boundary_summary,
            useful_findings=list(args.useful_findings),
            unresolved_within_visible_boundaries=list(args.unresolved_within_visible_boundaries),
        ),
    )


def submit_mathlib_recon_completed(runtime: Any, ctx: ToolExecutionContext, args: SubmitMathlibReconCompletedArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        MathlibReconCompletedSubmission(
            **_base_kwargs(ctx, tool_name="submit_mathlib_recon_completed", summary=args.summary),
            index_update_summary=args.index_update_summary,
            node_mathlib_hint_summary=args.node_mathlib_hint_summary,
            useful_findings=list(args.useful_findings),
            unresolved_in_mathlib=list(args.unresolved_in_mathlib),
        ),
    )


def submit_resource_recon_completed(runtime: Any, ctx: ToolExecutionContext, args: SubmitResourceReconCompletedArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        ResourceReconCompletedSubmission(
            **_base_kwargs(ctx, tool_name="submit_resource_recon_completed", summary=args.summary),
            material_change_summary=args.material_change_summary,
            checked_material_summary=args.checked_material_summary,
            useful_findings=list(args.useful_findings),
            unresolved_material_needs=list(args.unresolved_material_needs),
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
    expected_decl_names = list(ctx.decl_stage.batch_decls if ctx.decl_stage else [])
    if not expected_decl_names:
        return _fail(runtime, "stage_worker_expected_batch_missing", "Stage worker completed submit requires a current expected declaration batch.")
    ready = _decl_stage_candidate_ready(runtime, ctx, stage_name=stage_name, decl_names=expected_decl_names)
    if not ready.ok:
        return runtime.foundation.fail(ready.issues)
    return _prepared(
        runtime,
        DeclStageWorkerCompletedSubmission(
            **_base_kwargs(ctx, tool_name="submit_stage_worker_completed", summary=args.summary),
            stage=stage_name,
            round_id=round_id,
            completed_decl_names=expected_decl_names,
            changed_decl_names=list(args.changed_decl_names),
            notes=args.notes,
        ),
    )


def submit_stage_worker_blocked(runtime: Any, ctx: ToolExecutionContext, args: SubmitStageWorkerBlockedArgs) -> ServiceResult[PreparedSubmissionView]:
    stage = _require_stage(runtime, ctx)
    if not stage.ok or stage.value is None:
        return runtime.foundation.fail(stage.issues)
    stage_name, round_id = stage.value
    expected_decl_names = list(ctx.decl_stage.batch_decls if ctx.decl_stage else [])
    if not expected_decl_names:
        return _fail(runtime, "stage_worker_expected_batch_missing", "Stage worker blocked submit requires a current expected declaration batch.")
    unexpected_decl_names = sorted(set(args.affected_decl_names) - set(expected_decl_names))
    if unexpected_decl_names:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "stage_worker_blocked_decl_outside_batch",
                "Stage worker blocked submit affected_decl_names must belong to the current stage batch.",
                field="affected_decl_names",
                current=", ".join(unexpected_decl_names),
                expected=", ".join(expected_decl_names),
            )
        )
    return _prepared(
        runtime,
        DeclStageWorkerBlockedSubmission(
            **_base_kwargs(ctx, tool_name="submit_stage_worker_blocked", summary=args.reason),
            stage=stage_name,
            round_id=round_id,
            reason=args.reason,
            affected_decl_names=list(args.affected_decl_names),
            checked_context_summary=args.checked_context_summary,
            blocked_needs=list(args.blocked_needs),
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
    step_id = ctx.runtime.step_id
    if not step_id:
        return _fail(runtime, "review_step_context_missing", "Stage review submit requires current ARK step_id.")
    step_service = getattr(runtime.ark, "step_service", None)
    if step_service is None:
        return _fail(runtime, "step_service_missing", "ARK step service is not available.")
    try:
        current_step = step_service.store.get_step(step_id)
    except Exception as exc:
        return _fail(runtime, "review_step_not_found", f"Cannot load current reviewer step: {exc}")
    if current_step.step_type != "decl_stage_reviewer_agent_step" or not isinstance(current_step.state, DeclStageReviewerStepState):
        return _fail(runtime, "review_step_state_invalid", "Stage review submit requires a DeclStageReviewerAgentStep state.")
    step_state = current_step.state
    if step_state.round_id is not None and step_state.round_id != round_id:
        return _fail(runtime, "review_step_round_mismatch", "Reviewer step round does not match current submit context.")
    if step_state.node_path is not None and step_state.node_path != node.value:
        return _fail(runtime, "review_step_node_mismatch", "Reviewer step node does not match current submit context.")
    if step_state.stage is not None and step_state.stage != stage_name:
        return _fail(runtime, "review_step_stage_mismatch", "Reviewer step stage does not match current submit context.")
    expected_decl_names = list(step_state.expected_decl_names or (ctx.decl_stage.batch_decls if ctx.decl_stage else []))
    if not expected_decl_names:
        return _fail(runtime, "review_expected_batch_missing", "Stage review submit requires an expected declaration batch.")
    review = runtime.decl_graph.aggregate_stage_review_marks(
        ctx.repo_root,
        node_path=node.value,
        round_id=round_id,
        stage=stage_name,
        summary=args.summary,
        marks=list(step_state.review_marks),
        expected_decl_names=expected_decl_names,
    )
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
            reviewed_decl_names=list(review.value.reviewed_decl_names),
            failed_decl_names=list(review.value.failed_decl_names),
            missing_decl_names=list(review.value.missing_decl_names),
            feedback=list(review.value.feedback),
        ),
        agent_view=review.value.model_dump(mode="json"),
    )
