from __future__ import annotations

import io
import json
import socket
import tarfile
from pathlib import Path

import pytest
from urllib.error import URLError

from lean_constellation.services.external_clients import (
    LeanMcpToolkitClient,
    LeanMcpToolkitClientConfig,
    ToolkitDeclarationSoundnessTarget,
)
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


def test_toolkit_success_false_response_is_structured_failure() -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "search_arxiv_theorems"
        return {
            "success": False,
            "error_message": "HTTP request failed: http 500: Internal Server Error",
            "provider": "arxiv_theorems",
            "items": [],
        }

    result = LeanMcpToolkitClient(dispatcher=dispatch).call_tool("search_arxiv_theorems", {"query": "Nat"})

    assert result.ok is False
    assert result.issue_code == "arxiv_theorems_failed"
    assert "http 500" in result.summary
    assert isinstance(result.value, dict)
    assert result.value["success"] is False


def test_search_arxiv_theorems_falls_back_to_real_eprint_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "search_arxiv_theorems"
        return {
            "success": False,
            "error_message": "HTTP request failed: http 500: Internal Server Error",
            "provider": "arxiv_theorems",
            "items": [],
        }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return _arxiv_source_tarball(
                r"""
\title{Runtime Matrix Source}
\begin{theorem}
Every strict runtime matrix fallback test has a real TeX theorem body.
\end{theorem}
"""
            )

    requested_urls: list[str] = []

    def fake_urlopen(request, timeout: int):
        requested_urls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(toolkit_module, "urlopen", fake_urlopen)
    client = LeanMcpToolkitClient(
        LeanMcpToolkitClientConfig(base_url="http://127.0.0.1:18080", api_prefix="/api/v1"),
        dispatcher=dispatch,
    )

    result = client.search_arxiv_theorems("math/0001001", limit=1)

    assert result.ok is True
    assert requested_urls == ["https://arxiv.org/e-print/math/0001001"]
    assert result.items[0]["arxiv_id"] == "math/0001001"
    assert result.items[0]["source_tool"] == "arxiv_eprint_fallback"
    assert "real TeX theorem body" in result.items[0]["theorem"]
    assert {warning.code for warning in result.warnings} >= {"arxiv_theorems_failed", "arxiv_eprint_fallback"}


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


def _arxiv_source_tarball(source: str) -> bytes:
    payload = source.encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("main.tex")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


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


def test_declaration_soundness_batch_preserves_partial_typed_evidence() -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lsp.declaration_soundness_batch"
        assert payload["declarations"] == [
            {
                "module": "Upstream.Basic",
                "declaration_name": "Upstream.clean",
                "source_file_path": None,
            },
            {
                "module": "Upstream.Basic",
                "declaration_name": "Upstream.pending",
                "source_file_path": None,
            },
        ]
        return {
            "success": False,
            "error_message": "one item failed",
            "items": [
                {
                    "module": "Upstream.Basic",
                    "declaration_name": "Upstream.clean",
                    "success": True,
                    "source_file_path": None,
                    "error_message": None,
                    "axioms": ["propext"],
                    "warnings": [],
                    "axiom_count": 1,
                    "warning_count": 0,
                },
                {
                    "module": "Upstream.Basic",
                    "declaration_name": "Upstream.pending",
                    "success": False,
                    "source_file_path": None,
                    "error_message": "report not found",
                    "axioms": [],
                    "warnings": [],
                    "axiom_count": 0,
                    "warning_count": 0,
                },
            ],
            "count": 2,
            "success_count": 1,
            "failure_count": 1,
        }

    targets = [
        ToolkitDeclarationSoundnessTarget(
            module="Upstream.Basic",
            declaration_name="Upstream.clean",
        ),
        ToolkitDeclarationSoundnessTarget(
            module="Upstream.Basic",
            declaration_name="Upstream.pending",
        ),
    ]
    result = LeanMcpToolkitClient(dispatcher=dispatch).check_declaration_soundness_batch(
        Path("/repo"),
        targets,
    )

    assert result.protocol_ok is True
    assert result.batch_success is False
    assert result.success_count == 1
    assert result.items[0].axioms == ["propext"]
    assert result.items[1].error_message == "report not found"


@pytest.mark.parametrize(
    "mutation",
    [
        {"module": "Wrong.Module"},
        {"declaration_name": "Wrong.name"},
        {"axiom_count": 2},
    ],
)
def test_declaration_soundness_batch_fails_closed_on_malformed_item(
    mutation: dict[str, object],
) -> None:
    def dispatch(tool_name: str, payload: dict):
        assert tool_name == "lsp.declaration_soundness_batch"
        item = {
            "module": "Upstream.Basic",
            "declaration_name": "Upstream.clean",
            "success": True,
            "source_file_path": None,
            "error_message": None,
            "axioms": [],
            "warnings": [],
            "axiom_count": 0,
            "warning_count": 0,
        }
        item.update(mutation)
        return {
            "success": True,
            "error_message": None,
            "items": [item],
            "count": 1,
            "success_count": 1,
            "failure_count": 0,
        }

    result = LeanMcpToolkitClient(dispatcher=dispatch).check_declaration_soundness(
        Path("/repo"),
        ToolkitDeclarationSoundnessTarget(
            module="Upstream.Basic",
            declaration_name="Upstream.clean",
        ),
    )

    assert result.protocol_ok is False
    assert result.issue_code == "declaration_soundness_invalid_response"
