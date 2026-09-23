"""Agent-facing tools for explicit Restructure declaration work."""

from __future__ import annotations

from typing import Literal
from functools import wraps
from pathlib import Path

from pydantic import Field

from lean_constellation.domain.restructure import (
    DeclKind,
    DeclStatus,
    RepoPlan,
    ReviewOutcome,
    ReviewReceipt,
    SectionInput,
    RestructureStage,
)
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.restructure.submissions import (
    RestructureContentSubmission,
    RestructureRepoPlanSubmission,
    RestructureRepoRepairSubmission,
    RestructureReviewSubmission,
)
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.restructure import ContentGateError, RestructureService
from lean_constellation.services.tool_facade import PreparedSubmissionView, ToolCapability, ToolExecutionContext, ToolSpec
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.keys import SubmitToolGroupKey as SubmitGroup
from lean_constellation.tools.specs import handler_tool, submit_handler_tool
from lean_constellation.domain.common import StrictModel


class RestructureNodeArgs(StrictModel):
    node_path: str


class RestructureRepoArgs(StrictModel):
    repo_key: str | None = None


class RestructureRepoPlanArgs(StrictModel):
    plan: RepoPlan
    expected_version: int | None = None


class RestructureCreateDeclArgs(RestructureNodeArgs):
    name: str
    lean_name: str
    kind: DeclKind
    summary: str
    file_group: str | None = None


class RestructureSetDeclArgs(RestructureNodeArgs):
    name: str
    statement: SectionInput | None = None
    proof: SectionInput | None = None


class RestructureDeclFileArgs(RestructureNodeArgs):
    name: str


class RestructureEditDeclFileArgs(RestructureDeclFileArgs):
    content: str
    stage: RestructureStage = RestructureStage.DECLARED


class RestructureSupportFileArgs(RestructureNodeArgs):
    relative_file: str


class RestructureVisibleDeclArgs(StrictModel):
    repo_key: str | None = None
    node_path: str | None = None


class RestructureCheckArgs(RestructureNodeArgs):
    stage: RestructureStage = RestructureStage.DECLARED


class RestructureSubmitContentArgs(RestructureNodeArgs):
    stage: Literal["declared", "proved"]
    outcome: Literal["declared", "proved", "blocked"]
    issues: list[str] = Field(default_factory=list)


class RestructureSubmitPlanArgs(StrictModel):
    outcome: Literal["planned", "blocked"]
    plan_version: int | None = None
    issues: list[str] = Field(default_factory=list)


class RestructureSubmitRepairArgs(StrictModel):
    outcome: Literal["repaired", "blocked"]
    issues: list[str] = Field(default_factory=list)


class RestructureSubmitReviewArgs(StrictModel):
    artifact_id: str
    outcome: Literal["passed", "needs_content_fix", "needs_replan", "blocked"]
    findings: list[str] = Field(default_factory=list)


def current_task(handler):
    """Bind tool mutations to the current ARK task, including retry epoch."""
    @wraps(handler)
    def guarded(runtime, ctx, args):
        try:
            flow = runtime.ark.flow_service.get_flow(ctx.runtime.flow_id)
            step = runtime.ark.flow_service.get_step(ctx.runtime.step_id)
            inp = flow.input
            service = RestructureService(inp.workspace_root)
            if Path(inp.workspace_root).resolve() / inp.directory != ctx.repo_root.resolve():
                raise ValueError("task repo does not match MCP repo")
            if flow.current_step_id != step.step_id or step.agent_bindings.get(step.state.agent_role) != ctx.runtime.agent_id:
                raise ValueError("tool caller is not the active task Agent")
            with service.store._lock:
                run, _ = service.store.load_run()
                binding = next((b for b in run.bindings.values() if b.request_id == inp.request_id and not b.stale), None)
                if binding is None or binding.terminal_consumed or binding.flow_id != flow.flow_id:
                    raise ValueError("task binding is stale")
                if getattr(args, "node_path", None) is not None and hasattr(inp, "node_path") and args.node_path != inp.node_path:
                    raise ValueError("tool node does not match assigned Content")
                if hasattr(args, "stage") and hasattr(inp, "stage") and args.stage != inp.stage:
                    raise ValueError("tool stage does not match assigned stage")
                if hasattr(args, "plan") and args.plan.repo_key != inp.repo_key:
                    raise ValueError("plan belongs to a different repo")
                if hasattr(inp, "node_path"):
                    try:
                        work, _ = service.content.load(inp.directory, inp.node_path)
                    except KeyError:
                        work = None
                    if work is not None and work.attempt_epoch != inp.attempt_epoch:
                        raise ValueError("task epoch is stale")
                if handler.__name__ not in {"_check_files", "_check_content", "_submit_content", "_submit_repair"}:
                    return handler(runtime, ctx, args)
            return handler(runtime, ctx, args)
        except (ValueError, KeyError, AttributeError) as exc:
            return runtime.foundation.fail(runtime.foundation.issue("restructure_task_invalid", str(exc)))
    return guarded


def _service(ctx: ToolExecutionContext) -> tuple[RestructureService, str]:
    workspace = ctx.workspace_root or ctx.repo_root.parent
    service = RestructureService(workspace)
    workspace_plan, _ = service.store.load_workspace_plan()
    if workspace_plan is None:
        return service, ctx.repo_root.name
    try:
        return service, workspace_plan.repo_key_for_directory(ctx.repo_root.name)
    except KeyError:
        if ctx.repo.repo_key in workspace_plan.repos:
            return service, ctx.repo.repo_key
        return service, ctx.repo_root.name


def _directory(service: RestructureService, repo_ref: str) -> str:
    return service.directory_for_repo(repo_ref)


def _read_content(runtime, ctx, args: RestructureNodeArgs):
    service, repo_ref = _service(ctx)
    try:
        work = service.content_status(repo_ref, args.node_path)
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return runtime.foundation.fail(runtime.foundation.issue("restructure_content_read_failed", str(exc)))
    return runtime.foundation.ok(work.model_dump(mode="json"))


def _read_workspace(runtime, ctx, args: RestructureRepoArgs):
    del args
    service, _ = _service(ctx)
    plan, version = service.store.load_workspace_plan()
    if plan is None:
        return runtime.foundation.fail(runtime.foundation.issue("restructure_workspace_missing", "Workspace plan is not prepared."))
    return runtime.foundation.ok({"plan": plan.model_dump(mode="json"), "version": version})


def _read_repo_plan(runtime, ctx, args: RestructureRepoArgs):
    service, fallback = _service(ctx)
    repo_key = args.repo_key or fallback
    try:
        plan, version = service.load_repo_plan(repo_key)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_repo_plan_read_failed", str(exc)))
    return runtime.foundation.ok({"plan": plan.model_dump(mode="json"), "version": version})


def _read_content_plan(runtime, ctx, args: RestructureRepoArgs):
    return _read_repo_plan(runtime, ctx, args)


@current_task
def _upsert_repo_plan(runtime, ctx, args: RestructureRepoPlanArgs):
    service, _ = _service(ctx)
    try:
        version = service.submit_repo_plan(args.plan, expected_version=args.expected_version)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_repo_plan_write_failed", str(exc)))
    return runtime.foundation.ok({"repo_key": args.plan.repo_key, "version": version, "plan": args.plan.model_dump(mode="json")})


@current_task
def _create_decl(runtime, ctx, args: RestructureCreateDeclArgs):
    service, repo_ref = _service(ctx)
    try:
        directory = service.directory_for_repo(repo_ref)
    except Exception:
        return runtime.foundation.fail(runtime.foundation.issue("restructure_workspace_missing", "Workspace plan is not prepared."))
    repo = service.repo_spec(repo_ref)
    try:
        decl = service.content.create_decl(directory, repo.module_root, args.node_path, name=args.name, lean_name=args.lean_name, kind=args.kind, summary=args.summary, file_group=args.file_group)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_decl_create_failed", str(exc)))
    return runtime.foundation.ok(decl.model_dump(mode="json"))


@current_task
def _set_decl(runtime, ctx, args: RestructureSetDeclArgs):
    service, repo_ref = _service(ctx)
    try:
        decl = service.content.set_sections(service.directory_for_repo(repo_ref), args.node_path, args.name, statement=args.statement, proof=args.proof)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_decl_update_failed", str(exc)))
    return runtime.foundation.ok(decl.model_dump(mode="json"))


@current_task
def _check_content(runtime, ctx, args: RestructureCheckArgs):
    service, repo_ref = _service(ctx)
    flow = runtime.ark.flow_service.get_flow(ctx.runtime.flow_id)
    from lean_constellation.services.restructure.checks import check_content
    result = check_content(service, service.directory_for_repo(repo_ref), [args.node_path], args.stage,
        allow_declared_repair=flow.flow_type == "restructure_build" and flow.input.stage == "declared")
    return runtime.foundation.ok(result)


class RestructureReportArgs(StrictModel):
    report_id: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=300)


@current_task
def _read_report(runtime, ctx, args: RestructureReportArgs):
    service, repo_ref = _service(ctx)
    if Path(args.report_id).name != args.report_id or not args.report_id.startswith(("check_", "build_")):
        raise ValueError("invalid report id")
    path = service.store.repo_metadata_root(service.directory_for_repo(repo_ref)) / "builds" / (args.report_id + ".log")
    lines = path.read_text(errors="replace").splitlines()
    return runtime.foundation.ok(dict(lines=lines[args.offset:args.offset+args.limit],
                                     total_lines=len(lines), next_offset=min(len(lines), args.offset+args.limit)))


class RestructureCheckFilesArgs(StrictModel):
    files: list[str] = Field(min_length=1)


@current_task
def _check_files(runtime, ctx, args: RestructureCheckFilesArgs):
    service, repo_ref = _service(ctx)
    directory = service.directory_for_repo(repo_ref)
    flow = runtime.ark.flow_service.get_flow(ctx.runtime.flow_id)
    if hasattr(flow.input, "node_path"):
        work, _ = service.content.load(directory, flow.input.node_path)
        allowed = {d.file for d in work.decls.values()} | set(work.support_files)
        if not set(args.files) <= allowed:
            raise ValueError("check files must belong to the assigned Content")
    from lean_constellation.services.restructure.checks import check_files
    result = check_files(service, directory, args.files)
    result.pop('_sources')
    return runtime.foundation.ok(result)


def _read_decl_file(runtime, ctx, args: RestructureDeclFileArgs):
    service, repo_ref = _service(ctx)
    try:
        text = service.content.read_decl_file(service.directory_for_repo(repo_ref), args.node_path, args.name)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_decl_file_read_failed", str(exc)))
    return runtime.foundation.ok({"name": args.name, "content": text})


@current_task
def _edit_decl_file(runtime, ctx, args: RestructureEditDeclFileArgs):
    service, repo_ref = _service(ctx)
    try:
        service.content.edit_decl_file(service.directory_for_repo(repo_ref), args.node_path, args.name, args.content, stage=args.stage)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_decl_file_write_failed", str(exc)))
    return runtime.foundation.ok({"name": args.name, "written": True})


@current_task
def _register_support_file(runtime, ctx, args: RestructureSupportFileArgs):
    service, repo_ref = _service(ctx)
    try:
        work = service.content.register_support_file(service.directory_for_repo(repo_ref), args.node_path, args.relative_file)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_support_file_failed", str(exc)))
    return runtime.foundation.ok(work.model_dump(mode="json"))


def _read_visible_decls(runtime, ctx, args: RestructureVisibleDeclArgs):
    service, fallback = _service(ctx)
    repo_ref = args.repo_key or fallback
    try:
        plan, _ = service.load_repo_plan(repo_ref)
        directory = service.directory_for_repo(repo_ref)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_visible_decl_read_failed", str(exc)))
    result: list[dict[str, object]] = []
    paths = [args.node_path] if args.node_path else [node.path for node in plan.content_nodes()]
    for node_path in paths:
        try:
            work, _ = service.content.load(directory, node_path)
        except KeyError:
            continue
        for decl in work.decls.values():
            if decl.status in {DeclStatus.DECLARED, DeclStatus.PROVED}:
                result.append({"repo_key": repo_ref, "node_path": node_path, **decl.model_dump(mode="json")})
    return runtime.foundation.ok({"declarations": result})


def _read_decl_dependencies(runtime, ctx, args: RestructureDeclFileArgs):
    service, repo_ref = _service(ctx)
    try:
        work, _ = service.content.load(service.directory_for_repo(repo_ref), args.node_path)
        decl = work.decls[args.name]
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_decl_dependency_read_failed", str(exc)))
    return runtime.foundation.ok({"name": decl.name, "statement": {"deps": [item.model_dump(mode="json") for item in decl.statement.deps], "origins": [item.model_dump(mode="json") for item in decl.statement.nl.origins]}, "proof": {"deps": [item.model_dump(mode="json") for item in decl.proof.deps], "origins": [item.model_dump(mode="json") for item in decl.proof.nl.origins]} if decl.proof else None})


def _prepared(runtime, submission, summary: str):
    return runtime.foundation.ok(PreparedSubmissionView(submission=submission, summary=summary, agent_view={}))


@current_task
def _submit_content(runtime, ctx, args: RestructureSubmitContentArgs):
    service, repo_ref = _service(ctx)
    directory = service.directory_for_repo(repo_ref)
    stage = RestructureStage(args.stage)
    if args.outcome not in {"blocked", args.stage}:
        raise ValueError("submission outcome must match stage")
    if args.outcome != "blocked":
        try:
            from lean_constellation.services.restructure.checks import check_content
            checked = check_content(service, directory, [args.node_path], stage)
            if not checked["passed"]:
                return runtime.foundation.fail(runtime.foundation.issue("restructure_check_failed", "Batch check failed; repair the reported files.", details=checked))
        except ContentGateError as exc:
            return runtime.foundation.fail(runtime.foundation.issue("restructure_content_gate_failed", str(exc)))
    submission = RestructureContentSubmission(
        submission_id=new_submission_id("restructure_content"),
        tool_name="submit_restructure_content",
        repo_key=service.repo_key_for_directory(directory),
        node_path=args.node_path,
        outcome=args.outcome,
        stage=args.stage,
        issues=list(args.issues),
        submitted_by_agent_id=ctx.runtime.agent_id,
        summary=f"Submitted Restructure Content {args.stage}.",
    )
    return _prepared(runtime, submission, submission.summary or "Content submitted.")


@current_task
def _submit_plan(runtime, ctx, args: RestructureSubmitPlanArgs):
    service, repo_ref = _service(ctx)
    try:
        plan, version = service.load_repo_plan(repo_ref)
    except Exception as exc:  # noqa: BLE001
        return runtime.foundation.fail(runtime.foundation.issue("restructure_repo_plan_missing", str(exc)))
    if args.plan_version is not None and args.plan_version != version:
        return runtime.foundation.fail(runtime.foundation.issue("restructure_repo_plan_stale", f"expected plan version {args.plan_version}, found {version}"))
    if args.outcome == "planned":
        validation = service.planning.validate_repo(plan, workspace_plan=service.store.load_workspace_plan()[0])
        if not validation.ok:
            return runtime.foundation.fail(runtime.foundation.issue("restructure_repo_plan_gate_failed", "; ".join(validation.errors)))
    submission = RestructureRepoPlanSubmission(
        submission_id=new_submission_id("restructure_plan"),
        tool_name="submit_restructure_repo_plan",
        repo_key=service.repo_key_for_directory(service.directory_for_repo(repo_ref)),
        outcome=args.outcome,
        plan_version=args.plan_version,
        issues=list(args.issues),
        submitted_by_agent_id=ctx.runtime.agent_id,
        summary="Submitted Restructure repo plan.",
    )
    return _prepared(runtime, submission, submission.summary or "Repo plan submitted.")


@current_task
def _submit_review(runtime, ctx, args: RestructureSubmitReviewArgs):
    service, repo_ref = _service(ctx)
    directory = service.directory_for_repo(repo_ref)
    artifact = service.store.load_artifact(directory, args.artifact_id)
    if artifact is None:
        return runtime.foundation.fail(runtime.foundation.issue("restructure_review_artifact_missing", f"Unknown artifact: {args.artifact_id}"))
    receipt = ReviewReceipt(
        artifact_id=args.artifact_id,
        outcome=ReviewOutcome(args.outcome),
        findings=list(args.findings),
        reviewer=ctx.runtime.agent_id or "unknown",
    )
    service.record_review(directory, receipt)
    submission = RestructureReviewSubmission(
        submission_id=new_submission_id("restructure_review"),
        tool_name="submit_restructure_review",
        repo_key=service.repo_key_for_directory(directory),
        outcome=args.outcome,
        artifact_id=args.artifact_id,
        findings=list(args.findings),
        submitted_by_agent_id=ctx.runtime.agent_id,
        summary="Submitted Restructure review.",
    )
    return _prepared(runtime, submission, submission.summary or "Review submitted.")


@current_task
def _submit_repair(runtime, ctx, args: RestructureSubmitRepairArgs):
    if args.outcome == "repaired":
        service, repo_ref = _service(ctx)
        directory = service.directory_for_repo(repo_ref)
        flow = runtime.ark.flow_service.get_flow(ctx.runtime.flow_id)
        workspace, _ = service.store.load_workspace_plan()
        nodes = [n.path for n in workspace.repos[repo_ref].plan.content_nodes()]
        from lean_constellation.services.restructure.checks import check_content
        checked = check_content(service, directory, nodes, flow.input.stage,
                                allow_declared_repair=flow.input.stage == "declared")
        if not checked["passed"]:
            return runtime.foundation.fail(runtime.foundation.issue("restructure_check_failed", "Batch check failed; repair the reported files.", details=checked))
    submission = RestructureRepoRepairSubmission(
        submission_id=new_submission_id("restructure_repo_repair"),
        tool_name="submit_restructure_repo_repair",
        repo_key=ctx.repo.repo_key,
        outcome=args.outcome,
        issues=list(args.issues),
        submitted_by_agent_id=ctx.runtime.agent_id,
        summary="Submitted repository repair." if args.outcome == "repaired" else "Repository repair is blocked.",
    )
    return _prepared(runtime, submission, submission.summary)


def build_tool_specs() -> list[ToolSpec]:
    return [
        handler_tool(name="read_restructure_workspace", description="Read the current Restructure workspace plan.", args_model=RestructureRepoArgs, capability=ToolCapability.READ, result_view="restructure_workspace", groups={AppGroup.RESTRUCTURE_COORDINATOR_READ}, roles={"coordinator", "admin"}, handler=_read_workspace),
        handler_tool(name="read_restructure_repo_plan", description="Read the current repository node plan.", args_model=RestructureRepoArgs, capability=ToolCapability.READ, result_view="restructure_repo_plan", groups={AppGroup.RESTRUCTURE_COORDINATOR_READ}, roles={"coordinator", "admin"}, handler=_read_repo_plan),
        handler_tool(name="read_restructure_content_plan", description="Read the current repository node plan from a Content task.", args_model=RestructureRepoArgs, capability=ToolCapability.READ, result_view="restructure_repo_plan", groups={AppGroup.RESTRUCTURE_CONTENT_READ}, roles={"coordinator", "plan", "worker", "reviewer", "admin"}, handler=_read_content_plan),
        handler_tool(name="upsert_restructure_repo_plan", description="Validate, apply, and persist one complete repository node plan.", args_model=RestructureRepoPlanArgs, capability=ToolCapability.WRITE, result_view="restructure_repo_plan", groups={AppGroup.RESTRUCTURE_COORDINATOR_WRITE}, roles={"coordinator", "admin"}, handler=_upsert_repo_plan),
        handler_tool(name="read_restructure_content", description="Read the current explicit Content declaration catalogue.", args_model=RestructureNodeArgs, capability=ToolCapability.READ, result_view="restructure_content", groups={AppGroup.RESTRUCTURE_CONTENT_READ}, roles={"coordinator", "plan", "worker", "reviewer", "admin"}, handler=_read_content),
        handler_tool(name="create_restructure_decl", description="Create one tracked declaration and its standard Lean file template.", args_model=RestructureCreateDeclArgs, capability=ToolCapability.WRITE, result_view="restructure_decl", groups={AppGroup.RESTRUCTURE_CONTENT_WRITE}, roles={"plan", "worker", "admin"}, handler=_create_decl),
        handler_tool(name="set_restructure_decl", description="Review and register separate statement/proof NL, origins and deps against current source. Formal complete-file snapshots are system-owned.", args_model=RestructureSetDeclArgs, capability=ToolCapability.WRITE, result_view="restructure_decl", groups={AppGroup.RESTRUCTURE_CONTENT_WRITE}, roles={"coordinator", "plan", "worker", "admin"}, handler=_set_decl),
        handler_tool(name="read_restructure_decl_file", description="Read one declaration-owned Lean file before editing.", args_model=RestructureDeclFileArgs, capability=ToolCapability.READ, result_view="restructure_decl_file", groups={AppGroup.RESTRUCTURE_CONTENT_READ}, roles={"coordinator", "plan", "worker", "reviewer", "admin"}, handler=_read_decl_file),
        handler_tool(name="edit_restructure_decl_file", description="Edit one declaration-owned Lean file. Check or submit automatically registers compiled sources.", args_model=RestructureEditDeclFileArgs, capability=ToolCapability.WRITE, result_view="restructure_decl_file", groups={AppGroup.RESTRUCTURE_CONTENT_WRITE}, roles={"coordinator", "plan", "worker", "admin"}, handler=_edit_decl_file),
        handler_tool(name="register_restructure_support_file", description="Register one support file owned by the current Content node.", args_model=RestructureSupportFileArgs, capability=ToolCapability.WRITE, result_view="restructure_content", groups={AppGroup.RESTRUCTURE_CONTENT_WRITE}, roles={"plan", "worker", "admin"}, handler=_register_support_file),
        handler_tool(name="read_restructure_visible_decls", description="Read explicitly registered declarations visible to the current task.", args_model=RestructureVisibleDeclArgs, capability=ToolCapability.READ, result_view="restructure_visible_decls", groups={AppGroup.RESTRUCTURE_CONTENT_READ}, roles={"coordinator", "plan", "worker", "reviewer", "admin"}, handler=_read_visible_decls),
        handler_tool(name="read_restructure_decl_dependencies", description="Read one declaration's explicit dependencies and origins.", args_model=RestructureDeclFileArgs, capability=ToolCapability.READ, result_view="restructure_decl_dependencies", groups={AppGroup.RESTRUCTURE_CONTENT_READ}, roles={"coordinator", "plan", "worker", "reviewer", "admin"}, handler=_read_decl_dependencies),
        handler_tool(name="read_restructure_build_report", description="Read a page of the full build log when the error summary is insufficient.", args_model=RestructureReportArgs, capability=ToolCapability.READ, result_view="restructure_gate", groups={AppGroup.RESTRUCTURE_CONTENT_READ}, roles={"coordinator", "plan", "worker", "admin"}, handler=_read_report),
        handler_tool(name="check_restructure_files", description="Incrementally compile selected Lean files and their dependencies; reuse persistent Lake artifacts.", args_model=RestructureCheckFilesArgs, capability=ToolCapability.WRITE, result_view="restructure_gate", groups={AppGroup.RESTRUCTURE_CONTENT_WRITE}, roles={"coordinator", "plan", "worker", "admin"}, handler=_check_files),
        handler_tool(name="check_restructure_content", description="Compile the Content batch with dependencies, automatically register sources, and report metadata errors.", args_model=RestructureCheckArgs, capability=ToolCapability.WRITE, result_view="restructure_gate", groups={AppGroup.RESTRUCTURE_CONTENT_READ}, roles={"coordinator", "plan", "worker", "admin"}, handler=_check_content),
    ]


def build_submit_tool_specs() -> list[ToolSpec]:
    return [
        submit_handler_tool(name="submit_restructure_content", description="Submit one declared/proved Content candidate.", args_model=RestructureSubmitContentArgs, result_view="restructure_submission", groups={SubmitGroup.RESTRUCTURE_CONTENT_SUBMIT}, roles={"plan", "worker", "admin"}, handler=_submit_content, submit_behavior="terminal"),
        submit_handler_tool(name="submit_restructure_repo_plan", description="Submit a validated Restructure repo plan.", args_model=RestructureSubmitPlanArgs, result_view="restructure_submission", groups={SubmitGroup.RESTRUCTURE_COORDINATOR_SUBMIT}, roles={"coordinator", "admin"}, handler=_submit_plan, submit_behavior="terminal"),
        submit_handler_tool(name="submit_restructure_repo_repair", description="Submit a batched repository-level repair for another build attempt.", args_model=RestructureSubmitRepairArgs, result_view="restructure_submission", groups={SubmitGroup.RESTRUCTURE_COORDINATOR_SUBMIT}, roles={"coordinator", "admin"}, handler=_submit_repair, submit_behavior="terminal"),
        submit_handler_tool(name="submit_restructure_review", description="Submit a read-only Restructure review decision.", args_model=RestructureSubmitReviewArgs, result_view="restructure_submission", groups={SubmitGroup.RESTRUCTURE_REVIEW_SUBMIT}, roles={"reviewer", "admin"}, handler=_submit_review, submit_behavior="terminal"),
    ]


__all__ = ["build_submit_tool_specs", "build_tool_specs"]
