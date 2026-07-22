from __future__ import annotations

from agent_runtime_kit.flow.scheduler import RuntimeScheduleService
from agent_runtime_kit.flow.standard_steps import DispatchStep
from agent_runtime_kit.agent.report_policy import TraceReportPersistence

from lean_constellation.app import create_app_runtime_services, create_test_control_runtime_services
from lean_constellation.app.config import AgentTraceReportAppConfig, AutomaticCheckpointAppConfig
from lean_constellation.agents import derive_agent_type_spec
from lean_constellation.flows.testing import CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES


def test_app_runtime_factory_wires_ark_services_and_tool_registries(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")

    assert runtime.ark.flow_service is not None
    assert runtime.ark.step_service is not None
    assert runtime.ark.agent_service is not None
    assert isinstance(runtime.ark.schedule_service, RuntimeScheduleService)
    assert runtime.ark.pause_controller is not None
    assert runtime.test_control_enabled is False
    assert "requirement_group_repo_bootstrap" in runtime.ark.flow_service.flow_registry.types
    assert "repo_format_discovery_agent_step" in runtime.ark.step_service.step_registry.types
    assert runtime.ark.agent_service.agent_types.get("RepoFormatDiscoveryAgent").agent_type == "RepoFormatDiscoveryAgent"

    app_view = runtime.tool_facade.build_mcp_view_server("resource_curator")
    submit_view = runtime.tool_facade.build_mcp_view_server("repo_format_discovery_submit")
    assert app_view.ok and app_view.value is not None
    assert submit_view.ok and submit_view.value is not None
    assert "normalize_resource_target" in app_view.value.tool_names
    assert "submit_native_repo_choice" in submit_view.value.tool_names
    assert runtime.tool_facade.submit_submission.submission_gateway is not None


def test_app_runtime_factory_can_start_paused(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime", start_paused=True)

    assert runtime.ark.pause_controller is not None
    assert runtime.ark.pause_controller.is_paused()


def test_app_runtime_factory_wires_checkpoint_and_trace_report_policies(tmp_path) -> None:
    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        automatic_checkpoints=AutomaticCheckpointAppConfig(
            repo_flow_boundaries_enabled=False,
            content_task_progress_enabled=True,
        ),
        agent_trace_reports=AgentTraceReportAppConfig(
            persistence="disabled",
            include_in_snapshots=True,
        ),
    )

    assert runtime.app.automatic_checkpoints.repo_flow_boundaries_enabled is False
    assert runtime.app.automatic_checkpoints.content_task_progress_enabled is True
    assert runtime.ark.agent_service.trace_report_policy.persistence is TraceReportPersistence.DISABLED
    assert runtime.ark.snapshot_service.trace_report_policy.include_in_snapshots is True


def test_test_control_runtime_factory_wires_controlled_profile(tmp_path) -> None:
    runtime = create_test_control_runtime_services(runtime_root=tmp_path / ".agent_runtime")

    assert runtime.test_control_enabled is True
    assert runtime.ark.pause_controller is not None
    assert runtime.ark.pause_controller.is_paused()
    assert runtime.ark.agent_service.agent_types.get("RepoFormatDiscoveryControlledTestAgent").agent_type == (
        "RepoFormatDiscoveryControlledTestAgent"
    )
    assert runtime.ark.step_service.step_registry.get("repo_format_discovery_agent_step") is (
        CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES["repo_format_discovery_agent_step"]
    )
    assert "codex" in runtime.ark.agent_service.provider_registry


def test_app_runtime_factory_accepts_controlled_agent_specs_and_step_overrides(tmp_path) -> None:
    class ControlledDispatchStep(DispatchStep):
        step_type = DispatchStep.step_type

    controlled = derive_agent_type_spec(
        base_agent_type="CoordinatorAgent",
        agent_type="CoordinatorControlledTestAgent",
    )

    runtime = create_app_runtime_services(
        runtime_root=tmp_path / ".agent_runtime",
        extra_agent_type_specs=[controlled],
        step_type_overrides={DispatchStep.step_type: ControlledDispatchStep},
    )

    assert runtime.ark.agent_service.agent_types.get("CoordinatorControlledTestAgent").agent_type == "CoordinatorControlledTestAgent"
    assert runtime.ark.step_service.step_registry.get(DispatchStep.step_type) is ControlledDispatchStep

    view = runtime.tool_facade.build_tool_view("CoordinatorControlledTestAgent")
    assert view.ok
    assert view.value is not None
    assert view.value.key == "native_repo_coordinator"
