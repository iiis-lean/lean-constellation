from __future__ import annotations

from tests.unit.tools._family_helpers import assert_group_contains, assert_tools_registered


def test_resource_tools_are_registered() -> None:
    expected = {
        "get_material_context",
        "normalize_resource_target",
        "find_duplicate_resource",
        "acquire_material_resource",
        "extract_material_artifact",
        "import_material_file",
        "normalize_material_text",
        "read_resource_range",
        "list_resources",
        "get_resource",
        "allocate_resource_draft",
        "get_resource_draft",
        "check_resource_draft",
        "abandon_resource_draft",
    }

    assert_tools_registered(expected)


def test_resource_groups_expose_expected_tools() -> None:
    assert_group_contains(
        "resource_curation_context_read",
        {"get_material_context", "normalize_resource_target", "find_duplicate_resource"},
    )
    assert_group_contains("material_context_read", {"get_material_context"})
    assert_group_contains(
        "resource_target_preflight_read",
        {"normalize_resource_target", "find_duplicate_resource"},
    )
    assert_group_contains(
        "material_acquisition",
        {"acquire_material_resource", "extract_material_artifact", "import_material_file", "normalize_material_text"},
    )
    assert_group_contains("resource_library_read", {"read_resource_range", "list_resources", "get_resource"})
    assert_group_contains("resource_draft_write", {"allocate_resource_draft", "get_resource_draft", "check_resource_draft", "abandon_resource_draft"})
