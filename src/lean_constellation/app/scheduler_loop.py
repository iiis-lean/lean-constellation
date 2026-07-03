"""Background scheduler loop for production app server."""

from __future__ import annotations

from typing import Any

import anyio

from lean_constellation.services.runtime import LeanRuntimeServices


async def run_scheduler_loop(
    runtime: LeanRuntimeServices,
    *,
    tick_interval_s: float,
    idle_interval_s: float,
    error_interval_s: float,
    state: dict[str, Any] | None = None,
) -> None:
    """Run ARK scheduler ticks until the task is cancelled."""

    loop_state = state if state is not None else {}
    loop_state["running"] = True
    loop_state["tick_count"] = 0
    try:
        while True:
            try:
                schedule_service = runtime.ark.schedule_service
                if schedule_service is None:
                    loop_state["last_error"] = "ARK schedule_service is not configured."
                    await anyio.sleep(error_interval_s)
                    continue
                tick = schedule_service.schedule_ready()
                loop_state["tick_count"] = int(loop_state.get("tick_count", 0)) + 1
                loop_state["last_tick"] = tick.model_dump(mode="json") if hasattr(tick, "model_dump") else tick
                loop_state["last_error"] = None
                made_progress = bool(getattr(tick, "advanced_flow_ids", None) or getattr(tick, "started_step_ids", None))
                await anyio.sleep(tick_interval_s if made_progress else idle_interval_s)
            except Exception as exc:  # noqa: BLE001 - background server boundary.
                loop_state["last_error"] = str(exc)
                await anyio.sleep(error_interval_s)
    finally:
        loop_state["running"] = False


__all__ = ["run_scheduler_loop"]
