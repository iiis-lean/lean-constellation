"""Submit tool handlers that build typed ARK submissions after service gates."""

from __future__ import annotations

import re
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
from lean_constellation.domain.interface import DeclKind
from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services.decl_graph.models import DeclState
from lean_constellation.services.decl_graph.proof_nl_validation import validate_proof_deps, validate_proof_nl_candidate
from lean_constellation.services.decl_graph.statement_nl_validation import validate_statement_deps, validate_statement_nl_candidate
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
from lean_constellation.tools.source_index_ownership import authorize_source_index_flow_context
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


def _decl_stage_candidate_ready(runtime: Any, ctx: ToolExecutionContext, *, stage_name: str, decl_names: list[str]) -> ServiceResult[None]:
    node = _require_node(runtime, ctx)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    issues = []
    for decl_name in decl_names:
        revision = runtime.decl_graph.current_decl_revision_view(ctx.repo_root, node_path=node.value, name=decl_name)
        if not revision.ok or revision.value is None:
            return runtime.foundation.fail(revision.issues)
        if stage_name == "statement_nl":
            validation = validate_statement_nl_candidate(runtime, ctx.repo_root, node_path=node.value, round_id=ctx.decl_stage.round_id, decl_name=decl_name)
            if not validation.ok:
                issues.extend(validation.issues)
        elif stage_name == "statement_formal":
            if not revision.value.statement_lean_code:
                issues.append(runtime.foundation.issue("statement_formal_candidate_missing", "Statement formal candidate is missing.", object_ref=decl_name))
            if revision.value.statement_lean_check is None:
                issues.append(runtime.foundation.issue("statement_formal_check_missing", "Statement formal Lean check is missing.", object_ref=decl_name))
            formal = _formal_candidate_gate(runtime, ctx, node_path=node.value, decl_name=decl_name, stage="statement")
            if not formal.ok:
                issues.extend(formal.issues)
            deps = _statement_dep_gate(runtime, ctx, node_path=node.value, decl_name=decl_name, stage_label="Statement formal")
            if not deps.ok:
                issues.extend(deps.issues)
        elif stage_name == "proof_nl":
            validation = validate_proof_nl_candidate(runtime, ctx.repo_root, node_path=node.value, round_id=ctx.decl_stage.round_id, decl_name=decl_name)
            if not validation.ok:
                issues.extend(validation.issues)
        elif stage_name == "proof_formal":
            if not revision.value.proof_lean_code:
                issues.append(runtime.foundation.issue("proof_formal_candidate_missing", "Proof formal candidate is missing.", object_ref=decl_name))
            if revision.value.proof_lean_check is None:
                issues.append(runtime.foundation.issue("proof_formal_check_missing", "Proof formal Lean check is missing.", object_ref=decl_name))
            if not revision.value.proof_nl:
                issues.append(runtime.foundation.issue("proof_nl_candidate_missing", "Proof Formal submit requires an accepted Proof NL route.", object_ref=decl_name))
            formal = _formal_candidate_gate(runtime, ctx, node_path=node.value, decl_name=decl_name, stage="proof")
            if not formal.ok:
                issues.extend(formal.issues)
            deps = _proof_dep_gate(runtime, ctx, node_path=node.value, decl_name=decl_name)
            if not deps.ok:
                issues.extend(deps.issues)
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(None)


def _formal_candidate_gate(runtime: Any, ctx: ToolExecutionContext, *, node_path: str, decl_name: str, stage: str) -> ServiceResult[None]:
    sync = runtime.lean_projection.check_decl_file_snapshot_sync(ctx.repo_root, node_path=node_path, decl_name=decl_name, stage=stage)
    if not sync.ok or sync.value is None:
        return runtime.foundation.fail(sync.issues)
    if not sync.value.passed:
        return runtime.foundation.fail(sync.value.issues)
    consistency = runtime.decl_graph.check_formal_stage_consistency(ctx.repo_root, node_path=node_path, decl_name=decl_name, stage=stage)
    if not consistency.ok or consistency.value is None:
        return runtime.foundation.fail(consistency.issues)
    if not consistency.value.passed:
        return runtime.foundation.fail(consistency.value.issues)
    return runtime.foundation.ok(None)


def _statement_dep_gate(runtime: Any, ctx: ToolExecutionContext, *, node_path: str, decl_name: str, stage_label: str) -> ServiceResult[None]:
    del stage_label
    decl = runtime.decl_graph.get_decl(ctx.repo_root, node_path=node_path, name=decl_name)
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    revision = runtime.decl_graph.get_decl_revision(ctx.repo_root, node_path=node_path, name=decl_name, revision=decl.value.current_revision)
    if not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues)
    round_id = ctx.decl_stage.round_id if ctx.decl_stage is not None else None
    return validate_statement_deps(runtime, ctx.repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, deps=revision.value.statement.deps)


def _proof_dep_gate(runtime: Any, ctx: ToolExecutionContext, *, node_path: str, decl_name: str) -> ServiceResult[None]:
    decl = runtime.decl_graph.get_decl(ctx.repo_root, node_path=node_path, name=decl_name)
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    revision = runtime.decl_graph.get_decl_revision(ctx.repo_root, node_path=node_path, name=decl_name, revision=decl.value.current_revision)
    if not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues)
    deps = list(revision.value.proof.deps) if revision.value.proof is not None else []
    round_id = ctx.decl_stage.round_id if ctx.decl_stage is not None else None
    return validate_proof_deps(runtime, ctx.repo_root, node_path=node_path, round_id=round_id, decl_name=decl_name, deps=deps)


def _decl_state_rank(state: DeclState) -> int:
    return {
        DeclState.OBSOLETE: -1,
        DeclState.PLANNED: 0,
        DeclState.SPECIFIED: 1,
        DeclState.DECLARED: 2,
        DeclState.PROOF_PLANNED: 3,
        DeclState.PROVED: 4,
    }[state]


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
    evidence_summary = args.evidence_summary.strip()
    if not evidence_summary:
        return _fail(runtime, "evidence_summary_required", "Adapter repo choice requires non-empty evidence_summary.", field="evidence_summary")
    return _prepared(
        runtime,
        RepoFormatAdapterChoiceSubmission(
            **_base_kwargs(ctx, tool_name="submit_adapter_repo_choice", summary=evidence_summary),
            git_url=git_url,
            revision=args.revision.strip() if args.revision else None,
            subdir=args.subdir.strip().strip("/") if args.subdir else None,
            package_name=args.package_name.strip() if args.package_name else None,
            likely_import_module=args.likely_import_module.strip() if args.likely_import_module else None,
            evidence_summary=evidence_summary,
            known_risks=[risk.strip() for risk in args.known_risks if risk.strip()],
        ),
    )


def submit_native_repo_choice(runtime: Any, ctx: ToolExecutionContext, args: SubmitNativeRepoChoiceArgs) -> ServiceResult[PreparedSubmissionView]:
    return _prepared(
        runtime,
        RepoFormatNativeChoiceSubmission(
            **_base_kwargs(ctx, tool_name="submit_native_repo_choice", summary=args.summary),
            searched_targets=[target.strip() for target in args.searched_targets if target.strip()],
            rejected_candidates=[
                {
                    "git_url": candidate.git_url.strip() if candidate.git_url else None,
                    "name": candidate.name.strip() if candidate.name else None,
                    "reason": candidate.reason.strip(),
                    "evidence_summary": candidate.evidence_summary.strip() if candidate.evidence_summary else None,
                }
                for candidate in args.rejected_candidates
                if candidate.reason.strip()
            ],
        ),
    )


def submit_source_corpus_prepared(runtime: Any, ctx: ToolExecutionContext, args: SubmitSourceCorpusPreparedArgs) -> ServiceResult[PreparedSubmissionView]:
    expected_relpath = _expected_source_corpus_relpath(runtime, ctx)
    gate = runtime.material.check_source_corpus_prepared(
        ctx.repo_root,
        entry_path=args.entry_path,
        overview=args.overview,
        preparation_summary=args.preparation_summary,
        relpath=expected_relpath,
    )
    if not gate.ok or gate.value is None:
        return runtime.foundation.fail(gate.issues)
    return _prepared(
        runtime,
        SourceCorpusPreparedSubmission(
            **_base_kwargs(ctx, tool_name="submit_source_corpus_prepared", summary=args.summary),
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
        suggested_repo_name=args.suggested_repo_name,
        required_interfaces_hint=args.required_interfaces_hint,
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
            suggested_repo_name=args.suggested_repo_name,
            required_interfaces_hint=args.required_interfaces_hint,
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
            task_mode=args.task_mode,
            max_parallel_content_node_tasks=max_parallel,
        ))
    return _prepared(
        runtime,
        CoordinatorContentTasksSubmission(
            **_dispatch_kwargs(ctx, tool_name="submit_content_node_tasks", requests=requests, summary=args.summary),
            node_paths=args.node_paths,
            task_mode=args.task_mode,
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


def submit_repo_requirement(runtime: Any, ctx: ToolExecutionContext, args: SubmitRepoRequirementArgs) -> ServiceResult[PreparedSubmissionView]:
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
        try:
            kind = DeclKind(item.kind)
        except ValueError as exc:
            return runtime.foundation.fail(runtime.foundation.issue("requirement_interface_kind_invalid", str(exc), field="interfaces.kind"))
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
                "kind": kind.value,
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
            **_base_kwargs(ctx, tool_name="submit_repo_requirement", summary=args.summary),
            requirement_name=requirement_name,
            target_repo=target_repo,
            required_proof_availability=required_proof_availability,
            source_description=args.source_description.strip() if args.source_description else None,
            reason=args.reason.strip() if args.reason else None,
            interfaces=interfaces,
        ),
        agent_view={
            "requirement_name": requirement_name,
            "target_repo": target_repo,
            "required_proof_availability": str(required_proof_availability),
            "interfaces": interfaces,
            "summary": "Requirement submission validated; waiting state will be recorded after the submission is accepted.",
        },
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
    run_context = getattr(flow_input, "run_context", None)
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
    preview = runtime.validation_snapshot.preview_candidate_release(
        ctx.repo_root,
        base_release_id=getattr(run_context, "base_release_id", None),
        summary=args.summary,
    )
    if not preview.ok or preview.value is None:
        return runtime.foundation.fail(preview.issues)
    passed = _gate_or_fail(runtime, preview.value.gate)
    if not passed.ok:
        return runtime.foundation.fail(passed.issues)
    return _prepared(
        runtime,
        CoordinatorRepoReadySubmission(**_base_kwargs(ctx, tool_name="submit_repo_ready", summary=args.summary)),
        agent_view={
            "gate": preview.value.gate.model_dump(mode="json"),
            "blocking_issue_kinds": list(preview.value.blocking_issue_kinds),
            "summary": preview.value.summary,
        },
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
    completion = runtime.validation_snapshot.check_content_node_completion(ctx.repo_root, node_path=node.value)
    if not completion.ok or completion.value is None:
        return runtime.foundation.fail(completion.issues)
    passed = _gate_or_fail(runtime, completion.value.gate)
    if not passed.ok:
        return runtime.foundation.fail(passed.issues)
    return _prepared(
        runtime,
        ContentNodeReadySubmission(**_base_kwargs(ctx, tool_name="submit_content_node_ready", summary=args.summary)),
        agent_view={"completion": completion.value.model_dump(mode="json")},
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
