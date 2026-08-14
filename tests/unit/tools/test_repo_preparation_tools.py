from __future__ import annotations

from types import SimpleNamespace

from lean_constellation.domain.preparation import (
    RepoPreparationInput,
    SourceCorpusMode,
    SourceMaterialInput,
)
from lean_constellation.tools.args import NoArgs
from lean_constellation.tools.internal.repo_preparation import (
    PreparationInputAgentView,
    _get_preparation_input,
)
from tests.unit_services_helpers import make_runtime
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
        "search_github_lean_repositories",
        "inspect_github_lean_repository",
        "check_root_main_handoff_interfaces",
    }

    assert_tools_registered(expected)


def test_repo_preparation_groups_expose_expected_tools() -> None:
    assert_group_contains("repo_preparation_input_read", {"get_preparation_input"})
    assert_group_contains("repo_run_context_read", {"get_current_repo_run_context"})
    assert_group_contains("repo_preparation_start_preflight_read", {"get_preparation_start_preflight"})
    assert_group_contains("workspace_overview_read", {"inspect_workspace_for_coordinator"})
    assert_group_contains("workspace_provider_catalog_read", {"list_ready_provider_repos"})
    assert_group_contains(
        "workspace_requirement_read",
        {
            "list_open_requirement_groups",
            "get_requirement_group",
            "get_current_repo_requirement",
        },
    )
    assert_group_contains("lake_dependency_read", {"list_current_lake_dependencies"})
    assert_group_contains(
        "lake_dependency_write",
        {"attach_ready_workspace_repo_dependency"},
    )
    assert_group_contains(
        "upstream_repo_search",
        {"search_github_lean_repositories", "inspect_github_lean_repository"},
    )
    assert_group_contains("root_interface_prepare_read", {"check_root_main_handoff_interfaces"})


def test_repo_preparation_control_stays_outside_agent_registry() -> None:
    from lean_constellation.tools import build_application_tool_specs

    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert {
        "list_requirement_resume_candidates",
        "mark_requirement_result_observed",
        "attach_requirement_provider_dependency",
    }.isdisjoint(specs)
    assert specs["get_current_repo_requirement"].allowed_roles == {
        "worker",
        "coordinator",
        "admin",
    }
    assert specs["get_current_repo_run_context"].allowed_roles == {"coordinator", "admin"}


def test_repo_preparation_agent_views_are_compact_and_named_by_shape() -> None:
    from lean_constellation.tools import build_application_tool_specs

    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert specs["get_preparation_input"].result_view == "repo_preparation_input_detail"
    assert (
        specs["list_preparation_requirements"].result_view
        == "repo_preparation_requirement_list"
    )
    assert (
        specs["get_preparation_requirement"].result_view
        == "repo_preparation_requirement_detail"
    )
    assert (
        specs["search_github_lean_repositories"].result_view
        == "github_repo_candidate_list"
    )
    assert (
        specs["inspect_github_lean_repository"].result_view
        == "github_repo_candidate_detail"
    )


def test_get_preparation_input_exposes_source_material_requests(tmp_path) -> None:  # noqa: ANN001
    runtime = make_runtime()
    request = SourceMaterialInput(
        target="arXiv:2401.00001 or the corresponding paper title",
        included_scope="The complete paper.",
        role="primary_source",
    )
    written = runtime.repo_workspace.preparation.write_preparation_input(
        tmp_path,
        input=RepoPreparationInput(
            goal="Prepare a faithful SourceCorpus.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            source_material_inputs=[request],
        ),
    )
    assert written.ok

    loaded = _get_preparation_input(
        runtime,
        SimpleNamespace(repo_root=tmp_path),
        NoArgs(),
    )

    assert loaded.ok and loaded.value is not None
    assert loaded.value.source_material_inputs == [request]


def test_preparation_input_view_schema_describes_source_material_requests() -> None:
    schema = PreparationInputAgentView.model_json_schema()
    request = schema["$defs"]["SourceMaterialInput"]
    properties = request["properties"]

    assert "URL, DOI, arXiv id, title" in properties["target"]["description"]
    assert "faithfully include" in properties["included_scope"]["description"]
    assert properties["role"]["enum"] == [
        "primary_source",
        "formal_target",
        "solution",
        "proof_reference",
        "asset",
    ]
