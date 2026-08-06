from __future__ import annotations

from lean_constellation.app import (
    LeanAdminApi,
    LeanAppConfig,
    RepoRuntimeRegistry,
    StartRequirementGroupBootstrapInput,
)
from lean_constellation.domain.preparation import AdapterProviderRoute
from lean_constellation.services.external_clients.github_repo import (
    GitHubLeanRepoProbeView,
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


def test_admin_verifies_direct_adapter_route_before_creating_provider_shell(
    tmp_path,
    monkeypatch,
) -> None:
    registry = RepoRuntimeRegistry(
        LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False)
    )
    runtime = registry.workspace_runtime()
    consumer = tmp_path / "Consumer"
    assert runtime.repo_workspace.metadata.ensure_repo_model(consumer).ok
    route = AdapterProviderRoute(
        git_url="https://github.com/example/provider",
        revision="a" * 40,
        subdir="lean",
        package_name="Provider",
        likely_import_module="Provider",
        evidence_summary="Exact provider commit found.",
    )
    assert runtime.repo_workspace.create_requirement_with_interfaces(
        consumer,
        name="need_provider",
        target_repo="Provider",
        source_description="Need provider source.",
        reason="Expose helper theorem.",
        provider_route=route,
    ).ok
    monkeypatch.setattr(
        runtime.external.github_repo,
        "probe_github_lean_repo_candidate",
        lambda *args, **kwargs: GitHubLeanRepoProbeView(
            git_url=route.git_url,
            normalized_git_url=route.git_url,
            requested_revision=route.revision,
            resolved_revision=route.revision,
            requested_subdir=route.subdir,
            selected_subdir=route.subdir,
            is_lean_project=True,
            has_lakefile=True,
            has_lean_toolchain=True,
            package_name=route.package_name,
            likely_import_modules=[route.likely_import_module],
            lean_toolchain=runtime.repo_workspace.lake_dependency.config.lean_toolchain,
            evidence_summary="Exact remote route verified.",
            summary="Exact remote route verified.",
        ),
    )

    result = LeanAdminApi(
        runtime,
        workspace_root=tmp_path,
        repo_runtime_registry=registry,
    ).start_requirement_group_bootstrap(
        StartRequirementGroupBootstrapInput(
            workspace_root=tmp_path,
            target_repo="Provider",
        )
    )

    assert result.ok and result.value is not None
    provider_runtime = registry.try_get_loaded("Provider")
    assert provider_runtime is not None
    flow = provider_runtime.ark.flow_service.get_flow(result.value.flow_id)
    assert isinstance(flow.input.resolved_provider_route, AdapterProviderRoute)
    assert flow.input.verified_adapter_route is not None


def test_admin_rejects_official_mathlib_as_adapter_provider(
    tmp_path,
    monkeypatch,
) -> None:
    registry = RepoRuntimeRegistry(
        LeanAppConfig(workspace_root=tmp_path, materialize_agent_homes=False)
    )
    runtime = registry.workspace_runtime()
    route = AdapterProviderRoute(
        git_url="https://github.com/leanprover-community/mathlib4",
        revision="a" * 40,
        package_name="mathlib",
        likely_import_module="Mathlib",
        evidence_summary="Platform Mathlib fixture.",
    )
    monkeypatch.setattr(
        runtime.external.github_repo,
        "probe_github_lean_repo_candidate",
        lambda *args, **kwargs: GitHubLeanRepoProbeView(
            git_url=route.git_url,
            normalized_git_url=route.git_url,
            requested_revision=route.revision,
            resolved_revision=route.revision,
            is_lean_project=True,
            has_lakefile=True,
            has_lean_toolchain=True,
            is_mathlib_repository=True,
            adapter_candidate=False,
            evidence_summary="Official Mathlib.",
        ),
    )

    result = LeanAdminApi(
        runtime,
        workspace_root=tmp_path,
        repo_runtime_registry=registry,
    )._verify_requirement_adapter_route(route)

    assert not result.ok
    assert result.issues[0].kind == "adapter_provider_route_mathlib_forbidden"
