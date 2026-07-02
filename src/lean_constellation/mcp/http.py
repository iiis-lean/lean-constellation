"""Streamable HTTP MCP server for Lean Constellation ToolView endpoints."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from lean_constellation.mcp.server import create_mcp_server
from lean_constellation.mcp.stdio import create_mcp_protocol_server
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices


def create_mcp_http_app(
    runtime: LeanRuntimeServices,
    *,
    view_keys: Iterable[str] | None = None,
) -> ServiceResult[Starlette]:
    """Create one shared ASGI app exposing every requested ToolView as MCP HTTP."""

    resolved_keys = _resolve_view_keys(runtime, view_keys)
    if not resolved_keys.ok or resolved_keys.value is None:
        return runtime.foundation.fail(resolved_keys.issues)

    managers: list[StreamableHTTPSessionManager] = []
    routes: list[Any] = [
        Route("/health", _health),
        Route("/healthz", _health),
        Route("/mcp/views", _view_index_factory(resolved_keys.value)),
    ]
    for view_key in resolved_keys.value:
        protocol = create_mcp_protocol_server(runtime, view_key=view_key)
        if not protocol.ok or protocol.value is None:
            return runtime.foundation.fail(protocol.issues)
        manager = StreamableHTTPSessionManager(
            protocol.value,
            json_response=True,
            stateless=True,
        )
        managers.append(manager)
        routes.append(Mount(f"/mcp/views/{view_key}", app=_ManagerApp(manager)))

    @asynccontextmanager
    async def lifespan(app: Starlette):  # noqa: ANN001 - Starlette lifespan boundary.
        del app
        async with AsyncExitStack() as stack:
            for manager in managers:
                await stack.enter_async_context(manager.run())
            yield

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.lean_constellation_mcp_views = resolved_keys.value
    return runtime.foundation.ok(app)


async def run_mcp_http_server(
    runtime: LeanRuntimeServices,
    *,
    host: str,
    port: int,
    view_keys: Iterable[str] | None = None,
    log_level: str = "info",
) -> None:
    """Run the shared MCP HTTP server until the ASGI server exits."""

    app = create_mcp_http_app(runtime, view_keys=view_keys)
    if not app.ok or app.value is None:
        raise RuntimeError(_issues_summary(app))
    import uvicorn

    config = uvicorn.Config(app.value, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    await server.serve()


class _ManagerApp:
    def __init__(self, manager: StreamableHTTPSessionManager) -> None:
        self.manager = manager

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001 - ASGI boundary.
        await self.manager.handle_request(scope, receive, send)


async def _health(request: Request) -> JSONResponse:
    del request
    return JSONResponse({"ok": True, "service": "lean_constellation_mcp_http"})


def _view_index_factory(view_keys: list[str]):
    async def view_index(request: Request) -> JSONResponse:
        del request
        return JSONResponse({"ok": True, "views": view_keys})

    return view_index


def _resolve_view_keys(runtime: LeanRuntimeServices, view_keys: Iterable[str] | None) -> ServiceResult[list[str]]:
    if view_keys is not None:
        return runtime.foundation.ok(sorted({str(view_key) for view_key in view_keys}))
    server = create_mcp_server(runtime)
    if not server.ok or server.value is None:
        return runtime.foundation.fail(server.issues)
    return runtime.foundation.ok(server.value.list_endpoints())


def _issues_summary(result: ServiceResult[Any]) -> str:
    if not result.issues:
        return "MCP HTTP server setup failed."
    return "; ".join(issue.message for issue in result.issues)


__all__ = [
    "create_mcp_http_app",
    "run_mcp_http_server",
]
