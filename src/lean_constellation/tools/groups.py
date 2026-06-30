"""Application tool group definitions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lean_constellation.services.tool_facade import ToolGroupSpec, ToolSpec


GROUP_SKILL_KEYS: dict[str, list[str]] = {
    "node_contract_read_current": ["content-contract-reading"],
    "node_contract_read_coordinator": ["node-contract-design"],
    "node_boundary_read_current": ["node-dir-dependency-recon"],
    "node_contract_dependency_current_write": ["node-dir-dependency-recon"],
    "node_contract_material_current_write": ["resource-request-handling"],
    "node_mathlib_hint_read": ["mathlib-index-recon"],
    "node_mathlib_hint_write": ["mathlib-index-recon"],
    "mathlib_index_read": ["mathlib-index-recon"],
    "mathlib_index_write": ["mathlib-index-write"],
    "mathlib_semantic_search": ["mathlib-search-navigation"],
    "mathlib_navigation": ["mathlib-search-navigation"],
    "external_resource_discovery": ["external-resource-discovery"],
    "resource_curation_context_read": ["resource-curation"],
    "material_acquisition": ["resource-curation"],
    "resource_draft_write": ["resource-curation"],
    "resource_library_read": ["resource-curation"],
    "scope_export_interface_read": ["scope-export-interface-curation"],
    "scope_export_interface_write": ["scope-export-interface-curation"],
    "root_interface_prepare_read": ["scope-export-interface-curation"],
    "workspace_requirement_read": ["repo-requirement-coordination"],
    "workspace_requirement_write": ["repo-requirement-coordination"],
    "lake_dependency_read": ["repo-requirement-coordination"],
    "lake_dependency_write": ["repo-requirement-coordination"],
    "decl_graph_read_current": ["decl-round-planning"],
    "decl_strategy_write": ["decl-round-planning"],
    "decl_round_change_write": ["decl-round-planning"],
    "decl_round_closeout_write": ["decl-round-planning"],
    "decl_detail_read": ["decl-round-planning"],
    "decl_history_read": ["decl-round-planning"],
    "decl_readiness_read": ["decl-round-planning"],
    "decl_stage_statement_nl_write": ["decl-dependency-origin-curation"],
    "decl_stage_proof_nl_write": ["decl-dependency-origin-curation"],
    "decl_stage_review_mark_write": ["decl-dependency-origin-curation"],
    "decl_stage_statement_formal_file": ["decl-formal-file-workflow"],
    "decl_stage_proof_formal_file": ["decl-formal-file-workflow"],
    "formal_diagnostics_read": ["decl-formal-file-workflow"],
}


def build_application_tool_groups(tool_specs: Sequence[ToolSpec]) -> list[ToolGroupSpec]:
    """Build exact ToolGroupSpec entries from ToolSpec membership."""

    grouped: dict[str, set[str]] = {}
    for spec in tool_specs:
        for group_key in spec.tool_groups:
            grouped.setdefault(group_key, set()).add(spec.name)
    return [
        ToolGroupSpec(
            key=group_key,
            tool_names=sorted(tool_names),
            skill_keys=GROUP_SKILL_KEYS.get(group_key, []),
        )
        for group_key, tool_names in sorted(grouped.items())
    ]


def known_group_keys(group_specs: Iterable[ToolGroupSpec]) -> set[str]:
    return {group.key for group in group_specs}
