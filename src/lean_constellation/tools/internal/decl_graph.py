"""DeclGraph and ContentPlan ordinary tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    ChangeIdArgs,
    ChangeSummaryArgs,
    DeclCreateArgs,
    DeclDeleteArgs,
    DeclInspectArgs,
    DeclNameArgs,
    DeclNamesArgs,
    DeclReadyArgs,
    DeclRevisionArgs,
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
        proof_policy_satisfied = bool(report.value.proof_policy_satisfied)
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
        "state": view.state.value if view.state is not None and hasattr(view.state, "value") else view.state,
        "status": view.status.value if view.status is not None and hasattr(view.status, "value") else view.status,
        "proof_policy_satisfied": proof_policy_satisfied,
        "summary": view.summary,
        "updated_at": view.updated_at,
    }


def _decl_revision_item(runtime, repo_root, view, args: DeclInspectArgs) -> dict[str, object]:
    report = _decl_satisfaction(runtime, repo_root, node_path=view.node_path, decl_name=view.decl_name)
    proof_policy_satisfied = bool(report.value.proof_policy_satisfied) if report.ok and report.value is not None else False
    data = view.model_dump(mode="json")
    data["proof_policy_satisfied"] = proof_policy_satisfied
    if not args.include_statement_nl:
        data.pop("statement_nl", None)
    if not args.include_statement_formal:
        data.pop("statement_lean_code", None)
        data.pop("statement_lean_check", None)
    if not args.include_proof_nl:
        data.pop("proof_nl", None)
    if not args.include_proof_formal:
        data.pop("proof_lean_code", None)
        data.pop("proof_lean_check", None)
    return data


def _public_decl_item(runtime, repo_root, public_decl) -> dict[str, object]:
    ref = public_decl.ref
    view = runtime.decl_graph.get_decl_view(repo_root, node_path=ref.node, name=ref.name)
    if not view.ok or view.value is None:
        return {
            "ref": ref.model_dump(mode="json"),
            "kind": public_decl.kind,
            "summary": public_decl.summary,
            "public": public_decl.public,
            "state": None,
            "proof_policy_satisfied": False,
            "source": public_decl.source,
        }
    item = _decl_list_item(runtime, repo_root, view.value)
    return {
        "ref": ref.model_dump(mode="json"),
        "kind": item["kind"],
        "summary": item["summary"],
        "public": item["public"],
        "state": item["state"],
        "proof_policy_satisfied": item["proof_policy_satisfied"],
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
    return runtime.decl_graph.mark_round_terminal_view(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
        result_kind=args.result_kind,
        reason=args.reason,
    )


def _create_decl(runtime, ctx, args: DeclCreateArgs):
    return runtime.decl_graph.create_decl_revision_view(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _open_decl_update(runtime, ctx, args: DeclUpdateArgs):
    return runtime.decl_graph.open_decl_update_revision_view(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _mark_decl_delete(runtime, ctx, args: DeclDeleteArgs):
    return runtime.decl_graph.mark_decl_delete_revision_view(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _list_decls(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_decl_views(ctx.repo_root, node_path=_node(ctx))


def _list_current_node_decls(runtime, ctx, args: NoArgs):
    del args
    views = runtime.decl_graph.list_decl_views(ctx.repo_root, node_path=_node(ctx))
    if not views.ok or views.value is None:
        return runtime.foundation.fail(views.issues)
    return runtime.foundation.ok([_decl_list_item(runtime, ctx.repo_root, item) for item in views.value], warnings=views.issues)


def _get_decl(runtime, ctx, args: DeclNameArgs):
    return runtime.decl_graph.get_decl_view(ctx.repo_root, node_path=_node(ctx), name=args.decl_name)


def _get_decl_revision(runtime, ctx, args: DeclRevisionArgs):
    return runtime.decl_graph.get_decl_revision_view(
        ctx.repo_root,
        node_path=_node(ctx),
        name=args.decl_name,
        revision=args.revision,
    )


def _inspect_current_node_decl(runtime, ctx, args: DeclInspectArgs):
    if args.revision is None:
        view = runtime.decl_graph.current_decl_revision_view(ctx.repo_root, node_path=_node(ctx), name=args.decl_name)
    else:
        view = runtime.decl_graph.get_decl_revision_view(ctx.repo_root, node_path=_node(ctx), name=args.decl_name, revision=args.revision)
    if not view.ok or view.value is None:
        return runtime.foundation.fail(view.issues)
    return runtime.foundation.ok(_decl_revision_item(runtime, ctx.repo_root, view.value, args))


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
    public = runtime.node.public_decl_access.list_node_public_decls(
        ctx.repo_root,
        node_path=_node(ctx),
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )
    if not public.ok or public.value is None:
        return runtime.foundation.fail(public.issues)
    return runtime.foundation.ok([_public_decl_item(runtime, ctx.repo_root, item) for item in public.value], warnings=public.issues)


def _list_node_public_decls(runtime, ctx, args: NodePublicDeclListArgs):
    public = runtime.node.public_decl_access.list_node_public_decls(
        ctx.repo_root,
        node_path=args.node_path,
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )
    if not public.ok or public.value is None:
        return runtime.foundation.fail(public.issues)
    return runtime.foundation.ok([_public_decl_item(runtime, ctx.repo_root, item) for item in public.value], warnings=public.issues)


def _list_repo_public_decls(runtime, ctx, args: RepoPublicDeclListArgs):
    repo_key = runtime.foundation.layout.ensure_safe_key(args.repo_key)
    public = runtime.node.public_decl_access.list_repo_public_decls(
        ctx.repo_root,
        repo_key=repo_key,
        actor_role=_actor_role(ctx),
        current_node_path=_maybe_node(ctx),
    )
    if not public.ok or public.value is None:
        return runtime.foundation.fail(public.issues)
    provider_root = ctx.repo_root.parent / repo_key
    return runtime.foundation.ok([_public_decl_item(runtime, provider_root, item) for item in public.value], warnings=public.issues)


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
    ref = next((decl.ref for decl in public.value if decl.ref.name == args.decl_name), None)
    if ref is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "public_decl_not_found",
                "No public declaration with the requested name is visible on the node.",
                object_ref=f"{args.node_path}:{args.decl_name}",
            )
        )
    revision = args.revision or ref.revision
    view = runtime.decl_graph.get_decl_revision_view(ctx.repo_root, node_path=ref.node, name=ref.name, revision=revision)
    if not view.ok or view.value is None:
        return runtime.foundation.fail(view.issues)
    return runtime.foundation.ok(_decl_revision_item(runtime, ctx.repo_root, view.value, args))


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
    ref = next((decl.ref for decl in public.value if decl.ref.name == args.decl_name), None)
    if ref is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "public_decl_not_found",
                "No public declaration with the requested name is visible on the repo public interface.",
                object_ref=f"{repo_key}:{args.decl_name}",
            )
        )
    provider_root = ctx.repo_root.parent / repo_key
    revision = args.revision or ref.revision
    view = runtime.decl_graph.get_decl_revision_view(provider_root, node_path=ref.node, name=ref.name, revision=revision)
    if not view.ok or view.value is None:
        return runtime.foundation.fail(view.issues)
    return runtime.foundation.ok(_decl_revision_item(runtime, provider_root, view.value, args))


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
            description="Plan creation of a declaration in the current draft round.",
            args_model=DeclCreateArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_revision",
            groups={AppGroup.DECL_ROUND_CHANGE_WRITE, AppGroup.DECL_CATALOG_PLAN_WRITE},
            roles=plan_roles,
            handler=_create_decl,
        ),
        handler_tool(
            name="plan_update_decl",
            description="Open a new declaration revision for an update in the current draft round.",
            args_model=DeclUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_revision",
            groups={AppGroup.DECL_ROUND_CHANGE_WRITE, AppGroup.DECL_CATALOG_PLAN_WRITE},
            roles=plan_roles,
            handler=_open_decl_update,
        ),
        handler_tool(
            name="plan_delete_decl",
            description="Plan deletion of a declaration in the current draft round.",
            args_model=DeclDeleteArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_revision",
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
            description="Inspect a declaration revision in the current content node.",
            args_model=DeclInspectArgs,
            capability=ToolCapability.READ,
            result_view="decl_revision",
            groups={AppGroup.CURRENT_NODE_DECL_READ},
            roles=roles,
            handler=_inspect_current_node_decl,
        ),
        handler_tool(
            name="get_decl_revision",
            description="Inspect a specific declaration revision.",
            args_model=DeclRevisionArgs,
            capability=ToolCapability.READ,
            result_view="decl_revision",
            groups={AppGroup.DECL_HISTORY_READ},
            roles=roles,
            handler=_get_decl_revision,
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
            description="List current-repo nodes whose public boundary is visible in the current context.",
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
            description="List imported provider repos whose public interface is visible in the current context.",
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
            description="List public declarations exposed by the current node.",
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
            description="List public declarations exposed by a visible current-repo node.",
            args_model=NodePublicDeclListArgs,
            capability=ToolCapability.READ,
            result_view="public_decls",
            groups={AppGroup.PUBLIC_DECL_READ},
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
            groups={AppGroup.PUBLIC_DECL_READ},
            roles=roles,
            handler=_inspect_node_public_decl,
            required_context={"repo"},
        ),
        handler_tool(
            name="list_repo_public_decls",
            description="List public declarations exposed by a visible imported repo interface.",
            args_model=RepoPublicDeclListArgs,
            capability=ToolCapability.READ,
            result_view="public_decls",
            groups={AppGroup.PUBLIC_DECL_READ},
            roles=roles,
            handler=_list_repo_public_decls,
            required_context={"repo"},
        ),
        handler_tool(
            name="inspect_repo_public_decl",
            description="Inspect one public declaration exposed by a visible imported repo interface.",
            args_model=RepoPublicDeclInspectArgs,
            capability=ToolCapability.READ,
            result_view="decl_revision",
            groups={AppGroup.PUBLIC_DECL_READ},
            roles=roles,
            handler=_inspect_repo_public_decl,
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
