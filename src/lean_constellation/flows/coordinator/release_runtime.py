"""ARK runtime preflight for Coordinator release operations."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.services.foundation import GateReport, ServiceResult

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


ReleaseRuntimePhase = Literal["submission_preview", "prepare", "commit"]


def check_repo_release_runtime_closeout(
    runtime: LeanRuntimeServices,
    repo_root: Path,
    *,
    owner_flow_id: str,
    phase: ReleaseRuntimePhase,
    allowed_agent_id: str | None = None,
) -> ServiceResult[GateReport]:
    """Validate ARK ownership and runtime quiescence outside release Services."""
    repo_scope = f"repo:{Path(repo_root).name}"
    try:
        flows = runtime.list_flows()
        steps = runtime.list_steps()
        agent_service = runtime.ark.agent_service
        if agent_service is None or not hasattr(agent_service, "list_agents"):
            raise RuntimeError("ARK agent service does not expose list_agents.")
        agents = list(agent_service.list_agents())
    except Exception as exc:  # noqa: BLE001 - fail closed at the runtime boundary
        issue = runtime.foundation.issue(
            "release_workflow_inspection_failed",
            "Repository workflow closeout could not inspect ARK runtime truth.",
            details={"error": str(exc)},
        )
        return runtime.foundation.ok(
            runtime.foundation.gate_failed(
                "release_runtime_closeout",
                issue,
                summary="Repository runtime closeout inspection failed.",
            )
        )

    issues = []
    owner = next((flow for flow in flows if getattr(flow, "flow_id", None) == owner_flow_id), None)
    if owner is None:
        issues.append(runtime.foundation.issue(
            "release_workflow_owner_invalid",
            "Candidate release owner Flow is missing.",
            object_ref=owner_flow_id,
        ))
    elif getattr(owner, "flow_type", None) != "native_repo_coordinator" or getattr(owner, "scope_id", None) != repo_scope:
        issues.append(runtime.foundation.issue(
            "release_workflow_owner_invalid",
            "Candidate release owner must be a native Coordinator Flow in the repository scope.",
            object_ref=owner_flow_id,
        ))
    else:
        position_model = getattr(getattr(owner, "state", None), "position", "")
        position = str(position_model)
        position_phase = str(getattr(position_model, "phase", position_model))
        allowed = {
            "submission_preview": position_phase
            in {"coordinator_agent", "coordinator_callback", "coordinator_requirement_resume"},
            "prepare": position_phase == "mark_repo_ready",
            "commit": position_phase == "completed",
        }[phase]
        if not allowed:
            issues.append(runtime.foundation.issue(
                "release_workflow_owner_invalid",
                "Coordinator owner is not at the required release phase.",
                object_ref=owner_flow_id,
                current=position,
                expected=phase,
            ))

    for flow in flows:
        scope_id = str(getattr(flow, "scope_id", ""))
        if scope_id != repo_scope and not scope_id.startswith(f"{repo_scope}:node:"):
            continue
        if getattr(flow, "flow_id", None) == owner_flow_id:
            continue
        if getattr(flow, "status", None) not in {FlowStatus.COMPLETED, FlowStatus.FAILED}:
            issues.append(runtime.foundation.issue(
                "release_workflow_not_closed",
                "Another repo workflow is still nonterminal.",
                object_ref=getattr(flow, "flow_id", None),
            ))

    allowed_step_type = {
        "submission_preview": "coordinator_agent_step",
        "prepare": "mark_coordinator_repo_ready_step",
        "commit": None,
    }[phase]
    for step in steps:
        scope_id = str(getattr(step, "scope_id", ""))
        status = str(getattr(getattr(step, "status", None), "value", getattr(step, "status", "")))
        if status != "running" or (scope_id != repo_scope and not scope_id.startswith(f"{repo_scope}:node:")):
            continue
        if (
            allowed_step_type is not None
            and getattr(step, "flow_id", None) == owner_flow_id
            and getattr(step, "step_type", None) == allowed_step_type
        ):
            continue
        issues.append(runtime.foundation.issue(
            "release_workflow_not_closed",
            "A repo Step is still running.",
            object_ref=getattr(step, "step_id", None),
        ))

    for agent in agents:
        scope_id = str(getattr(agent, "scope_id", ""))
        if getattr(agent, "status", None) != "running" or (
            scope_id != repo_scope and not scope_id.startswith(f"{repo_scope}:node:")
        ):
            continue
        if phase == "submission_preview" and getattr(agent, "agent_id", None) == allowed_agent_id:
            continue
        issues.append(runtime.foundation.issue(
            "release_workflow_not_closed",
            "A repo Agent is still running.",
            object_ref=getattr(agent, "agent_id", None),
        ))

    return runtime.foundation.ok(
        runtime.foundation.gate_failed("release_runtime_closeout", issues)
        if issues
        else runtime.foundation.gate_passed(
            "release_runtime_closeout", summary="Repository ARK runtime is closed for release."
        )
    )


__all__ = ["ReleaseRuntimePhase", "check_repo_release_runtime_closeout"]
