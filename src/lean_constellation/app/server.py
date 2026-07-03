"""Unified production app server for Admin HTTP and MCP HTTP."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial

import anyio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from lean_constellation.app.admin_http import create_admin_http_routes
from lean_constellation.app.config import LeanAppConfig
from lean_constellation.app.runtime import create_app_runtime_from_config
from lean_constellation.app.scheduler_loop import run_scheduler_loop
from lean_constellation.mcp.http import create_mcp_http_app
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices


def create_production_app_server(
    config: LeanAppConfig,
    *,
    runtime: LeanRuntimeServices | None = None,
    view_keys: Iterable[str] | None = None,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    agent_providers: dict[str, object] | None = None,
) -> ServiceResult[Starlette]:
    """Create one ASGI app containing Admin HTTP, MCP HTTP, and scheduler loop."""

    resolved_runtime = runtime or create_app_runtime_from_config(
        config,
        external_config=external_config,
        external_overrides=external_overrides,
        agent_providers=agent_providers,
        start_paused=config.server_start_paused,
    )
    mcp_app_result = create_mcp_http_app(resolved_runtime, view_keys=view_keys)
    if not mcp_app_result.ok or mcp_app_result.value is None:
        return resolved_runtime.foundation.fail(mcp_app_result.issues)
    mcp_app = mcp_app_result.value
    scheduler_state: dict[str, object] = {"running": False}

    async def health(request) -> JSONResponse:  # noqa: ANN001 - ASGI route boundary.
        del request
        return JSONResponse(
            {
                "ok": True,
                "service": "lean_constellation_production_app",
                "admin_base_url": config.admin_http_effective_base_url(),
                "mcp_base_url": config.mcp_http_effective_base_url(),
                "scheduler": scheduler_state,
            }
        )

    routes = [
        Route("/health", health, methods=["GET"]),
        *create_admin_http_routes(resolved_runtime),
        *mcp_app.routes,
    ]

    @asynccontextmanager
    async def lifespan(app: Starlette):  # noqa: ANN001 - Starlette lifespan boundary.
        app.state.lean_constellation_runtime = resolved_runtime
        app.state.lean_constellation_config = config
        app.state.lean_constellation_scheduler = scheduler_state
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            async with anyio.create_task_group() as task_group:
                if config.scheduler_enabled:
                    task_group.start_soon(
                        partial(
                            run_scheduler_loop,
                            resolved_runtime,
                            tick_interval_s=config.scheduler_tick_interval_s,
                            idle_interval_s=config.scheduler_idle_interval_s,
                            error_interval_s=config.scheduler_error_interval_s,
                            state=scheduler_state,
                        )
                    )
                try:
                    yield
                finally:
                    task_group.cancel_scope.cancel()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.lean_constellation_runtime = resolved_runtime
    app.state.lean_constellation_config = config
    app.state.lean_constellation_scheduler = scheduler_state
    return resolved_runtime.foundation.ok(app)


async def run_production_app_server(
    config: LeanAppConfig,
    *,
    view_keys: Iterable[str] | None = None,
    log_level: str = "info",
) -> None:
    """Run the unified production app server until uvicorn exits."""

    app = create_production_app_server(config, view_keys=view_keys)
    if not app.ok or app.value is None:
        message = "; ".join(issue.message for issue in app.issues) or "Production app server setup failed."
        raise RuntimeError(message)
    import uvicorn

    uvicorn_config = uvicorn.Config(
        app.value,
        host=config.admin_http_host,
        port=config.admin_http_port,
        log_level=log_level,
        ws="wsproto",
    )
    server = uvicorn.Server(uvicorn_config)
    await server.serve()


__all__ = [
    "create_production_app_server",
    "run_production_app_server",
]
