"""Background scheduler loop for production app server."""

from __future__ import annotations

from typing import Any

import anyio

from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry
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


async def run_registry_scheduler_loop(
    registry: RepoRuntimeRegistry,
    *,
    tick_interval_s: float,
    idle_interval_s: float,
    error_interval_s: float,
    state: dict[str, Any] | None = None,
) -> None:
    """Run scheduler ticks for every active repo-local runtime until cancelled."""

    loop_state = state if state is not None else {}
    loop_state["running"] = True
    loop_state["tick_count"] = 0
    loop_state["repo_tick_count"] = {}
    try:
        while True:
            made_progress = False
            loaded_records = registry.loaded_records()
            loop_state["loaded_repo_count"] = len(loaded_records)
            for record in loaded_records:
                if record.state in {"paused", "dormant", "failed", "unloaded", "loading"}:
                    continue
                runtime = record.runtime
                if runtime is None:
                    continue
                try:
                    with record.lock:
                        if record.state in {"paused", "dormant", "failed", "unloaded", "loading"}:
                            continue
                        runtime = record.runtime
                        if runtime is None:
                            continue
                        schedule_service = runtime.ark.schedule_service
                        if schedule_service is None:
                            raise RuntimeError("ARK schedule_service is not configured.")
                        tick = schedule_service.schedule_ready()
                        if bool(getattr(tick, "auto_paused", False)):
                            record.state = "paused"
                    repo_tick_count = dict(loop_state.get("repo_tick_count", {}) or {})
                    repo_tick_count[record.repo_key] = int(repo_tick_count.get(record.repo_key, 0)) + 1
                    loop_state["repo_tick_count"] = repo_tick_count
                    loop_state["last_repo_tick"] = {
                        "repo_key": record.repo_key,
                        "tick": tick.model_dump(mode="json") if hasattr(tick, "model_dump") else tick,
                    }
                    loop_state["last_error"] = None
                    if bool(getattr(tick, "advanced_flow_ids", None) or getattr(tick, "started_step_ids", None)):
                        made_progress = True
                except Exception as exc:  # noqa: BLE001 - background server boundary.
                    registry.mark_failed(record.repo_key, exc)
                    loop_state["last_error"] = f"{record.repo_key}: {exc}"
                    await anyio.sleep(error_interval_s)
            loop_state["tick_count"] = int(loop_state.get("tick_count", 0)) + 1
            await anyio.sleep(tick_interval_s if made_progress else idle_interval_s)
    finally:
        loop_state["running"] = False


__all__ = ["run_registry_scheduler_loop", "run_scheduler_loop"]
