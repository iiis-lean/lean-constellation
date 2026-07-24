"""Narrow terminal preparation history reads for ContentPlan."""

from __future__ import annotations

from lean_constellation.flows.content_node_task.context_brief import (
    get_preparation_result,
    list_preparation_results,
)
from lean_constellation.services.tool_facade import (
    ToolCapability,
    ToolExecutionContext,
    ToolSpec,
)
from lean_constellation.tools.args import (
    ContentPreparationResultArgs,
    ContentPreparationResultListArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import handler_tool


def _content_flow_id(runtime, ctx: ToolExecutionContext):
    if not ctx.runtime.flow_id:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "content_preparation_context_missing",
                "Preparation history requires an active ContentNodeTask Flow context.",
            )
        )
    flow = runtime.get_flow(ctx.runtime.flow_id)
    if getattr(flow, "flow_type", None) == "content_node_task":
        return runtime.foundation.ok(flow.flow_id)
    parent_flow_id = getattr(flow, "parent_flow_id", None)
    if parent_flow_id:
        parent = runtime.get_flow(parent_flow_id)
        if getattr(parent, "flow_type", None) == "content_node_task":
            return runtime.foundation.ok(parent.flow_id)
    return runtime.foundation.fail(
        runtime.foundation.issue(
            "content_preparation_context_missing",
            "Preparation history is only available inside the current content task.",
            current=getattr(flow, "flow_type", None),
            expected="content_node_task",
        )
    )


def _list_results(
    runtime,
    ctx: ToolExecutionContext,
    args: ContentPreparationResultListArgs,
):
    content_flow = _content_flow_id(runtime, ctx)
    if not content_flow.ok or content_flow.value is None:
        return content_flow
    return runtime.foundation.ok(
        list_preparation_results(
            runtime,
            content_flow_id=content_flow.value,
            kind=args.kind,
        )
    )


def _get_result(
    runtime,
    ctx: ToolExecutionContext,
    args: ContentPreparationResultArgs,
):
    content_flow = _content_flow_id(runtime, ctx)
    if not content_flow.ok or content_flow.value is None:
        return content_flow
    result = get_preparation_result(
        runtime,
        content_flow_id=content_flow.value,
        kind=args.kind,
        attempt=args.attempt,
    )
    if result is None:
        attempt = "latest" if args.attempt is None else str(args.attempt)
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "content_preparation_result_not_found",
                "No matching terminal content preparation result was found.",
                object_ref=f"{args.kind}:{attempt}",
            )
        )
    return runtime.foundation.ok(result)


def build_tool_specs() -> list[ToolSpec]:
    roles = {"plan", "admin"}
    return [
        handler_tool(
            name="list_content_preparation_results",
            description=(
                "List compact terminal preparation outcomes for the current content "
                "task. Use only when older attempts matter; the current callback result "
                "is already present in the turn."
            ),
            args_model=ContentPreparationResultListArgs,
            capability=ToolCapability.READ,
            result_view="content_preparation_result_index",
            groups={AppGroup.CONTENT_PREPARATION_HISTORY_READ},
            roles=roles,
            handler=_list_results,
        ),
        handler_tool(
            name="get_content_preparation_result",
            description=(
                "Read one terminal preparation result for the current content task by "
                "kind and optional one-based attempt."
            ),
            args_model=ContentPreparationResultArgs,
            capability=ToolCapability.READ,
            result_view="content_preparation_result",
            groups={AppGroup.CONTENT_PREPARATION_HISTORY_READ},
            roles=roles,
            handler=_get_result,
        ),
    ]
