"""Admin test-control helpers for Runtime Matrix scheduler tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.app import (
    AdminFlowAdvanceInput,
    AdminRunUntilStepCreatedInput,
    AdminStepStartInput,
    ManualCheckpointInput,
    SetAgentStepOverrideInput,
    SnapshotRestoreInput,
)
from lean_constellation.flows.testing import ControlledAgentOverrideSpec
from lean_constellation.services.foundation import ServiceResult


def unwrap(result: ServiceResult[Any]) -> Any:
    assert result.ok and result.value is not None, getattr(result, "issues", None)
    return result.value


def run_next_created_step(admin, flow_id: str, *, timeout_s: float = 10) -> str:
    advanced = unwrap(admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id)))
    assert advanced.created_step_id is not None, advanced
    started = unwrap(admin.start_step_once(AdminStepStartInput(step_id=advanced.created_step_id, wait=True, timeout_s=timeout_s)))
    assert started.status == "completed", started
    return advanced.created_step_id


def run_until_step_created(admin, flow_id: str, step_type: str, *, max_advances: int = 20) -> str:
    advanced = unwrap(
        admin.run_until_step_created(
            AdminRunUntilStepCreatedInput(flow_id=flow_id, step_type=step_type, max_advances=max_advances)
        )
    )
    assert advanced.created_step_id is not None, advanced
    return advanced.created_step_id


def checkpoint_branch(admin, repo_root: Path, *, scope_ids: list[str], label: str):
    return unwrap(
        admin.create_manual_test_checkpoint(
            ManualCheckpointInput(repo_root=repo_root, scope_ids=scope_ids, label=label)
        )
    )


def restore_branch(admin, repo_root: Path, snapshot_id: str) -> None:
    restored = unwrap(
        admin.restore_snapshot(
            SnapshotRestoreInput(
                repo_root=repo_root,
                snapshot_id=snapshot_id,
                leave_runtime_paused=True,
                prune_extra_files=True,
            )
        )
    )
    assert restored.snapshot_id == snapshot_id
    assert restored.dry_run is False


def set_scripted_provider_override(
    admin,
    step_id: str,
    *,
    agent_type: str,
    prompt_overlay: str | None = None,
    env_overrides: dict[str, str] | None = None,
) -> None:
    view = unwrap(
        admin.set_agent_step_override(
            SetAgentStepOverrideInput(
                step_id=step_id,
                override=ControlledAgentOverrideSpec(
                    strategy="fresh_test_agent_type",
                    agent_type_override=agent_type,
                    provider_type_override="scripted",
                    prompt_overlay=prompt_overlay,
                    env_overrides=env_overrides or {},
                ),
            )
        )
    )
    assert view.override is not None


def run_scripted_submit(
    admin,
    step_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
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
    provider.enqueue(agent_type, ("submit", tool_name, arguments))
    started = unwrap(admin.start_step_once(AdminStepStartInput(step_id=step_id, wait=True, timeout_s=timeout_s)))
    assert started.status == "completed", started
    return dict(provider.calls[-1])


def assert_flow_completed(runtime, flow_id: str, *, outcome: str | None = None):
    flow = runtime.ark.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    if outcome is not None:
        assert flow.result.outcome == outcome, flow.result
    return flow
