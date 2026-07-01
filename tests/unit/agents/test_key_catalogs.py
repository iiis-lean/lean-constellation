from __future__ import annotations

from enum import StrEnum

from lean_constellation.agents import build_agent_type_specs, derive_agent_type_spec, validate_agent_resources
from lean_constellation.agents.keys import ProductionAgentTypeKey, SkillKey
from lean_constellation.agents.skills import SKILL_DEFINITIONS
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    build_submit_tool_groups,
    build_submit_tool_specs,
    build_submit_tool_views,
)
from lean_constellation.tools.keys import (
    ApplicationToolGroupKey,
    ApplicationToolViewKey,
    SubmitToolGroupKey,
    SubmitToolViewKey,
)


def _values(enum_cls: type[StrEnum]) -> set[str]:
    return {item.value for item in enum_cls}


def test_key_catalog_values_are_unique() -> None:
    for enum_cls in (
        SkillKey,
        ApplicationToolGroupKey,
        SubmitToolGroupKey,
        ApplicationToolViewKey,
        SubmitToolViewKey,
        ProductionAgentTypeKey,
    ):
        values = [item.value for item in enum_cls]
        assert len(values) == len(set(values)), enum_cls.__name__


def test_skill_catalog_matches_registry_and_required_groups() -> None:
    app_group_values = _values(ApplicationToolGroupKey)
    submit_group_values = _values(SubmitToolGroupKey)

    assert set(SKILL_DEFINITIONS) == _values(SkillKey)
    for skill_key, definition in SKILL_DEFINITIONS.items():
        assert skill_key in _values(SkillKey)
        assert set(definition.required_tool_groups) <= app_group_values | submit_group_values


def test_agent_type_registry_uses_catalog_keys() -> None:
    specs = build_agent_type_specs()
    skill_values = _values(SkillKey)
    app_view_values = _values(ApplicationToolViewKey)
    submit_view_values = _values(SubmitToolViewKey)

    assert {spec.agent_type for spec in specs} == _values(ProductionAgentTypeKey)
    for spec in specs:
        assert set(spec.skill_keys) <= skill_values
        assert spec.application_tool_view_key in app_view_values
        assert spec.submit_tool_view_key in submit_view_values


def test_tool_specs_and_views_use_catalog_keys() -> None:
    app_specs = build_application_tool_specs()
    submit_specs = build_submit_tool_specs()
    app_groups = build_application_tool_groups(app_specs)
    submit_groups = build_submit_tool_groups(submit_specs)
    app_views = build_application_tool_views(app_groups)
    submit_views = build_submit_tool_views(submit_groups)

    app_group_values = _values(ApplicationToolGroupKey)
    submit_group_values = _values(SubmitToolGroupKey)

    assert {group.key for group in app_groups} == app_group_values
    assert {group.key for group in submit_groups} == submit_group_values
    assert {view.key for view in app_views} == _values(ApplicationToolViewKey)
    assert {view.key for view in submit_views} == _values(SubmitToolViewKey)

    for spec in app_specs:
        assert set(spec.tool_groups) <= app_group_values, spec.name
    for spec in submit_specs:
        assert set(spec.tool_groups) <= submit_group_values, spec.name
    for view in app_views:
        assert set(view.group_keys) <= app_group_values, view.key
    for view in submit_views:
        assert set(view.group_keys) <= submit_group_values, view.key


def test_controlled_agent_type_can_still_use_dynamic_string_key() -> None:
    controlled = derive_agent_type_spec(
        base_agent_type=ProductionAgentTypeKey.COORDINATOR.value,
        agent_type="CoordinatorDynamicCatalogTestAgent",
    )
    specs = build_agent_type_specs(extra_specs=[controlled])

    report = validate_agent_resources(specs)

    assert report.ok
    assert controlled.agent_type not in _values(ProductionAgentTypeKey)
