from __future__ import annotations

from lean_constellation.app import (
    LeanAdminApi,
    LeanAppConfig,
    RepoRuntimeRegistry,
    StartRequirementGroupBootstrapInput,
)


def test_admin_starts_requirement_group_bootstrap_through_flow_service(tmp_path) -> None:
    registry = RepoRuntimeRegistry(
        LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False)
    )
    runtime = registry.workspace_runtime()
    consumer = tmp_path / "Consumer"
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    assert runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need provider source.",
        reason="Expose helper theorem.",
    ).ok

    result = LeanAdminApi(
        runtime,
        workspace_root=tmp_path,
        repo_runtime_registry=registry,
    ).start_requirement_group_bootstrap(
        StartRequirementGroupBootstrapInput(workspace_root=tmp_path, target_repo="Provider")
    )

    assert result.ok and result.value is not None
    assert result.value.flow_type == "requirement_group_repo_bootstrap"
    provider_root = tmp_path / "Provider"
    assert result.value.repo_root == str(provider_root)
    assert (provider_root / ".lean_constellation" / "preparation_input.json").exists()
    assert (provider_root / ".agent_runtime" / "homes").is_dir()
    provider_runtime = registry.try_get_loaded("Provider")
    assert provider_runtime is not None
    flow = provider_runtime.ark.flow_service.get_flow(result.value.flow_id)
    assert flow.flow_type == "requirement_group_repo_bootstrap"
    assert flow.scope_id == "repo:Provider"
