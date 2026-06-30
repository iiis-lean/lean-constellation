from __future__ import annotations

from lean_constellation.agents import get_agent_type_spec, validate_agent_resources
from lean_constellation.services.tool_facade import ToolViewSpec


def test_default_agent_resources_validate_against_current_tooling() -> None:
    report = validate_agent_resources()

    assert report.ok
    assert report.issues == []


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
