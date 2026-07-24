"""DeclGraph and ContentPlan ordinary tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.tools.args import (
    ChangeIdArgs,
    ChangeSummaryArgs,
    DeclCreateArgs,
    DeclDeleteArgs,
    DeclFormalReadArgs,
    DeclInspectArgs,
    DeclNameArgs,
    DeclNamesArgs,
    DeclReadyArgs,
    NodeDeclInspectArgs,
    NodeDeclListArgs,
    DeclUpdateArgs,
    NodePublicDeclInspectArgs,
    NodePublicDeclListArgs,
    NoArgs,
    RepoPublicDeclInspectArgs,
    RepoPublicDeclListArgs,
    RoundDraftArgs,
    RoundIdArgs,
    RoundSummaryArgs,
    RoundTerminalArgs,
    StrategyCloseArgs,
    StrategyEnsureArgs,
    StrategyIdArgs,
    VisibleDeclLeanFileArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import current_node_path, handler_tool


def _node(ctx) -> str:
    return current_node_path(ctx)


def _maybe_node(ctx) -> str | None:
    return ctx.node.node_path if ctx.node is not None else None


def _actor_role(ctx) -> str:
    role = ctx.actor.role
    return role.value if hasattr(role, "value") else str(role)


def _decl_satisfaction(runtime, repo_root, *, node_path: str, decl_name: str):
    report = runtime.decl_graph.check_decl_proof_policy_satisfied(repo_root, node_path=node_path, decl_name=decl_name)
    if not report.ok or report.value is None:
        return report
    return runtime.foundation.ok(report.value)


def _decl_list_item(runtime, repo_root, view) -> dict[str, object]:
    report = _decl_satisfaction(runtime, repo_root, node_path=view.node_path, decl_name=view.name)
    if not report.ok or report.value is None:
        proof_policy_satisfied = False
    else:
        proof_policy_satisfied = report.value.ready
    release = runtime.repo_workspace.release.get_decl_release_status(
        repo_root, node_path=view.node_path, decl_name=view.name
    )
    released_state = release.value.released_state if release.ok and release.value is not None else None
    release_protected = bool(release.value.release_protected) if release.ok and release.value is not None else False
    return {
        "name": view.name,
        "node_path": view.node_path,
        "kind": view.kind,
        "lifecycle": view.lifecycle.value if hasattr(view.lifecycle, "value") else str(view.lifecycle),
        "public": view.public,
        "visibility": view.visibility,
        "current_revision": view.current_revision,
        "revision_ids": list(view.revision_ids),
        "module": view.module,
        "lean_decl_name": view.lean_decl_name,
        "state": view.state.value if view.state is not None and hasattr(view.state, "value") else view.state,
        "status": view.status.value if view.status is not None and hasattr(view.status, "value") else view.status,
        "proof_policy_satisfied": proof_policy_satisfied,
        "released_state": released_state,
        "release_protected": release_protected,
        "summary": view.summary,
        "updated_at": view.updated_at,
    }


def _decl_revision_item(runtime, repo_root, *, decl, revision) -> dict[str, object]:
    release = runtime.repo_workspace.release.get_decl_release_status(
        repo_root, node_path=decl.node_path, decl_name=decl.name
    )
    change = revision.change
    statement_formal = revision.statement.formal
    proof_formal = revision.proof.formal if revision.proof is not None else None
    return {
        "decl_name": decl.name,
        "node_path": decl.node_path,
        "revision": revision.revision,
        "current_revision": decl.current_revision,
        "kind": decl.kind,
        "lifecycle": decl.lifecycle.value,
        "visibility": "public" if decl.public else "private",
        "state": revision.state.value,
        "status": revision.status.value,
        "module": decl.module,
        "lean_decl_name": revision.lean_decl_name,
        "change": None
        if change is None
        else {
            "kind": change.kind.value,
            "base_revision": change.base_revision,
            "reset_to_state": change.reset_to_state.value if change.reset_to_state is not None else None,
            "target_state": change.target_state.value if change.target_state is not None else None,
            "require_target_state_satisfied": change.require_target_state_satisfied,
            "objective": change.objective,
            "summary": change.summary,
        },
        "artifacts": {
            "statement_nl": "present" if revision.statement.nl is not None else "absent",
            "statement_formal": _formal_artifact_status(statement_formal),
            "proof_nl": "present" if revision.proof is not None and revision.proof.nl is not None else "absent",
            "proof_formal": _formal_artifact_status(proof_formal),
        },
        "released_state": release.value.released_state if release.ok and release.value is not None else None,
        "release_protected": bool(release.value.release_protected) if release.ok and release.value is not None else False,
    }


def _formal_artifact_status(formal) -> str:  # noqa: ANN001
    if formal is None or formal.code is None:
        return "absent"
    if formal.check is None:
        return "unchecked"
    return formal.check.status


def _load_decl_revision(runtime, repo_root, *, node_path: str, decl_name: str, revision: int | None):
    decl = runtime.decl_graph.get_decl(repo_root, node_path=node_path, name=decl_name)
    if not decl.ok or decl.value is None:
        return decl, None
    resolved_revision = revision or decl.value.current_revision
    loaded = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=node_path,
        name=decl_name,
        revision=resolved_revision,
    )
    return decl, loaded


def _read_statement_nl(runtime, ctx, args: DeclNameArgs):
    decl, loaded = _load_decl_revision(
        runtime,
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
        revision=None,
    )
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    if loaded is None or not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues if loaded is not None else [])
    nl = loaded.value.statement.nl
    if nl is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_statement_nl_missing",
                "The current declaration revision has no Statement NL content.",
                object_ref=f"{_node(ctx)}:{args.decl_name}",
            )
        )
    return runtime.foundation.ok(
        {
            "decl_name": args.decl_name,
            "node_path": _node(ctx),
            "revision": loaded.value.revision,
            "text": nl.text,
            "origins": [item.model_dump(mode="json", exclude_none=True) for item in nl.origin],
            "dependencies": [item.model_dump(mode="json", exclude_none=True) for item in loaded.value.statement.deps],
        }
    )


def _read_proof_nl(runtime, ctx, args: DeclNameArgs):
    decl, loaded = _load_decl_revision(
        runtime,
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
        revision=None,
    )
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    if loaded is None or not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues if loaded is not None else [])
    proof = loaded.value.proof
    if proof is None or proof.nl is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "decl_proof_nl_missing",
                "The current declaration revision has no Proof NL content.",
                object_ref=f"{_node(ctx)}:{args.decl_name}",
            )
        )
    return runtime.foundation.ok(
        {
            "decl_name": args.decl_name,
            "node_path": _node(ctx),
            "revision": loaded.value.revision,
            "text": proof.nl.text,
            "origins": [item.model_dump(mode="json", exclude_none=True) for item in proof.nl.origin],
            "dependencies": [item.model_dump(mode="json", exclude_none=True) for item in proof.deps],
        }
    )


def _read_formal(runtime, ctx, args: DeclFormalReadArgs):
    read = runtime.lean_projection.decl_file.read_decl_owned_lean_file(
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
    )
    if not read.ok or read.value is None:
        return runtime.foundation.fail(read.issues)
    content = read.value.content
    if not args.include_docstring:
        repo_format = runtime.repo_workspace.metadata.get_repo_format(ctx.repo_root)
        managed = not (
            repo_format.ok
            and repo_format.value is not None
            and repo_format.value.repo_format == RepoFormat.ADAPTER
        )
        if managed:
            marker = runtime.lean_projection.annotation.parse_target_marker(content)
            if marker.ok and marker.value is not None:
                content = (
                    content[: marker.value.docstring_start_offset]
                    + content[marker.value.docstring_end_offset :]
                )
    decl, loaded = _load_decl_revision(
        runtime,
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
        revision=read.value.revision,
    )
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    if loaded is None or not loaded.ok or loaded.value is None:
        return runtime.foundation.fail(loaded.issues if loaded is not None else [])
    formal = (
        loaded.value.proof.formal
        if read.value.stage == "proof" and loaded.value.proof is not None
        else loaded.value.statement.formal
    )
    return runtime.foundation.ok(
        {
            "decl_name": args.decl_name,
            "node_path": _node(ctx),
            "revision": read.value.revision,
            "stage": read.value.stage,
            "module": read.value.module,
            "path": read.value.path,
            "lean_decl_name": read.value.lean_decl_name,
            "code": content,
            "check": formal.check.model_dump(mode="json", exclude_none=True) if formal is not None and formal.check is not None else None,
        }
    )


def _dependency_display_lines(runtime, repo_root, *, node_path: str, dependencies: list[object]) -> list[str]:
    resolved = runtime.lean_projection.decl_file.resolve_dependency_projections(
        repo_root,
        consumer_node_path=node_path,
        dependencies=dependencies,
        require_complete=False,
    )
    if not resolved.ok or resolved.value is None:
        return []
    return [runtime.lean_projection.annotation.format_dependency(item) for item in resolved.value]


def _public_decl_item(runtime, repo_root, public_decl) -> dict[str, object]:
    ref = public_decl.ref
    resolved_revision = public_decl.resolved_revision or ref.revision
    decl = runtime.decl_graph.get_decl_view(repo_root, node_path=ref.node, name=ref.name)
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=ref.node,
        name=ref.name,
        revision=resolved_revision,
    )
    if not decl.ok or decl.value is None or not revision.ok or revision.value is None:
        return {
            "ref": ref.model_dump(mode="json"),
            "anchor_revision": ref.revision,
            "resolved_revision": public_decl.resolved_revision,
            "compatible": bool(public_decl.ready and not public_decl.stale),
            "resolution_reason": public_decl.resolution_reason,
            "kind": public_decl.kind,
            "name": ref.name,
            "node_path": ref.node,
            "module": None,
            "lean_decl_name": None,
            "summary": public_decl.summary,
            "public": public_decl.public,
            "visibility": "public",
            "state": None,
            "status": None,
            "proof_policy_satisfied": False,
            "released_state": public_decl.released_state,
            "release_protected": public_decl.release_protected,
            "source": public_decl.source,
        }
    return {
        "ref": ref.model_dump(mode="json"),
        "anchor_revision": ref.revision,
        "resolved_revision": public_decl.resolved_revision,
        "compatible": bool(public_decl.ready and not public_decl.stale),
        "resolution_reason": public_decl.resolution_reason,
        "kind": decl.value.kind,
        "name": ref.name,
        "node_path": ref.node,
        "module": decl.value.module,
        "lean_decl_name": revision.value.lean_decl_name,
        "summary": decl.value.summary,
        "public": decl.value.public,
        "visibility": "public",
        "state": revision.value.state.value,
        "status": revision.value.status.value,
        "proof_policy_satisfied": public_decl.ready and not public_decl.stale,
        "released_state": public_decl.released_state,
        "release_protected": public_decl.release_protected,
        "source": public_decl.source,
    }


def _required_round_id(runtime, ctx, round_id: str | None) -> str:
    if round_id and round_id.strip():
        return round_id.strip()
    if ctx.decl_stage and ctx.decl_stage.round_id:
        return ctx.decl_stage.round_id
    raise ValueError("round_id is required when current tool context has no decl-stage round.")


def _ensure_graph(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.ensure_decl_graph(ctx.repo_root, node_path=_node(ctx))


def _graph_index(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.get_decl_graph_index(ctx.repo_root, node_path=_node(ctx))


def _graph_store(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.get_decl_graph_store_view(ctx.repo_root, node_path=_node(ctx))


def _node_graph_index(runtime, ctx, args: NodeDeclListArgs):
    return runtime.decl_graph.get_decl_graph_index(ctx.repo_root, node_path=args.node_path)


def _node_graph_store(runtime, ctx, args: NodeDeclListArgs):
    return runtime.decl_graph.get_decl_graph_store_view(ctx.repo_root, node_path=args.node_path)


def _rebuild_graph(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.rebuild_decl_graph_index(ctx.repo_root, node_path=_node(ctx))


def _ensure_open_strategy(runtime, ctx, args: StrategyEnsureArgs):
    return runtime.decl_graph.ensure_open_strategy_view(
        ctx.repo_root,
        node_path=_node(ctx),
        objective=args.objective,
        rationale=args.rationale,
    )


def _close_strategy(runtime, ctx, args: StrategyCloseArgs):
    return runtime.decl_graph.close_strategy_view(
        ctx.repo_root,
        node_path=_node(ctx),
        strategy_id=args.strategy_id,
        summary=args.summary,
        reason=args.reason,
        failed=args.failed,
    )


def _list_strategies(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_strategy_views(ctx.repo_root, node_path=_node(ctx))


def _get_strategy(runtime, ctx, args: StrategyIdArgs):
    return runtime.decl_graph.get_strategy_view(ctx.repo_root, node_path=_node(ctx), strategy_id=args.strategy_id)


def _create_round_draft(runtime, ctx, args: RoundDraftArgs):
    return runtime.decl_graph.create_round_draft_view(
        ctx.repo_root,
        node_path=_node(ctx),
        strategy_id=args.strategy_id,
        objective=args.objective,
    )


def _list_rounds(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_round_views(ctx.repo_root, node_path=_node(ctx))


def _get_round(runtime, ctx, args: RoundIdArgs):
    return runtime.decl_graph.get_round_view(ctx.repo_root, node_path=_node(ctx), round_id=_required_round_id(runtime, ctx, args.round_id))


def _write_change_summary(runtime, ctx, args: ChangeSummaryArgs):
    return runtime.decl_graph.write_decl_change_summary_view(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
        change_id=args.change_id,
        summary=args.summary,
    )


def _write_round_summary(runtime, ctx, args: RoundSummaryArgs):
    return runtime.decl_graph.write_round_summary_view(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
        summary=args.summary,
    )


def _mark_round_terminal(runtime, ctx, args: RoundTerminalArgs):
    outcome = {
        "success": "completed",
        "blocked": "blocked",
        "failed": "failed",
    }[args.result_kind]
    closed = runtime.decl_graph.closeout_round(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
        reason=args.reason,
        outcome=outcome,
    )
    if not closed.ok:
        return closed
    return runtime.decl_graph.get_round_view(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
    )


def _create_decl(runtime, ctx, args: DeclCreateArgs):
    return runtime.decl_graph.create_decl(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _open_decl_update(runtime, ctx, args: DeclUpdateArgs):
    return runtime.decl_graph.open_decl_update(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _mark_decl_delete(runtime, ctx, args: DeclDeleteArgs):
    return runtime.decl_graph.mark_decl_delete(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _list_decls(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_decl_views(ctx.repo_root, node_path=_node(ctx))


def _list_current_node_decls(runtime, ctx, args: NoArgs):
    del args
    views = runtime.decl_graph.list_decl_views(ctx.repo_root, node_path=_node(ctx))
    if not views.ok or views.value is None:
        return runtime.foundation.fail(views.issues)
    return runtime.foundation.ok([_decl_list_item(runtime, ctx.repo_root, item) for item in views.value], warnings=views.issues)


def _list_node_decls(runtime, ctx, args: NodeDeclListArgs):
    views = runtime.decl_graph.list_decl_views(ctx.repo_root, node_path=args.node_path)
    if not views.ok or views.value is None:
        return runtime.foundation.fail(views.issues)
    return runtime.foundation.ok([_decl_list_item(runtime, ctx.repo_root, item) for item in views.value], warnings=views.issues)


def _get_decl(runtime, ctx, args: DeclNameArgs):
    return runtime.decl_graph.get_decl_view(ctx.repo_root, node_path=_node(ctx), name=args.decl_name)


def _inspect_current_node_decl(runtime, ctx, args: DeclInspectArgs):
    decl, revision = _load_decl_revision(
        runtime,
        ctx.repo_root,
        node_path=_node(ctx),
        decl_name=args.decl_name,
        revision=args.revision,
    )
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    if revision is None or not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues if revision is not None else [])
    return runtime.foundation.ok(
        _decl_revision_item(runtime, ctx.repo_root, decl=decl.value, revision=revision.value)
    )


def _inspect_node_decl(runtime, ctx, args: NodeDeclInspectArgs):
    decl, revision = _load_decl_revision(
        runtime,
        ctx.repo_root,
        node_path=args.node_path,
        decl_name=args.decl_name,
        revision=args.revision,
    )
    if not decl.ok or decl.value is None:
        return runtime.foundation.fail(decl.issues)
    if revision is None or not revision.ok or revision.value is None:
        return runtime.foundation.fail(revision.issues if revision is not None else [])
    return runtime.foundation.ok(
        _decl_revision_item(runtime, ctx.repo_root, decl=decl.value, revision=revision.value)
    )


def _get_decl_change(runtime, ctx, args: ChangeIdArgs):
    return runtime.decl_graph.get_decl_change(ctx.repo_root, node_path=_node(ctx), change_id=args.change_id)


def _compute_delete_closure(runtime, ctx, args: DeclNamesArgs):
    return runtime.decl_graph.compute_delete_closure(ctx.repo_root, node_path=_node(ctx), decl_names=args.decl_names)


def _validate_round_draft(runtime, ctx, args: RoundIdArgs):
    return runtime.decl_graph.validate_round_draft(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
    )


def _compute_dependency_closure(runtime, ctx, args: DeclNamesArgs):
    return runtime.decl_graph.compute_dependency_closure(ctx.repo_root, node_path=_node(ctx), decl_names=args.decl_names)


def _preview_delete_closure(runtime, ctx, args: DeclNamesArgs):
    return runtime.decl_graph.compute_delete_closure(ctx.repo_root, node_path=_node(ctx), decl_names=args.decl_names)


def _check_decl_ready(runtime, ctx, args: DeclReadyArgs):
    return runtime.decl_graph.check_decl_ready(ctx.repo_root, node_path=_node(ctx), decl_name=args.decl_name, policy=args.policy)


def _list_content_public_decls(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_content_public_decls(ctx.repo_root, node_path=_node(ctx))


def _list_visible_nodes(runtime, ctx, args: NoArgs):
    del args
    return runtime.node.public_decl_access.list_visible_nodes(
        ctx.repo_root,
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )


def _list_imported_repos(runtime, ctx, args: NoArgs):
    del args
    return runtime.node.public_decl_access.list_imported_repos(
        ctx.repo_root,
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )


def _list_current_node_public_decls(runtime, ctx, args: NoArgs):
    del args
    result = runtime.node.public_decl_access.list_node_public_decl_items(
        ctx.repo_root,
        node_path=_node(ctx),
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )
    if not result.ok or result.value is None:
        return runtime.foundation.fail(result.issues)
    return runtime.foundation.ok(
        [item.model_dump(mode="json", exclude_none=True) for item in result.value],
        warnings=result.issues,
    )


def _list_node_public_decls(runtime, ctx, args: NodePublicDeclListArgs):
    result = runtime.node.public_decl_access.list_node_public_decl_items(
        ctx.repo_root,
        node_path=args.node_path,
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )
    if not result.ok or result.value is None:
        return runtime.foundation.fail(result.issues)
    return runtime.foundation.ok(
        [item.model_dump(mode="json", exclude_none=True) for item in result.value],
        warnings=result.issues,
    )


def _list_repo_public_decls(runtime, ctx, args: RepoPublicDeclListArgs):
    repo_key = runtime.foundation.layout.ensure_safe_key(args.repo_key)
    result = runtime.node.public_decl_access.list_repo_public_decl_items(
        ctx.repo_root,
        repo_key=repo_key,
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )
    if not result.ok or result.value is None:
        return runtime.foundation.fail(result.issues)
    return runtime.foundation.ok(
        [item.model_dump(mode="json", exclude_none=True) for item in result.value],
        warnings=result.issues,
    )


def _inspect_current_node_public_decl(runtime, ctx, args: DeclInspectArgs):
    node_args = NodePublicDeclInspectArgs(node_path=_node(ctx), **args.model_dump())
    return _inspect_node_public_decl(runtime, ctx, node_args)


def _inspect_node_public_decl(runtime, ctx, args: NodePublicDeclInspectArgs):
    public = runtime.node.public_decl_access.list_node_public_decls(
        ctx.repo_root,
        node_path=args.node_path,
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )
    if not public.ok or public.value is None:
        return runtime.foundation.fail(public.issues)
    public_decl = next((decl for decl in public.value if decl.ref.name == args.decl_name), None)
    if public_decl is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "public_decl_not_found",
                "No public declaration with the requested name is visible on the node.",
                object_ref=f"{args.node_path}:{args.decl_name}",
            )
        )
    result = runtime.node.public_decl_access.inspect_public_decl_item(
        ctx.repo_root,
        public_decl=public_decl,
        repository="current repo",
        expose_node_path=True,
        revision=args.revision,
    )
    if not result.ok or result.value is None:
        return runtime.foundation.fail(result.issues)
    return runtime.foundation.ok(
        result.value.model_dump(mode="json", exclude_none=True),
        warnings=result.issues,
    )


def _inspect_repo_public_decl(runtime, ctx, args: RepoPublicDeclInspectArgs):
    repo_key = runtime.foundation.layout.ensure_safe_key(args.repo_key)
    public = runtime.node.public_decl_access.list_repo_public_decls(
        ctx.repo_root,
        repo_key=repo_key,
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )
    if not public.ok or public.value is None:
        return runtime.foundation.fail(public.issues)
    public_decl = next((decl for decl in public.value if decl.ref.name == args.decl_name), None)
    if public_decl is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "public_decl_not_found",
                "No public declaration with the requested name is visible on the repo public interface.",
                object_ref=f"{repo_key}:{args.decl_name}",
            )
        )
    provider_root = ctx.repo_root.parent / repo_key
    result = runtime.node.public_decl_access.inspect_public_decl_item(
        provider_root,
        public_decl=public_decl,
        repository=repo_key,
        expose_node_path=False,
        revision=args.revision,
    )
    if not result.ok or result.value is None:
        return runtime.foundation.fail(result.issues)
    return runtime.foundation.ok(
        result.value.model_dump(mode="json", exclude_none=True),
        warnings=result.issues,
    )


def _read_visible_decl_lean_file(runtime, ctx, args: VisibleDeclLeanFileArgs):
    role = _actor_role(ctx)
    coordinator = role in {"coordinator", "admin"}
    target_root = ctx.repo_root
    target_node = args.node_path
    resolved_revision = args.revision
    expected_visibility: str | None = None

    if args.repo_key is not None:
        repo_key = runtime.foundation.layout.ensure_safe_key(args.repo_key)
        public = runtime.node.public_decl_access.list_repo_public_decls(
            ctx.repo_root,
            repo_key=repo_key,
            actor_role=role,
            current_node_path=_maybe_node(ctx),
        )
        if not public.ok or public.value is None:
            return runtime.foundation.fail(public.issues)
        selected = next(
            (
                item
                for item in public.value
                if item.ref.node == args.node_path
                and item.ref.name == args.decl_name
                and item.ready
                and not item.stale
            ),
            None,
        )
        if selected is None:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "visible_decl_file_not_public",
                    "The requested declaration is not visible on the provider public boundary.",
                    object_ref=f"{repo_key}::{args.node_path}::{args.decl_name}",
                )
            )
        allowed_revisions = {selected.ref.revision, selected.resolved_revision or selected.ref.revision}
        if resolved_revision is not None and resolved_revision not in allowed_revisions:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "visible_decl_revision_not_public",
                    "The requested revision is not the visible public anchor or its compatible resolved revision.",
                    object_ref=f"{repo_key}::{args.node_path}::{args.decl_name}@{resolved_revision}",
                )
            )
        resolved_revision = resolved_revision or selected.resolved_revision or selected.ref.revision
        target_root = ctx.repo_root.parent / repo_key
        target_node = selected.ref.node
        expected_visibility = "public"
    elif not coordinator:
        public = runtime.node.public_decl_access.list_node_public_decls(
            ctx.repo_root,
            node_path=args.node_path,
            actor_role=role,
            current_node_path=_maybe_node(ctx),
        )
        if not public.ok or public.value is None:
            return runtime.foundation.fail(public.issues)
        selected = next(
            (
                item
                for item in public.value
                if item.ref.name == args.decl_name and item.ready and not item.stale
            ),
            None,
        )
        if selected is None:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "visible_decl_file_not_public",
                    "The requested declaration is not visible on the node public boundary.",
                    object_ref=f"{args.node_path}::{args.decl_name}",
                )
            )
        allowed_revisions = {selected.ref.revision, selected.resolved_revision or selected.ref.revision}
        if resolved_revision is not None and resolved_revision not in allowed_revisions:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "visible_decl_revision_not_public",
                    "The requested revision is not the visible public anchor or its compatible resolved revision.",
                    object_ref=f"{args.node_path}::{args.decl_name}@{resolved_revision}",
                )
            )
        resolved_revision = resolved_revision or selected.resolved_revision or selected.ref.revision
        target_node = selected.ref.node
        expected_visibility = "public"

    read = runtime.lean_projection.decl_file.read_decl_owned_lean_file(
        target_root,
        node_path=target_node,
        decl_name=args.decl_name,
        revision=resolved_revision,
    )
    if not read.ok or read.value is None:
        return runtime.foundation.fail(read.issues)
    if expected_visibility is not None and read.value.visibility != expected_visibility:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "visible_decl_file_not_public",
                "The resolved declaration file is not public.",
                object_ref=f"{target_node}::{args.decl_name}",
            )
        )
    return read


def _list_active_decl_names(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_active_decl_names(ctx.repo_root, node_path=_node(ctx))


def _check_content_ready(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.check_content_node_ready(ctx.repo_root, node_path=_node(ctx))


def _check_content_completion(runtime, ctx, args: NoArgs):
    del args
    return runtime.validation_snapshot.check_content_node_completion(ctx.repo_root, node_path=_node(ctx))


def build_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    plan_roles = {"plan", "admin"}
    return [
        handler_tool(
            name="ensure_current_decl_graph",
            description="Ensure the current content node DeclGraph store exists.",
            args_model=NoArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_graph_store",
            groups={AppGroup.DECL_GRAPH_CURRENT_WRITE},
            roles=plan_roles,
            handler=_ensure_graph,
        ),
        handler_tool(
            name="get_current_decl_graph_index",
            description="Read the current content node DeclGraph index.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_graph_index",
            groups={AppGroup.DECL_GRAPH_READ_CURRENT},
            roles=roles,
            handler=_graph_index,
        ),
        handler_tool(
            name="get_current_decl_graph_store",
            description="Read DeclGraph store counts and paths for the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_graph_store",
            groups={AppGroup.DECL_GRAPH_READ_CURRENT},
            roles=roles,
            handler=_graph_store,
        ),
        handler_tool(
            name="get_node_decl_graph_index",
            description="Read the DeclGraph index for one permitted node in the current repository.",
            args_model=NodeDeclListArgs,
            capability=ToolCapability.READ,
            result_view="decl_graph_index",
            groups={AppGroup.DECL_GRAPH_READ_COORDINATOR},
            roles={"coordinator", "admin"},
            handler=_node_graph_index,
            required_context={"repo"},
        ),
        handler_tool(
            name="get_node_decl_graph_store",
            description="Read DeclGraph store counts and paths for one permitted node in the current repository.",
            args_model=NodeDeclListArgs,
            capability=ToolCapability.READ,
            result_view="decl_graph_store",
            groups={AppGroup.DECL_GRAPH_READ_COORDINATOR},
            roles={"coordinator", "admin"},
            handler=_node_graph_store,
            required_context={"repo"},
        ),
        handler_tool(
            name="rebuild_current_decl_graph_index",
            description="Rebuild the current content node DeclGraph index.",
            args_model=NoArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_graph_index",
            groups={AppGroup.DECL_GRAPH_CURRENT_WRITE},
            roles=plan_roles,
            handler=_rebuild_graph,
        ),
        handler_tool(
            name="ensure_open_decl_strategy",
            description="Ensure the current content node has one open declaration strategy.",
            args_model=StrategyEnsureArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_strategy",
            groups={AppGroup.DECL_STRATEGY_WRITE},
            roles=plan_roles,
            handler=_ensure_open_strategy,
        ),
        handler_tool(
            name="close_decl_strategy",
            description="Close an open declaration strategy as closed or failed.",
            args_model=StrategyCloseArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_strategy",
            groups={AppGroup.DECL_STRATEGY_WRITE},
            roles=plan_roles,
            handler=_close_strategy,
        ),
        handler_tool(
            name="list_decl_strategies",
            description="List declaration strategies for the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_strategy_list",
            groups={AppGroup.DECL_GRAPH_READ_CURRENT},
            roles=roles,
            handler=_list_strategies,
        ),
        handler_tool(
            name="get_decl_strategy",
            description="Inspect one declaration strategy in the current content node.",
            args_model=StrategyIdArgs,
            capability=ToolCapability.READ,
            result_view="decl_strategy",
            groups={AppGroup.DECL_GRAPH_READ_CURRENT},
            roles=roles,
            handler=_get_strategy,
        ),
        handler_tool(
            name="create_decl_round_draft",
            description="Create a draft declaration round under an open strategy.",
            args_model=RoundDraftArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_round",
            groups={AppGroup.DECL_ROUND_CHANGE_WRITE},
            roles=plan_roles,
            handler=_create_round_draft,
        ),
        handler_tool(
            name="list_decl_rounds",
            description="List declaration rounds for the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_round_list",
            groups={AppGroup.DECL_GRAPH_READ_CURRENT},
            roles=roles,
            handler=_list_rounds,
        ),
        handler_tool(
            name="get_decl_round",
            description="Inspect a declaration round in the current content node.",
            args_model=RoundIdArgs,
            capability=ToolCapability.READ,
            result_view="decl_round",
            groups={AppGroup.DECL_GRAPH_READ_CURRENT},
            roles=roles,
            handler=_get_round,
        ),
        handler_tool(
            name="write_decl_change_summary",
            description="Write one declaration change closeout summary.",
            args_model=ChangeSummaryArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_round",
            groups={AppGroup.DECL_ROUND_CLOSEOUT_WRITE},
            roles=plan_roles,
            handler=_write_change_summary,
        ),
        handler_tool(
            name="write_decl_round_summary",
            description="Write the declaration round closeout summary.",
            args_model=RoundSummaryArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_round",
            groups={AppGroup.DECL_ROUND_CLOSEOUT_WRITE},
            roles=plan_roles,
            handler=_write_round_summary,
        ),
        handler_tool(
            name="mark_decl_round_terminal",
            description="Mark a declaration round as success, blocked, or failed after closeout.",
            args_model=RoundTerminalArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_round",
            groups={AppGroup.DECL_ROUND_CLOSEOUT_WRITE},
            roles=plan_roles,
            handler=_mark_round_terminal,
        ),
        handler_tool(
            name="plan_create_decl",
            description="Plan creation of a flat-key declaration in the current draft round; native module and Lean full-name identity are derived later.",
            args_model=DeclCreateArgs,
            capability=ToolCapability.WRITE,
            result_view="public_decl_detail",
            groups={AppGroup.DECL_ROUND_CHANGE_WRITE, AppGroup.DECL_CATALOG_PLAN_WRITE},
            roles=plan_roles,
            handler=_create_decl,
        ),
        handler_tool(
            name="plan_update_decl",
            description="Open a new declaration revision for an update in the current draft round.",
            args_model=DeclUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="public_decl_detail",
            groups={AppGroup.DECL_ROUND_CHANGE_WRITE, AppGroup.DECL_CATALOG_PLAN_WRITE},
            roles=plan_roles,
            handler=_open_decl_update,
        ),
        handler_tool(
            name="plan_delete_decl",
            description="Plan deletion of a declaration in the current draft round.",
            args_model=DeclDeleteArgs,
            capability=ToolCapability.WRITE,
            result_view="public_decl_detail",
            groups={AppGroup.DECL_ROUND_CHANGE_WRITE, AppGroup.DECL_CATALOG_PLAN_WRITE},
            roles=plan_roles,
            handler=_mark_decl_delete,
        ),
        handler_tool(
            name="list_current_decls",
            description="List declarations in the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_list",
            groups={AppGroup.DECL_DETAIL_READ},
            roles=roles,
            handler=_list_decls,
        ),
        handler_tool(
            name="list_current_node_decls",
            description="List declarations in the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_list",
            groups={AppGroup.CURRENT_NODE_DECL_READ},
            roles=roles,
            handler=_list_current_node_decls,
        ),
        handler_tool(
            name="list_node_decls",
            description="List all public and private declarations in one permitted node of the current repository.",
            args_model=NodeDeclListArgs,
            capability=ToolCapability.READ,
            result_view="decl_list",
            groups={AppGroup.DECL_GRAPH_READ_COORDINATOR},
            roles={"coordinator", "admin"},
            handler=_list_node_decls,
            required_context={"repo"},
        ),
        handler_tool(
            name="get_decl",
            description="Inspect a declaration catalog entry in the current content node.",
            args_model=DeclNameArgs,
            capability=ToolCapability.READ,
            result_view="decl",
            groups={AppGroup.DECL_DETAIL_READ},
            roles=roles,
            handler=_get_decl,
        ),
        handler_tool(
            name="inspect_current_node_decl",
            description="Inspect compact catalog, lifecycle, change, and artifact-presence state for one current-node declaration revision.",
            args_model=DeclInspectArgs,
            capability=ToolCapability.READ,
            result_view="decl_inspection",
            groups={AppGroup.CURRENT_NODE_DECL_READ},
            roles=roles,
            handler=_inspect_current_node_decl,
        ),
        handler_tool(
            name="read_statement_nl",
            description="Read the complete Statement NL text, origins, and typed Statement dependencies for one current-node declaration.",
            args_model=DeclNameArgs,
            capability=ToolCapability.READ,
            result_view="decl_statement_nl",
            groups={AppGroup.DECL_STAGE_STATEMENT_NL_READ},
            roles=roles,
            handler=_read_statement_nl,
        ),
        handler_tool(
            name="read_proof_nl",
            description="Read the complete Proof NL text, origins, and typed Proof dependencies for one current-node declaration.",
            args_model=DeclNameArgs,
            capability=ToolCapability.READ,
            result_view="decl_proof_nl",
            groups={AppGroup.DECL_STAGE_PROOF_NL_READ},
            roles=roles,
            handler=_read_proof_nl,
        ),
        handler_tool(
            name="read_formal",
            description="Read the latest available Statement or Proof Formal Lean source for one current-node declaration; managed docstrings are omitted by default.",
            args_model=DeclFormalReadArgs,
            capability=ToolCapability.READ,
            result_view="decl_formal",
            groups={AppGroup.DECL_STAGE_FORMAL_READ},
            roles=roles,
            handler=_read_formal,
        ),
        handler_tool(
            name="inspect_node_decl",
            description="Inspect one public or private declaration revision in a permitted node of the current repository.",
            args_model=NodeDeclInspectArgs,
            capability=ToolCapability.READ,
            result_view="decl_revision",
            groups={AppGroup.DECL_GRAPH_READ_COORDINATOR},
            roles={"coordinator", "admin"},
            handler=_inspect_node_decl,
            required_context={"repo"},
        ),
        handler_tool(
            name="get_decl_change",
            description="Inspect a declaration change by change id.",
            args_model=ChangeIdArgs,
            capability=ToolCapability.READ,
            result_view="decl_change",
            groups={AppGroup.DECL_HISTORY_READ},
            roles=roles,
            handler=_get_decl_change,
        ),
        handler_tool(
            name="preview_decl_delete_closure",
            description="Compute downstream declarations that must be deleted with the requested roots.",
            args_model=DeclNamesArgs,
            capability=ToolCapability.READ,
            result_view="decl_delete_closure",
            groups={AppGroup.DECL_ROUND_CHANGE_WRITE},
            roles=plan_roles,
            handler=_compute_delete_closure,
        ),
        handler_tool(
            name="validate_decl_round_draft",
            description="Validate a draft declaration round before submit.",
            args_model=RoundIdArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={AppGroup.DECL_ROUND_CHANGE_WRITE},
            roles=plan_roles,
            handler=_validate_round_draft,
        ),
        handler_tool(
            name="compute_decl_dependency_closure",
            description="Compute upstream and downstream dependency closure for declarations.",
            args_model=DeclNamesArgs,
            capability=ToolCapability.READ,
            result_view="decl_dependency_closure",
            groups={AppGroup.DECL_READINESS_READ},
            roles=roles,
            handler=_compute_dependency_closure,
        ),
        handler_tool(
            name="compute_current_node_decl_dependency_closure",
            description="Compute upstream and downstream dependency closure for current-node declarations.",
            args_model=DeclNamesArgs,
            capability=ToolCapability.READ,
            result_view="decl_dependency_closure",
            groups={AppGroup.DECL_DEPENDENCY_ANALYSIS_READ},
            roles=roles,
            handler=_compute_dependency_closure,
        ),
        handler_tool(
            name="preview_current_node_decl_delete_closure",
            description="Preview the downstream declaration closure affected by deleting current-node declarations.",
            args_model=DeclNamesArgs,
            capability=ToolCapability.READ,
            result_view="decl_delete_closure",
            groups={AppGroup.DECL_DEPENDENCY_ANALYSIS_READ},
            roles=roles,
            handler=_preview_delete_closure,
        ),
        handler_tool(
            name="check_decl_ready",
            description="Check dynamic readiness of a declaration under the repo policy.",
            args_model=DeclReadyArgs,
            capability=ToolCapability.READ,
            result_view="decl_readiness",
            groups={AppGroup.DECL_READINESS_READ},
            roles=roles,
            handler=_check_decl_ready,
        ),
        handler_tool(
            name="list_content_public_decls",
            description="List public declarations exposed by the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="content_public_decls",
            groups={AppGroup.DECL_READINESS_READ},
            roles=roles,
            handler=_list_content_public_decls,
        ),
        handler_tool(
            name="list_visible_nodes",
            description="List visible current-repo nodes in the current context with each node's complete compact public declaration list.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="visible_nodes",
            groups={AppGroup.NODE_VISIBILITY_READ_CURRENT},
            roles=roles,
            handler=_list_visible_nodes,
            required_context={"repo"},
        ),
        handler_tool(
            name="list_imported_repos",
            description="List provider repository boundaries visible in the current context.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="imported_repos",
            groups={AppGroup.NODE_VISIBILITY_READ_CURRENT},
            roles=roles,
            handler=_list_imported_repos,
            required_context={"repo"},
        ),
        handler_tool(
            name="list_current_node_public_decls",
            description="List compact public declarations exposed by the current node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="public_decls",
            groups={AppGroup.PUBLIC_DECL_READ},
            roles=roles,
            handler=_list_current_node_public_decls,
        ),
        handler_tool(
            name="inspect_current_node_public_decl",
            description="Inspect one public declaration exposed by the current node.",
            args_model=DeclInspectArgs,
            capability=ToolCapability.READ,
            result_view="decl_revision",
            groups={AppGroup.PUBLIC_DECL_READ},
            roles=roles,
            handler=_inspect_current_node_public_decl,
        ),
        handler_tool(
            name="list_node_public_decls",
            description="List compact public declarations exposed by a visible current-repo node.",
            args_model=NodePublicDeclListArgs,
            capability=ToolCapability.READ,
            result_view="public_decls",
            groups={AppGroup.PUBLIC_DECL_READ, AppGroup.PUBLIC_DECL_READ_COORDINATOR},
            roles=roles,
            handler=_list_node_public_decls,
            required_context={"repo"},
        ),
        handler_tool(
            name="inspect_node_public_decl",
            description="Inspect one public declaration exposed by a visible current-repo node.",
            args_model=NodePublicDeclInspectArgs,
            capability=ToolCapability.READ,
            result_view="decl_revision",
            groups={AppGroup.PUBLIC_DECL_READ, AppGroup.PUBLIC_DECL_READ_COORDINATOR},
            roles=roles,
            handler=_inspect_node_public_decl,
            required_context={"repo"},
        ),
        handler_tool(
            name="list_repo_public_decls",
            description="List compact repo-level public declarations from a provider repository visible in the current context; provider node inventory stays private.",
            args_model=RepoPublicDeclListArgs,
            capability=ToolCapability.READ,
            result_view="public_decls",
            groups={AppGroup.PUBLIC_DECL_READ, AppGroup.PUBLIC_DECL_READ_COORDINATOR},
            roles=roles,
            handler=_list_repo_public_decls,
            required_context={"repo"},
        ),
        handler_tool(
            name="inspect_repo_public_decl",
            description="Inspect one public declaration exposed by a repository boundary visible in the current context.",
            args_model=RepoPublicDeclInspectArgs,
            capability=ToolCapability.READ,
            result_view="decl_revision",
            groups={AppGroup.PUBLIC_DECL_READ, AppGroup.PUBLIC_DECL_READ_COORDINATOR},
            roles=roles,
            handler=_inspect_repo_public_decl,
            required_context={"repo"},
        ),
        handler_tool(
            name="read_visible_decl_lean_file",
            description="Read the declaration-owned Lean file visible in the current caller context.",
            args_model=VisibleDeclLeanFileArgs,
            capability=ToolCapability.READ,
            result_view="decl_owned_lean_file",
            groups={AppGroup.VISIBLE_DECL_LEAN_FILE_READ},
            roles=roles,
            handler=_read_visible_decl_lean_file,
            required_context={"repo"},
        ),
        handler_tool(
            name="list_active_decl_names",
            description="List active declaration names in the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_names",
            groups={AppGroup.DECL_GRAPH_READ_CURRENT},
            roles=roles,
            handler=_list_active_decl_names,
        ),
        handler_tool(
            name="check_content_node_ready",
            description="Check whether the current content node can be submitted ready.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={AppGroup.DECL_READINESS_READ},
            roles={"plan", "admin"},
            handler=_check_content_ready,
        ),
        handler_tool(
            name="check_current_content_node_completion",
            description="Check whether the current content node satisfies the current completion gate.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={AppGroup.CONTENT_COMPLETION_GATE_READ},
            roles={"plan", "admin"},
            handler=_check_content_completion,
        ),
    ]
