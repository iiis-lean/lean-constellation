from __future__ import annotations

import json
import socket

import pytest
from urllib.error import URLError

from lean_constellation.services.external_clients import LeanMcpToolkitClient, LeanMcpToolkitClientConfig
from lean_constellation.services.external_clients import lean_mcp_toolkit as toolkit_module


def test_search_mathlib_parses_json_string_payload_and_keeps_excerpt() -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lean_explore.find"
        return json.dumps(
            {
                "results": [
                    {
                        "name": "Nat.add_assoc",
                        "module": "Init",
                        "docstring": "Associativity of Nat.add",
                    }
                ]
            }
        )

    result = LeanMcpToolkitClient(dispatcher=dispatch).search_mathlib("Nat.add", limit=5)

    assert result.ok is True
    assert result.items == [
        {
            "name": "Nat.add_assoc",
            "module": "Init",
            "docstring": "Associativity of Nat.add",
            "source_tool": "lean_explore.find",
        }
    ]
    assert result.raw_excerpt
    assert result.warnings == []


def test_search_mathlib_reports_malformed_json_payload() -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lean_explore.find"
        return '{"results": ['

    result = LeanMcpToolkitClient(dispatcher=dispatch).search_mathlib("Nat.add", limit=5)

    assert result.ok is False
    assert result.issue_code == "toolkit_malformed_response"
    assert "not valid JSON" in result.summary
    assert result.raw_excerpt == '\'{"results": [\''


def test_toolkit_call_timeout_is_structured() -> None:
    def dispatch(tool_name: str, payload: dict):
        raise TimeoutError("slow backend")

    result = LeanMcpToolkitClient(dispatcher=dispatch).call_tool("lean_explore.find", {"query": "Nat"})

    assert result.ok is False
    assert result.issue_code == "toolkit_timeout"
    assert "slow backend" in result.summary


def test_toolkit_http_invalid_json_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return b"{bad json"

    monkeypatch.setattr(toolkit_module, "urlopen", lambda request, timeout: FakeResponse())
    client = LeanMcpToolkitClient(
        LeanMcpToolkitClientConfig(base_url="http://127.0.0.1:18080", api_prefix="/api/v1")
    )

    result = client.call_tool("lean_explore.find", {"query": "Nat"})
    catalog = client.probe_tool_catalog(["lean_explore.find"])

    assert result.ok is False
    assert result.issue_code == "toolkit_malformed_response"
    assert "{bad json" in (result.raw_excerpt or "")
    assert catalog.ok is False
    assert catalog.issue_code == "toolkit_malformed_response"


def test_toolkit_http_timeout_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request, timeout: int):
        raise URLError(socket.timeout("timed out"))

    monkeypatch.setattr(toolkit_module, "urlopen", fake_urlopen)
    client = LeanMcpToolkitClient(
        LeanMcpToolkitClientConfig(base_url="http://127.0.0.1:18080", api_prefix="/api/v1")
    )

    result = client.call_tool("lean_explore.find", {"query": "Nat"})
    catalog = client.probe_tool_catalog(["lean_explore.find"])

    assert result.ok is False
    assert result.issue_code == "toolkit_timeout"
    assert catalog.ok is False
    assert catalog.issue_code == "toolkit_timeout"


def test_search_mathlib_warns_on_missing_optional_candidate_fields() -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lean_explore.find"
        return {"results": [{"name": "Nat.add_assoc"}]}

    result = LeanMcpToolkitClient(dispatcher=dispatch).search_mathlib("Nat.add", limit=5)

    assert result.ok is True
    assert result.items[0]["name"] == "Nat.add_assoc"
    assert [warning.code for warning in result.warnings] == ["toolkit_candidate_missing_optional_field"]
    assert result.warnings[0].field == "module"
    assert result.warnings[0].item_index == 0
