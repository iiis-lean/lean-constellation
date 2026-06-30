"""Adapter upstream navigation toolkit tool grouping."""

from __future__ import annotations

from collections.abc import Iterable

from lean_constellation.services.tool_facade import ToolSpec
from lean_constellation.tools.internal import adapter as internal_adapter


TOOL_NAMES = (
    "search_upstream_declarations",
    "search_upstream_modules",
    "list_upstream_module_declarations",
    "inspect_upstream_declaration",
    "read_upstream_source_context",
    "capture_upstream_declaration_code",
    "inspect_upstream_module_imports",
)


def select_tool_specs(tool_specs: Iterable[ToolSpec]) -> list[ToolSpec]:
    names = set(TOOL_NAMES)
    return [spec for spec in tool_specs if spec.name in names]


def build_tool_specs() -> list[ToolSpec]:
    return select_tool_specs(internal_adapter.build_tool_specs())
