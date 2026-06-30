"""DeclGraph and ContentPlan ordinary tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    ChangeIdArgs,
    ChangeSummaryArgs,
    DeclCreateArgs,
    DeclDeleteArgs,
    DeclNameArgs,
    DeclNamesArgs,
    DeclReadyArgs,
    DeclRevisionArgs,
    DeclUpdateArgs,
    NoArgs,
    RoundDraftArgs,
    RoundIdArgs,
    RoundSummaryArgs,
    RoundTerminalArgs,
    StrategyCloseArgs,
    StrategyEnsureArgs,
    StrategyIdArgs,
)
from lean_constellation.tools.specs import current_node_path, handler_tool


def _node(ctx) -> str:
    return current_node_path(ctx)


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
    return runtime.decl_graph.ensure_open_strategy(
        ctx.repo_root,
        node_path=_node(ctx),
        objective=args.objective,
        rationale=args.rationale,
    )


def _close_strategy(runtime, ctx, args: StrategyCloseArgs):
    return runtime.decl_graph.close_strategy(
        ctx.repo_root,
        node_path=_node(ctx),
        strategy_id=args.strategy_id,
        summary=args.summary,
        reason=args.reason,
        failed=args.failed,
    )


def _list_strategies(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_strategies(ctx.repo_root, node_path=_node(ctx))


def _get_strategy(runtime, ctx, args: StrategyIdArgs):
    return runtime.decl_graph.get_strategy(ctx.repo_root, node_path=_node(ctx), strategy_id=args.strategy_id)


def _create_round_draft(runtime, ctx, args: RoundDraftArgs):
    return runtime.decl_graph.create_round_draft(
        ctx.repo_root,
        node_path=_node(ctx),
        strategy_id=args.strategy_id,
        objective=args.objective,
        change_ids=args.change_ids,
    )


def _list_rounds(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_rounds(ctx.repo_root, node_path=_node(ctx))


def _get_round(runtime, ctx, args: RoundIdArgs):
    return runtime.decl_graph.get_round(ctx.repo_root, node_path=_node(ctx), round_id=_required_round_id(runtime, ctx, args.round_id))


def _write_change_summary(runtime, ctx, args: ChangeSummaryArgs):
    return runtime.decl_graph.write_decl_change_summary(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
        change_id=args.change_id,
        summary=args.summary,
    )


def _write_round_summary(runtime, ctx, args: RoundSummaryArgs):
    return runtime.decl_graph.write_round_summary(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
        summary=args.summary,
    )


def _mark_round_terminal(runtime, ctx, args: RoundTerminalArgs):
    return runtime.decl_graph.mark_round_terminal(
        ctx.repo_root,
        node_path=_node(ctx),
        round_id=_required_round_id(runtime, ctx, args.round_id),
        result_kind=args.result_kind,
        reason=args.reason,
    )


def _create_decl(runtime, ctx, args: DeclCreateArgs):
    return runtime.decl_graph.create_decl(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _open_decl_update(runtime, ctx, args: DeclUpdateArgs):
    return runtime.decl_graph.open_decl_update(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _mark_decl_delete(runtime, ctx, args: DeclDeleteArgs):
    return runtime.decl_graph.mark_decl_delete(ctx.repo_root, node_path=_node(ctx), **args.model_dump())


def _list_decls(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_decls(ctx.repo_root, node_path=_node(ctx))


def _get_decl(runtime, ctx, args: DeclNameArgs):
    return runtime.decl_graph.get_decl(ctx.repo_root, node_path=_node(ctx), name=args.decl_name)


def _get_decl_revision(runtime, ctx, args: DeclRevisionArgs):
    return runtime.decl_graph.get_decl_revision(
        ctx.repo_root,
        node_path=_node(ctx),
        name=args.decl_name,
        revision=args.revision,
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


def _check_decl_ready(runtime, ctx, args: DeclReadyArgs):
    return runtime.decl_graph.check_decl_ready(ctx.repo_root, node_path=_node(ctx), decl_name=args.decl_name, policy=args.policy)


def _list_content_public_decls(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_content_public_decls(ctx.repo_root, node_path=_node(ctx))


def _list_active_decl_names(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.list_active_decl_names(ctx.repo_root, node_path=_node(ctx))


def _check_content_ready(runtime, ctx, args: NoArgs):
    del args
    return runtime.decl_graph.check_content_node_ready(ctx.repo_root, node_path=_node(ctx))


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
            groups={"decl_graph_read_current"},
            roles=plan_roles,
            handler=_ensure_graph,
        ),
        handler_tool(
            name="get_current_decl_graph_index",
            description="Read the current content node DeclGraph index.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_graph_index",
            groups={"decl_graph_read_current"},
            roles=roles,
            handler=_graph_index,
        ),
        handler_tool(
            name="get_current_decl_graph_store",
            description="Read DeclGraph store counts and paths for the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_graph_store",
            groups={"decl_graph_read_current"},
            roles=roles,
            handler=_graph_store,
        ),
        handler_tool(
            name="rebuild_current_decl_graph_index",
            description="Rebuild the current content node DeclGraph index.",
            args_model=NoArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_graph_index",
            groups={"decl_graph_read_current"},
            roles=plan_roles,
            handler=_rebuild_graph,
        ),
        handler_tool(
            name="ensure_open_decl_strategy",
            description="Ensure the current content node has one open declaration strategy.",
            args_model=StrategyEnsureArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_strategy",
            groups={"decl_strategy_write"},
            roles=plan_roles,
            handler=_ensure_open_strategy,
        ),
        handler_tool(
            name="close_decl_strategy",
            description="Close an open declaration strategy as closed or failed.",
            args_model=StrategyCloseArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_strategy",
            groups={"decl_strategy_write"},
            roles=plan_roles,
            handler=_close_strategy,
        ),
        handler_tool(
            name="list_decl_strategies",
            description="List declaration strategies for the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_strategy_list",
            groups={"decl_graph_read_current"},
            roles=roles,
            handler=_list_strategies,
        ),
        handler_tool(
            name="get_decl_strategy",
            description="Inspect one declaration strategy in the current content node.",
            args_model=StrategyIdArgs,
            capability=ToolCapability.READ,
            result_view="decl_strategy",
            groups={"decl_graph_read_current"},
            roles=roles,
            handler=_get_strategy,
        ),
        handler_tool(
            name="create_decl_round_draft",
            description="Create a draft declaration round under an open strategy.",
            args_model=RoundDraftArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_round",
            groups={"decl_round_change_write"},
            roles=plan_roles,
            handler=_create_round_draft,
        ),
        handler_tool(
            name="list_decl_rounds",
            description="List declaration rounds for the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_round_list",
            groups={"decl_graph_read_current"},
            roles=roles,
            handler=_list_rounds,
        ),
        handler_tool(
            name="get_decl_round",
            description="Inspect a declaration round in the current content node.",
            args_model=RoundIdArgs,
            capability=ToolCapability.READ,
            result_view="decl_round",
            groups={"decl_graph_read_current"},
            roles=roles,
            handler=_get_round,
        ),
        handler_tool(
            name="write_decl_change_summary",
            description="Write one declaration change closeout summary.",
            args_model=ChangeSummaryArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_round",
            groups={"decl_round_closeout_write"},
            roles=plan_roles,
            handler=_write_change_summary,
        ),
        handler_tool(
            name="write_decl_round_summary",
            description="Write the declaration round closeout summary.",
            args_model=RoundSummaryArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_round",
            groups={"decl_round_closeout_write"},
            roles=plan_roles,
            handler=_write_round_summary,
        ),
        handler_tool(
            name="mark_decl_round_terminal",
            description="Mark a declaration round as success, blocked, or failed after closeout.",
            args_model=RoundTerminalArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_round",
            groups={"decl_round_closeout_write"},
            roles=plan_roles,
            handler=_mark_round_terminal,
        ),
        handler_tool(
            name="plan_create_decl",
            description="Plan creation of a declaration in the current draft round.",
            args_model=DeclCreateArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_change",
            groups={"decl_round_change_write", "decl_catalog_plan_write"},
            roles=plan_roles,
            handler=_create_decl,
        ),
        handler_tool(
            name="plan_update_decl",
            description="Open a new declaration revision for an update in the current draft round.",
            args_model=DeclUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_change",
            groups={"decl_round_change_write", "decl_catalog_plan_write"},
            roles=plan_roles,
            handler=_open_decl_update,
        ),
        handler_tool(
            name="plan_delete_decl",
            description="Plan deletion of a declaration in the current draft round.",
            args_model=DeclDeleteArgs,
            capability=ToolCapability.WRITE,
            result_view="decl_change",
            groups={"decl_round_change_write", "decl_catalog_plan_write"},
            roles=plan_roles,
            handler=_mark_decl_delete,
        ),
        handler_tool(
            name="list_current_decls",
            description="List declarations in the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_list",
            groups={"decl_detail_read"},
            roles=roles,
            handler=_list_decls,
        ),
        handler_tool(
            name="get_decl",
            description="Inspect a declaration catalog entry in the current content node.",
            args_model=DeclNameArgs,
            capability=ToolCapability.READ,
            result_view="decl",
            groups={"decl_detail_read"},
            roles=roles,
            handler=_get_decl,
        ),
        handler_tool(
            name="get_decl_revision",
            description="Inspect a specific declaration revision.",
            args_model=DeclRevisionArgs,
            capability=ToolCapability.READ,
            result_view="decl_revision",
            groups={"decl_history_read"},
            roles=roles,
            handler=_get_decl_revision,
        ),
        handler_tool(
            name="get_decl_change",
            description="Inspect a declaration change by change id.",
            args_model=ChangeIdArgs,
            capability=ToolCapability.READ,
            result_view="decl_change",
            groups={"decl_history_read"},
            roles=roles,
            handler=_get_decl_change,
        ),
        handler_tool(
            name="preview_decl_delete_closure",
            description="Compute downstream declarations that must be deleted with the requested roots.",
            args_model=DeclNamesArgs,
            capability=ToolCapability.READ,
            result_view="decl_delete_closure",
            groups={"decl_round_change_write"},
            roles=plan_roles,
            handler=_compute_delete_closure,
        ),
        handler_tool(
            name="validate_decl_round_draft",
            description="Validate a draft declaration round before submit.",
            args_model=RoundIdArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={"decl_round_change_write"},
            roles=plan_roles,
            handler=_validate_round_draft,
        ),
        handler_tool(
            name="compute_decl_dependency_closure",
            description="Compute upstream and downstream dependency closure for declarations.",
            args_model=DeclNamesArgs,
            capability=ToolCapability.READ,
            result_view="decl_dependency_closure",
            groups={"decl_readiness_read"},
            roles=roles,
            handler=_compute_dependency_closure,
        ),
        handler_tool(
            name="check_decl_ready",
            description="Check dynamic readiness of a declaration under the repo policy.",
            args_model=DeclReadyArgs,
            capability=ToolCapability.READ,
            result_view="decl_readiness",
            groups={"decl_readiness_read"},
            roles=roles,
            handler=_check_decl_ready,
        ),
        handler_tool(
            name="list_content_public_decls",
            description="List public declarations exposed by the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="content_public_decls",
            groups={"decl_readiness_read"},
            roles=roles,
            handler=_list_content_public_decls,
        ),
        handler_tool(
            name="list_active_decl_names",
            description="List active declaration names in the current content node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="decl_names",
            groups={"decl_graph_read_current"},
            roles=roles,
            handler=_list_active_decl_names,
        ),
        handler_tool(
            name="check_content_node_ready",
            description="Check whether the current content node can be submitted ready.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={"decl_readiness_read"},
            roles={"plan", "admin"},
            handler=_check_content_ready,
        ),
    ]
