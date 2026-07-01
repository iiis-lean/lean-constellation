"""Strict Runtime Matrix helper wrappers that record execution evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lean_constellation.app import (
    AdminStepStartInput,
    ExternalTakeoverCompleteInput,
    ExternalTakeoverToolCallInput,
    ExternalTakeoverToolListInput,
    ManualCheckpointInput,
    SnapshotRestoreInput,
)
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.runtime_matrix.admin_helpers import read_handoff_json, unwrap, wait_for_pending_handoff
from tests.real.runtime_matrix.evidence import EvidenceRecorder, ToolViewKind


def call_tool_with_evidence(
    server: Any,
    view_key: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    runtime_context: RuntimeToolContext | None = None,
    env: dict[str, str] | None = None,
    recorder: EvidenceRecorder,
    view_kind: ToolViewKind = "application",
    expected_failure: bool = False,
    assertion_summary: str = "",
) -> Any:
    called = unwrap(
        server.call_tool(
            view_key,
            tool_name,
            dict(arguments),
            runtime_context=runtime_context,
            env=env,
        )
    )
    ok = bool(getattr(called, "ok", False))
    if expected_failure:
        assert ok is False, called
    else:
        assert ok is True, called
    recorder.record_tool_call(
        tool_name=tool_name,
        view_key=view_key,
        view_kind=view_kind,
        agent_type=getattr(runtime_context, "agent_type", None) if runtime_context is not None else (env or {}).get("LEAN_CONSTELLATION_AGENT_TYPE"),
        step_id=getattr(runtime_context, "step_id", None) if runtime_context is not None else (env or {}).get("ARK_STEP_ID"),
        ok=ok,
        expected_failure=expected_failure,
        assertion_summary=assertion_summary,
    )
    return called


def checkpoint_with_evidence(
    admin: Any,
    repo_root: Path,
    *,
    scope_ids: list[str],
    label: str,
    recorder: EvidenceRecorder,
) -> Any:
    checkpoint = unwrap(
        admin.create_manual_test_checkpoint(
            ManualCheckpointInput(repo_root=repo_root, scope_ids=scope_ids, label=label)
        )
    )
    recorder.record_checkpoint(snapshot_id=checkpoint.snapshot_id, scope_ids=scope_ids, label=label)
    return checkpoint


def restore_with_evidence(
    admin: Any,
    repo_root: Path,
    snapshot_id: str,
    *,
    scope_ids: list[str],
    label: str,
    recorder: EvidenceRecorder,
    prune_extra_files: bool = True,
) -> None:
    restored = unwrap(
        admin.restore_snapshot(
            SnapshotRestoreInput(
                repo_root=repo_root,
                snapshot_id=snapshot_id,
                leave_runtime_paused=True,
                prune_extra_files=prune_extra_files,
            )
        )
    )
    assert restored.snapshot_id == snapshot_id
    assert restored.dry_run is False
    recorder.record_restore(
        snapshot_id=snapshot_id,
        scope_ids=scope_ids,
        label=label,
        pruned=prune_extra_files,
    )


def run_external_submit_with_evidence(
    admin: Any,
    step_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    recorder: EvidenceRecorder,
    timeout_s: float = 10,
) -> dict[str, Any]:
    started = unwrap(admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=False)))
    assert started.status in {"created", "running"}, started
    handoff = wait_for_pending_handoff(admin)
    payload = read_handoff_json(handoff.handoff_path)
    submit_view = payload["env"]["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"]
    listed = unwrap(admin.list_external_takeover_tools(ExternalTakeoverToolListInput(handoff_id=handoff.handoff_id, view_kind="submit")))
    assert tool_name in {tool.name for tool in listed}
    called = unwrap(
        admin.call_external_takeover_tool(
            ExternalTakeoverToolCallInput(
                handoff_id=handoff.handoff_id,
                view_kind="submit",
                tool_name=tool_name,
                arguments=arguments,
            )
        )
    )
    assert called.ok is True, called
    recorder.record_tool_call(
        tool_name=tool_name,
        view_key=submit_view,
        view_kind="submit",
        agent_type=payload["env"].get("LEAN_CONSTELLATION_AGENT_TYPE"),
        step_id=payload["env"].get("ARK_STEP_ID"),
        ok=True,
        assertion_summary=f"External takeover submit through handoff {handoff.handoff_id}.",
    )
    completed = unwrap(
        admin.complete_external_takeover(
            ExternalTakeoverCompleteInput(
                handoff_id=handoff.handoff_id,
                final_response=f"Runtime Matrix strict external takeover called {tool_name}.",
                thread_id=f"runtime-matrix-strict-{handoff.handoff_id}",
            )
        )
    )
    assert completed.status == "completed"
    waited = unwrap(admin.wait_step(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=timeout_s)))
    assert waited.status == "completed", waited
    return payload


def run_external_actions_with_evidence(
    admin: Any,
    step_id: str,
    actions: list[tuple[ToolViewKind, str, dict[str, Any]]],
    *,
    recorder: EvidenceRecorder,
    timeout_s: float = 10,
) -> dict[str, Any]:
    started = unwrap(admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=False)))
    assert started.status in {"created", "running"}, started
    handoff = wait_for_pending_handoff(admin)
    payload = read_handoff_json(handoff.handoff_path)
    listed_by_kind: dict[ToolViewKind, set[str]] = {}
    for view_kind, tool_name, arguments in actions:
        if view_kind not in listed_by_kind:
            listed = unwrap(
                admin.list_external_takeover_tools(
                    ExternalTakeoverToolListInput(handoff_id=handoff.handoff_id, view_kind=view_kind)
                )
            )
            listed_by_kind[view_kind] = {tool.name for tool in listed}
        assert tool_name in listed_by_kind[view_kind]
        called = unwrap(
            admin.call_external_takeover_tool(
                ExternalTakeoverToolCallInput(
                    handoff_id=handoff.handoff_id,
                    view_kind=view_kind,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            )
        )
        assert called.ok is True, called
        view_key = _handoff_view_key(payload, view_kind)
        recorder.record_tool_call(
            tool_name=tool_name,
            view_key=view_key,
            view_kind=view_kind,
            agent_type=payload["env"].get("LEAN_CONSTELLATION_AGENT_TYPE"),
            step_id=payload["env"].get("ARK_STEP_ID"),
            ok=True,
            assertion_summary=f"External takeover {view_kind} call through handoff {handoff.handoff_id}.",
        )
    completed = unwrap(
        admin.complete_external_takeover(
            ExternalTakeoverCompleteInput(
                handoff_id=handoff.handoff_id,
                final_response=f"Runtime Matrix strict external takeover executed {len(actions)} actions.",
                thread_id=f"runtime-matrix-strict-{handoff.handoff_id}",
            )
        )
    )
    assert completed.status == "completed"
    waited = unwrap(admin.wait_step(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=timeout_s)))
    assert waited.status == "completed", waited
    return payload


def _handoff_view_key(payload: dict[str, Any], view_kind: ToolViewKind) -> str:
    env = payload["env"]
    if view_kind == "submit":
        return env["LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW"]
    return env.get("LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW") or env["LEAN_CONSTELLATION_EXPECTED_TOOL_VIEW"]
