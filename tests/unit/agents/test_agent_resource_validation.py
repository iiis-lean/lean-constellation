from __future__ import annotations

from lean_constellation.agents import build_agent_type_specs, derive_agent_type_spec, get_agent_type_spec, validate_agent_resources
from lean_constellation.services.tool_facade import ToolGroupSpec, ToolViewSpec


def test_default_agent_resources_validate_against_current_tooling() -> None:
    report = validate_agent_resources()

    assert report.ok
    assert report.issues == []
    assert report.warnings == []


def test_tool_group_without_related_skill_does_not_warn() -> None:
    spec = get_agent_type_spec("ContentPlanAgent").model_copy(
        update={
            "skill_keys": [],
            "application_tool_view_key": "custom_app",
            "submit_tool_view_key": "custom_submit",
        }
    )

    report = validate_agent_resources(
        [spec],
        application_groups=[ToolGroupSpec(key="material_acquisition", tool_names=[])],
        submit_groups=[],
        application_views=[
            ToolViewSpec(
                key="custom_app",
                group_keys=["material_acquisition"],
                allowed_agent_types=["ContentPlanAgent"],
            )
        ],
        submit_views=[
            ToolViewSpec(
                key="custom_submit",
                group_keys=[],
                allowed_agent_types=["ContentPlanAgent"],
            )
        ],
    )

    assert report.ok
    assert report.issues == []
    assert report.warnings == []


def test_skill_required_group_missing_reports_warning_only() -> None:
    spec = get_agent_type_spec("MathlibReconAgent").model_copy(
        update={
            "skill_keys": ["mathlib-semantic-search-navigation"],
            "application_tool_view_key": "custom_app",
            "submit_tool_view_key": "custom_submit",
        }
    )

    report = validate_agent_resources(
        [spec],
        application_groups=[
            ToolGroupSpec(key="mathlib_semantic_search", tool_names=[]),
            ToolGroupSpec(key="mathlib_navigation", tool_names=[]),
        ],
        submit_groups=[],
        application_views=[
            ToolViewSpec(
                key="custom_app",
                group_keys=["mathlib_semantic_search"],
                allowed_agent_types=["MathlibReconAgent"],
            )
        ],
        submit_views=[
            ToolViewSpec(
                key="custom_submit",
                group_keys=[],
                allowed_agent_types=["MathlibReconAgent"],
            )
        ],
    )

    assert report.ok
    assert report.issues == []
    assert [warning.code for warning in report.warnings] == ["skill_required_tool_group_missing"]
    assert report.warnings[0].resource_key == "mathlib-semantic-search-navigation"
    assert report.warnings[0].details["missing_groups"] == "mathlib_navigation"


def test_missing_skill_reports_structured_error() -> None:
    spec = get_agent_type_spec("ContentPlanAgent").model_copy(
        update={"skill_keys": ["missing-skill"]}
    )

    report = validate_agent_resources([spec])

    assert not report.ok
    assert report.issues[0].code == "skill_not_registered"
    assert report.issues[0].agent_type == "ContentPlanAgent"
    assert report.issues[0].resource_key == "missing-skill"


def test_missing_tool_view_reports_structured_error() -> None:
    spec = get_agent_type_spec("ContentPlanAgent").model_copy(
        update={"application_tool_view_key": "missing_view"}
    )

    report = validate_agent_resources([spec])

    assert not report.ok
    assert any(issue.code == "tool_view_not_registered" for issue in report.issues)


def test_missing_tool_group_reports_structured_error() -> None:
    spec = get_agent_type_spec("ContentPlanAgent").model_copy(
        update={"application_tool_view_key": "custom_view"}
    )
    custom_view = ToolViewSpec(
        key="custom_view",
        group_keys=["missing_group"],
        allowed_agent_types=["ContentPlanAgent"],
    )

    report = validate_agent_resources(
        [spec],
        application_groups=[],
        application_views=[custom_view],
    )

    assert not report.ok
    assert any(issue.code == "tool_group_not_registered" for issue in report.issues)


def test_derived_agent_type_reuses_base_tool_view_permissions() -> None:
    controlled = derive_agent_type_spec(
        base_agent_type="CoordinatorAgent",
        agent_type="CoordinatorControlledTestAgent",
    )
    specs = build_agent_type_specs(extra_specs=[controlled])

    report = validate_agent_resources(specs)

    assert report.ok


def test_unknown_agent_type_inheritance_reports_structured_error() -> None:
    spec = get_agent_type_spec("CoordinatorAgent").model_copy(
        update={
            "agent_type": "CoordinatorBrokenTestAgent",
            "extends_agent_type": "MissingBaseAgent",
        }
    )

    report = validate_agent_resources([spec])

    assert not report.ok
    assert any(issue.code == "agent_type_extends_unknown" for issue in report.issues)
