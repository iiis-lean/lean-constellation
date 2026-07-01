"""Submit tool group definitions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lean_constellation.services.tool_facade import ToolGroupSpec, ToolSpec


def build_submit_tool_groups(tool_specs: Sequence[ToolSpec]) -> list[ToolGroupSpec]:
    grouped: dict[str, set[str]] = {}
    for spec in tool_specs:
        for group_key in spec.tool_groups:
            grouped.setdefault(group_key, set()).add(spec.name)
    return [
        ToolGroupSpec(
            key=group_key,
            tool_names=sorted(tool_names),
        )
        for group_key, tool_names in sorted(grouped.items())
    ]


def known_submit_group_keys(group_specs: Iterable[ToolGroupSpec]) -> set[str]:
    return {group.key for group in group_specs}
