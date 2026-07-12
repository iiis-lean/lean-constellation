from __future__ import annotations

from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_repo_preparation_tools_are_registered() -> None:
    expected = {
        "get_preparation_input",
        "get_current_repo_run_context",
        "get_preparation_start_preflight",
        "inspect_workspace_for_coordinator",
        "list_ready_provider_repos",
        "list_open_requirement_groups",
        "get_requirement_group",
        "get_current_repo_requirement",
        "list_current_lake_dependencies",
        "attach_ready_workspace_repo_dependency",
        "attach_requirement_provider_dependency",
        "list_requirement_resume_candidates",
        "mark_requirement_result_observed",
        "search_github_lean_repositories",
        "inspect_github_lean_repository",
        "check_root_main_handoff_interfaces",
    }

    assert_tools_registered(expected)


def test_repo_preparation_groups_expose_expected_tools() -> None:
    assert_group_contains("repo_preparation_input_read", {"get_preparation_input"})
    assert_group_contains("repo_run_context_read", {"get_current_repo_run_context"})
    assert_group_contains("repo_preparation_start_preflight_read", {"get_preparation_start_preflight"})
    assert_group_contains("workspace_provider_catalog_read", {"inspect_workspace_for_coordinator", "list_ready_provider_repos"})
    assert_group_contains(
        "workspace_requirement_read",
        {
            "list_open_requirement_groups",
            "get_requirement_group",
            "get_current_repo_requirement",
        },
    )
    assert_group_contains("workspace_requirement_control_read", {"list_requirement_resume_candidates"})
    assert_group_contains("workspace_requirement_control_write", {"mark_requirement_result_observed"})
    assert_group_contains("lake_dependency_read", {"list_current_lake_dependencies"})
    assert_group_contains(
        "lake_dependency_write",
        {"attach_ready_workspace_repo_dependency"},
    )
    assert_group_contains("requirement_provider_admin_write", {"attach_requirement_provider_dependency"})
    assert_group_contains(
        "upstream_repo_search",
        {"search_github_lean_repositories", "inspect_github_lean_repository"},
    )
    assert_group_contains("root_interface_prepare_read", {"check_root_main_handoff_interfaces"})


def test_requirement_control_tools_are_admin_only() -> None:
    from lean_constellation.tools import build_application_tool_specs

    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert specs["list_requirement_resume_candidates"].allowed_roles == {"admin"}
    assert specs["mark_requirement_result_observed"].allowed_roles == {"admin"}
    assert specs["attach_requirement_provider_dependency"].allowed_roles == {"admin"}
    assert specs["get_current_repo_requirement"].allowed_roles == {"coordinator", "admin"}
    assert specs["get_current_repo_run_context"].allowed_roles == {"coordinator", "admin"}
