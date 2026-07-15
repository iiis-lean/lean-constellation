"""Decl stage worker ordinary tools."""

from __future__ import annotations

from lean_constellation.flows.content_node_task.decl_round.steps import DeclStageReviewerStepState
from lean_constellation.domain.refs import DeclRef, MathlibRef
from lean_constellation.services.decl_graph.models import DeclOriginRef, DeclStageMutationToolView, DeclState, MathlibDeclDep, RepoDeclDep
from lean_constellation.services.decl_graph.proof_nl_validation import validate_proof_deps, validate_proof_origin_ref
from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    DeclNameArgs,
    DeclStageFileCheckArgs,
    NoArgs,
    ProofDeclDepAddArgs,
    ProofDepRemoveArgs,
    ProofDepsClearArgs,
    ProofFormalReviewPassedArgs,
    ProofFormalReviewRejectedArgs,
    ProofMathlibDepAddArgs,
    ProofNlReviewPassedArgs,
    ProofNlReviewRejectedArgs,
    ProofNlSetArgs,
    ProofOriginRemoveArgs,
    ProofOriginsClearArgs,
    ProofResourceOriginAddArgs,
    ProofSourceOriginAddArgs,
    StatementDeclDepAddArgs,
    StatementDepRemoveArgs,
    StatementDepsClearArgs,
    StatementFormalReviewPassedArgs,
    StatementFormalReviewRejectedArgs,
    StatementMathlibDepAddArgs,
    StatementNlReviewPassedArgs,
    StatementNlReviewRejectedArgs,
    StatementNlSetArgs,
    StatementOriginRemoveArgs,
    StatementOriginsClearArgs,
    StatementResourceOriginAddArgs,
    StatementSourceOriginAddArgs,
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


def _assert_any_stage(runtime, ctx, *, expected_stages: set[str], decl_name: str):
    if ctx.decl_stage is None or ctx.decl_stage.stage not in expected_stages:
        current = ctx.decl_stage.stage if ctx.decl_stage is not None else None
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_stage_mutation_rejected",
                "Tool is not available in the current declaration stage.",
                object_ref=decl_name,
                current=current,
                expected=",".join(sorted(expected_stages)),
            )
        )
    return _assert_stage(runtime, ctx, expected_stage=ctx.decl_stage.stage, decl_name=decl_name)


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
        "proof_formal": "proof",
    }
    expected_stage = stage_map.get(current_stage)
    if expected_stage is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_stage_formal_read_rejected",
                "Formal file checks are only available in current formal worker or reviewer stages.",
                object_ref=decl_name,
                current=current_stage,
                expected="statement_formal,proof_formal",
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


def _current_decl_view(runtime, ctx, decl_name: str):
    return runtime.decl_graph.current_decl_revision_view(ctx.repo_root, node_path=_node(ctx), name=decl_name)


def _current_decl_mutation_view(runtime, ctx, decl_name: str, mutation):
    current = _current_decl_view(runtime, ctx, decl_name)
    if not current.ok or current.value is None:
        return current
    return runtime.foundation.ok(
        DeclStageMutationToolView(
            decl=current.value,
            projection_stage=mutation.value.projection_stage,
            managed_projection_changed=mutation.value.managed_projection_changed,
            changed_files=list(mutation.value.changed_files),
            reread_required=mutation.value.reread_required,
            summary=mutation.value.summary,
        ),
        warnings=[*mutation.issues, *current.issues],
    )


def _set_statement_nl(runtime, ctx, args: StatementNlSetArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.set_statement_nl(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        nl=args.text,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _add_statement_source_origin(runtime, ctx, args: StatementSourceOriginAddArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    if args.end_line < args.start_line:
        return runtime.foundation.fail(
            runtime.foundation.issue("statement_origin_line_range_invalid", "Source origin end_line must be >= start_line.", object_ref=args.decl_name, field="end_line")
        )
    origin = DeclOriginRef(kind="source", source_path=args.source_path, start_line=args.start_line, end_line=args.end_line, note=args.note)
    written = runtime.decl_graph.add_statement_origin(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        origin=origin,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _add_statement_resource_origin(runtime, ctx, args: StatementResourceOriginAddArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    origin = DeclOriginRef(kind="resource", resource_key=args.resource_key, start_locator=args.start_locator, end_locator=args.end_locator, note=args.note)
    written = runtime.decl_graph.add_statement_origin(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        origin=origin,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _remove_statement_origin(runtime, ctx, args: StatementOriginRemoveArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.remove_statement_origin(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        index=args.index,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _clear_statement_origins(runtime, ctx, args: StatementOriginsClearArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="statement_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.clear_statement_origins(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _actor_role(ctx) -> str:
    role = getattr(ctx, "agent_role", None)
    if role is None:
        return "worker"
    return role.value if hasattr(role, "value") else str(role)


def _resolved_mathlib_dep_module(runtime, entry, *, requested_module: str | None, dep_name: str, field_prefix: str):
    entry_module = getattr(entry, "module", None)
    module = requested_module or entry_module
    if not module:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                f"{field_prefix}_mathlib_dep_module_missing",
                "Mathlib dependency must include a module or refer to a MathlibIndex entry with a module.",
                object_ref=dep_name,
                field="module",
            )
        )
    if requested_module and entry_module and requested_module != entry_module:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                f"{field_prefix}_mathlib_dep_module_mismatch",
                "Mathlib dependency module does not match the repo-level MathlibIndex entry.",
                object_ref=dep_name,
                field="module",
                current=requested_module,
                expected=entry_module,
            )
        )
    return runtime.foundation.ok(module)


def _assert_statement_decl_dep_visible(runtime, ctx, *, decl_name: str, args: StatementDeclDepAddArgs):
    current_node = _node(ctx)
    if args.dep_repo:
        repo_key = runtime.foundation.layout.ensure_safe_key(args.dep_repo)
        public = runtime.node.public_decl_access.list_repo_public_decls(
            ctx.repo_root,
            repo_key=repo_key,
            actor_role=_actor_role(ctx),
            current_node_path=current_node,
        )
        if not public.ok or public.value is None:
            return runtime.foundation.fail(public.issues)
        ref = next((item.ref for item in public.value if item.ref.name == args.dep_name), None)
        if ref is None:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "statement_dep_not_visible",
                    "Statement dependency is not visible on the requested provider repo public interface.",
                    object_ref=args.dep_name,
                    current=repo_key,
                )
            )
        if args.revision is not None:
            ref = ref.model_copy(update={"revision": args.revision})
        return runtime.foundation.ok(ref)

    if args.dep_node and args.dep_node != current_node:
        public = runtime.node.public_decl_access.list_node_public_decls(
            ctx.repo_root,
            node_path=args.dep_node,
            actor_role=_actor_role(ctx),
            current_node_path=current_node,
        )
        if not public.ok or public.value is None:
            return runtime.foundation.fail(public.issues)
        ref = next((item.ref for item in public.value if item.ref.name == args.dep_name), None)
        if ref is None:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "statement_dep_not_visible",
                    "Statement dependency is not visible on the requested provider node public interface.",
                    object_ref=args.dep_name,
                    current=args.dep_node,
                )
            )
        if args.revision is not None:
            ref = ref.model_copy(update={"revision": args.revision})
        return runtime.foundation.ok(ref)

    deps_allowed = _assert_statement_deps_visible(runtime, ctx, decl_name=decl_name, deps=[args.dep_name])
    if not deps_allowed.ok:
        return deps_allowed
    revision = runtime.decl_graph.current_decl_revision_view(ctx.repo_root, node_path=current_node, name=args.dep_name)
    if not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues)
    return runtime.foundation.ok(DeclRef(node=current_node, name=args.dep_name, revision=revision.value.revision))


def _add_statement_decl_dep(runtime, ctx, args: StatementDeclDepAddArgs):
    allowed = _assert_any_stage(runtime, ctx, expected_stages={"statement_nl", "statement_formal"}, decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    deps_allowed = _assert_statement_decl_dep_visible(runtime, ctx, decl_name=args.decl_name, args=args)
    if not deps_allowed.ok:
        return deps_allowed
    dep = RepoDeclDep(ref=deps_allowed.value, reason=args.reason)
    written = runtime.decl_graph.add_statement_dep(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        dep=dep,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _add_statement_mathlib_dep(runtime, ctx, args: StatementMathlibDepAddArgs):
    allowed = _assert_any_stage(runtime, ctx, expected_stages={"statement_nl", "statement_formal"}, decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    entry = runtime.mathlib.get_mathlib_decl_entry(ctx.repo_root, name=args.mathlib_decl_name)
    if not entry.ok or entry.value is None:
        return runtime.foundation.fail(entry.issues)
    module = _resolved_mathlib_dep_module(runtime, entry.value, requested_module=args.module, dep_name=args.mathlib_decl_name, field_prefix="statement")
    if not module.ok:
        return module
    dep = MathlibDeclDep(ref=MathlibRef(name=args.mathlib_decl_name, module=module.value), reason=args.reason)
    written = runtime.decl_graph.add_statement_dep(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        dep=dep,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _remove_statement_dep(runtime, ctx, args: StatementDepRemoveArgs):
    allowed = _assert_any_stage(runtime, ctx, expected_stages={"statement_nl", "statement_formal"}, decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.remove_statement_dep(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        index=args.index,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _clear_statement_deps(runtime, ctx, args: StatementDepsClearArgs):
    allowed = _assert_any_stage(runtime, ctx, expected_stages={"statement_nl", "statement_formal"}, decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.clear_statement_deps(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _set_proof_nl(runtime, ctx, args: ProofNlSetArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.set_proof_nl(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        nl=args.text,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _add_proof_source_origin(runtime, ctx, args: ProofSourceOriginAddArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    if args.end_line < args.start_line:
        return runtime.foundation.fail(
            runtime.foundation.issue("proof_origin_line_range_invalid", "Source origin end_line must be >= start_line.", object_ref=args.decl_name, field="end_line")
        )
    origin = DeclOriginRef(kind="source", source_path=args.source_path, start_line=args.start_line, end_line=args.end_line, note=args.note)
    validated = validate_proof_origin_ref(runtime, ctx.repo_root, origin=origin, decl_name=args.decl_name)
    if not validated.ok:
        return validated
    written = runtime.decl_graph.add_proof_origin(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        origin=origin,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _add_proof_resource_origin(runtime, ctx, args: ProofResourceOriginAddArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    origin = DeclOriginRef(kind="resource", resource_key=args.resource_key, start_locator=args.start_locator, end_locator=args.end_locator, note=args.note)
    validated = validate_proof_origin_ref(runtime, ctx.repo_root, origin=origin, decl_name=args.decl_name)
    if not validated.ok:
        return validated
    written = runtime.decl_graph.add_proof_origin(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        origin=origin,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _remove_proof_origin(runtime, ctx, args: ProofOriginRemoveArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.remove_proof_origin(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        index=args.index,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _clear_proof_origins(runtime, ctx, args: ProofOriginsClearArgs):
    allowed = _assert_stage(runtime, ctx, expected_stage="proof_nl", decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.clear_proof_origins(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _assert_proof_decl_dep_visible(runtime, ctx, *, decl_name: str, args: ProofDeclDepAddArgs):
    current_node = _node(ctx)
    if args.dep_repo:
        repo_key = runtime.foundation.layout.ensure_safe_key(args.dep_repo)
        public = runtime.node.public_decl_access.list_repo_public_decls(
            ctx.repo_root,
            repo_key=repo_key,
            actor_role=_actor_role(ctx),
            current_node_path=current_node,
        )
        if not public.ok or public.value is None:
            return runtime.foundation.fail(public.issues)
        ref = next((item.ref for item in public.value if item.ref.name == args.dep_name), None)
        if ref is None:
            return runtime.foundation.fail(
                runtime.foundation.issue("proof_dep_not_visible", "Proof dependency is not visible on the requested provider repo public interface.", object_ref=args.dep_name, current=repo_key)
            )
        if args.revision is not None:
            ref = ref.model_copy(update={"revision": args.revision})
        return runtime.foundation.ok(ref)

    if args.dep_node and args.dep_node != current_node:
        public = runtime.node.public_decl_access.list_node_public_decls(
            ctx.repo_root,
            node_path=args.dep_node,
            actor_role=_actor_role(ctx),
            current_node_path=current_node,
        )
        if not public.ok or public.value is None:
            return runtime.foundation.fail(public.issues)
        ref = next((item.ref for item in public.value if item.ref.name == args.dep_name), None)
        if ref is None:
            return runtime.foundation.fail(
                runtime.foundation.issue("proof_dep_not_visible", "Proof dependency is not visible on the requested provider node public interface.", object_ref=args.dep_name, current=args.dep_node)
            )
        if args.revision is not None:
            ref = ref.model_copy(update={"revision": args.revision})
        return runtime.foundation.ok(ref)

    dep = runtime.decl_graph.current_decl_revision_view(ctx.repo_root, node_path=current_node, name=args.dep_name)
    if not dep.ok or dep.value is None:
        return runtime.foundation.fail(runtime.foundation.issue("proof_dep_not_visible", "Proof dependency is not a visible current declaration.", object_ref=args.dep_name))
    return runtime.foundation.ok(DeclRef(node=current_node, name=args.dep_name, revision=args.revision or dep.value.revision))


def _add_proof_decl_dep(runtime, ctx, args: ProofDeclDepAddArgs):
    allowed = _assert_any_stage(runtime, ctx, expected_stages={"proof_nl", "proof_formal"}, decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    deps_allowed = _assert_proof_decl_dep_visible(runtime, ctx, decl_name=args.decl_name, args=args)
    if not deps_allowed.ok:
        return deps_allowed
    dep = RepoDeclDep(ref=deps_allowed.value, reason=args.reason)
    validation = validate_proof_deps(
        runtime,
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        deps=[dep],
    )
    if not validation.ok:
        return validation
    written = runtime.decl_graph.add_proof_dep(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        dep=dep,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _add_proof_mathlib_dep(runtime, ctx, args: ProofMathlibDepAddArgs):
    allowed = _assert_any_stage(runtime, ctx, expected_stages={"proof_nl", "proof_formal"}, decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    entry = runtime.mathlib.get_mathlib_decl_entry(ctx.repo_root, name=args.mathlib_decl_name)
    if not entry.ok or entry.value is None:
        return runtime.foundation.fail(entry.issues)
    module = _resolved_mathlib_dep_module(runtime, entry.value, requested_module=args.module, dep_name=args.mathlib_decl_name, field_prefix="proof")
    if not module.ok:
        return module
    dep = MathlibDeclDep(ref=MathlibRef(name=args.mathlib_decl_name, module=module.value), reason=args.reason)
    written = runtime.decl_graph.add_proof_dep(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        dep=dep,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _remove_proof_dep(runtime, ctx, args: ProofDepRemoveArgs):
    allowed = _assert_any_stage(runtime, ctx, expected_stages={"proof_nl", "proof_formal"}, decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.remove_proof_dep(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
        index=args.index,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _clear_proof_deps(runtime, ctx, args: ProofDepsClearArgs):
    allowed = _assert_any_stage(runtime, ctx, expected_stages={"proof_nl", "proof_formal"}, decl_name=args.decl_name)
    if not allowed.ok:
        return allowed
    written = runtime.decl_graph.clear_proof_deps(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_round_id(ctx),
        decl_name=args.decl_name,
    )
    if not written.ok:
        return written
    return _current_decl_mutation_view(runtime, ctx, args.decl_name, written)


def _assert_statement_deps_visible(runtime, ctx, *, decl_name: str, deps: list[str]):
    round_id = _round_id(ctx)
    round_record = runtime.decl_graph.get_round(ctx.repo_root, node_path=_node(ctx), round_id=round_id)
    if not round_record.ok or round_record.value is None:
        return runtime.foundation.fail(round_record.issues)
    round_ref_names = {ref.decl_name for ref in round_record.value.revision_refs}
    issues = []
    for dep_name in sorted({dep.strip() for dep in deps if dep and dep.strip()}):
        dep = runtime.decl_graph.current_decl_revision_view(ctx.repo_root, node_path=_node(ctx), name=dep_name)
        if not dep.ok or dep.value is None:
            issues.append(runtime.foundation.issue("statement_dep_not_visible", "Statement dependency is not a visible current declaration.", object_ref=dep_name))
            continue
        if dep_name in round_ref_names and _decl_state_rank(DeclState(dep.value.state)) < _decl_state_rank(DeclState.DECLARED):
            current = dep.value.state.value if hasattr(dep.value.state, "value") else str(dep.value.state)
            issues.append(
                runtime.foundation.issue(
                    "statement_dep_same_round_not_declared",
                    "Statement dependency points to a same-round declaration that is not accepted declared state.",
                    object_ref=dep_name,
                    current=current,
                    expected="declared",
                )
            )
    if issues:
        return runtime.foundation.fail(issues)
    return runtime.foundation.ok(None)


def _decl_state_rank(state: DeclState) -> int:
    return {
        DeclState.OBSOLETE: -1,
        DeclState.PLANNED: 0,
        DeclState.SPECIFIED: 1,
        DeclState.DECLARED: 2,
        DeclState.PROOF_PLANNED: 3,
        DeclState.PROVED: 4,
    }[state]


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


_STATEMENT_FORMAL_REVIEW_ISSUE_CATEGORIES = {
    "formal_not_equivalent_to_nl",
    "statement_too_strong",
    "statement_too_weak",
    "missing_hypothesis",
    "extra_hidden_assumption",
    "wrong_binder_or_domain",
    "wrong_typeclass_or_instance_context",
    "wrong_mathlib_concept",
    "wrong_local_dependency",
    "unavailable_dependency",
    "unnecessary_dependency",
    "unavailable_repo_decl_dependency",
    "unresolved_mathlib_dependency",
    "ambiguous_mathlib_dependency",
    "proof_only_dependency_in_statement_deps",
    "same_round_repo_decl_dependency",
    "source_or_resource_mismatch",
    "node_boundary_violation",
    "semantic_shortcut_or_gate_gap",
    "unclear_worker_intent",
}


_PROOF_NL_REVIEW_ISSUE_CATEGORIES = {
    "proof_route_incomplete",
    "proof_route_too_vague",
    "logical_gap",
    "invalid_inference",
    "missing_case",
    "missing_assumption",
    "proves_wrong_statement",
    "formal_statement_mismatch",
    "origin_missing",
    "origin_invalid",
    "external_material_needs_resource",
    "source_proof_misaligned",
    "missing_decl_dependency",
    "missing_mathlib_dependency",
    "invalid_dependency",
    "same_round_dependency",
    "needs_helper_decl",
    "not_formalization_ready",
    "previous_failure_not_addressed",
    "planning_required",
}


_PROOF_FORMAL_REVIEW_ISSUE_CATEGORIES = {
    "proof_not_aligned_with_proof_nl",
    "source_proof_mismatch",
    "unjustified_alternative_proof",
    "missing_major_proof_dep",
    "wrong_proof_dep_semantics",
    "hidden_helper_should_be_decl",
    "local_helper_too_complex",
    "proof_uses_unintended_strong_theorem",
    "metadata_mismatch",
    "semantic_shortcut_or_gate_gap",
}


_PROOF_FORMAL_REVIEW_NEXT_ACTIONS = {
    "worker_repairable",
    "needs_proof_nl_update",
    "needs_helper_decl",
    "source_mismatch",
    "gate_gap",
}


def _record_statement_formal_review_passed(runtime, ctx, args: StatementFormalReviewPassedArgs):
    mark = _build_stage_specific_review_mark(
        runtime,
        ctx,
        expected_stage="statement_formal",
        decl_name=args.decl_name,
        passed=True,
        summary=args.summary,
    )
    if not mark.ok or mark.value is None:
        return runtime.foundation.fail(mark.issues)
    return _persist_review_mark(runtime, ctx, ctx.runtime.step_id, mark.value)


def _record_statement_formal_review_rejected(runtime, ctx, args: StatementFormalReviewRejectedArgs):
    unknown_categories = sorted(set(args.issue_categories) - _STATEMENT_FORMAL_REVIEW_ISSUE_CATEGORIES)
    if unknown_categories:
        return runtime.foundation.fail(
            [
                runtime.foundation.issue(
                    "statement_formal_review_issue_category_invalid",
                    "Statement Formal review rejection uses an unsupported issue category.",
                    object_ref=category,
                    expected=", ".join(sorted(_STATEMENT_FORMAL_REVIEW_ISSUE_CATEGORIES)),
                )
                for category in unknown_categories
            ]
        )
    mark = _build_stage_specific_review_mark(
        runtime,
        ctx,
        expected_stage="statement_formal",
        decl_name=args.decl_name,
        passed=False,
        summary=args.summary,
        issue_categories=args.issue_categories,
        required_changes=args.required_changes,
    )
    if not mark.ok or mark.value is None:
        return runtime.foundation.fail(mark.issues)
    return _persist_review_mark(runtime, ctx, ctx.runtime.step_id, mark.value)


def _record_proof_nl_review_passed(runtime, ctx, args: ProofNlReviewPassedArgs):
    mark = _build_stage_specific_review_mark(
        runtime,
        ctx,
        expected_stage="proof_nl",
        decl_name=args.decl_name,
        passed=True,
        summary=args.summary,
    )
    if not mark.ok or mark.value is None:
        return runtime.foundation.fail(mark.issues)
    return _persist_review_mark(runtime, ctx, ctx.runtime.step_id, mark.value)


def _record_proof_nl_review_rejected(runtime, ctx, args: ProofNlReviewRejectedArgs):
    unknown_categories = sorted(set(args.issue_categories) - _PROOF_NL_REVIEW_ISSUE_CATEGORIES)
    if unknown_categories:
        return runtime.foundation.fail(
            [
                runtime.foundation.issue(
                    "proof_nl_review_issue_category_invalid",
                    "Proof NL review rejection uses an unsupported issue category.",
                    object_ref=category,
                    expected=", ".join(sorted(_PROOF_NL_REVIEW_ISSUE_CATEGORIES)),
                )
                for category in unknown_categories
            ]
        )
    required_changes = list(args.required_changes)
    if args.recommended_next_action:
        required_changes.append(f"Recommended next action: {args.recommended_next_action.strip()}")
    mark = _build_stage_specific_review_mark(
        runtime,
        ctx,
        expected_stage="proof_nl",
        decl_name=args.decl_name,
        passed=False,
        summary=args.summary,
        issue_categories=args.issue_categories,
        required_changes=required_changes,
    )
    if not mark.ok or mark.value is None:
        return runtime.foundation.fail(mark.issues)
    return _persist_review_mark(runtime, ctx, ctx.runtime.step_id, mark.value)


def _record_proof_formal_review_passed(runtime, ctx, args: ProofFormalReviewPassedArgs):
    mark = _build_stage_specific_review_mark(
        runtime,
        ctx,
        expected_stage="proof_formal",
        decl_name=args.decl_name,
        passed=True,
        summary=args.summary,
    )
    if not mark.ok or mark.value is None:
        return runtime.foundation.fail(mark.issues)
    return _persist_review_mark(runtime, ctx, ctx.runtime.step_id, mark.value)


def _record_proof_formal_review_rejected(runtime, ctx, args: ProofFormalReviewRejectedArgs):
    unknown_categories = sorted(set(args.issue_categories) - _PROOF_FORMAL_REVIEW_ISSUE_CATEGORIES)
    if unknown_categories:
        return runtime.foundation.fail(
            [
                runtime.foundation.issue(
                    "proof_formal_review_issue_category_invalid",
                    "Proof Formal review rejection uses an unsupported issue category.",
                    object_ref=category,
                    expected=", ".join(sorted(_PROOF_FORMAL_REVIEW_ISSUE_CATEGORIES)),
                )
                for category in unknown_categories
            ]
        )
    if args.recommended_next_action not in _PROOF_FORMAL_REVIEW_NEXT_ACTIONS:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "proof_formal_review_next_action_invalid",
                "Proof Formal review rejection uses an unsupported recommended next action.",
                object_ref=args.decl_name,
                field="recommended_next_action",
                current=args.recommended_next_action,
                expected=", ".join(sorted(_PROOF_FORMAL_REVIEW_NEXT_ACTIONS)),
            )
        )
    mark = _build_stage_specific_review_mark(
        runtime,
        ctx,
        expected_stage="proof_formal",
        decl_name=args.decl_name,
        passed=False,
        summary=args.summary,
        issue_categories=args.issue_categories,
        required_changes=args.required_changes,
        recommended_next_action=args.recommended_next_action,
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
    recommended_next_action: str | None = None,
):
    return _build_stage_specific_review_mark(
        runtime,
        ctx,
        expected_stage="statement_nl",
        decl_name=decl_name,
        passed=passed,
        summary=summary,
        issue_categories=issue_categories,
        required_changes=required_changes,
        recommended_next_action=recommended_next_action,
    )


def _build_stage_specific_review_mark(
    runtime,
    ctx,
    *,
    expected_stage: str,
    decl_name: str,
    passed: bool,
    summary: str,
    issue_categories: list[str] | None = None,
    required_changes: list[str] | None = None,
    recommended_next_action: str | None = None,
):
    if ctx.decl_stage is None:
        return runtime.foundation.fail(runtime.foundation.issue("decl_stage_context_missing", "Current context has no decl stage."))
    if ctx.decl_stage.stage != expected_stage:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "review_mark_stage_mismatch",
                f"{expected_stage} review tools are only valid in the {expected_stage} review context.",
                object_ref=decl_name,
                current=ctx.decl_stage.stage,
                expected=expected_stage,
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
        stage=expected_stage,
        decl_name=decl_name,
        passed=passed,
        summary=summary,
        issue_categories=issue_categories,
        required_changes=required_changes,
        recommended_next_action=recommended_next_action,
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
            name="set_statement_nl",
            description="Set the natural-language statement text for one declaration in the current Statement NL stage without changing origins, dependencies, proof artifacts, or declaration state.",
            args_model=StatementNlSetArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE},
            roles=worker_roles,
            handler=_set_statement_nl,
        ),
        handler_tool(
            name="add_statement_source_origin",
            description="Add one typed source-origin range supporting the statement NL candidate in the current Statement NL stage.",
            args_model=StatementSourceOriginAddArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE},
            roles=worker_roles,
            handler=_add_statement_source_origin,
        ),
        handler_tool(
            name="add_statement_resource_origin",
            description="Add one typed resource-origin reference supporting the statement NL candidate in the current Statement NL stage.",
            args_model=StatementResourceOriginAddArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE},
            roles=worker_roles,
            handler=_add_statement_resource_origin,
        ),
        handler_tool(
            name="remove_statement_origin",
            description="Remove one statement origin from the current Statement NL candidate by 0-based origin index.",
            args_model=StatementOriginRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE},
            roles=worker_roles,
            handler=_remove_statement_origin,
        ),
        handler_tool(
            name="clear_statement_origins",
            description="Clear all statement origins from the current Statement NL candidate without changing statement text or dependencies.",
            args_model=StatementOriginsClearArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE},
            roles=worker_roles,
            handler=_clear_statement_origins,
        ),
        handler_tool(
            name="add_statement_decl_dep",
            description="Add one typed project declaration dependency needed to express the statement candidate.",
            args_model=StatementDeclDepAddArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE, AppGroup.DECL_STAGE_STATEMENT_FORMAL_DEP_WRITE},
            roles=worker_roles,
            handler=_add_statement_decl_dep,
        ),
        handler_tool(
            name="add_statement_mathlib_dep",
            description="Add one typed Mathlib declaration dependency needed to express the statement candidate.",
            args_model=StatementMathlibDepAddArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE, AppGroup.DECL_STAGE_STATEMENT_FORMAL_DEP_WRITE},
            roles=worker_roles,
            handler=_add_statement_mathlib_dep,
        ),
        handler_tool(
            name="remove_statement_dep",
            description="Remove one statement dependency from the current candidate by 0-based dependency index.",
            args_model=StatementDepRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE, AppGroup.DECL_STAGE_STATEMENT_FORMAL_DEP_WRITE},
            roles=worker_roles,
            handler=_remove_statement_dep,
        ),
        handler_tool(
            name="clear_statement_deps",
            description="Clear all statement dependencies from the current candidate without changing statement text or origins.",
            args_model=StatementDepsClearArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_WRITE, AppGroup.DECL_STAGE_STATEMENT_FORMAL_DEP_WRITE},
            roles=worker_roles,
            handler=_clear_statement_deps,
        ),
        handler_tool(
            name="set_proof_nl",
            description="Set the natural-language proof route for one theorem-like declaration in the current Proof NL stage without changing origins, dependencies, formal artifacts, or declaration state.",
            args_model=ProofNlSetArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE},
            roles=worker_roles,
            handler=_set_proof_nl,
        ),
        handler_tool(
            name="add_proof_source_origin",
            description="Add one typed source-origin range supporting the proof route in the current Proof NL stage.",
            args_model=ProofSourceOriginAddArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE},
            roles=worker_roles,
            handler=_add_proof_source_origin,
        ),
        handler_tool(
            name="add_proof_resource_origin",
            description="Add one typed resource-origin reference supporting the proof route in the current Proof NL stage.",
            args_model=ProofResourceOriginAddArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE},
            roles=worker_roles,
            handler=_add_proof_resource_origin,
        ),
        handler_tool(
            name="remove_proof_origin",
            description="Remove one proof origin from the current Proof NL candidate by 0-based origin index.",
            args_model=ProofOriginRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE},
            roles=worker_roles,
            handler=_remove_proof_origin,
        ),
        handler_tool(
            name="clear_proof_origins",
            description="Clear all proof origins from the current Proof NL candidate without changing proof text or dependencies.",
            args_model=ProofOriginsClearArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE},
            roles=worker_roles,
            handler=_clear_proof_origins,
        ),
        handler_tool(
            name="add_proof_decl_dep",
            description="Add one typed project declaration dependency used by the proof route or formal proof.",
            args_model=ProofDeclDepAddArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE, AppGroup.DECL_STAGE_PROOF_FORMAL_DEP_WRITE},
            roles=worker_roles,
            handler=_add_proof_decl_dep,
        ),
        handler_tool(
            name="add_proof_mathlib_dep",
            description="Add one typed Mathlib declaration dependency used by the proof route or formal proof.",
            args_model=ProofMathlibDepAddArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE, AppGroup.DECL_STAGE_PROOF_FORMAL_DEP_WRITE},
            roles=worker_roles,
            handler=_add_proof_mathlib_dep,
        ),
        handler_tool(
            name="remove_proof_dep",
            description="Remove one proof dependency from the current candidate by 0-based dependency index.",
            args_model=ProofDepRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE, AppGroup.DECL_STAGE_PROOF_FORMAL_DEP_WRITE},
            roles=worker_roles,
            handler=_remove_proof_dep,
        ),
        handler_tool(
            name="clear_proof_deps",
            description="Clear all proof dependencies from the current candidate without changing proof text or origins.",
            args_model=ProofDepsClearArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_stage_mutation",
            groups={AppGroup.DECL_STAGE_PROOF_NL_WRITE, AppGroup.DECL_STAGE_PROOF_FORMAL_DEP_WRITE},
            roles=worker_roles,
            handler=_clear_proof_deps,
        ),
        handler_tool(
            name="prepare_statement_formal_file",
            description="Create or restore the managed statement formal file; this can replace uncaptured edits.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="lean_file",
            groups={AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE_WRITE},
            roles=worker_roles,
            handler=_prepare_statement_file,
        ),
        handler_tool(
            name="capture_statement_formal_file",
            description="Build the statement module, discover and confirm its Lean declaration name, run capture checks, and save the accepted statement formal capture into the current DeclRevision.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="formal_capture",
            groups={AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE_WRITE},
            roles=worker_roles,
            handler=_capture_statement_file,
        ),
        handler_tool(
            name="prepare_proof_formal_file",
            description="Create or restore the managed proof formal file from the accepted statement capture; this can replace uncaptured edits.",
            args_model=DeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="lean_file",
            groups={AppGroup.DECL_STAGE_PROOF_FORMAL_FILE_WRITE},
            roles=worker_roles,
            handler=_prepare_proof_file,
        ),
        handler_tool(
            name="capture_proof_formal_file",
            description="Build the proof module, verify the registered Lean declaration name and header, run capture checks, and save the accepted proof formal capture into the current DeclRevision.",
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
            name="record_statement_formal_review_passed",
            description="Record a passed semantic review mark for one declaration in the current Statement Formal review step.",
            args_model=StatementFormalReviewPassedArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_STATEMENT_FORMAL_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_statement_formal_review_passed,
        ),
        handler_tool(
            name="record_statement_formal_review_rejected",
            description="Record a rejected semantic review mark with issue categories and required changes for one declaration in the current Statement Formal review step.",
            args_model=StatementFormalReviewRejectedArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_STATEMENT_FORMAL_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_statement_formal_review_rejected,
        ),
        handler_tool(
            name="record_proof_nl_review_passed",
            description="Record a passed semantic review mark for one declaration in the current Proof NL review step.",
            args_model=ProofNlReviewPassedArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_PROOF_NL_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_proof_nl_review_passed,
        ),
        handler_tool(
            name="record_proof_nl_review_rejected",
            description="Record a rejected proof-route review mark with issue categories and required changes for one declaration in the current Proof NL review step.",
            args_model=ProofNlReviewRejectedArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_PROOF_NL_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_proof_nl_review_rejected,
        ),
        handler_tool(
            name="record_proof_formal_review_passed",
            description="Record a passed semantic review mark for one declaration in the current Proof Formal review step.",
            args_model=ProofFormalReviewPassedArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_PROOF_FORMAL_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_proof_formal_review_passed,
        ),
        handler_tool(
            name="record_proof_formal_review_rejected",
            description="Record a rejected proof-formal review mark with issue categories, required changes, and recommended next action for one declaration in the current Proof Formal review step.",
            args_model=ProofFormalReviewRejectedArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_review_mark",
            groups={AppGroup.DECL_STAGE_PROOF_FORMAL_REVIEW_MARK_WRITE},
            roles={"reviewer", "admin"},
            handler=_record_proof_formal_review_rejected,
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
