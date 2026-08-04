from __future__ import annotations

import pytest

from tests.real.runtime_matrix.transport import (
    codex_force_full_access_enabled,
    requested_mcp_transport_mode,
    selected_mcp_transports,
    stdio_compare_enabled,
)


def test_requested_mcp_transport_mode_defaults_to_http() -> None:
    assert requested_mcp_transport_mode({}) == "http"
    assert selected_mcp_transports({}) == ("http",)


def test_requested_mcp_transport_mode_supports_stdio_and_both() -> None:
    assert requested_mcp_transport_mode({"LEAN_CONSTELLATION_MCP_TRANSPORT": "stdio"}) == "stdio"
    assert selected_mcp_transports({"LEAN_CONSTELLATION_MCP_TRANSPORT": "stdio"}) == ("stdio",)
    assert requested_mcp_transport_mode({"LEAN_CONSTELLATION_MCP_TRANSPORT": "both"}) == "both"
    assert selected_mcp_transports({"LEAN_CONSTELLATION_MCP_TRANSPORT": "both"}) == ("http",)
    assert selected_mcp_transports(
        {
            "LEAN_CONSTELLATION_MCP_TRANSPORT": "both",
            "LEAN_CONSTELLATION_RUN_MCP_STDIO_COMPARE": "1",
        },
        include_stdio_compare=True,
    ) == ("http", "stdio")


def test_real_codex_transport_env_takes_precedence() -> None:
    assert (
        selected_mcp_transports(
            {
                "LEAN_CONSTELLATION_MCP_TRANSPORT": "http",
                "LEAN_CONSTELLATION_REAL_CODEX_MCP_TRANSPORT": "stdio",
            }
        )
        == ("stdio",)
    )
    assert stdio_compare_enabled({"LEAN_CONSTELLATION_RUN_MCP_STDIO_COMPARE": "1"}) is True


def test_requested_mcp_transport_mode_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        requested_mcp_transport_mode({"LEAN_CONSTELLATION_MCP_TRANSPORT": "pipe"})


def test_codex_force_full_access_env_matches_app_boolean_forms() -> None:
    assert codex_force_full_access_enabled({}) is False
    assert codex_force_full_access_enabled(
        {"LEAN_CONSTELLATION_CODEX_FORCE_FULL_ACCESS": "1"}
    ) is True
    assert codex_force_full_access_enabled(
        {"LEAN_CONSTELLATION_CODEX_FORCE_FULL_ACCESS": "true"}
    ) is True
    assert codex_force_full_access_enabled(
        {"LEAN_CONSTELLATION_CODEX_FORCE_FULL_ACCESS": "off"}
    ) is False
    with pytest.raises(ValueError):
        codex_force_full_access_enabled(
            {"LEAN_CONSTELLATION_CODEX_FORCE_FULL_ACCESS": "sometimes"}
        )
