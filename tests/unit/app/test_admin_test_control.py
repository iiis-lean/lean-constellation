from __future__ import annotations

from typing import ClassVar

from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, StepStatus, StepTerminalReceipt
from agent_runtime_kit.flow.standard_steps import AgentStepState

from lean_constellation.app import (
    AdminFlowAdvanceInput,
    AdminRunUntilStepCreatedInput,
    AdminStepStartInput,
    ClearAgentStepOverrideInput,
    LeanAdminApi,
    ManualCheckpointInput,
    SnapshotListInput,
    SetAgentStepOverrideInput,
    StartFlowInput,
    create_app_runtime_services,
    create_test_control_runtime_services,
)
from lean_constellation.flows.common.agent_steps import ResourceCuratorAgentStep
from lean_constellation.flows.testing import ControlledAgentOverrideSpec


class ManualAdminTestStep(BaseStep):
    step_type: ClassVar[str] = "manual_admin_test_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState

    def run(self, ctx) -> StepTerminalReceipt:
        return ctx.complete_step(BaseStepResult(result_type="manual_admin_test", summary="done"))


def _start_resource_flow(admin: LeanAdminApi) -> str:
    started = admin.start_arbitrary_flow(
        StartFlowInput(
            flow_type="resource_curation",
            scope_id="repo:Repo",
            params={
                "repo_key": "Repo",
                "target_kind": "web",
                "target": "https://example.com/source",
            },
            enqueue=False,
        )
    )
    assert started.ok and started.value is not None
    return started.value.flow_id


def _attach_step(runtime, flow_id: str, step: BaseStep) -> None:
    store = runtime.ark.flow_service.store
    with store.edit_session(step.scope_id) as tx:
        flow = tx.load_flow_for_update(flow_id)
        tx.add_step(step)
        if step.step_id not in flow.step_ids:
            flow.step_ids.append(step.step_id)
        flow.current_step_id = step.step_id


def test_test_control_mutation_is_rejected_on_default_runtime(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    admin = LeanAdminApi(runtime)

    result = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id="missing"))

    assert not result.ok
    assert result.issues[0].kind == "test_control_disabled"


def test_test_control_advance_flow_once_works_while_runtime_paused(tmp_path) -> None:
    runtime = create_test_control_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_resource_flow(admin)

    result = admin.advance_flow_once(AdminFlowAdvanceInput(flow_id=flow_id))

    assert result.ok and result.value is not None
    assert result.value.created_step_id is not None
    assert result.value.flow_status == "created"
    assert runtime.ark.pause_controller.is_paused()
    queues = admin.get_test_control_runtime_view()
    assert queues.ok and queues.value is not None
    assert result.value.created_step_id in queues.value.candidate_queues.queued_step_ids


def test_run_until_step_created_stops_at_requested_step_type(tmp_path) -> None:
    runtime = create_test_control_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_resource_flow(admin)

    result = admin.run_until_step_created(
        AdminRunUntilStepCreatedInput(
            flow_id=flow_id,
            step_type="resource_curation_preflight_step",
            max_advances=1,
        )
    )

    assert result.ok and result.value is not None
    assert result.value.created_step_id is not None
    step = runtime.ark.step_service.store.get_step(result.value.created_step_id)
    assert step.step_type == "resource_curation_preflight_step"


def test_agent_step_override_api_sets_and_clears_created_agent_step(tmp_path) -> None:
    runtime = create_test_control_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    admin = LeanAdminApi(runtime)
    flow_id = _start_resource_flow(admin)
    step = ResourceCuratorAgentStep(
        step_id="resource_curator_agent_for_admin",
        flow_id=flow_id,
        scope_id="repo:Repo",
        state=AgentStepState(
            agent_role="resource_curator",
            agent_type="ResourceCuratorAgent",
            home_id="ResourceCuratorAgent",
            create_agent_if_missing=True,
        ),
    )
    _attach_step(runtime, flow_id, step)

    set_result = admin.set_agent_step_override(
        SetAgentStepOverrideInput(
            step_id=step.step_id,
            override=ControlledAgentOverrideSpec(
                strategy="fresh_test_agent_type",
                agent_type_override="ResourceCuratorControlledTestAgent",
                cli_type_override="external_takeover",
                prompt_overlay="Call the rejected-resource submit tool.",
            ),
        )
    )

    assert set_result.ok and set_result.value is not None
    assert set_result.value.override is not None
    assert set_result.value.override["agent_type_override"] == "ResourceCuratorControlledTestAgent"
    assert set_result.value.tool_view_key == "resource_curator"

    clear_result = admin.clear_agent_step_override(ClearAgentStepOverrideInput(step_id=step.step_id))

    assert clear_result.ok and clear_result.value is not None
    assert clear_result.value.override is None


def test_start_step_once_bypasses_pause_for_one_manual_step(tmp_path) -> None:
    runtime = create_test_control_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    runtime.ark.step_service.step_registry.register(ManualAdminTestStep)
    admin = LeanAdminApi(runtime)
    flow_id = _start_resource_flow(admin)
    step = ManualAdminTestStep(
        step_id="manual_admin_step",
        flow_id=flow_id,
        scope_id="repo:Repo",
    )
    _attach_step(runtime, flow_id, step)

    result = admin.start_step_once(AdminStepStartInput(step_id=step.step_id, wait=True))

    assert result.ok and result.value is not None
    assert result.value.status == "completed"
    assert runtime.ark.pause_controller.is_paused()
    saved = runtime.ark.step_service.store.get_step(step.step_id)
    assert saved.status is StepStatus.COMPLETED


def test_manual_test_checkpoint_requires_explicit_scopes_and_can_be_listed(tmp_path) -> None:
    runtime = create_test_control_runtime_services(runtime_root=tmp_path / ".agent_runtime")
    admin = LeanAdminApi(runtime)
    _start_resource_flow(admin)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "README.md").write_text("repo\n", encoding="utf-8")

    missing_scope = admin.create_manual_test_checkpoint(
        ManualCheckpointInput(repo_root=repo_root, scope_ids=[])
    )
    assert not missing_scope.ok
    assert missing_scope.issues[0].kind == "manual_checkpoint_scope_ids_required"

    created = admin.create_manual_test_checkpoint(
        ManualCheckpointInput(repo_root=repo_root, scope_ids=["repo:Repo"], label="manual-test")
    )
    assert created.ok and created.value is not None
    assert created.value.checkpoint_kind == "manual_test_stable_point"
    assert created.value.ark_runtime_snapshot_id is not None

    listed = admin.list_snapshots(SnapshotListInput(repo_root=repo_root, checkpoint_kind="manual_test_stable_point"))
    assert listed.ok and listed.value is not None
    assert [snapshot.snapshot_id for snapshot in listed.value] == [created.value.snapshot_id]
