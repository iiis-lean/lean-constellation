"""Runtime Matrix MCP transport helpers used by real Codex tests."""

from __future__ import annotations

import os
import socket
import threading
import time
from typing import Any
import urllib.request
import weakref

import anyio

from lean_constellation.mcp.http import create_mcp_http_app

MCP_TRANSPORTS = ("http", "stdio")
MCP_TRANSPORT_MODES = (*MCP_TRANSPORTS, "both")


class RuntimeMcpHttpTestServer:
    def __init__(self, *, base_url: str, server: Any, thread: threading.Thread) -> None:
        self.base_url = base_url
        self.server = server
        self.thread = thread

    def close(self) -> None:
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout=5)


def ensure_runtime_mcp_http_server(owner: object, runtime: object | None = None) -> RuntimeMcpHttpTestServer:
    """Start or reuse one background MCP HTTP server for a runtime test owner."""

    existing = getattr(owner, "_runtime_mcp_http_server", None)
    if isinstance(existing, RuntimeMcpHttpTestServer) and existing.thread.is_alive():
        return existing
    resolved_runtime = runtime if runtime is not None else getattr(owner, "runtime")
    server = start_runtime_mcp_http_server(resolved_runtime)
    setattr(owner, "_runtime_mcp_http_server", server)
    weakref.finalize(owner, server.close)
    return server


def requested_mcp_transport_mode(env: dict[str, str] | None = None, *, default: str = "http") -> str:
    source = os.environ if env is None else env
    raw = source.get(
        "LEAN_CONSTELLATION_REAL_CODEX_MCP_TRANSPORT",
        source.get("LEAN_CONSTELLATION_MCP_TRANSPORT", default),
    )
    mode = str(raw).strip().lower()
    if mode not in MCP_TRANSPORT_MODES:
        raise ValueError("MCP transport must be one of: http, stdio, both")
    return mode


def stdio_compare_enabled(env: dict[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get("LEAN_CONSTELLATION_RUN_MCP_STDIO_COMPARE") == "1"


def codex_force_full_access_enabled(env: dict[str, str] | None = None) -> bool:
    """Read the LC app-level Codex Home override used by real test helpers."""

    source = os.environ if env is None else env
    raw = str(source.get("LEAN_CONSTELLATION_CODEX_FORCE_FULL_ACCESS", "false"))
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(
        "LEAN_CONSTELLATION_CODEX_FORCE_FULL_ACCESS must be a boolean value"
    )


def selected_mcp_transports(
    env: dict[str, str] | None = None,
    *,
    default: str = "http",
    include_stdio_compare: bool = False,
) -> tuple[str, ...]:
    mode = requested_mcp_transport_mode(env, default=default)
    if mode in MCP_TRANSPORTS:
        return (mode,)
    if include_stdio_compare and stdio_compare_enabled(env):
        return ("http", "stdio")
    return ("http",)


def start_runtime_mcp_http_server(runtime: object) -> RuntimeMcpHttpTestServer:
    app_result = create_mcp_http_app(runtime)  # type: ignore[arg-type]
    assert app_result.ok and app_result.value is not None, app_result.issues
    port = _free_local_port()
    base_url = f"http://127.0.0.1:{port}"
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            app_result.value,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="on",
            ws="wsproto",
        )
    )
    thread = threading.Thread(target=lambda: anyio.run(server.serve), name=f"runtime-mcp-http-{port}", daemon=True)
    thread.start()
    _wait_for_http_health(f"{base_url}/health", server=server)
    return RuntimeMcpHttpTestServer(base_url=base_url, server=server, thread=thread)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http_health(url: str, *, server: Any, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if getattr(server, "should_exit", False):
            raise RuntimeError("MCP HTTP server exited before becoming ready")
        try:
            with urllib.request.urlopen(url, timeout=0.5) as response:  # noqa: S310 - local test server only.
                if response.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - readiness polling.
            last_error = exc
        time.sleep(0.05)
    raise RuntimeError(f"MCP HTTP server did not become ready at {url}: {last_error}")


__all__ = [
    "MCP_TRANSPORT_MODES",
    "MCP_TRANSPORTS",
    "RuntimeMcpHttpTestServer",
    "codex_force_full_access_enabled",
    "ensure_runtime_mcp_http_server",
    "requested_mcp_transport_mode",
    "selected_mcp_transports",
    "start_runtime_mcp_http_server",
    "stdio_compare_enabled",
]
