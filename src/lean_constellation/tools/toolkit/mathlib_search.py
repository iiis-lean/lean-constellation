"""Mathlib search toolkit tool grouping."""

from __future__ import annotations

from collections.abc import Iterable

from lean_constellation.services.tool_facade import ToolSpec
from lean_constellation.tools.internal import mathlib as internal_mathlib


TOOL_NAMES = (
    "search_external_mathlib",
    "search_mathlib_declarations",
)


def select_tool_specs(tool_specs: Iterable[ToolSpec]) -> list[ToolSpec]:
    names = set(TOOL_NAMES)
    return [spec for spec in tool_specs if spec.name in names]


def build_tool_specs() -> list[ToolSpec]:
    """Return the registered Mathlib search ToolSpecs.

    The canonical specs are defined in the Mathlib internal module because the
    backing service performs candidate caching and MathlibIndex ingestion.
    This module gives toolkit-oriented code and tests a stable import point.
    """

    return select_tool_specs(internal_mathlib.build_tool_specs())
