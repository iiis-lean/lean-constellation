"""Decl stage worker ordinary tools."""

from __future__ import annotations

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    DeclNameArgs,
    DeclReviewMarkArgs,
    DeclStageFileCheckArgs,
    DeclStageNlArgs,
    NoArgs,
    StatementNlReviewPassedArgs,
    StatementNlReviewRejectedArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import current_node_path, handler_tool


def _node(ctx) -> str:
    return current_node_path(ctx)


def _round_id(ctx, explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    if ctx.decl_stage and ctx.decl_stage.round_id:
        return ctx.decl_stage.round_id
    raise ValueError("round_id is required when current tool context has no decl-stage round.")


def _assert_stage(runtime, ctx, *, expected_stage: str, decl_name: str):
    check = runtime.tool_facade.permission_guard.assert_decl_stage_mutation_allowed(
        ctx,
        stage=expected_stage,
        decl_name=decl_name,
    )
    if not check.ok:
        return runtime.foundation.fail(check.issues)
    return runtime.foundation.ok(None)


def _normalize_formal_check_stage(stage: str) -> str | None:
    normalized = stage.strip().lower()
    if normalized in {"statement", "statement_formal"}:
        return "statement"
    if normalized in {"proof", "proof_formal"}:
        return "proof"
    return None


def _assert_formal_read_stage(runtime, ctx, *, requested_stage: str, decl_name: str):
    if ctx.decl_stage is None:
        return runtime.foundation.fail(runtime.foundation.issue("decl_stage_context_missing", "Current context has no decl stage."))
    current_stage = ctx.decl_stage.stage
    stage_map = {
        "statement_formal": "statement",
        "statement_formal_review": "statement",
        "proof_formal": "proof",
        "proof_formal_review": "proof",
    }
    expected_stage = stage_map.get(current_stage)
    if expected_stage is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_stage_formal_read_rejected",
                "Formal file checks are only available in current formal worker or reviewer stages.",
                object_ref=decl_name,
                current=current_stage,
                expected="statement_formal,statement_formal_review,proof_formal,proof_formal_review",
            )
        )
    normalized_requested = _normalize_formal_check_stage(requested_stage)
    if normalized_requested is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_stage_formal_read_rejected",
                "Formal check stage must be statement or proof.",
                object_ref=decl_name,
                field="stage",
                current=requested_stage,
                expected="statement or proof",
            )
        )
    if normalized_requested != expected_stage:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_stage_formal_read_rejected",
                "Formal check stage does not match the current decl stage.",
                object_ref=decl_name,
                field="stage",
                current=normalized_requested,
                expected=expected_stage,
            )
        )
    if ctx.decl_stage.batch_decls and decl_name not in ctx.decl_stage.batch_decls:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_stage_formal_read_rejected",
                "Decl is not in the current stage batch.",
                object_ref=decl_name,
                expected=",".join(ctx.decl_stage.batch_decls),
            )
        )
    return runtime.foundation.ok(normalized_requested)


def _write_statement_nl(runtime, ctx, args: DeclStageNlArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.write_statement_nl(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx, args.round_id),
        decl_name=args.decl_name,
        nl=args.nl,
        origin=args.origin,
        deps=args.deps,
    )
    if not written.ok:
        return written
    return runtime.decl_graph.current_decl_revision_view(ctx.repo_root, node_path=_node(ctx), name=args.decl_name)


def _write_proof_nl(runtime, ctx, args: DeclStageNlArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.write_proof_nl(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx, args.round_id),
        decl_name=args.decl_name,
        nl=args.nl,
        origin=args.origin,
        deps=args.deps,
    )
    if not written.ok:
        return written
    return runtime.decl_graph.current_decl_revision_view(ctx.repo_root, node_path=_node(ctx), name=args.decl_name)


def _prepare_statement_file(runtime, ctx, args: DeclNameArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_formal", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    return runtime.lean_projection.prepare_statement_formal_stage_file(ctx.repo_root, node_path=_node(ctx), decl_name=args.decl_name)


def _capture_statement_file(runtime, ctx, args: DeclNameArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_formal", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    return runtime.lean_projection.capture_statement_formal(ctx.repo_root, node_path=_node(ctx), decl_name=args.decl_name)


def _prepare_proof_file(runtime, ctx, args: DeclNameArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_formal", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    return runtime.lean_projection.prepare_proof_formal_stage_file(ctx.repo_root, node_path=_node(ctx), decl_name=args.decl_name)


def _capture_proof_file(runtime, ctx, args: DeclNameArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_formal", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    return runtime.lean_projection.capture_proof_formal(ctx.repo_root, node_path=_node(ctx), decl_name=args.decl_name)


def _check_file_capture_sync(runtime, ctx, args: DeclStageFileCheckArgs):
    allowed = _assert_formal_read_stage(runtime, ctx, requested_stage=args.stage, decl_name=args.decl_name)
    if not allowed.ok or allowed.value is None:
        return allowed
    return runtime.lean_projection.check_decl_file_snapshot_sync(
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
        stage=allowed.value,
    )


def _check_formal_stage_consistency(runtime, ctx, args: DeclStageFileCheckArgs):
    allowed = _assert_formal_read_stage(runtime, ctx, requested_stage=args.stage, decl_name=args.decl_name)
    if not allowed.ok or allowed.value is None:
        return allowed
    return runtime.decl_graph.check_formal_stage_consistency(
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
        stage=allowed.value,
    )


def _record_decl_review(runtime, ctx, args: DeclReviewMarkArgs):
    if ctx.decl_stage is None:
        return runtime.foundation.fail(runtime.foundation.issue("decl_stage_context_missing", "Current context has no decl stage."))
    current_round_id = _round_id(ctx)
    if args.round_id != current_round_id:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "review_mark_round_mismatch",
                "Review mark round_id must match the current decl-stage context.",
                object_ref=args.decl_name,
                current=args.round_id,
                expected=current_round_id,
            )
        )
    if args.stage != ctx.decl_stage.stage:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "review_mark_stage_mismatch",
                "Review mark stage must match the current decl-stage context.",
                object_ref=args.decl_name,
                current=args.stage,
                expected=ctx.decl_stage.stage,
            )
        )
    mark = runtime.decl_graph.build_decl_review_mark(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=current_round_id,
        stage=args.stage,
        decl_name=args.decl_name,
        passed=args.passed,
        summary=args.summary,
        issue_kind=args.issue_kind,
        suggested_fix=args.suggested_fix,
    )
    if not mark.ok or mark.value is None:
        return runtime.foundation.fail(mark.issues)
    step = _load_reviewer_step(runtime, ctx)
    if not step.ok or step.value is None:
        return runtime.foundation.fail(step.issues)
    current_step = step.value
    context = _review_step_context(runtime, ctx, current_step.state)
    if not context.ok or context.value is None:
        return runtime.foundation.fail(context.issues)
    expected_decl_names = context.value["expected_decl_names"]
    if args.decl_name not in expected_decl_names:
        return runtime.foundation.fail(
            runtime.foundation.issue("review_decl_not_expected", "Declaration is not in the current reviewer expected batch.", object_ref=args.decl_name)
        )
    return _persist_review_mark(runtime, ctx, current_step.step_id, mark.value)


def _record_statement_nl_review_passed(runtime, ctx, args: StatementNlReviewPassedArgs):
    mark = _build_statement_nl_mark(
        runtime,
        ctx,
        decl_name=args.decl_name,
        passed=True,
        summary=args.summary,
    )
    if not mark.ok or mark.value is None:
        return runtime.foundation.fail(mark.issues)
    return _persist_review_mark(runtime, ctx, ctx.runtime.step_id, mark.value)


def _record_statement_nl_review_rejected(runtime, ctx, args: StatementNlReviewRejectedArgs):
    mark = _build_statement_nl_mark(
        runtime,
        ctx,
        decl_name=args.decl_name,
        passed=False,
        summary=args.summary,
        issue_categories=args.issue_categories,
        required_changes=args.required_changes,
    )
    if not mark.ok or mark.value is None:
        return runtime.foundation.fail(mark.issues)
    return _persist_review_mark(runtime, ctx, ctx.runtime.step_id, mark.value)


def _build_statement_nl_mark(
    runtime,
    ctx,
    *,
    decl_name: str,
    passed: bool,
    summary: str,
    issue_categories: list[str] | None = None,
    required_changes: list[str] | None = None,
):
    if ctx.decl_stage is None:
        return runtime.foundation.fail(runtime.foundation.issue("decl_stage_context_missing", "Current context has no decl stage."))
    if ctx.decl_stage.stage != "statement_nl":
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "review_mark_stage_mismatch",
                "Statement NL review tools are only valid in the statement_nl review context.",
                object_ref=decl_name,
                current=ctx.decl_stage.stage,
                expected="statement_nl",
            )
        )
    step = _load_reviewer_step(runtime, ctx)
    if not step.ok or step.value is None:
        return runtime.foundation.fail(step.issues)
    context = _review_step_context(runtime, ctx, step.value.state)
    if not context.ok or context.value is None:
        return runtime.foundation.fail(context.issues)
    expected_decl_names = context.value["expected_decl_names"]
    if decl_name not in expected_decl_names:
        return runtime.foundation.fail(
            runtime.foundation.issue("review_decl_not_expected", "Declaration is not in the current reviewer expected batch.", object_ref=decl_name)
        )
    return runtime.decl_graph.build_decl_review_mark(
        ctx.repo_root,
        node_path=context.value["node_path"],
        round_id=context.value["round_id"],
        stage="statement_nl",
        decl_name=decl_name,
        passed=passed,
        summary=summary,
        issue_categories=issue_categories,
        required_changes=required_changes,
    )


def _inspect_current_stage_review_status(runtime, ctx, _args: NoArgs):
    step = _load_reviewer_step(runtime, ctx)
    if not step.ok or step.value is None:
        return runtime.foundation.fail(step.issues)
    context = _review_step_context(runtime, ctx, step.value.state)
    if not context.ok or context.value is None:
        return runtime.foundation.fail(context.issues)
    expected_decl_names = context.value["expected_decl_names"]
    marks = list(step.value.state.review_marks)
    invalid_context_marks: list[dict[str, object]] = []
    mark_by_decl = {}
    for mark in marks:
        reasons: list[str] = []
        if mark.round_id != context.value["round_id"]:
            reasons.append("round_id")
        if mark.node_path != context.value["node_path"]:
            reasons.append("node_path")
        if mark.stage.value != context.value["stage"]:
            reasons.append("stage")
        if mark.decl_name not in expected_decl_names:
            reasons.append("decl_name")
        if not mark.passed and not mark.required_changes:
            reasons.append("required_changes")
        if mark.decl_name in mark_by_decl:
            reasons.append("duplicate")
        if reasons:
            invalid_context_marks.append(
                {
                    "decl_name": mark.decl_name,
                    "round_id": mark.round_id,
                    "node_path": mark.node_path,
                    "stage": mark.stage.value,
                    "reasons": reasons,
                }
            )
            continue
        mark_by_decl[mark.decl_name] = mark
    reviewed = sorted(mark_by_decl)
    passed = sorted(name for name, mark in mark_by_decl.items() if mark.passed)
    failed = sorted(name for name, mark in mark_by_decl.items() if not mark.passed)
    missing = sorted(name for name in expected_decl_names if name not in mark_by_decl)
    return runtime.foundation.ok(
        {
            "round_id": context.value["round_id"],
            "node_path": context.value["node_path"],
            "stage": context.value["stage"],
            "expected_decl_names": expected_decl_names,
            "reviewed_decl_names": reviewed,
            "passed_decl_names": passed,
            "failed_decl_names": failed,
            "missing_decl_names": missing,
            "invalid_context_marks": invalid_context_marks,
            "ready_to_submit": not missing and not invalid_context_marks,
            "marks": [runtime.decl_graph.review_mark_view(mark).model_dump(mode="json") for mark in marks],
        }
    )


def _load_reviewer_step(runtime, ctx):
    step_id = ctx.runtime.step_id
    if not step_id:
        return runtime.foundation.fail(runtime.foundation.issue("review_step_context_missing", "Review mark recording requires current ARK step_id."))
    step_service = getattr(runtime.ark, "step_service", None)
    if step_service is None:
        return runtime.foundation.fail(runtime.foundation.issue("step_service_missing", "ARK step service is not available."))
    try:
        current_step = step_service.store.get_step(step_id)
    except Exception as exc:
        return runtime.foundation.fail(runtime.foundation.issue("review_step_not_found", f"Cannot load current reviewer step: {exc}", object_ref=step_id))
    if current_step.step_type != "decl_stage_reviewer_agent_step" or not isinstance(current_step.state, DeclStageReviewerStepState):
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "review_step_state_invalid",
                "Review marks can only be recorded on a DeclStageReviewerAgentStep with DeclStageReviewerStepState.",
                object_ref=step_id,
                current=f"{current_step.step_type}:{type(current_step.state).__name__}",
            )
        )
    return runtime.foundation.ok(current_step)


def _review_step_context(runtime, ctx, state: DeclStageReviewerStepState):
    if ctx.decl_stage is None:
        return runtime.foundation.fail(runtime.foundation.issue("decl_stage_context_missing", "Current context has no decl stage."))
    round_id = _round_id(ctx)
    node_path = _node(ctx)
    stage = ctx.decl_stage.stage
    expected_decl_names = list(state.expected_decl_names or ctx.decl_stage.batch_decls)
    if not expected_decl_names:
        return runtime.foundation.fail(runtime.foundation.issue("review_expected_batch_missing", "Reviewer step has no expected declaration batch."))
    issues = []
    if state.round_id is not None and state.round_id != round_id:
        issues.append(runtime.foundation.issue("review_step_round_mismatch", "Reviewer step round does not match current context.", current=state.round_id, expected=round_id))
    if state.node_path is not None and state.node_path != node_path:
        issues.append(runtime.foundation.issue("review_step_node_mismatch", "Reviewer step node does not match current context.", current=state.node_path, expected=node_path))
    if state.stage is not None and state.stage != stage:
        issues.append(runtime.foundation.issue("review_step_stage_mismatch", "Reviewer step stage does not match current context.", current=state.stage, expected=stage))
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(
        {
            "round_id": round_id,
            "node_path": node_path,
            "stage": stage,
            "expected_decl_names": sorted(set(expected_decl_names)),
        }
    )


def _persist_review_mark(runtime, ctx, step_id: str | None, mark):
    if step_id is None:
        return runtime.foundation.fail(runtime.foundation.issue("review_step_context_missing", "Review mark recording requires current ARK step_id."))
    step_service = getattr(runtime.ark, "step_service", None)
    if step_service is None:
        return runtime.foundation.fail(runtime.foundation.issue("step_service_missing", "ARK step service is not available."))

    def update_review_marks(step) -> None:
        state = step.state
        if isinstance(state, DeclStageReviewerStepState):
            state.round_id = state.round_id or mark.round_id
            state.node_path = state.node_path or mark.node_path
            state.stage = state.stage or mark.stage.value
            if not state.expected_decl_names and ctx.decl_stage is not None:
                state.expected_decl_names = list(ctx.decl_stage.batch_decls)
        state.review_marks = [
            item
            for item in state.review_marks
            if not (
                item.round_id == mark.round_id
                and item.node_path == mark.node_path
                and item.stage == mark.stage
                and item.decl_name == mark.decl_name
            )
        ]
        state.review_marks.append(mark)

    try:
        step_service.store.update_step_record(step_id, update_review_marks)
    except Exception as exc:
        return runtime.foundation.fail(
            runtime.foundation.issue("review_step_update_failed", f"Cannot persist review mark on step state: {exc}", object_ref=step_id)
        )
    return runtime.foundation.ok(runtime.decl_graph.review_mark_view(mark))


def _run_round_local_audit(runtime, ctx, args: NoArgs):
    if ctx.decl_stage is None or not ctx.decl_stage.stage:
        return runtime.foundation.fail(runtime.foundation.issue("decl_stage_context_missing", "Current context has no decl stage."))
    return runtime.decl_graph.run_round_local_audit(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        stage=ctx.decl_stage.stage,
    )


def build_tool_specs() -> list[ToolSpec]:
    worker_roles = {"worker", "admin"}
    read_roles = {"worker", "reviewer", "plan", "admin"}
    return [
        handler_tool(
            name="write_statement_nl",
            description="Write natural-language statement text, supporting origins, and draft dependencies for one declaration in the current statement natural-language stage.",
            args_model=DeclStageNlArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_revision",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE},
            roles=worker_roles,
            handler=_write_statement_nl,
        ),
        handler_tool(
            name="write_proof_nl",
            description="Write a natural-language proof route, supporting origins, and draft proof dependencies for one declaration in the current proof natural-language stage.",
            args_model=DeclStageNlArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_revision",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE},
            roles=worker_roles,
            handler=_write_proof_nl,
        ),
        handler_tool(
            name="prepare_statement_formal_file",
            description="Create or refresh the declaration-owned Lean file scaffold for statement formalization in the current stage.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="lean_file",
            groups={AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE_WRITE},
            roles=worker_roles,
            handler=_prepare_statement_file,
        ),
        handler_tool(
            name="capture_statement_formal_file",
            description="Run capture checks on the statement formal Lean file and save the accepted statement formal capture into the current DeclRevision.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="formal_capture",
            groups={AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE_WRITE},
            roles=worker_roles,
            handler=_capture_statement_file,
        ),
        handler_tool(
            name="prepare_proof_formal_file",
            description="Create or refresh the declaration-owned Lean file scaffold for proof formalization in the current stage.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="lean_file",
            groups={AppGroup.DECL_STAGE_PROOF_FORMAL_FILE_WRITE},
            roles=worker_roles,
            handler=_prepare_proof_file,
        ),
        handler_tool(
            name="capture_proof_formal_file",
            description="Run capture checks on the proof formal Lean file and save the accepted proof formal capture into the current DeclRevision.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="formal_capture",
            groups={AppGroup.DECL_STAGE_PROOF_FORMAL_FILE_WRITE},
            roles=worker_roles,
            handler=_capture_proof_file,
        ),
        handler_tool(
            name="check_decl_file_snapshot_sync",
            description="Check whether a declaration-owned Lean file still matches the captured formal file stored in the DeclRevision.",
            args_model=DeclStageFileCheckArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE, AppGroup.DECL_STAGE_PROOF_FORMAL_FILE},
            roles=read_roles,
            handler=_check_file_capture_sync,
        ),
        handler_tool(
            name="check_formal_stage_consistency",
            description="Run deterministic consistency checks between a formal stage artifact, its DeclRevision state, and the current round context.",
            args_model=DeclStageFileCheckArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE, AppGroup.DECL_STAGE_PROOF_FORMAL_FILE},
            roles=read_roles,
            handler=_check_formal_stage_consistency,
        ),
        handler_tool(
            name="record_decl_review",
            description="Record the reviewer pass/fail mark, issue category, and suggested fix for one declaration in the current DeclGraph stage review.",
            args_model=DeclReviewMarkArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_decl_review,
        ),
        handler_tool(
            name="record_statement_nl_review_passed",
            description="Record a passed review mark for one declaration in the current Statement NL review step.",
            args_model=StatementNlReviewPassedArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_statement_nl_review_passed,
        ),
        handler_tool(
            name="record_statement_nl_review_rejected",
            description="Record a rejected review mark with issue categories and required changes for one declaration in the current Statement NL review step.",
            args_model=StatementNlReviewRejectedArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_statement_nl_review_rejected,
        ),
        handler_tool(
            name="inspect_current_stage_review_status",
            description="Inspect mark coverage and submit readiness for the current decl-stage reviewer step.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="stage_review_status",
            groups={AppGroup.DECL_STAGE_REVIEW_STATUS_READ},
            roles={"reviewer", "admin"},
            handler=_inspect_current_stage_review_status,
        ),
        handler_tool(
            name="run_decl_round_local_audit",
            description="Run a local audit for the current declaration round and stage using the current decl-stage tool context.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="audit_report",
            groups={AppGroup.DECL_GRAPH_READ_CURRENT},
            roles=read_roles,
            handler=_run_round_local_audit,
        ),
    ]
