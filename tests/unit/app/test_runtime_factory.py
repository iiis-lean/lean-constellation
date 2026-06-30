from __future__ import annotations

from agent_runtime_kit.flow.scheduler import RuntimeScheduleService

from lean_constellation.app import create_app_runtime_services


def test_app_runtime_factory_wires_ark_services_and_tool_registries(tmp_path) -> None:
    runtime = create_app_runtime_services(runtime_root=tmp_path / ".agent_runtime")

    assert runtime.ark.flow_service is not None
    assert runtime.ark.step_service is not None
    assert runtime.ark.agent_service is not None
    assert isinstance(runtime.ark.schedule_service, RuntimeScheduleService)
    assert runtime.ark.pause_controller is not None
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
