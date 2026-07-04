from __future__ import annotations

from pathlib import Path

import pytest

from lean_constellation.app.config import LeanToolkitAppConfig
from lean_constellation.app.toolkit_process import ManagedToolkitProcess


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


def test_managed_toolkit_process_strict_startup_failure_stops_child() -> None:
    config = LeanToolkitAppConfig(
        mode="managed",
        python_executable=Path("/bin/false"),
        startup_timeout_s=0.2,
        health_interval_s=0.01,
    )
    process = ManagedToolkitProcess(config)

    with pytest.raises(RuntimeError, match="did not become healthy"):
        process.start()

    assert process.process is not None
    assert process.process.poll() is not None
