"""Expanded Agent surface reports built from production registries."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lean_constellation.agents.models import AgentTypeSpec
from lean_constellation.agents.registry import build_agent_type_specs
from lean_constellation.agents.skills import SKILL_DEFINITIONS
from lean_constellation.domain.common import StrictModel
from lean_constellation.services.tool_facade import SubmitBehavior, ToolCapability, ToolGroupSpec, ToolSpec, ToolViewSpec
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    build_submit_tool_groups,
    build_submit_tool_specs,
    build_submit_tool_views,
)


class AgentSurfaceTool(StrictModel):
    name: str
    capability: ToolCapability
    required_agent_capabilities: list[str]
    required_context: list[str]
    result_view: str
    tool_groups: list[str]
    submit_behavior: SubmitBehavior = SubmitBehavior.NONE


class AgentSurfaceSkill(StrictModel):
    key: str
    required_tool_groups: list[str]


class AgentSurfaceReport(StrictModel):
    agent_type: str
    role: str
    capabilities: list[str]
    lifecycle_group: str
    context_scope: str
    application_tool_view_key: str
    application_group_keys: list[str]
    application_tools: list[AgentSurfaceTool]
    submit_tool_view_key: str
    submit_group_keys: list[str]
    submit_tools: list[AgentSurfaceTool]
    skills: list[AgentSurfaceSkill]
    missing_skill_required_groups: dict[str, list[str]]


def build_agent_surface_reports(
    *,
    specs: Iterable[AgentTypeSpec] | None = None,
    application_tool_specs: Sequence[ToolSpec] | None = None,
    submit_tool_specs: Sequence[ToolSpec] | None = None,
    application_groups: Sequence[ToolGroupSpec] | None = None,
    submit_groups: Sequence[ToolGroupSpec] | None = None,
    application_views: Sequence[ToolViewSpec] | None = None,
    submit_views: Sequence[ToolViewSpec] | None = None,
) -> dict[str, AgentSurfaceReport]:
    """Return expanded ToolView and Skill surface for each AgentType."""

    resolved_specs = list(specs) if specs is not None else build_agent_type_specs()
    app_specs = list(application_tool_specs) if application_tool_specs is not None else build_application_tool_specs()
    sub_specs = list(submit_tool_specs) if submit_tool_specs is not None else build_submit_tool_specs()
    app_groups = list(application_groups) if application_groups is not None else build_application_tool_groups(app_specs)
    sub_groups = list(submit_groups) if submit_groups is not None else build_submit_tool_groups(sub_specs)
    app_views = list(application_views) if application_views is not None else build_application_tool_views(app_groups)
    sub_views = list(submit_views) if submit_views is not None else build_submit_tool_views(sub_groups)

    app_tools_by_name = {tool.name: tool for tool in app_specs}
    sub_tools_by_name = {tool.name: tool for tool in sub_specs}
    app_group_by_key = {group.key: group for group in app_groups}
    sub_group_by_key = {group.key: group for group in sub_groups}
    app_view_by_key = {view.key: view for view in app_views}
    sub_view_by_key = {view.key: view for view in sub_views}

    reports: dict[str, AgentSurfaceReport] = {}
    for spec in resolved_specs:
        app_view = app_view_by_key[spec.application_tool_view_key]
        submit_view = sub_view_by_key[spec.submit_tool_view_key]
        app_tool_names = _expand_tool_names(app_view, app_group_by_key)
        submit_tool_names = _expand_tool_names(submit_view, sub_group_by_key)
        visible_groups = set(app_view.group_keys) | set(submit_view.group_keys)
        skills = [
            AgentSurfaceSkill(
                key=skill_key,
                required_tool_groups=list(SKILL_DEFINITIONS[skill_key].required_tool_groups),
            )
            for skill_key in spec.skill_keys
        ]
        missing_required = {
            skill.key: sorted(set(skill.required_tool_groups) - visible_groups)
            for skill in skills
            if set(skill.required_tool_groups) - visible_groups
        }
        reports[spec.agent_type] = AgentSurfaceReport(
            agent_type=spec.agent_type,
            role=spec.role,
            capabilities=sorted(spec.capabilities),
            lifecycle_group=spec.lifecycle_group,
            context_scope=spec.context_scope,
            application_tool_view_key=app_view.key,
            application_group_keys=list(app_view.group_keys),
            application_tools=[_surface_tool(app_tools_by_name[name]) for name in app_tool_names],
            submit_tool_view_key=submit_view.key,
            submit_group_keys=list(submit_view.group_keys),
            submit_tools=[_surface_tool(sub_tools_by_name[name]) for name in submit_tool_names],
            skills=skills,
            missing_skill_required_groups=missing_required,
        )
    return reports


def _expand_tool_names(view: ToolViewSpec, group_by_key: dict[str, ToolGroupSpec]) -> list[str]:
    names: list[str] = []
    for group_key in view.group_keys:
        names.extend(group_by_key[group_key].tool_names)
    return names


def _surface_tool(tool: ToolSpec) -> AgentSurfaceTool:
    return AgentSurfaceTool(
        name=tool.name,
        capability=tool.capability,
        required_agent_capabilities=sorted(tool.required_agent_capabilities),
        required_context=sorted(tool.required_context),
        result_view=tool.result_view,
        tool_groups=sorted(tool.tool_groups),
        submit_behavior=tool.submit_behavior,
    )


__all__ = [
    "AgentSurfaceReport",
    "AgentSurfaceSkill",
    "AgentSurfaceTool",
    "build_agent_surface_reports",
]
