from __future__ import annotations

from typing import Any


REPO_FLOW_BOUNDARY_CHECKPOINT_KINDS = frozenset(
    {
        "before_native_coordinator_dispatch",
        "coordinator_requirement_waiting",
        "before_content_task_dispatch",
        "after_content_task_batch_terminal",
        "before_resource_request_dispatch",
        "after_resource_request_terminal",
    }
)


def repo_flow_boundary_checkpoints_enabled(app: Any) -> bool:
    config = getattr(app, "automatic_checkpoints", None)
    return bool(getattr(config, "repo_flow_boundaries_enabled", True))


def content_task_progress_checkpoints_enabled(app: Any) -> bool:
    config = getattr(app, "automatic_checkpoints", None)
    return bool(getattr(config, "content_task_progress_enabled", False))


def record_checkpoint_skip_summary(ctx: Any, reason: str) -> None:
    flow_service = getattr(getattr(ctx, "ark", None), "flow_service", None)
    store = getattr(flow_service, "store", None)
    if store is None:
        return

    def patch_step(step: Any) -> None:
        result = getattr(step, "result", None)
        if result is None or not hasattr(result, "model_copy"):
            return
        current = str(getattr(result, "summary", "") or "").strip()
        summary = f"{current} {reason}".strip()
        step.result = result.model_copy(update={"summary": summary})

    store.update_step_record(ctx.step.step_id, patch_step)
