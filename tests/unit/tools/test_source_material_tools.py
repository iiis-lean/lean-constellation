from __future__ import annotations

from lean_constellation.tools import build_application_tool_specs
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_views,
)
from lean_constellation.tools.args import SourceRangeArgs
from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_source_material_tools_are_registered() -> None:
    expected = {
        "get_source_index",
        "get_source_index_overview",
        "list_source_index_files",
        "list_source_blocks",
        "get_source_block",
        "get_source_index_update_context",
        "set_source_index_overview",
        "create_source_block",
        "update_source_block",
        "add_source_block_ref",
        "remove_source_block_ref",
        "mark_block_refs_done",
        "create_source_link",
        "mark_block_links_done",
        "mark_block_completed",
        "set_file_survey_status",
        "set_file_indexing_status",
        "validate_source_index",
        "get_source_index_coverage",
        "scan_source_corpus",
        "check_source_corpus_draft",
        "acquire_source_material",
        "extract_source_artifact",
        "import_source_material",
        "normalize_source_text_material",
        "search_source_text",
        "read_source_range",
        "validate_source_range",
        "preview_source_ref",
    }

    assert_tools_registered(expected)


def test_source_range_reads_are_exact_by_default() -> None:
    args = SourceRangeArgs(path="article.tex", start_line=10, end_line=12)
    assert args.context_lines == 0

    spec = next(spec for spec in build_application_tool_specs() if spec.name == "read_source_range")
    assert "context_lines=0 by default" in spec.description
    assert "current node material assignment" in spec.description


def test_legacy_material_search_tool_is_not_agent_facing() -> None:
    names = {spec.name for spec in build_application_tool_specs()}

    assert "search_source_text" in names
    assert "search_resource_text" in names
    assert "search_material_text" not in names


def test_source_material_groups_expose_expected_tools() -> None:
    assert_group_contains("source_corpus_read", {"scan_source_corpus", "check_source_corpus_draft"})
    assert_group_contains(
        "source_acquisition",
        {"acquire_source_material", "extract_source_artifact", "import_source_material", "normalize_source_text_material"},
    )
    assert_group_contains(
        "source_index_draft_write",
        {"create_source_block", "create_source_link", "set_file_indexing_status"},
    )
    assert_group_contains(
        "source_index_navigation_read",
        {
            "get_source_index_overview",
            "list_source_index_files",
            "list_source_blocks",
            "get_source_block",
            "get_source_index_coverage",
        },
    )
    assert_group_contains(
        "source_index_draft_context_read",
        {"get_source_index_update_context", "validate_source_index"},
    )
    assert_group_contains(
        "source_index_full_audit_read",
        {"get_source_index"},
    )
    assert_group_contains("source_material_text_read", {"search_source_text", "read_source_range", "validate_source_range", "preview_source_ref"})


def test_source_index_update_context_is_limited_to_build_review_roles() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert specs["get_source_index_update_context"].allowed_roles == {"worker", "reviewer", "admin"}


def test_full_committed_source_index_is_only_in_audit_views() -> None:
    specs = build_application_tool_specs()
    groups = build_application_tool_groups(specs)
    views = {
        view.key: view
        for view in build_application_tool_views(groups)
    }
    group_tools = {
        group.key: set(group.tool_names)
        for group in groups
    }

    def tools(view_key: str) -> set[str]:
        return {
            tool
            for group in views[view_key].group_keys
            for tool in group_tools[group]
        }

    assert "get_source_index" in tools("native_repo_coordinator")
    assert "get_source_index" in tools("root_interface_prepare")
    assert "get_source_index" not in tools("content_plan")
    assert "get_source_index" not in tools("proof_nl_worker")
    assert "get_source_index_overview" in tools("content_plan")
    assert "get_source_block" in tools("proof_nl_worker")
