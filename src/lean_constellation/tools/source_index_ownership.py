"""Flow-owned SourceIndex update context for Agent-facing tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolExecutionContext


def resolve_source_index_update_owner(
    runtime,
    ctx: ToolExecutionContext,
    *,
    allowed_step_types: set[str],
):
    flow_id = ctx.runtime.flow_id
    step_id = ctx.runtime.step_id
    if not flow_id or not step_id:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_flow_context_required",
                "SourceIndex access requires the current SourceIndex build Flow context.",
            )
        )
    try:
        flow = runtime.get_flow(flow_id)
        step = runtime.get_step(step_id)
    except Exception as exc:  # noqa: BLE001 - normalize runtime identity failures.
        return runtime.foundation.fail(
            runtime.foundation.issue("source_index_flow_context_invalid", str(exc), object_ref=flow_id)
        )
    update_id = getattr(getattr(flow, "state", None), "active_update_id", None)
    if getattr(flow, "flow_type", None) != "source_index_build" or not update_id:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_update_owner_mismatch",
                "Current Flow does not own an active SourceIndex update.",
                object_ref=flow_id,
            )
        )
    flow_input = getattr(flow, "input", None)
    if getattr(flow_input, "repo_root", None) is not None and str(flow_input.repo_root) != str(ctx.repo_root):
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_repo_context_mismatch",
                "The owning SourceIndex Flow is bound to a different repository.",
            )
        )
    if getattr(step, "flow_id", None) != flow_id or getattr(step, "step_type", None) not in allowed_step_types:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_update_owner_mismatch",
                "Current step does not own this SourceIndex operation.",
                object_ref=step_id,
            )
        )
    return runtime.foundation.ok(str(update_id))


__all__ = ["resolve_source_index_update_owner"]
