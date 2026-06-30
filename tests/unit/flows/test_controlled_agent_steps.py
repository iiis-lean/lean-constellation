from __future__ import annotations

from agent_runtime_kit.flow.models import StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import create_fake_lean_flow_runtime
from lean_constellation.flows.resource_request.submissions import ResourceRejectedSubmission
from lean_constellation.flows.testing import (
    CONTROLLED_AGENT_OVERRIDE_ALIASES,
    CONTROLLED_AGENT_RECORD_KEY,
    CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES,
)


ControlledResourceCuratorAgentStep = CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES["resource_curator_agent_step"]


def _controlled_runtime(tmp_path):
    return create_fake_lean_flow_runtime(
        tmp_path,
        step_type_overrides=CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES,
    )


def _start_resource_flow(runtime) -> str:
    return runtime.start_flow(
        "resource_curation",
        {
            "repo_key": "Repo",
            "target_kind": "web",
            "target": "https://example.com/source",
        },
        scope_id="repo:Repo",
    )


def _rejected_submission() -> ResourceRejectedSubmission:
    return ResourceRejectedSubmission(
        submission_id=new_submission_id("sub"),
        submission_type="resource_rejected",
        tool_name="submit_resource_rejected",
        target_kind="web",
        target="https://example.com/source",
        reason="Out of scope.",
        summary="Rejected.",
    )


def test_controlled_agent_step_can_start_fresh_test_agent_type_with_overrides(tmp_path) -> None:
    runtime = _controlled_runtime(tmp_path)
    flow_id = _start_resource_flow(runtime)
    step = ControlledResourceCuratorAgentStep(
        step_id="step_resource_curator_controlled",
        flow_id=flow_id,
        scope_id="repo:Repo",
        state=AgentStepState(
            agent_role="resource_curator",
            agent_type="ResourceCuratorAgent",
            home_id="ResourceCuratorAgent",
            create_agent_if_missing=True,
            prompt_override="Base prompt.",
            env_overrides={
                "ARK_STEP_ID": "wrong",
                "LEAN_CONSTELLATION_AGENT_TYPE": "WrongAgent",
            },
            variables={
                CONTROLLED_AGENT_OVERRIDE_ALIASES[0]: {
                    "strategy": "fresh_test_agent_type",
                    "agent_type_override": "ResourceCuratorControlledTestAgent",
                    "prompt_overlay": "Call the rejection submit tool.",
                    "developer_instructions_override": "Controlled developer instructions.",
                    "env_overrides": {"EXTRA_TEST_ENV": "1"},
                    "workdir_override": str(tmp_path / "repo"),
                    "metadata": {"case": "fresh_test"},
                }
            },
        ),
    )
    runtime.attach_step(step)
    runtime.agent_service.queue_submission(_rejected_submission())

    runtime.run_step(step.step_id)

    saved = runtime.flow_service.get_step(step.step_id)
    record = runtime.agent_service.start_records[0]
    assert saved.status is StepStatus.COMPLETED
    assert record.prompt == "Base prompt.\n\nCall the rejection submit tool."
    assert record.developer_instructions_template_override == "Controlled developer instructions."
    assert record.env["ARK_STEP_ID"] == step.step_id
    assert record.env["LEAN_CONSTELLATION_AGENT_TYPE"] == "ResourceCuratorControlledTestAgent"
    assert record.env["EXTRA_TEST_ENV"] == "1"
    assert record.workdir == str(tmp_path / "repo")
    assert runtime.agent_service.get_agent(record.agent_id).home_id == "ResourceCuratorControlledTestAgent"
    assert saved.state.variables[CONTROLLED_AGENT_RECORD_KEY]["strategy"] == "fresh_test_agent_type"
    assert saved.state.variables[CONTROLLED_AGENT_RECORD_KEY]["metadata"] == {"case": "fresh_test"}


def test_controlled_agent_step_without_override_uses_base_agent_step_behavior(tmp_path) -> None:
    runtime = _controlled_runtime(tmp_path)
    flow_id = _start_resource_flow(runtime)
    step = ControlledResourceCuratorAgentStep(
        step_id="step_resource_curator_no_override",
        flow_id=flow_id,
        scope_id="repo:Repo",
        state=AgentStepState(
            agent_role="resource_curator",
            agent_type="ResourceCuratorAgent",
            create_agent_if_missing=True,
            prompt_override="Base prompt.",
        ),
    )
    runtime.attach_step(step)
    runtime.agent_service.queue_submission(_rejected_submission())

    runtime.run_step(step.step_id)

    saved = runtime.flow_service.get_step(step.step_id)
    record = runtime.agent_service.start_records[0]
    assert saved.status is StepStatus.COMPLETED
    assert record.prompt == "Base prompt."
    assert "LEAN_CONSTELLATION_AGENT_TYPE" not in record.env
    assert CONTROLLED_AGENT_RECORD_KEY not in saved.state.variables


def test_controlled_agent_step_can_bind_fresh_same_type_to_flow(tmp_path) -> None:
    runtime = _controlled_runtime(tmp_path)
    flow_id = _start_resource_flow(runtime)
    step = ControlledResourceCuratorAgentStep(
        step_id="step_resource_curator_flow_bind",
        flow_id=flow_id,
        scope_id="repo:Repo",
        state=AgentStepState(
            agent_role="resource_curator",
            agent_type="ResourceCuratorAgent",
            create_agent_if_missing=True,
            variables={
                CONTROLLED_AGENT_OVERRIDE_ALIASES[0]: {
                    "strategy": "fresh_same_agent_type_bind_flow",
                }
            },
        ),
    )
    runtime.attach_step(step)
    runtime.agent_service.queue_submission(_rejected_submission())

    runtime.run_step(step.step_id)

    saved = runtime.flow_service.get_step(step.step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    agent_id = saved.agent_bindings.get("resource_curator")
    assert agent_id is not None
    assert flow.agent_bindings.get("resource_curator") == agent_id
    assert runtime.agent_service.get_agent(agent_id).agent_type == "ResourceCuratorAgent"


def test_controlled_agent_step_can_fork_bound_agent_without_overwriting_flow_binding(tmp_path) -> None:
    runtime = _controlled_runtime(tmp_path)
    flow_id = _start_resource_flow(runtime)
    source = runtime.agent_service.create_agent(
        "repo:Repo",
        "ResourceCuratorAgent",
        home_id="ResourceCuratorAgent",
    )
    runtime.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: flow.agent_bindings.by_role.__setitem__("resource_curator", source.agent_id),
    )
    step = ControlledResourceCuratorAgentStep(
        step_id="step_resource_curator_fork",
        flow_id=flow_id,
        scope_id="repo:Repo",
        state=AgentStepState(
            agent_role="resource_curator",
            agent_type="ResourceCuratorAgent",
            create_agent_if_missing=False,
            variables={
                CONTROLLED_AGENT_OVERRIDE_ALIASES[0]: {
                    "strategy": "fork_bound_agent",
                }
            },
        ),
    )
    runtime.attach_step(step)
    runtime.agent_service.queue_submission(_rejected_submission())

    runtime.run_step(step.step_id)

    saved = runtime.flow_service.get_step(step.step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    forked_agent_id = saved.agent_bindings.get("resource_curator")
    assert forked_agent_id is not None
    assert forked_agent_id != source.agent_id
    assert flow.agent_bindings.get("resource_curator") == source.agent_id
    assert runtime.agent_service.get_agent(forked_agent_id).agent_type == "ResourceCuratorAgent"
