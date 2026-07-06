"""Repo preparation and workspace Agent-facing tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    ExpectedFormatArgs,
    NoArgs,
    ProviderRepoArgs,
    QueryLimitArgs,
    RequirementNameArgs,
    RequirementObservedArgs,
    TargetRepoArgs,
    UrlOrSlugArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import direct_tool, handler_tool


def _workspace_root(ctx):
    return ctx.repo_root.parent


def _list_open_requirement_groups(runtime, ctx, args: NoArgs):
    del args
    return runtime.repo_workspace.workspace_catalog.list_open_requirement_groups(_workspace_root(ctx))


def _get_requirement_group(runtime, ctx, args: TargetRepoArgs):
    return runtime.repo_workspace.workspace_catalog.get_requirement_group(_workspace_root(ctx), target_repo=args.target_repo)


def _list_ready_provider_repos(runtime, ctx, args: NoArgs):
    del args
    return runtime.repo_workspace.workspace_catalog.list_ready_provider_repos(
        _workspace_root(ctx),
        current_repo=ctx.repo_root.name,
    )


def _list_resume_candidates(runtime, ctx, args: ProviderRepoArgs):
    return runtime.repo_workspace.list_resume_candidates_for_requirement(
        _workspace_root(ctx),
        provider_repo=args.provider_repo,
    )


def build_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    return [
        direct_tool(
            name="get_preparation_input",
            description="Read the current repo preparation input.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="repo_workspace",
            backing_component="preparation",
            backing_method="get_preparation_input",
            result_view="repo_preparation_input",
            groups={AppGroup.REPO_PREPARATION_INPUT_READ},
            roles=roles,
        ),
        direct_tool(
            name="get_current_repo_work_config",
            description="Read the current repo proof availability target and work mode.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="repo_workspace",
            backing_component="metadata",
            backing_method="get_repo_work_config",
            result_view="repo_work_config",
            groups={AppGroup.REPO_WORK_CONFIG_READ},
            roles={"coordinator", "plan", "admin"},
        ),
        direct_tool(
            name="get_preparation_start_preflight",
            description="Check whether the current repo can start native or adapter preparation.",
            args_model=ExpectedFormatArgs,
            capability=ToolCapability.READ,
            backing_service="repo_workspace",
            backing_method="get_preparation_start_preflight",
            result_view="preparation_start_preflight",
            groups={AppGroup.REPO_PREPARATION_INPUT_READ},
            roles={"coordinator", "admin"},
        ),
        direct_tool(
            name="check_root_main_handoff_interfaces",
            description="Check that the Main scope interfaces are valid for native handoff to the Coordinator.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_method="check_root_main_handoff_interfaces",
            result_view="gate_report",
            groups={AppGroup.ROOT_INTERFACE_PREPARE_READ},
            roles={"worker", "coordinator", "admin"},
        ),
        direct_tool(
            name="inspect_workspace_for_coordinator",
            description="Read the workspace repo catalog and stable provider repos for the current coordinator.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="repo_workspace",
            backing_method="inspect_workspace_for_coordinator",
            result_view="workspace_coordinator",
            groups={AppGroup.WORKSPACE_REPO_CATALOG_READ, AppGroup.WORKSPACE_PROVIDER_CATALOG_READ},
            roles={"coordinator", "admin"},
        ),
        handler_tool(
            name="list_ready_provider_repos",
            description="List stable provider repos in the current workspace, excluding the current repo.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="workspace_provider_repos",
            groups={AppGroup.WORKSPACE_PROVIDER_CATALOG_READ},
            roles={"coordinator", "admin"},
            handler=_list_ready_provider_repos,
        ),
        handler_tool(
            name="list_open_requirement_groups",
            description="List open dependency requirement groups across the current workspace.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="requirement_group_list",
            groups={AppGroup.WORKSPACE_REQUIREMENT_READ},
            roles={"coordinator", "admin"},
            handler=_list_open_requirement_groups,
        ),
        handler_tool(
            name="get_requirement_group",
            description="Read one open dependency requirement group by target repo.",
            args_model=TargetRepoArgs,
            capability=ToolCapability.READ,
            result_view="requirement_group",
            groups={AppGroup.WORKSPACE_REQUIREMENT_READ},
            roles={"coordinator", "admin"},
            handler=_get_requirement_group,
        ),
        direct_tool(
            name="list_current_lake_dependencies",
            description="List Lake dependencies declared by the current repo.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="repo_workspace",
            backing_component="workspace_catalog",
            backing_method="list_current_lake_dependency_repos",
            result_view="lake_dependencies",
            groups={AppGroup.LAKE_DEPENDENCY_READ},
            roles={"coordinator", "admin"},
        ),
        direct_tool(
            name="attach_requirement_provider_dependency",
            description="Attach the satisfied provider repo for one requirement as a Lake dependency and mark it handled.",
            args_model=RequirementNameArgs,
            capability=ToolCapability.WRITE,
            backing_service="repo_workspace",
            backing_method="attach_provider_for_requirement",
            result_view="requirement_consume",
            groups={AppGroup.LAKE_DEPENDENCY_WRITE},
            roles={"coordinator", "admin"},
        ),
        handler_tool(
            name="list_requirement_resume_candidates",
            description="List consumer repos whose waiting requirements can resume from a stable provider repo.",
            args_model=ProviderRepoArgs,
            capability=ToolCapability.READ,
            result_view="requirement_resume_candidates",
            groups={AppGroup.WORKSPACE_REQUIREMENT_READ},
            roles={"coordinator", "admin"},
            handler=_list_resume_candidates,
        ),
        direct_tool(
            name="mark_requirement_result_observed",
            description="Mark a waiting requirement provider result as observed in the current repo.",
            args_model=RequirementObservedArgs,
            capability=ToolCapability.WRITE,
            backing_service="repo_workspace",
            backing_method="mark_requirement_result_observed",
            result_view="requirement_waiting",
            groups={AppGroup.WORKSPACE_REQUIREMENT_WRITE},
            roles={"coordinator", "admin"},
        ),
        direct_tool(
            name="search_github_lean_repositories",
            description="Search candidate GitHub Lean repositories for repo format discovery.",
            args_model=QueryLimitArgs,
            capability=ToolCapability.READ,
            backing_service="external",
            backing_component="github_repo",
            backing_method="search_repositories",
            result_view="github_repo_search",
            groups={AppGroup.UPSTREAM_REPO_SEARCH},
            roles={"coordinator", "admin"},
            required_context=set(),
        ),
        direct_tool(
            name="inspect_github_lean_repository",
            description="Inspect a candidate GitHub Lean repository by URL or owner/name slug.",
            args_model=UrlOrSlugArgs,
            capability=ToolCapability.READ,
            backing_service="external",
            backing_component="github_repo",
            backing_method="inspect_repository",
            result_view="github_repo_candidate",
            groups={AppGroup.UPSTREAM_REPO_SEARCH},
            roles={"coordinator", "admin"},
            required_context=set(),
        ),
    ]
