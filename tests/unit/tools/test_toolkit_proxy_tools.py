from __future__ import annotations

from lean_constellation.tools import build_application_tool_specs
from lean_constellation.tools.toolkit import external_search, formal_diagnostics, mathlib_navigation, mathlib_search, upstream_navigation


def test_toolkit_grouping_modules_select_registered_tools() -> None:
    specs = build_application_tool_specs()
    registered = {spec.name for spec in specs}

    for module in (mathlib_search, mathlib_navigation, external_search, upstream_navigation, formal_diagnostics):
        selected = module.build_tool_specs()
        assert selected
        assert {spec.name for spec in selected} <= registered


def test_toolkit_tool_names_are_registered_once() -> None:
    specs = build_application_tool_specs()
    names = [spec.name for spec in specs]
    toolkit_names = set(
        mathlib_search.TOOL_NAMES
        + mathlib_navigation.TOOL_NAMES
        + external_search.TOOL_NAMES
        + upstream_navigation.TOOL_NAMES
        + tuple(spec.name for spec in formal_diagnostics.build_tool_specs())
    )

    for name in toolkit_names:
        assert names.count(name) == 1
