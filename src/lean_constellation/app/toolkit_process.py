"""Managed Lean MCP Toolkit process helpers for production server startup."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from pydantic import Field

from lean_constellation.app.config import LeanToolkitAppConfig
from lean_constellation.domain.common import StrictModel
from lean_constellation.services.external_clients import LeanMcpToolkitClient, LeanMcpToolkitClientConfig


class ManagedToolkitView(StrictModel):
    mode: str
    base_url: str | None = None
    pid: int | None = None
    running: bool = False
    health_ok: bool = False
    missing_tools: list[str] = Field(default_factory=list)
    command: list[str] = Field(default_factory=list)
    summary: str


class ManagedToolkitProcess:
    """Small lifecycle wrapper around the Lean MCP Toolkit CLI server."""

    def __init__(self, config: LeanToolkitAppConfig) -> None:
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self.view: ManagedToolkitView | None = None

    def command(self) -> list[str]:
        python = str(self.config.python_executable or Path(sys.executable))
        command = [
            python,
            "-m",
            self.config.module,
            "serve",
            "--mode",
            "unified",
            "--host",
            self.config.host,
            "--port",
            str(self.config.port),
        ]
        if self.config.config_path is not None:
            command.extend(["--config", str(self.config.config_path)])
        if self.config.project_root is not None:
            command.extend(["--project-root", str(self.config.project_root)])
        for group in self.config.enabled_groups:
            command.extend(["--enable-group", group])
        return command

    def start(self) -> ManagedToolkitView:
        if self.config.mode != "managed":
            view = ManagedToolkitView(
                mode=self.config.mode,
                base_url=self.config.effective_base_url(),
                running=False,
                summary="Toolkit process is not managed by Lean Constellation.",
            )
            self.view = view
            return view
        command = self.command()
        self.process = subprocess.Popen(command)  # noqa: S603 - command is explicit local toolkit launcher.
        base_url = self.config.effective_base_url()
        health_ok, missing_tools = self._wait_ready(base_url)
        running = self.process.poll() is None
        view = ManagedToolkitView(
            mode="managed",
            base_url=base_url,
            pid=self.process.pid,
            running=running,
            health_ok=health_ok,
            missing_tools=missing_tools,
            command=command,
            summary="Managed Toolkit process is healthy." if health_ok else "Managed Toolkit process did not become healthy.",
        )
        self.view = view
        if self.config.strict_startup and not health_ok:
            self.stop()
            raise RuntimeError(view.summary)
        return view

    def stop(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=self.config.shutdown_timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=self.config.shutdown_timeout_s)

    def _wait_ready(self, base_url: str | None) -> tuple[bool, list[str]]:
        if not base_url:
            return False, list(self.config.required_tools)
        client = LeanMcpToolkitClient.from_config(
            LeanMcpToolkitClientConfig(
                base_url=base_url,
                api_prefix=self.config.api_prefix,
                auth_token=self.config.auth_token,
                timeout_seconds=self.config.timeout_seconds,
                enabled_groups=self.config.enabled_groups,
                response_excerpt_chars=self.config.response_excerpt_chars,
            )
        )
        deadline = time.monotonic() + self.config.startup_timeout_s
        missing_tools = list(self.config.required_tools)
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                return False, missing_tools
            catalog = client.probe_tool_catalog(self.config.required_tools)
            if catalog.ok:
                self._run_warmups(client)
                return True, []
            if catalog.issue_code == "toolkit_required_tools_missing":
                return False, list(catalog.missing_tools)
            health = client.call_tool("health", {})
            if health.ok and not self.config.required_tools:
                self._run_warmups(client)
                return True, []
            time.sleep(self.config.health_interval_s)
        return False, missing_tools

    def _run_warmups(self, client: LeanMcpToolkitClient) -> None:
        for tool_name in self.config.warmup_tools:
            client.call_tool(tool_name, {})
