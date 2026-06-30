"""Submit tool group definitions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lean_constellation.services.tool_facade import ToolGroupSpec, ToolSpec


SUBMIT_GROUP_SKILL_KEYS: dict[str, list[str]] = {
    "repo_format_discovery_submit": [],
    "source_corpus_prepare_submit": [],
    "source_index_builder_submit": [],
    "source_index_reviewer_submit": [],
    "root_interface_prepare_submit": ["scope-export-interface-curation"],
    "adapter_ready_submit": [],
    "resource_request_submit": ["resource-request-handling"],
    "resource_curator_submit": ["resource-curation"],
    "coordinator_submit": ["repo-requirement-coordination"],
    "content_plan_submit": ["decl-round-planning"],
    "content_completion_submit": [],
    "preparation_recon_submit": [],
    "decl_stage_worker_submit": [],
    "decl_stage_reviewer_submit": [],
}


def build_submit_tool_groups(tool_specs: Sequence[ToolSpec]) -> list[ToolGroupSpec]:
    grouped: dict[str, set[str]] = {}
    for spec in tool_specs:
        for group_key in spec.tool_groups:
            grouped.setdefault(group_key, set()).add(spec.name)
    return [
        ToolGroupSpec(
            key=group_key,
            tool_names=sorted(tool_names),
            skill_keys=SUBMIT_GROUP_SKILL_KEYS.get(group_key, []),
        )
        for group_key, tool_names in sorted(grouped.items())
    ]


def known_submit_group_keys(group_specs: Iterable[ToolGroupSpec]) -> set[str]:
    return {group.key for group in group_specs}
