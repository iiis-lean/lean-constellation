"""Decl stage worker ordinary tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import DeclNameArgs, DeclReviewMarkArgs, DeclStageFileCheckArgs, DeclStageNlArgs, NoArgs
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


def _write_statement_nl(runtime, ctx, args: DeclStageNlArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    return runtime.decl_graph.write_statement_nl(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx, args.round_id),
        decl_name=args.decl_name,
        nl=args.nl,
        origin=args.origin,
        deps=args.deps,
    )


def _write_proof_nl(runtime, ctx, args: DeclStageNlArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    return runtime.decl_graph.write_proof_nl(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx, args.round_id),
        decl_name=args.decl_name,
        nl=args.nl,
        origin=args.origin,
        deps=args.deps,
    )


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


def _check_file_snapshot_sync(runtime, ctx, args: DeclStageFileCheckArgs):
    return runtime.lean_projection.check_decl_file_snapshot_sync(
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
        stage=args.stage,
    )


def _sync_file_after_reset(runtime, ctx, args: DeclNameArgs):
    return runtime.lean_projection.sync_decl_file_after_revision_reset(ctx.repo_root, node_path=_node(ctx), decl_name=args.decl_name)


def _remove_decl_file(runtime, ctx, args: DeclNameArgs):
    return runtime.lean_projection.remove_decl_file_for_delete(ctx.repo_root, node_path=_node(ctx), decl_name=args.decl_name)


def _check_formal_stage_consistency(runtime, ctx, args: DeclStageFileCheckArgs):
    return runtime.decl_graph.check_formal_stage_consistency(
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
        stage=args.stage,
    )


def _record_decl_review(runtime, ctx, args: DeclReviewMarkArgs):
    return runtime.decl_graph.record_decl_review(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx, args.round_id),
        stage=args.stage,
        decl_name=args.decl_name,
        passed=args.passed,
        summary=args.summary,
        issue_kind=args.issue_kind,
        suggested_fix=args.suggested_fix,
    )


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
            description="Write natural-language statement, origins, and draft deps for a decl in the current statement_nl stage.",
            args_model=DeclStageNlArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_revision",
            groups={"decl_stage_statement_nl_write"},
            roles=worker_roles,
            handler=_write_statement_nl,
        ),
        handler_tool(
            name="write_proof_nl",
            description="Write natural-language proof route, origins, and draft deps for a decl in the current proof_nl stage.",
            args_model=DeclStageNlArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_revision",
            groups={"decl_stage_proof_nl_write"},
            roles=worker_roles,
            handler=_write_proof_nl,
        ),
        handler_tool(
            name="prepare_statement_formal_file",
            description="Prepare the Decl-owned Lean file for statement formalization in the current stage.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="lean_file",
            groups={"decl_stage_statement_formal_file"},
            roles=worker_roles,
            handler=_prepare_statement_file,
        ),
        handler_tool(
            name="capture_statement_formal_file",
            description="Capture/check the statement formal Lean file and save the statement formal snapshot.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="formal_capture",
            groups={"decl_stage_statement_formal_file"},
            roles=worker_roles,
            handler=_capture_statement_file,
        ),
        handler_tool(
            name="prepare_proof_formal_file",
            description="Prepare the Decl-owned Lean file for proof formalization in the current stage.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="lean_file",
            groups={"decl_stage_proof_formal_file"},
            roles=worker_roles,
            handler=_prepare_proof_file,
        ),
        handler_tool(
            name="capture_proof_formal_file",
            description="Capture/check the proof formal Lean file and save the proof formal snapshot.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="formal_capture",
            groups={"decl_stage_proof_formal_file"},
            roles=worker_roles,
            handler=_capture_proof_file,
        ),
        handler_tool(
            name="check_decl_file_snapshot_sync",
            description="Check whether a Decl-owned Lean file is synchronized with the captured DeclRevision snapshot.",
            args_model=DeclStageFileCheckArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={"decl_stage_statement_formal_file", "decl_stage_proof_formal_file"},
            roles=read_roles,
            handler=_check_file_snapshot_sync,
        ),
        handler_tool(
            name="sync_decl_file_after_revision_reset",
            description="Synchronize a Decl-owned Lean file after an update reset.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="mutation",
            groups={"decl_stage_statement_formal_file", "decl_stage_proof_formal_file"},
            roles=worker_roles,
            handler=_sync_file_after_reset,
        ),
        handler_tool(
            name="remove_decl_file_for_delete",
            description="Remove a Decl-owned Lean file for a planned delete operation.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="mutation",
            groups={"decl_stage_statement_formal_file", "decl_stage_proof_formal_file"},
            roles=worker_roles,
            handler=_remove_decl_file,
        ),
        handler_tool(
            name="check_formal_stage_consistency",
            description="Run deterministic consistency checks for one formal stage artifact.",
            args_model=DeclStageFileCheckArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={"decl_stage_statement_formal_file", "decl_stage_proof_formal_file"},
            roles=read_roles,
            handler=_check_formal_stage_consistency,
        ),
        handler_tool(
            name="record_decl_review",
            description="Record the reviewer mark for one declaration in the current DeclGraph stage review.",
            args_model=DeclReviewMarkArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={"decl_stage_review_mark_write"},
            roles={"reviewer", "admin"},
            handler=_record_decl_review,
        ),
        handler_tool(
            name="run_decl_round_local_audit",
            description="Run a local audit for the current decl round and stage.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="audit_report",
            groups={"decl_graph_read_current"},
            roles=read_roles,
            handler=_run_round_local_audit,
        ),
    ]
