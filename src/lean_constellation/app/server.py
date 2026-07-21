"""Unified production app server for Admin HTTP and MCP HTTP."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import asynccontextmanager
import faulthandler
from functools import partial
import logging
import os
from pathlib import Path
import shutil
import uuid

import anyio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from lean_constellation.app.admin_http import create_workspace_admin_http_routes
from lean_constellation.app.config import LeanAppConfig
from lean_constellation.app.operator_data.api import OperatorDataApi
from lean_constellation.app.operator_data.http import create_operator_data_http_routes
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry
from lean_constellation.app.scheduler_loop import run_registry_scheduler_loop
from lean_constellation.app.toolkit_process import ManagedToolkitProcess
from lean_constellation.mcp.http import create_repo_mcp_http_routes
from lean_constellation.services.foundation import ServiceResult


logger = logging.getLogger(__name__)


def create_production_app_server(
    config: LeanAppConfig,
    *,
    view_keys: Iterable[str] | None = None,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    materialize_agent_homes: bool | None = None,
    toolkit_state: object | None = None,
) -> ServiceResult[Starlette]:
    """Create one ASGI app containing Admin HTTP, MCP HTTP, and scheduler loop."""

    return _create_registry_production_app_server(
        config,
        view_keys=view_keys,
        external_config=external_config,
        external_overrides=external_overrides,
        materialize_agent_homes=materialize_agent_homes,
        toolkit_state=toolkit_state,
    )


def _create_registry_production_app_server(
    config: LeanAppConfig,
    *,
    view_keys: Iterable[str] | None = None,
    external_config: object | None = None,
    external_overrides: dict[str, object] | None = None,
    materialize_agent_homes: bool | None = None,
    toolkit_state: object | None = None,
) -> ServiceResult[Starlette]:
    """Create the production workspace app with repo-local runtime registry."""

    if materialize_agent_homes is not None:
        config = config.model_copy(update={"materialize_agent_homes": materialize_agent_homes})
    registry = RepoRuntimeRegistry(
        config,
        external_config=external_config,
        external_overrides=external_overrides,
    )
    resolved_mcp_base_url = config.production_mcp_http_effective_base_url()
    process_instance_id = f"lc_{uuid.uuid4().hex}"
    scheduler_state: dict[str, object] = {"running": False}
    repo_mcp_router, repo_mcp_routes = create_repo_mcp_http_routes(registry, view_keys=view_keys)
    operator_data_api = OperatorDataApi(registry) if config.operator_data_api_enabled else None

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
                "process_instance_id": process_instance_id,
                "process": _process_telemetry(config.workspace_root),
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
        *(
            create_operator_data_http_routes(registry, api=operator_data_api)
            if operator_data_api is not None
            else []
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
        app.state.lean_constellation_operator_data_api = operator_data_api
        app.state.lean_constellation_process_instance_id = process_instance_id
        logger.info(
            "Lean Constellation server startup instance=%s telemetry=%s",
            process_instance_id,
            _process_telemetry(config.workspace_root),
        )
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
                logger.info(
                    "Lean Constellation server shutdown instance=%s telemetry=%s",
                    process_instance_id,
                    _process_telemetry(config.workspace_root),
                )

    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.lean_constellation_registry = registry
    app.state.lean_constellation_config = config
    app.state.lean_constellation_scheduler = scheduler_state
    app.state.lean_constellation_mcp_base_url = resolved_mcp_base_url
    app.state.lean_constellation_mcp_router = repo_mcp_router
    app.state.lean_constellation_toolkit = toolkit_state
    app.state.lean_constellation_operator_data_api = operator_data_api
    app.state.lean_constellation_process_instance_id = process_instance_id
    return registry.result.ok(app)


async def run_production_app_server(
    config: LeanAppConfig,
    *,
    view_keys: Iterable[str] | None = None,
    log_level: str = "info",
) -> None:
    """Run the unified production app server until uvicorn exits."""

    toolkit_process: ManagedToolkitProcess | None = None
    toolkit_state: object | None = None
    if not faulthandler.is_enabled():
        faulthandler.enable()
    if config.toolkit.mode == "managed":
        toolkit_process = ManagedToolkitProcess(config.toolkit)
        toolkit_process.start()
        toolkit_state = toolkit_process
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


def _process_telemetry(workspace_root: Path) -> dict[str, object]:
    disk = shutil.disk_usage(Path(workspace_root).expanduser().resolve().parent)
    return {
        "pid": os.getpid(),
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "cgroup_memory_current": _read_optional_int(Path("/sys/fs/cgroup/memory.current")),
        "cgroup_memory_max": _read_optional_int(Path("/sys/fs/cgroup/memory.max")),
        "cgroup_memory_events": _read_optional_text(Path("/sys/fs/cgroup/memory.events")),
    }


def _read_optional_int(path: Path) -> int | str | None:
    value = _read_optional_text(path)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _read_optional_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


__all__ = [
    "create_production_app_server",
    "run_production_app_server",
]
