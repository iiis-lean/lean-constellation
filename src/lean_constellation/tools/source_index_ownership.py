"""Runtime context authorization for Agent-facing SourceIndex tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolExecutionContext


def authorize_source_index_flow_context(
    runtime,
    ctx: ToolExecutionContext,
    *,
    allowed_step_types: set[str],
    allowed_actor_roles: set[str],
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
    if getattr(flow, "flow_type", None) != "source_index_build":
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_flow_context_mismatch",
                "Current Flow is not a SourceIndex build Flow.",
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
                "source_index_step_context_mismatch",
                "Current Step does not authorize this SourceIndex operation.",
                object_ref=step_id,
            )
        )
    if ctx.actor.role not in allowed_actor_roles:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "source_index_actor_context_mismatch",
                "Current actor role does not authorize this SourceIndex operation.",
                current=ctx.actor.role,
                expected=", ".join(sorted(allowed_actor_roles)),
            )
        )
    return runtime.foundation.ok(None)


__all__ = ["authorize_source_index_flow_context"]
