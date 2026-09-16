"""Operator-only, paused provider migration for one repo runtime."""

from pydantic import Field

from agent_runtime_kit.flow.provider_switch import (
    apply_provider_switch,
    plan_provider_switch,
    switch_boundary,
)
from lean_constellation.domain.common import StrictModel
from lean_constellation.agents.models import AgentHomeType
from agent_runtime_kit.flow import FlowStatus, StepStatus


def _consumed_failed_children(service):
    """Prove historical status from a successful exact dispatch callback, not time."""
    flows = {f.flow_id: f for f in service.store.list_flows()}
    steps = {s.step_id: s for s in service.store.list_steps()}
    historical = {f.flow_id for f in flows.values() if f.status == FlowStatus.COMPLETED}
    for child in flows.values():
        parent = flows.get(child.parent_flow_id)
        dispatch = steps.get(child.parent_dispatch_step_id)
        if (child.status != FlowStatus.FAILED or parent is None or dispatch is None
                or parent.flow_type != "native_repo_coordinator"
                or dispatch.flow_id != parent.flow_id or dispatch.status != StepStatus.COMPLETED
                or child.parent_dispatch_step_id == parent.state.waiting_dispatch_step_id):
            continue
        if any(
            s.flow_id == parent.flow_id and s.step_id in parent.step_ids
            and s.status == StepStatus.COMPLETED and s.submission is not None
            and s.step_type == "coordinator_agent_step"
            and getattr(s.state, "callback_dispatch_step_id", None) == dispatch.step_id
            and getattr(s.result, "outcome", "incomplete") != "incomplete"
            for s in steps.values()
        ):
            historical.add(child.flow_id)
    # Failed descendants under a proven consumed branch are historical too.
    while True:
        previous = set(historical)
        historical.update(f.flow_id for f in flows.values()
                          if f.status == FlowStatus.FAILED and f.parent_flow_id in previous)
        if historical == previous:
            return tuple(sorted(i for i in historical if flows[i].status == FlowStatus.FAILED))


class ProviderSwitchInput(StrictModel):
    source_agent_ids: list[str] = Field(min_length=1)
    target_provider: AgentHomeType
    expected_plan_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


def provider_switch(
    runtime, repo_key: str, request: ProviderSwitchInput, *, apply: bool
):
    service = runtime.ark.flow_service
    args = dict(
        scope_id=f"repo:{repo_key}",
        source_agent_ids=request.source_agent_ids,
        target_provider=request.target_provider,
        historical_input_agent_flow_types=(
            "repo_resource_discovery",
            "repo_lean_provider_discovery",
            "repo_mathlib_recon",
        ),
    )
    if apply:
        if request.expected_plan_hash is None:
            raise ValueError("Apply requires expected_plan_hash")
        with switch_boundary(service):
            return apply_provider_switch(
                service, **args, expected_plan_hash=request.expected_plan_hash,
                historical_failed_flow_ids=_consumed_failed_children(service),
            )
    if request.expected_plan_hash is not None:
        raise ValueError("Plan must not provide expected_plan_hash")
    with switch_boundary(service):
        return plan_provider_switch(
            service, **args, historical_failed_flow_ids=_consumed_failed_children(service),
        )
