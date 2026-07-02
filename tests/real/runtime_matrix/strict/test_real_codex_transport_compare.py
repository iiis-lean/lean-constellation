"""Env-gated real Codex transport comparison smoke tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from time import monotonic
from typing import Callable

import pytest

from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.transport import requested_mcp_transport_mode, stdio_compare_enabled
from tests.real.runtime_matrix.strict.test_real_codex_agent_resource_matrix import (
    test_strict_real_codex_coordinator_resources_tools_and_submit as _run_coordinator_case,
    test_strict_real_codex_mathlib_recon_resources_tools_and_submit as _run_mathlib_recon_case,
    test_strict_real_codex_statement_formal_worker_resources_tools_and_submit as _run_statement_formal_case,
)


pytestmark = [pytest.mark.real, pytest.mark.slow, pytest.mark.real_codex, pytest.mark.transport_compare]


_CASE_RUNNERS: dict[str, Callable[[Path, EvidenceRecorder], None]] = {
    "coordinator": _run_coordinator_case,
    "statement_formal_worker": _run_statement_formal_case,
    "mathlib_recon": _run_mathlib_recon_case,
}


def _compare_transports() -> tuple[str, ...]:
    mode = requested_mcp_transport_mode()
    if mode == "both":
        return ("http", "stdio")
    return (mode,)


@pytest.mark.parametrize("case_name", ["coordinator", "statement_formal_worker", "mathlib_recon"])
@pytest.mark.parametrize("transport", _compare_transports())
def test_real_codex_transport_compare_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    transport: str,
) -> None:
    if transport == "stdio" and not stdio_compare_enabled():
        pytest.skip("Set LEAN_CONSTELLATION_RUN_MCP_STDIO_COMPARE=1 to run stdio transport compare branch.")
    monkeypatch.setenv("LEAN_CONSTELLATION_REAL_CODEX_MCP_TRANSPORT", transport)
    recorder = EvidenceRecorder()
    started = monotonic()

    _CASE_RUNNERS[case_name](tmp_path, recorder)

    elapsed_s = monotonic() - started
    _write_transport_compare_artifact(
        tmp_path,
        case_name=case_name,
        transport=transport,
        elapsed_s=elapsed_s,
        recorder=recorder,
    )

def _write_transport_compare_artifact(
    tmp_path: Path,
    *,
    case_name: str,
    transport: str,
    elapsed_s: float,
    recorder: EvidenceRecorder,
) -> Path:
    artifacts = []
    tool_calls = []
    for codex_artifact in recorder.evidence.codex_artifacts:
        artifact = {
            "agent_type": codex_artifact.agent_type,
            "step_id": codex_artifact.step_id,
            "artifact_path": codex_artifact.artifact_path,
            "transcript_path": codex_artifact.transcript_path,
            "mcp_transport": codex_artifact.mcp_transport,
            "mcp_server_urls": list(codex_artifact.mcp_server_urls),
        }
        artifacts.append(artifact)
        if codex_artifact.transcript_path:
            transcript_path = Path(codex_artifact.transcript_path)
            if transcript_path.exists():
                transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
                for call in transcript.get("tool_calls", []):
                    if isinstance(call, dict):
                        tool_calls.append(
                            {
                                "tool_name": call.get("tool_name") or call.get("name"),
                                "duration_ms": call.get("duration_ms"),
                                "elapsed_ms": call.get("elapsed_ms"),
                                "ok": call.get("ok"),
                            }
                        )
    payload = {
        "case_name": case_name,
        "transport": transport,
        "elapsed_s": elapsed_s,
        "codex_artifacts": artifacts,
        "tool_calls": tool_calls,
    }
    artifact_dir = _transport_artifact_dir(tmp_path)
    path = artifact_dir / f"{case_name}_{transport}_transport_compare.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _transport_artifact_dir(tmp_path: Path) -> Path:
    configured = os.environ.get("LEAN_CONSTELLATION_TRANSPORT_COMPARE_ARTIFACT_DIR")
    root = Path(configured).expanduser() if configured else tmp_path / "transport_compare"
    root.mkdir(parents=True, exist_ok=True)
    return root
