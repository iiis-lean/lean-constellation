from __future__ import annotations

from lean_constellation.services.tool_facade import SubmitBehavior, ToolCapability
from lean_constellation.tools import build_submit_tool_groups, build_submit_tool_specs, build_submit_tool_views
from lean_constellation.tools.registry import build_application_tool_specs


EXPECTED_SUBMIT_TOOLS = {
    "submit_adapter_repo_choice",
    "submit_native_repo_choice",
    "submit_source_corpus_prepared",
    "submit_source_corpus_blocked",
    "submit_source_index_builder_round",
    "submit_source_index_review_round",
    "submit_root_interface_prepare_ready",
    "submit_adapter_catalog_ready",
    "submit_adapter_catalog_blocked",
    "submit_resource_request",
    "submit_resource_duplicate",
    "submit_local_resource_created",
    "submit_external_repo_required",
    "submit_resource_rejected",
    "submit_content_node_tasks",
    "submit_repo_exploration",
    "submit_repo_resource_discovery_result",
    "submit_repo_lean_provider_discovery_result",
    "submit_repo_mathlib_recon_result",
    "submit_repo_requirement",
    "submit_adapter_repo_requirement",
    "submit_native_repo_requirement",
    "submit_repo_ready",
    "submit_content_preparation_recon",
    "submit_current_decl_round",
    "submit_content_node_ready",
    "submit_content_node_blocked",
    "submit_content_node_failed",
    "submit_node_dir_dependency_recon_completed",
    "submit_mathlib_recon_completed",
    "submit_resource_recon_completed",
    "submit_resource_recon_blocked",
    "submit_stage_worker_completed",
    "submit_stage_worker_blocked",
    "submit_stage_review",
}


def _missing_schema_descriptions(schema: dict, *, path: str) -> list[str]:
    missing: list[str] = []
    if schema.get("type") == "object":
        for field_name, field_schema in schema.get("properties", {}).items():
            if "$ref" not in field_schema and "description" not in field_schema:
                missing.append(f"{path}.{field_name}")
            missing.extend(_missing_schema_descriptions(field_schema, path=f"{path}.{field_name}"))
    for defs_key in ("$defs", "definitions"):
        for name, nested_schema in schema.get(defs_key, {}).items():
            missing.extend(_missing_schema_descriptions(nested_schema, path=f"{path}.{defs_key}.{name}"))
    for union_key in ("anyOf", "oneOf", "allOf"):
        for nested_schema in schema.get(union_key, []):
            missing.extend(_missing_schema_descriptions(nested_schema, path=path))
    if "items" in schema:
        missing.extend(_missing_schema_descriptions(schema["items"], path=f"{path}[]"))
    return missing


def test_submit_registry_contains_only_real_submit_tools() -> None:
    specs = build_submit_tool_specs()
    names = {spec.name for spec in specs}

    assert names == EXPECTED_SUBMIT_TOOLS
    assert not (names & {spec.name for spec in build_application_tool_specs()})
    assert all(spec.name.startswith("submit_") for spec in specs)
    assert all(spec.capability == ToolCapability.SUBMIT for spec in specs)
    assert all(spec.submit_behavior != SubmitBehavior.NONE for spec in specs)
    assert any(spec.submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS for spec in specs)
    for spec in specs:
        missing = _missing_schema_descriptions(spec.args_model.model_json_schema(), path=spec.name)
        assert not missing, f"{spec.name} has undocumented schema fields: {missing}"


def test_submit_groups_and_views_are_self_contained() -> None:
    specs = build_submit_tool_specs()
    groups = build_submit_tool_groups(specs)
    views = build_submit_tool_views(groups)
    grouped = {tool for group in groups for tool in group.tool_names}

    assert grouped == {spec.name for spec in specs}
    assert {view.key for view in views}
    for view in views:
        assert view.group_keys


def test_submit_tool_groups_do_not_reverse_bind_skills() -> None:
    specs = build_submit_tool_specs()
    groups = build_submit_tool_groups(specs)

    assert groups
    assert all(group.skill_keys == [] for group in groups)
