from __future__ import annotations

import json
from pathlib import Path

from lean_constellation.app.admin_http import create_workspace_admin_http_routes
from lean_constellation.app.interface_docs import (
    build_admin_catalog,
    build_agent_tools_catalog,
    build_operator_catalog,
    export_interface_docs,
)
from lean_constellation.app.operator_data.decl_projection_http import DECL_PROJECTION_ROUTES
from lean_constellation.app.operator_data.http import _NODE_ROUTES
from lean_constellation.app.operator_data.release_http import RELEASE_HTTP_ROUTES
from lean_constellation.app.operator_data.repo_material_http import REPO_MATERIAL_HTTP_ROUTES
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    build_submit_tool_groups,
    build_submit_tool_specs,
    build_submit_tool_views,
)


def test_interface_catalogs_follow_live_registries() -> None:
    operator = build_operator_catalog()
    admin = build_admin_catalog()
    tools = build_agent_tools_catalog()

    assert operator["operation_count"] == (
        1
        + len(REPO_MATERIAL_HTTP_ROUTES)
        + len(_NODE_ROUTES)
        + len(DECL_PROJECTION_ROUTES)
        + len(RELEASE_HTTP_ROUTES)
    )
    assert admin["operation_count"] == len(create_workspace_admin_http_routes(object()))  # type: ignore[arg-type]

    application_tools = build_application_tool_specs()
    submit_tools = build_submit_tool_specs()
    application_groups = build_application_tool_groups(application_tools)
    submit_groups = build_submit_tool_groups(submit_tools)
    assert tools["tool_count"] == len(application_tools) + len(submit_tools)
    assert tools["group_count"] == len(application_groups) + len(submit_groups)
    assert tools["view_count"] == len(build_application_tool_views(application_groups)) + len(
        build_submit_tool_views(submit_groups)
    )

    create = next(
        item for item in operator["operations"] if item["operation_id"] == "create_native_repo"
    )
    assert create["input_model"] == "NativeRepoCreateInput"
    assert "project_name" in create["input_schema"]["properties"]

    start = next(
        item for item in admin["operations"] if item["operation_id"] == "repo_run_initial"
    )
    assert start["input_model"] == "RepoRunStartInput"
    assert start["schema_status"] == "typed_body"

    submit_requirement = next(
        item for item in tools["tools"] if item["name"] == "submit_repo_requirement"
    )
    assert submit_requirement["layer"] == "submit"
    assert submit_requirement["submit_behavior"] != "none"
    assert submit_requirement["args_schema"]["properties"]


def test_export_interface_docs_is_deterministic_and_machine_readable(tmp_path: Path) -> None:
    first = export_interface_docs(tmp_path)
    first_contents = {path.name: path.read_bytes() for path in first}
    second = export_interface_docs(tmp_path)

    assert [path.name for path in first] == [path.name for path in second]
    assert {path.name: path.read_bytes() for path in second} == first_contents
    assert len(first) == 6
    for path in first:
        assert path.read_text(encoding="utf-8").strip()
        if path.suffix == ".json":
            assert json.loads(path.read_text(encoding="utf-8"))["catalog_version"] == 1
