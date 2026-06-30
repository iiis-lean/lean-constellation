from __future__ import annotations

from lean_constellation.app.cli import build_parser, main


def test_cli_help_mentions_admin_commands() -> None:
    help_text = build_parser().format_help()

    assert "Lean Constellation admin CLI" in help_text
    assert "config-view" in help_text
    assert "start-flow" in help_text
    assert "snapshot" in help_text


def test_cli_config_view_prints_redacted_config(tmp_path, capsys) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(f'workspace_root = "{tmp_path / "workspace"}"\n', encoding="utf-8")

    exit_code = main(["--config", str(config_path), "config-view"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "workspace" in output
    assert "secret-token" not in output
