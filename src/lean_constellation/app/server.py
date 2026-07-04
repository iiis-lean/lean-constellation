"""Unified production app server for Admin HTTP and MCP HTTP."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial

import anyio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from lean_constellation.app.admin_http import create_admin_http_routes, create_workspace_admin_http_routes
from lean_constellation.app.bootstrap import ProductionAgentHomesView, materialize_production_agent_homes
from lean_constellation.app.config import LeanAppConfig
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry
from lean_constellation.app.runtime import create_app_runtime_from_config
from lean_constellation.app.scheduler_loop import run_registry_scheduler_loop, run_scheduler_loop
from lean_constellation.app.toolkit_process import ManagedToolkitProcess, ManagedToolkitView
from lean_constellation.mcp.http import create_mcp_http_app, create_repo_mcp_http_routes
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
    materialize_agent_homes: bool | None = None,
    toolkit_state: ManagedToolkitView | None = None,
) -> ServiceResult[Starlette]:
    """Create one ASGI app containing Admin HTTP, MCP HTTP, and scheduler loop."""

    if runtime is None:
        return _create_registry_production_app_server(
            config,
            view_keys=view_keys,
            external_config=external_config,
            external_overrides=external_overrides,
            agent_providers=agent_providers,
            materialize_agent_homes=materialize_agent_homes,
            toolkit_state=toolkit_state,
        )
    return _create_legacy_production_app_server(
        config,
        runtime=runtime,
        view_keys=view_keys,
        materialize_agent_homes=materialize_agent_homes,
        toolkit_state=toolkit_state,
    )


def _create_legacy_production_app_server(
    config: LeanAppConfig,
    *,
    runtime: LeanRuntimeServices,
    view_keys: Iterable[str] | None = None,
    materialize_agent_homes: bool | None = None,
    toolkit_state: ManagedToolkitView | None = None,
) -> ServiceResult[Starlette]:
    """Create the pre repo-local single-runtime ASGI app for tests/debug."""

    resolved_runtime = runtime
    resolved_mcp_base_url = config.production_mcp_http_effective_base_url()
    homes_view: ProductionAgentHomesView | None = None
    should_materialize_homes = config.materialize_agent_homes if materialize_agent_homes is None else materialize_agent_homes
    if should_materialize_homes:
        homes_result = materialize_production_agent_homes(
            resolved_runtime,
            mcp_http_base_url=resolved_mcp_base_url,
            base_config_path=config.codex_base_config_path,
            auth_json_path=config.codex_auth_json_path,
        )
        if not homes_result.ok or homes_result.value is None:
            return resolved_runtime.foundation.fail(homes_result.issues)
        homes_view = homes_result.value
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
                "mcp_base_url": resolved_mcp_base_url,
                "scheduler": scheduler_state,
                "agent_homes": homes_view.model_dump(mode="json") if homes_view is not None else None,
                "toolkit": toolkit_state.model_dump(mode="json") if toolkit_state is not None else None,
            }
        )

    routes = [
        Route("/health", health, methods=["GET"]),
        *create_admin_http_routes(
            resolved_runtime,
            workspace_root=config.workspace_root,
            toolkit_state=toolkit_state,
        ),
        *mcp_app.routes,
    ]

    @asynccontextmanager
    async def lifespan(app: Starlette):  # noqa: ANN001 - Starlette lifespan boundary.
        app.state.lean_constellation_runtime = resolved_runtime
        app.state.lean_constellation_config = config
        app.state.lean_constellation_scheduler = scheduler_state
        app.state.lean_constellation_mcp_base_url = resolved_mcp_base_url
        app.state.lean_constellation_agent_homes = homes_view
        app.state.lean_constellation_toolkit = toolkit_state
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
    app.state.lean_constellation_mcp_base_url = resolved_mcp_base_url
    app.state.lean_constellation_agent_homes = homes_view
    app.state.lean_constellation_toolkit = toolkit_state
    return resolved_runtime.foundation.ok(app)


def _create_registry_production_app_server(
    config: LeanAppConfig,
    *,
    view_keys: Iterable[str] | None = None,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    agent_providers: dict[str, object] | None = None,
    materialize_agent_homes: bool | None = None,
    toolkit_state: ManagedToolkitView | None = None,
) -> ServiceResult[Starlette]:
    """Create the production workspace app with repo-local runtime registry."""

    if materialize_agent_homes is not None:
        config = config.model_copy(update={"materialize_agent_homes": materialize_agent_homes})
    registry = RepoRuntimeRegistry(
        config,
        external_config=external_config,
        external_overrides=external_overrides,
        agent_providers=agent_providers,
    )
    resolved_mcp_base_url = config.production_mcp_http_effective_base_url()
    scheduler_state: dict[str, object] = {"running": False}
    repo_mcp_router, repo_mcp_routes = create_repo_mcp_http_routes(registry, view_keys=view_keys)

    async def health(request) -> JSONResponse:  # noqa: ANN001 - ASGI route boundary.
        del request
        repos = registry.list_status()
        return JSONResponse(
            {
                "ok": repos.ok,
                "service": "lean_constellation_production_app",
                "admin_base_url": config.admin_http_effective_base_url(),
                "mcp_base_url": resolved_mcp_base_url,
                "scheduler": scheduler_state,
                "repo_runtimes": repos.value.model_dump(mode="json") if repos.ok and repos.value is not None else None,
                "toolkit": toolkit_state.model_dump(mode="json") if toolkit_state is not None else None,
            }
        )

    routes = [
        Route("/health", health, methods=["GET"]),
        *create_workspace_admin_http_routes(
            registry,
            toolkit_state=toolkit_state,
            on_repo_unload=repo_mcp_router.cleanup_repo,
        ),
        *repo_mcp_routes,
    ]

    @asynccontextmanager
    async def lifespan(app: Starlette):  # noqa: ANN001 - Starlette lifespan boundary.
        app.state.lean_constellation_registry = registry
        app.state.lean_constellation_config = config
        app.state.lean_constellation_scheduler = scheduler_state
        app.state.lean_constellation_mcp_base_url = resolved_mcp_base_url
        app.state.lean_constellation_toolkit = toolkit_state
        async with anyio.create_task_group() as task_group:
            if config.scheduler_enabled:
                task_group.start_soon(
                    partial(
                        run_registry_scheduler_loop,
                        registry,
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
                await repo_mcp_router.shutdown()
                registry.shutdown_all()

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.lean_constellation_registry = registry
    app.state.lean_constellation_config = config
    app.state.lean_constellation_scheduler = scheduler_state
    app.state.lean_constellation_mcp_base_url = resolved_mcp_base_url
    app.state.lean_constellation_mcp_router = repo_mcp_router
    app.state.lean_constellation_toolkit = toolkit_state
    return registry.result.ok(app)


async def run_production_app_server(
    config: LeanAppConfig,
    *,
    view_keys: Iterable[str] | None = None,
    log_level: str = "info",
) -> None:
    """Run the unified production app server until uvicorn exits."""

    toolkit_process: ManagedToolkitProcess | None = None
    toolkit_state: ManagedToolkitView | None = None
    if config.toolkit.mode == "managed":
        toolkit_process = ManagedToolkitProcess(config.toolkit)
        toolkit_state = toolkit_process.start()
    try:
        app = create_production_app_server(config, view_keys=view_keys, toolkit_state=toolkit_state)
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
    finally:
        if toolkit_process is not None:
            toolkit_process.stop()


__all__ = [
    "create_production_app_server",
    "run_production_app_server",
]
