from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.real.runtime_matrix.evidence import EvidenceRecorder
from tests.real.runtime_matrix.strict.surface import current_runtime_surface, strict_missing_report
from tests.real.runtime_matrix.strict.tool_cases import implemented_tool_cases, pending_tool_cases


pytestmark = [pytest.mark.real, pytest.mark.slow]


def test_strict_session_evidence_covers_registered_runtime_surface(
    runtime_matrix_session_evidence: EvidenceRecorder,
    runtime_matrix_evidence_dir: str,
) -> None:
    recorder = runtime_matrix_session_evidence
    evidence = recorder.evidence
    if not evidence.flows and not evidence.application_tool_calls and not evidence.submit_tool_calls:
        pytest.skip("session evidence is empty; run the full strict Runtime Matrix suite to audit actual coverage")

    surface = current_runtime_surface()
    report = strict_missing_report(recorder, surface)
    implemented_tools = set(implemented_tool_cases())
    pending_tools = pending_tool_cases()
    pending_env_tools = {name for name, case in pending_tools.items() if case.status == "pending_env"}
    pending_fixture_tools = {name for name, case in pending_tools.items() if case.status == "pending_fixture"}

    _export_audit_artifacts(
        recorder,
        Path(runtime_matrix_evidence_dir),
        report=report,
        pending_env_tools=pending_env_tools,
        pending_fixture_tools=pending_fixture_tools,
    )

    assert not report["missing_flows"], report
    assert not report["missing_logic_steps"], report
    assert not report["missing_agent_steps"], report
    assert not report["missing_submit_tools"], report
    assert recorder.missing_application_tools(implemented_tools) == set()
    assert set(report["missing_application_tools"]) == pending_env_tools | pending_fixture_tools
    assert pending_env_tools, "strict audit expected explicit env-gated tools instead of silently declaring full completion"
    assert pending_fixture_tools, "strict audit expected explicit pending fixture tools instead of schema-only completion"
    assert len(pending_env_tools) == 8
    assert len(pending_fixture_tools) == 18
    assert any(item.event == "checkpoint" for item in evidence.snapshots)
    assert any(item.event == "restore" for item in evidence.snapshots)
    _assert_checkpointed_write_tools_have_assertion_evidence(recorder, implemented_tools)
    assert not _schema_only_tool_calls(recorder), _schema_only_tool_calls(recorder)


def _assert_checkpointed_write_tools_have_assertion_evidence(
    recorder: EvidenceRecorder,
    implemented_tools: set[str],
) -> None:
    checkpointed_tools = {
        name
        for name, case in implemented_tool_cases().items()
        if name in implemented_tools and case.restore_policy == "checkpoint"
    }
    asserted_calls = {
        item.tool_name
        for item in recorder.evidence.application_tool_calls
        if item.ok and item.tool_name in checkpointed_tools and item.assertion_summary.strip()
    }
    missing_assertions = sorted(checkpointed_tools - asserted_calls)
    assert not missing_assertions, missing_assertions
    assert any(item.event == "restore" and item.pruned is True for item in recorder.evidence.snapshots)


def _schema_only_tool_calls(recorder: EvidenceRecorder) -> list[str]:
    calls = [*recorder.evidence.application_tool_calls, *recorder.evidence.submit_tool_calls]
    return [
        item.tool_name
        for item in calls
        if "schema_only" in item.assertion_summary or "schema-only" in item.assertion_summary
    ]


def _export_audit_artifacts(
    recorder: EvidenceRecorder,
    evidence_dir: Path,
    *,
    report: dict[str, list[str]],
    pending_env_tools: set[str],
    pending_fixture_tools: set[str],
) -> None:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    recorder.export_json(evidence_dir / "strict_session_evidence.json")
    recorder.export_markdown_summary(evidence_dir / "strict_session_evidence.md")
    audit = {
        **report,
        "pending_env_tools": sorted(pending_env_tools),
        "pending_fixture_tools": sorted(pending_fixture_tools),
        "covered_counts": {
            "flows": len(recorder.evidence.flow_types),
            "logic_steps": len(recorder.evidence.logic_step_types),
            "agent_steps": len(recorder.evidence.agent_step_types),
            "application_tools": len(recorder.evidence.application_tool_names),
            "submit_tools": len(recorder.evidence.submit_tool_names),
            "snapshots": len(recorder.evidence.snapshots),
            "codex_artifacts": len(recorder.evidence.codex_artifacts),
        },
    }
    (evidence_dir / "strict_session_missing_report.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
