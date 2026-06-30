from __future__ import annotations

from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_source_material_tools_are_registered() -> None:
    expected = {
        "create_draft_source_index",
        "get_source_index",
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
        "search_material_text",
        "read_source_range",
    }

    assert_tools_registered(expected)


def test_source_material_groups_expose_expected_tools() -> None:
    assert_group_contains("source_corpus_read", {"scan_source_corpus", "check_source_corpus_draft"})
    assert_group_contains(
        "source_acquisition",
        {"acquire_source_material", "extract_source_artifact", "import_source_material", "normalize_source_text_material"},
    )
    assert_group_contains(
        "source_index_draft_write",
        {"create_draft_source_index", "create_source_block", "create_source_link", "set_file_indexing_status"},
    )
    assert_group_contains("source_index_draft_read", {"get_source_index", "validate_source_index", "get_source_index_coverage"})
    assert_group_contains("source_index_committed_read", {"get_source_index", "get_source_index_coverage"})
    assert_group_contains("source_material_text_read", {"search_material_text", "read_source_range"})
