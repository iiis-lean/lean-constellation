from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from lean_constellation.app.config import LeanToolkitAppConfig
from lean_constellation.app.toolkit_process import ManagedToolkitProcess


class _SequencedProcess:
    def __init__(self, poll_results: list[int | None]) -> None:
        self.pid = 43210
        self._poll_results = iter(poll_results)
        self._last_result: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        try:
            self._last_result = next(self._poll_results)
        except StopIteration:
            pass
        return self._last_result

    def terminate(self) -> None:
        self.terminated = True
        self._last_result = 1

    def wait(self, *, timeout: float) -> int:
        del timeout
        return self._last_result or 0

    def kill(self) -> None:
        self._last_result = -9


def test_managed_toolkit_process_command_uses_configured_cli_args(tmp_path: Path) -> None:
    config = LeanToolkitAppConfig(
        mode="managed",
        host="127.0.0.2",
        port=19001,
        config_path=tmp_path / "toolkit.json",
        project_root=tmp_path / "project",
        python_executable=Path("/tmp/python"),
        enabled_groups=["lean", "mathlib"],
    )

    command = ManagedToolkitProcess(config).command()

    assert command[:4] == ["/tmp/python", "-m", "lean_mcp_toolkit.app.cli", "serve"]
    assert command[command.index("--mode") + 1] == "unified"
    assert command[command.index("--host") + 1] == "127.0.0.2"
    assert command[command.index("--port") + 1] == "19001"
    assert command[command.index("--config") + 1] == str(tmp_path / "toolkit.json")
    assert command[command.index("--project-root") + 1] == str(tmp_path / "project")
    assert command.count("--enable-group") == 2


def test_managed_toolkit_process_external_mode_does_not_spawn_child() -> None:
    config = LeanToolkitAppConfig(mode="external", base_url="http://127.0.0.1:19002")
    process = ManagedToolkitProcess(config)

    view = process.start()

    assert process.process is None
    assert view.mode == "external"
    assert view.base_url == "http://127.0.0.1:19002"
    assert view.running is False
    assert view.health_ok is False


def test_managed_toolkit_process_strict_startup_failure_stops_child(monkeypatch: pytest.MonkeyPatch) -> None:
    config = LeanToolkitAppConfig(
        mode="managed",
        python_executable=Path("/bin/false"),
        startup_timeout_s=0.2,
        health_interval_s=0.01,
    )
    process = ManagedToolkitProcess(config)
    monkeypatch.setattr(process, "_endpoint_in_use", lambda: False)

    with pytest.raises(RuntimeError, match="did not become healthy"):
        process.start()

    assert process.process is not None
    assert process.process.poll() is not None


def test_managed_toolkit_process_rejects_existing_listener_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = LeanToolkitAppConfig(mode="managed", strict_startup=True)
    process = ManagedToolkitProcess(config)
    monkeypatch.setattr(process, "_endpoint_in_use", lambda: True)
    monkeypatch.setattr(
        "lean_constellation.app.toolkit_process.subprocess.Popen",
        lambda _command: pytest.fail("managed child must not spawn when its endpoint is already occupied"),
    )

    with pytest.raises(RuntimeError, match="endpoint is already in use"):
        process.start()

    assert process.process is None
    assert process.view is not None
    assert process.view.running is False
    assert process.view.health_ok is False


def test_managed_toolkit_process_rejects_external_health_when_child_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = LeanToolkitAppConfig(
        mode="managed",
        startup_timeout_s=0.2,
        health_interval_s=0.01,
        required_tools=["diagnostics.file"],
    )
    child = _SequencedProcess([None, None, 1])
    client = SimpleNamespace(
        probe_tool_catalog=lambda _required: SimpleNamespace(ok=True),
    )
    monkeypatch.setattr("lean_constellation.app.toolkit_process.subprocess.Popen", lambda _command: child)
    monkeypatch.setattr(ManagedToolkitProcess, "_endpoint_in_use", lambda _self: False)
    monkeypatch.setattr(
        "lean_constellation.app.toolkit_process.LeanMcpToolkitClient.from_config",
        lambda _config: client,
    )
    process = ManagedToolkitProcess(config)

    with pytest.raises(RuntimeError, match="did not become healthy"):
        process.start()

    assert child.poll() == 1


def test_managed_toolkit_process_admin_view_refreshes_exited_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = LeanToolkitAppConfig(
        mode="managed",
        startup_timeout_s=0.2,
        health_interval_s=0.01,
        required_tools=["diagnostics.file"],
    )
    child = _SequencedProcess([None, None, None, None, None, 1])
    client = SimpleNamespace(
        probe_tool_catalog=lambda _required: SimpleNamespace(ok=True),
    )
    monkeypatch.setattr("lean_constellation.app.toolkit_process.subprocess.Popen", lambda _command: child)
    monkeypatch.setattr(ManagedToolkitProcess, "_endpoint_in_use", lambda _self: False)
    monkeypatch.setattr(
        "lean_constellation.app.toolkit_process.LeanMcpToolkitClient.from_config",
        lambda _config: client,
    )
    process = ManagedToolkitProcess(config)

    started = process.start()
    refreshed = process.model_dump(mode="json")

    assert started.running is True
    assert started.health_ok is True
    assert refreshed["running"] is False
    assert refreshed["health_ok"] is False
    assert refreshed["summary"] == "Managed Toolkit child process is not running."
