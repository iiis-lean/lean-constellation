"""Application ToolSpec registry bootstrap."""

from __future__ import annotations

from collections.abc import Sequence

from lean_constellation.services.foundation import MutationSummaryView, ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices
from lean_constellation.services.tool_facade import SubmitBehavior, ToolGroupSpec, ToolSpec, ToolViewSpec
from lean_constellation.tools.groups import build_application_tool_groups as _build_groups
from lean_constellation.tools.internal import adapter, decl_graph, decl_stage, mathlib, node_contract, repo_preparation, resource, source_material
from lean_constellation.tools.toolkit import formal_diagnostics
from lean_constellation.tools.views import build_application_tool_views as _build_views


def build_application_tool_specs() -> list[ToolSpec]:
    """Collect every non-submit application ToolSpec."""

    specs: list[ToolSpec] = []
    for module in (
        repo_preparation,
        source_material,
        adapter,
        resource,
        node_contract,
        mathlib,
        decl_graph,
        decl_stage,
        formal_diagnostics,
    ):
        specs.extend(module.build_tool_specs())
    _validate_tool_specs(specs)
    return specs


def build_application_tool_groups(tool_specs: Sequence[ToolSpec] | None = None) -> list[ToolGroupSpec]:
    specs = list(tool_specs) if tool_specs is not None else build_application_tool_specs()
    return _build_groups(specs)


def build_application_tool_views(group_specs: Sequence[ToolGroupSpec] | None = None) -> list[ToolViewSpec]:
    groups = list(group_specs) if group_specs is not None else build_application_tool_groups()
    return _build_views(groups)


def register_application_tooling(runtime: LeanRuntimeServices) -> ServiceResult[MutationSummaryView]:
    """Register application tools, groups, and default views on a runtime."""

    specs = build_application_tool_specs()
    groups = build_application_tool_groups(specs)
    views = build_application_tool_views(groups)

    tools_result = runtime.tool_facade.register_application_tools(specs)
    if not tools_result.ok:
        return runtime.foundation.fail(tools_result.issues)
    groups_result = runtime.tool_facade.register_tool_groups(groups)
    if not groups_result.ok:
        return runtime.foundation.fail(groups_result.issues)
    views_result = runtime.tool_facade.register_tool_views(views)
    if not views_result.ok:
        return runtime.foundation.fail(views_result.issues)

    return runtime.foundation.ok(
        runtime.foundation.mutation_view(
            object_ref="application_tooling",
            changed=True,
            summary=f"Registered {len(specs)} tools, {len(groups)} groups, and {len(views)} views.",
            changed_items=["tools", "groups", "views"],
        )
    )


def _validate_tool_specs(specs: Sequence[ToolSpec]) -> None:
    names = [spec.name for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate application tool names: {', '.join(duplicates)}")
    submit_like = [
        spec.name
        for spec in specs
        if spec.submit_behavior != SubmitBehavior.NONE or spec.name.startswith("submit_")
    ]
    if submit_like:
        raise ValueError(f"Submit tools are not allowed in layer 2 registry: {', '.join(sorted(submit_like))}")
    missing_groups = [spec.name for spec in specs if not spec.tool_groups]
    if missing_groups:
        raise ValueError(f"Every application tool must declare at least one group: {', '.join(sorted(missing_groups))}")
    missing_roles = [spec.name for spec in specs if not spec.allowed_roles]
    if missing_roles:
        raise ValueError(f"Every application tool must declare allowed roles: {', '.join(sorted(missing_roles))}")
