from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.app import AdminStepStartInput, SetAgentStepOverrideInput
from lean_constellation.flows.testing import ControlledAgentOverrideSpec
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import LakeCommandClient, LakeCommandClientConfig
from tests.real.runtime_matrix.admin_helpers import run_next_created_step, run_until_step_created, unwrap
from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.fixtures import create_runtime_matrix_workspace
from tests.real.runtime_matrix.strict.real_codex_helpers import (
    materialize_strict_codex_home,
    require_real_codex,
    strict_controlled_agent_specs,
    write_noninteractive_codex_base_config,
)
from tests.real.runtime_matrix.strict.test_real_codex_agent_resource_matrix import (
    _assert_decl_stage_step,
    _complete_statement_nl_stage_for_real_codex,
    _mcp_transport_info_from_artifact,
    _read_artifact,
    _record_real_codex_artifact,
    _require_lake_and_lean,
    _start_decl_round,
)


pytestmark = [pytest.mark.real, pytest.mark.slow, pytest.mark.real_codex, pytest.mark.lean_latency]

_TARGET_APPLICATION_TOOLS = (
    "prepare_statement_formal_file",
    "run_lean_file_diagnostics",
    "check_statement_formal_policy",
)


def test_strict_real_codex_statement_formal_lean_tool_latency_probe(
    tmp_path: Path,
    evidence_recorder: EvidenceRecorder,
) -> None:
    _require_lake_and_lean()
    config_home = require_real_codex()
    base_config_path = write_noninteractive_codex_base_config(config_home, tmp_path)
    agent_type = "StatementFormalWorkerControlledTestAgent"
    agent_specs = strict_controlled_agent_specs("StatementFormalWorkerAgent")
    ws = create_runtime_matrix_workspace(
        tmp_path,
        lake_client=LakeCommandClient(LakeCommandClientConfig(timeout_seconds=120)),
        include_codex_provider=True,
    )
    initial_build = ws.lake.run_lake_build(ws.provider_repo, timeout_seconds=120)
    assert initial_build.ok, initial_build
    round_fixture = ws.create_decl_round(end_after_state=DeclState.PROVED)
    ws.create_home("StatementNLWorkerControlledTestAgent")
    ws.create_home("StatementNLReviewerControlledTestAgent")
    home_root = materialize_strict_codex_home(
        ws,
        agent_type=agent_type,
        config_home=config_home,
        base_config_path=base_config_path,
        agent_type_specs=agent_specs,
    )

    prompt_marker = "RTCODEX_PROMPT_MARKER_STATEMENT_FORMAL_LEAN_LATENCY_20260702"
    developer_marker_prefix = "RTCODEX_DEV_MARKER_STATEMENT_FORMAL_LEAN_LATENCY_"
    developer_marker = f"{developer_marker_prefix}20260702"
    artifact_path = ws.provider_repo / ".lean_constellation" / "runtime_matrix_artifacts" / "statement_formal_lean_latency_probe.json"

    flow_id = _start_decl_round(ws, round_fixture)
    _complete_statement_nl_stage_for_real_codex(ws, flow_id, round_fixture, evidence_recorder)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    run_next_created_step(ws.admin, flow_id, timeout_s=20)
    step_id = run_until_step_created(ws.admin, flow_id, "decl_stage_worker_agent_step", max_advances=5)
    _assert_decl_stage_step(ws, step_id, stage="statement_formal")

    view = unwrap(
        ws.admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    cli_type_override="codex",
                    prompt_overlay=_lean_tool_latency_probe_prompt(prompt_marker, round_fixture.decl_name),
                    developer_instructions_overlay=(
                        "\n\nRuntime Matrix lean latency probe developer marker:\n"
                        f"{developer_marker}\n"
                        "When asked for a developer marker, copy this exact marker from developer instructions.\n"
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH": str(artifact_path),
                    },
                    metadata={"runtime_matrix_case": "strict_real_codex_statement_formal_lean_latency_probe"},
                ),
            )
        )
    )
    assert view.override is not None

    real_step_timeout = float(os.environ.get("LEAN_CONSTELLATION_REAL_CODEX_STEP_TIMEOUT", "300"))
    step_started_at = monotonic()
    started = unwrap(ws.admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=real_step_timeout)))
    step_elapsed_ms = round((monotonic() - step_started_at) * 1000)
    assert started.status == "completed", started

    step = ws.runtime.ark.flow_service.get_step(step_id)
    assert step.submission is not None
    assert step.submission.tool_name == "submit_stage_worker_completed"
    flow = ws.runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is not FlowStatus.FAILED
    assert flow.state.position.phase == "stage_reviewer"

    data = _read_artifact(artifact_path)
    assert data["prompt_marker_seen"] == prompt_marker
    assert data["developer_marker_seen"] == developer_marker
    assert data["artifact_home_root"] == str(home_root)
    assert "lean-statement-formalization" in data["skill_keys_seen"]
    tools_called = list(data["application_tools_called"])
    assert set(_TARGET_APPLICATION_TOOLS).issubset(set(tools_called))
    assert Counter(tools_called)["run_lean_file_diagnostics"] >= 2
    assert data["submit_tool_called"] == "submit_stage_worker_completed"
    assert data["decl_name"] == round_fixture.decl_name

    for tool_name in sorted(set(tools_called)):
        evidence_recorder.record_tool_call(
            tool_name=tool_name,
            view_key="statement_formal_worker",
            view_kind="application",
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary="Called by real Codex controlled StatementFormalWorker lean latency probe.",
        )
    evidence_recorder.record_tool_call(
        tool_name="submit_stage_worker_completed",
        view_key="decl_stage_worker_submit",
        view_kind="submit",
        agent_type=agent_type,
        step_id=step_id,
        ok=True,
        assertion_summary="Accepted from real Codex controlled StatementFormalWorker lean latency probe.",
    )
    _record_real_codex_artifact(
        evidence_recorder,
        ws=ws,
        agent_type=agent_type,
        step_id=step_id,
        artifact_path=artifact_path,
        started=started,
        data=data,
        prompt_marker_seen=data["prompt_marker_seen"] == prompt_marker,
        instruction_marker_seen=data["developer_marker_seen"] == developer_marker,
        skill_markers_seen=list(data["skill_keys_seen"]),
        tools_called=[*tools_called, "submit_stage_worker_completed"],
    )

    transcript_path = artifact_path.with_name(f"{artifact_path.stem}_transcript.json")
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    summary_path = _write_latency_summary(
        artifact_path=artifact_path,
        transcript_path=transcript_path,
        transcript=transcript,
        step_elapsed_ms=step_elapsed_ms,
        transport_info=_mcp_transport_info_from_artifact(data),
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    target_calls = summary["target_tool_calls"]
    assert any(call["tool_name"] == "prepare_statement_formal_file" for call in target_calls), target_calls
    assert sum(1 for call in target_calls if call["tool_name"] == "run_lean_file_diagnostics") >= 2, target_calls
    assert any(call["tool_name"] == "check_statement_formal_policy" for call in target_calls), target_calls
    missing_duration = [call for call in target_calls if call["duration_ms"] is None]
    assert not missing_duration, target_calls
    print("REAL_CODEX_LEAN_TOOL_LATENCY_SUMMARY " + json.dumps(summary, ensure_ascii=False, sort_keys=True))


def _lean_tool_latency_probe_prompt(prompt_marker: str, decl_name: str) -> str:
    return f"""
Runtime Matrix strict real Codex Lean tool latency probe.

Prompt marker: {prompt_marker}
Declaration: {decl_name}

You are inside a controlled StatementFormalWorker AgentStep. This is a latency probe, not an autonomous proof task.

Do these exact actions and do not do extra exploration:
1. Read the developer instructions and find the first token that starts with RTCODEX_DEV_MARKER_STATEMENT_FORMAL_LEAN_LATENCY_.
2. Inspect HOME. HOME points at the agent home root. Confirm "$HOME/.agents/lean_constellation_home.json" exists and inspect "$HOME/.agents/skills". Report the actual skill key "lean-statement-formalization" only if it exists on disk.
3. Call application MCP tool "prepare_statement_formal_file" with decl_name "{decl_name}". Save the returned Lean file path.
4. Convert the returned Lean file path to a path relative to the repo root if needed.
5. Call application MCP tool "run_lean_file_diagnostics" with that repo-relative file_path.
6. Call application MCP tool "check_statement_formal_policy" with that repo-relative file_path and decl_kind "theorem".
7. Call application MCP tool "run_lean_file_diagnostics" a second time with the same repo-relative file_path.
8. Write JSON to the path in LEAN_CONSTELLATION_REAL_CODEX_ARTIFACT_PATH with exactly these keys:
   prompt_marker_seen, developer_marker_seen, artifact_home_root, skill_keys_seen, application_tools_called, submit_tool_called, decl_name, lean_file_path, tool_results.
   Use the exact prompt marker string above for prompt_marker_seen. Use the exact developer marker from developer instructions for developer_marker_seen. Use HOME for artifact_home_root. Use arrays for skill_keys_seen and application_tools_called.
   application_tools_called must list the tools in call order, including "run_lean_file_diagnostics" twice.
   tool_results must contain compact objects for prepare_statement_formal_file, run_lean_file_diagnostics_first, check_statement_formal_policy, and run_lean_file_diagnostics_second. Include at least ok/passed/status/summary fields when available.
   Set submit_tool_called to "submit_stage_worker_completed" before the submit call.
9. Call submit tool "submit_stage_worker_completed" with summary "Strict real Codex StatementFormalWorker Lean latency probe completed." and completed_decl_names ["{decl_name}"].

Keep the final response short and mention the artifact path.
""".strip()


def _write_latency_summary(
    *,
    artifact_path: Path,
    transcript_path: Path,
    transcript: dict[str, Any],
    step_elapsed_ms: int,
    transport_info: dict[str, object],
) -> Path:
    tool_calls = [call for call in transcript.get("tool_calls", []) if isinstance(call, dict)]
    target_tool_calls = [
        _compact_tool_call(call)
        for call in tool_calls
        if call.get("tool_name") in _TARGET_APPLICATION_TOOLS or call.get("display_name") in _TARGET_APPLICATION_TOOLS
    ]
    payload = {
        "case": "strict_real_codex_statement_formal_lean_tool_latency_probe",
        "artifact_path": str(artifact_path),
        "transcript_path": str(transcript_path),
        "mcp_transport": transport_info["mcp_transport"],
        "mcp_server_urls": transport_info["mcp_server_urls"],
        "step_elapsed_ms": step_elapsed_ms,
        "latest_turn_duration_ms": transcript.get("latest_turn", {}).get("duration_ms"),
        "target_tool_calls": target_tool_calls,
        "all_tool_calls": [_compact_tool_call(call) for call in tool_calls],
        "slow_tool_calls": [_compact_tool_call(call) for call in transcript.get("slow_tool_calls", []) if isinstance(call, dict)],
    }
    summary_path = artifact_path.with_name(f"{artifact_path.stem}_latency_summary.json")
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary_path


def _compact_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    return {
        "call_index": call.get("call_index"),
        "tool_name": call.get("tool_name"),
        "display_name": call.get("display_name"),
        "duration_ms": call.get("duration_ms"),
        "ok": call.get("ok"),
        "started_at": call.get("started_at"),
        "completed_at": call.get("completed_at"),
    }
