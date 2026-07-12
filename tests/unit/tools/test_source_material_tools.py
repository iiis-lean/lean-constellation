from __future__ import annotations

from lean_constellation.tools import build_application_tool_specs
from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_source_material_tools_are_registered() -> None:
    expected = {
        "get_source_index",
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
        "source_index_draft_read",
        {"get_source_index", "get_source_index_update_context", "validate_source_index", "get_source_index_coverage"},
    )
    assert_group_contains("source_index_committed_read", {"get_source_index", "get_source_index_coverage"})
    assert_group_contains("source_material_text_read", {"search_source_text", "read_source_range", "validate_source_range", "preview_source_ref"})


def test_source_index_update_context_is_limited_to_build_review_roles() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}

    assert specs["get_source_index_update_context"].allowed_roles == {"worker", "reviewer", "admin"}
