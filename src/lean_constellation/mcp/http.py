"""Streamable HTTP MCP server for Lean Constellation ToolView endpoints."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

import anyio
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from lean_constellation.mcp.server import create_mcp_server
from lean_constellation.mcp.stdio import create_mcp_protocol_server
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices

if TYPE_CHECKING:
    from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry


@dataclass
class _RunningManager:
    manager: StreamableHTTPSessionManager
    stop_event: anyio.Event
    started_event: anyio.Event
    stopped_event: anyio.Event
    error: BaseException | None = None


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


class RepoMcpHttpRouter:
    """Dynamic ASGI router for repo-prefixed MCP HTTP endpoints."""

    def __init__(
        self,
        registry: RepoRuntimeRegistry,
        *,
        view_keys: Iterable[str] | None = None,
    ) -> None:
        self.registry = registry
        self.view_keys = sorted({str(view_key) for view_key in view_keys}) if view_keys is not None else None
        self._managers: dict[tuple[str, str], StreamableHTTPSessionManager] = {}
        self._manager_runners: dict[tuple[str, str], _RunningManager] = {}
        self._task_group_cm: Any | None = None
        self._task_group: anyio.abc.TaskGroup | None = None

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001 - ASGI boundary.
        if scope["type"] != "http":
            response = JSONResponse({"ok": False, "error": "Unsupported ASGI scope type."}, status_code=404)
            await response(scope, receive, send)
            return
        parsed = self._parse_path(scope.get("path", ""))
        if parsed is None:
            response = JSONResponse({"ok": False, "error": "Unknown repo MCP route."}, status_code=404)
            await response(scope, receive, send)
            return
        repo_key, view_key, is_index = parsed
        if is_index:
            await self._send_view_index(repo_key, scope, receive, send)
            return
        if view_key is None:
            response = JSONResponse({"ok": False, "error": "MCP view key is required."}, status_code=404)
            await response(scope, receive, send)
            return
        manager_result = await self._get_manager(repo_key, view_key)
        if not manager_result.ok or manager_result.value is None:
            response = JSONResponse(manager_result.model_dump(mode="json"), status_code=400)
            await response(scope, receive, send)
            return
        manager_scope = dict(scope)
        manager_scope["path"] = "/"
        manager_scope["raw_path"] = b"/"
        headers = list(manager_scope.get("headers") or [])
        record = self.registry.discover_repo(repo_key)
        if record.ok and record.value is not None:
            headers.append((b"x-lean-constellation-expected-repo-key", record.value.repo_key.encode()))
            headers.append((b"x-lean-constellation-expected-repo-root", str(record.value.repo_root).encode()))
        manager_scope["headers"] = headers
        await manager_result.value.handle_request(manager_scope, receive, send)

    async def shutdown(self) -> None:
        for key in list(self._manager_runners):
            await self._stop_manager(key)
        if self._task_group is not None:
            self._task_group.cancel_scope.cancel()
        if self._task_group_cm is not None:
            await self._task_group_cm.__aexit__(None, None, None)
        self._task_group = None
        self._task_group_cm = None

    async def cleanup_repo(self, repo_key: str) -> None:
        normalized = self.registry.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            return
        prefix = normalized.value
        keys = [key for key in self._managers if key[0] == prefix]
        for key in keys:
            await self._stop_manager(key)

    async def _send_view_index(self, repo_key: str, scope, receive, send) -> None:  # noqa: ANN001
        loaded = self.registry.get_or_load(repo_key, refresh_homes=False)
        if not loaded.ok or loaded.value is None:
            response = JSONResponse(loaded.model_dump(mode="json"), status_code=400)
            await response(scope, receive, send)
            return
        resolved = _resolve_view_keys(loaded.value, self.view_keys)
        if not resolved.ok or resolved.value is None:
            response = JSONResponse(resolved.model_dump(mode="json"), status_code=400)
            await response(scope, receive, send)
            return
        response = JSONResponse({"ok": True, "repo_key": repo_key, "views": resolved.value})
        await response(scope, receive, send)

    async def _get_manager(self, repo_key: str, view_key: str) -> ServiceResult[StreamableHTTPSessionManager]:
        normalized = self.registry.normalize_repo_key(repo_key)
        if not normalized.ok or normalized.value is None:
            return self.registry.result.fail(normalized.issues)
        repo_key = normalized.value
        if self.view_keys is not None and view_key not in self.view_keys:
            return self.registry.result.fail(
                self.registry.result.issue(
                    "mcp_view_not_allowed",
                    f"MCP view {view_key!r} is not exposed by this server.",
                    object_ref=view_key,
                )
            )
        cache_key = (repo_key, view_key)
        existing = self._managers.get(cache_key)
        if existing is not None:
            return self.registry.result.ok(existing)
        loaded = self.registry.get_or_load(repo_key, refresh_homes=False)
        if not loaded.ok or loaded.value is None:
            return self.registry.result.fail(loaded.issues)
        protocol = create_mcp_protocol_server(loaded.value, view_key=view_key)
        if not protocol.ok or protocol.value is None:
            return loaded.value.foundation.fail(protocol.issues)
        manager = StreamableHTTPSessionManager(
            protocol.value,
            json_response=True,
            stateless=True,
        )
        runner = await self._start_manager(manager)
        if runner.error is not None:
            return self.registry.result.fail(
                self.registry.result.issue(
                    "mcp_manager_start_failed",
                    f"Failed to start MCP manager: {runner.error}",
                    object_ref=f"{repo_key}:{view_key}",
                )
            )
        self._manager_runners[cache_key] = runner
        self._managers[cache_key] = manager
        return self.registry.result.ok(manager)

    async def _ensure_task_group(self) -> anyio.abc.TaskGroup:
        if self._task_group is None:
            self._task_group_cm = anyio.create_task_group()
            self._task_group = await self._task_group_cm.__aenter__()
        return self._task_group

    async def _start_manager(self, manager: StreamableHTTPSessionManager) -> _RunningManager:
        task_group = await self._ensure_task_group()
        runner = _RunningManager(
            manager=manager,
            stop_event=anyio.Event(),
            started_event=anyio.Event(),
            stopped_event=anyio.Event(),
        )
        task_group.start_soon(self._run_manager, runner)
        await runner.started_event.wait()
        return runner

    async def _run_manager(self, runner: _RunningManager) -> None:
        try:
            async with runner.manager.run():
                runner.started_event.set()
                await runner.stop_event.wait()
        except BaseException as exc:  # noqa: BLE001 - manager lifecycle boundary.
            runner.error = exc
            runner.started_event.set()
        finally:
            runner.stopped_event.set()

    async def _stop_manager(self, key: tuple[str, str]) -> None:
        self._managers.pop(key, None)
        runner = self._manager_runners.pop(key, None)
        if runner is None:
            return
        runner.stop_event.set()
        await runner.stopped_event.wait()

    @staticmethod
    def _parse_path(path: str) -> tuple[str, str | None, bool] | None:
        parts = [unquote(part) for part in path.strip("/").split("/") if part]
        if len(parts) == 3 and parts[1:] == ["mcp", "views"]:
            return parts[0], None, True
        if len(parts) >= 4 and parts[1:3] == ["mcp", "views"]:
            return parts[0], parts[3], False
        if len(parts) >= 4 and parts[0] == "repos" and parts[2:4] == ["mcp", "views"]:
            return parts[1], parts[4] if len(parts) > 4 else None, len(parts) == 4
        return None


def create_repo_mcp_http_routes(
    registry: RepoRuntimeRegistry,
    *,
    view_keys: Iterable[str] | None = None,
) -> tuple[RepoMcpHttpRouter, list[Any]]:
    """Create repo-prefixed MCP HTTP routes for a workspace server."""

    router = RepoMcpHttpRouter(registry, view_keys=view_keys)
    routes = [
        Mount("/repos", app=router),
    ]
    return router, routes


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

    config = uvicorn.Config(app.value, host=host, port=port, log_level=log_level, ws="wsproto")
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
    "create_repo_mcp_http_routes",
    "RepoMcpHttpRouter",
    "run_mcp_http_server",
]
