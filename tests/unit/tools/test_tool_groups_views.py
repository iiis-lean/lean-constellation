from __future__ import annotations

from lean_constellation.services import create_test_runtime_services
from lean_constellation.tools import build_application_tool_specs


def test_every_application_view_expands_without_overlap() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    view_keys = sorted(runtime.tool_facade.tool_view._views)

    assert view_keys
    for view_key in view_keys:
        expanded = runtime.tool_facade.tool_view.tool_names_for_view(view_key)
        assert expanded.ok, f"{view_key}: {expanded.issues}"
        assert expanded.value is not None
        assert len(expanded.value) == len(set(expanded.value))


def test_representative_agent_type_resolves_expected_view() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    coordinator = runtime.tool_facade.build_tool_view("native_repo_coordinator")
    plan = runtime.tool_facade.build_tool_view("content_plan")
    statement_worker = runtime.tool_facade.build_tool_view("statement_nl_worker", {"stage": "statement_nl"})

    assert coordinator.ok
    assert coordinator.value is not None
    assert coordinator.value.key == "native_repo_coordinator"
    assert plan.ok
    assert plan.value is not None
    assert plan.value.key == "content_plan"
    assert statement_worker.ok
    assert statement_worker.value is not None
    assert statement_worker.value.key == "statement_nl_worker"


def test_group_queries_return_registered_tools() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)
    expected = {spec.name for spec in build_application_tool_specs() if "mathlib_index_read" in spec.tool_groups}

    listed = runtime.tool_facade.list_registered_tools(group_key="mathlib_index_read")

    assert listed.ok
    assert {tool.name for tool in listed.value or []} == expected
