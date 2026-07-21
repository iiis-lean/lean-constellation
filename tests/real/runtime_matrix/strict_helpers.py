"""Strict Runtime Matrix helper wrappers that record execution evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lean_constellation.app import (
    AdminStepStartInput,
    ManualCheckpointInput,
    SnapshotRestoreInput,
)
from lean_constellation.services.tool_facade import RuntimeToolContext
from tests.real.runtime_matrix.admin_helpers import unwrap
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


def run_scripted_submit_with_evidence(
    admin: Any,
    step_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    recorder: EvidenceRecorder,
    timeout_s: float = 10,
) -> dict[str, Any]:
    return run_scripted_actions_with_evidence(
        admin,
        step_id,
        [("submit", tool_name, arguments)],
        recorder=recorder,
        timeout_s=timeout_s,
    )


def run_scripted_actions_with_evidence(
    admin: Any,
    step_id: str,
    actions: list[tuple[ToolViewKind, str, dict[str, Any]]],
    *,
    recorder: EvidenceRecorder,
    timeout_s: float = 10,
) -> dict[str, Any]:
    from tests.real.runtime_matrix.scripted_provider import get_or_install_scripted_provider

    provider = get_or_install_scripted_provider(admin.runtime)
    step = admin.runtime.ark.step_service.store.get_step(step_id)
    raw_override = dict(getattr(getattr(step, "state", None), "variables", {}) or {}).get(
        "test_override_spec"
    )
    agent_type = (
        raw_override.get("agent_type_override")
        if isinstance(raw_override, dict)
        else getattr(raw_override, "agent_type_override", None)
    )
    if not agent_type:
        raise AssertionError(f"scripted step has no agent_type override: {step_id}")
    start_index = len(provider.calls)
    provider.enqueue(agent_type, list(actions))
    started = unwrap(admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=timeout_s)))
    assert started.status == "completed", started
    calls = provider.calls[start_index:]
    assert len(calls) == len(actions)
    for call in calls:
        view_kind = call["view_kind"]
        recorder.record_tool_call(
            tool_name=call["tool_name"],
            view_key=call["view_key"],
            view_kind=view_kind,
            agent_type=agent_type,
            step_id=step_id,
            ok=True,
            assertion_summary=f"Standard scripted provider {view_kind} call.",
        )
    first = calls[0]
    return {"env": first["env"], "prompt": first["prompt"]}
