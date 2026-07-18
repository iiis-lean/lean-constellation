"""Deterministic interface-reference export for application-owned API surfaces."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from copy import deepcopy
import inspect
import json
from pathlib import Path
from typing import Any, get_type_hints

from pydantic import BaseModel

from lean_constellation.app.admin_http import create_workspace_admin_http_routes
from lean_constellation.app.operator_data.api import OperatorDataApi
from lean_constellation.app.operator_data.decl_projection import DeclProjectionOperator
from lean_constellation.app.operator_data.decl_projection_http import DECL_PROJECTION_ROUTES
from lean_constellation.app.operator_data.http import _NODE_ROUTES
from lean_constellation.app.operator_data.node import NodeOperatorApi
from lean_constellation.app.operator_data.release import ReleaseCheckpointOperatorApi
from lean_constellation.app.operator_data.release_http import RELEASE_HTTP_ROUTES
from lean_constellation.app.operator_data.repo_material import RepoMaterialOperatorApi
from lean_constellation.app.operator_data.repo_material_http import REPO_MATERIAL_HTTP_ROUTES
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    build_submit_tool_groups,
    build_submit_tool_specs,
    build_submit_tool_views,
)


CATALOG_VERSION = 1
SURFACES = ("operator", "admin", "agent-tools")


def build_interface_catalog(surface: str) -> dict[str, Any]:
    """Build one deterministic catalog directly from current code registries."""

    if surface == "operator":
        return build_operator_catalog()
    if surface == "admin":
        return build_admin_catalog()
    if surface == "agent-tools":
        return build_agent_tools_catalog()
    raise ValueError(f"Unknown interface surface: {surface}")


def build_operator_catalog() -> dict[str, Any]:
    operations: list[dict[str, Any]] = [
        _operator_operation(
            category="management",
            method="POST",
            path="/admin/operator/repos/{repo_key}/management/prepare",
            operation="prepare_repo_management",
            owner=OperatorDataApi,
            input_model=None,
        )
    ]
    operations.extend(
        _operator_operation(
            category="repo-material",
            method=route.method,
            path=route.path,
            operation=route.api_method,
            owner=RepoMaterialOperatorApi,
            input_model=route.input_model,
        )
        for route in REPO_MATERIAL_HTTP_ROUTES
    )
    operations.extend(
        _operator_operation(
            category="node-interface-mathlib",
            method=route.method,
            path=route.path,
            operation=route.handler_name,
            owner=NodeOperatorApi,
            input_model=_request_model(NodeOperatorApi, route.handler_name),
        )
        for route in _NODE_ROUTES
    )
    operations.extend(
        _operator_operation(
            category="decl-projection",
            method=route.method,
            path=f"/admin/operator{route.path}",
            operation=route.handler_name,
            owner=DeclProjectionOperator,
            input_model=route.input_model,
        )
        for route in DECL_PROJECTION_ROUTES
    )
    operations.extend(
        _operator_operation(
            category="release-checkpoint",
            method=route.method,
            path=route.path,
            operation=route.api_method,
            owner=ReleaseCheckpointOperatorApi,
            input_model=route.input_model,
        )
        for route in RELEASE_HTTP_ROUTES
    )
    operations.sort(key=lambda item: (item["path"], item["method"], item["operation_id"]))
    return {
        "catalog_version": CATALOG_VERSION,
        "surface": "operator",
        "source_of_truth": [
            "lean_constellation.app.operator_data.*_http route declarations",
            "Operator input Pydantic models",
            "Operator facade method annotations",
        ],
        "coverage_notes": [
            "Request schemas are complete for fixed route bodies.",
            "Responses use the stable OperatorResult envelope.",
            "Value schemas are named from facade annotations when available; some facade methods intentionally retain a generic ServiceResult annotation.",
        ],
        "operation_count": len(operations),
        "operations": operations,
    }


def build_admin_catalog() -> dict[str, Any]:
    # Route construction is side-effect free. Endpoints only dereference the
    # registry when called, so a sentinel is sufficient for documentation.
    routes = create_workspace_admin_http_routes(object())  # type: ignore[arg-type]
    operations: list[dict[str, Any]] = []
    for route in routes:
        methods = sorted(set(route.methods or ()) - {"HEAD", "OPTIONS"})
        input_model = _admin_input_model(route.endpoint)
        route_owned_fields = _admin_route_owned_fields(route.endpoint)
        input_schema = _without_schema_fields(
            _model_schema(input_model),
            route_owned_fields,
        )
        for method in methods:
            operations.append(
                {
                    "operation_id": route.name or route.endpoint.__name__,
                    "method": method,
                    "path": route.path,
                    "handler": route.endpoint.__name__,
                    "input_model": input_model.__name__ if input_model is not None else None,
                    "input_schema": input_schema,
                    "route_owned_fields": route_owned_fields,
                    "response_contract": "ServiceResult JSON envelope; endpoint-specific value view",
                    "schema_status": _admin_schema_status(input_model, input_schema),
                }
            )
    operations.sort(key=lambda item: (item["path"], item["method"], item["operation_id"]))
    return {
        "catalog_version": CATALOG_VERSION,
        "surface": "admin",
        "source_of_truth": [
            "create_workspace_admin_http_routes() Starlette Route objects",
            "Pydantic Input models referenced by each endpoint handler",
        ],
        "coverage_notes": [
            "All registered paths and methods are exported.",
            "Typed JSON bodies are exported when the endpoint directly references a Pydantic *Input model.",
            "Fields owned by repo/release path routing are removed from request-body schemas and listed separately as route_owned_fields.",
            "Raw-body and query parameter contracts remain documented by the handwritten manual and endpoint implementation.",
            "Response value schemas are not yet declared in Admin route metadata.",
        ],
        "operation_count": len(operations),
        "operations": operations,
    }


def build_agent_tools_catalog() -> dict[str, Any]:
    application_tools = build_application_tool_specs()
    submit_tools = build_submit_tool_specs()
    application_groups = build_application_tool_groups(application_tools)
    submit_groups = build_submit_tool_groups(submit_tools)
    application_views = build_application_tool_views(application_groups)
    submit_views = build_submit_tool_views(submit_groups)

    tools = [
        _tool_record("application", spec) for spec in application_tools
    ] + [_tool_record("submit", spec) for spec in submit_tools]
    groups = [
        {"layer": "application", **group.model_dump(mode="json")}
        for group in application_groups
    ] + [
        {"layer": "submit", **group.model_dump(mode="json")}
        for group in submit_groups
    ]
    views = _resolved_tool_views("application", application_views, application_groups) + _resolved_tool_views(
        "submit", submit_views, submit_groups
    )
    tools.sort(key=lambda item: (item["layer"], item["name"]))
    groups.sort(key=lambda item: (item["layer"], item["key"]))
    views.sort(key=lambda item: (item["layer"], item["key"]))
    return {
        "catalog_version": CATALOG_VERSION,
        "surface": "agent-tools",
        "source_of_truth": [
            "build_application_tool_specs/groups/views()",
            "build_submit_tool_specs/groups/views()",
            "ToolSpec.args_model Pydantic schemas",
        ],
        "coverage_notes": [
            "Application and submit tools, groups, roles, required context, and resolved ToolViews are complete.",
            "result_view is the registered semantic view name; the registry does not currently carry a Pydantic response model.",
            "Dynamically discovered external toolkit proxy tools are outside this static application catalog.",
        ],
        "tool_count": len(tools),
        "group_count": len(groups),
        "view_count": len(views),
        "tools": tools,
        "groups": groups,
        "views": views,
    }


def export_interface_docs(
    output_dir: Path,
    *,
    surfaces: Sequence[str] = SURFACES,
    formats: Sequence[str] = ("json", "markdown"),
) -> list[Path]:
    """Export selected catalogs without timestamps so repeated runs are stable."""

    unknown_surfaces = sorted(set(surfaces) - set(SURFACES))
    if unknown_surfaces:
        raise ValueError(f"Unknown interface surfaces: {', '.join(unknown_surfaces)}")
    unknown_formats = sorted(set(formats) - {"json", "markdown"})
    if unknown_formats:
        raise ValueError(f"Unknown interface formats: {', '.join(unknown_formats)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for surface in surfaces:
        catalog = build_interface_catalog(surface)
        stem = surface.replace("-", "_") + "_reference"
        if "json" in formats:
            path = output_dir / f"{stem}.json"
            path.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            written.append(path)
        if "markdown" in formats:
            path = output_dir / f"{stem}.md"
            path.write_text(render_catalog_markdown(catalog), encoding="utf-8")
            written.append(path)
    return written


def render_catalog_markdown(catalog: dict[str, Any]) -> str:
    surface = str(catalog["surface"])
    if surface == "agent-tools":
        return _render_agent_tools_markdown(catalog)
    return _render_http_markdown(catalog)


def _operator_operation(
    *,
    category: str,
    method: str,
    path: str,
    operation: str,
    owner: type,
    input_model: type[BaseModel] | None,
) -> dict[str, Any]:
    function = getattr(owner, operation)
    hints = get_type_hints(function)
    return_annotation = hints.get("return")
    return {
        "category": category,
        "operation_id": operation,
        "method": method,
        "path": path,
        "input_model": input_model.__name__ if input_model is not None else None,
        "input_schema": _model_schema(input_model),
        "response_annotation": _type_name(return_annotation),
        "response_contract": "OperatorResult JSON envelope with path-free public issue vocabulary",
    }


def _request_model(owner: type, method_name: str) -> type[BaseModel] | None:
    hints = get_type_hints(getattr(owner, method_name))
    model = hints.get("request")
    return model if inspect.isclass(model) and issubclass(model, BaseModel) else None


def _admin_input_model(endpoint: Any) -> type[BaseModel] | None:
    for name in endpoint.__code__.co_names:
        value = endpoint.__globals__.get(name)
        if (
            name.endswith("Input")
            and inspect.isclass(value)
            and issubclass(value, BaseModel)
        ):
            return value
    return None


def _admin_route_owned_fields(endpoint: Any) -> list[str]:
    names = set(endpoint.__code__.co_names)
    if "_repo_path_model_route" in names:
        return ["release_id", "repo_key", "repo_root"]
    if names.intersection(
        {
            "_repo_lifecycle_model_route",
            "_repo_semantic_model_route",
            "_repo_root_semantic_model_route",
        }
    ):
        return ["repo_key", "repo_root"]
    return []


def _without_schema_fields(
    schema: dict[str, Any] | None,
    field_names: Sequence[str],
) -> dict[str, Any] | None:
    if schema is None or not field_names:
        return schema
    filtered = deepcopy(schema)
    properties = filtered.get("properties")
    if isinstance(properties, dict):
        for field_name in field_names:
            properties.pop(field_name, None)
    required = filtered.get("required")
    if isinstance(required, list):
        remaining = [field_name for field_name in required if field_name not in field_names]
        if remaining:
            filtered["required"] = remaining
        else:
            filtered.pop("required", None)
    return filtered


def _admin_schema_status(
    input_model: type[BaseModel] | None,
    input_schema: dict[str, Any] | None,
) -> str:
    if input_model is None:
        return "route_only"
    if input_schema is not None and not input_schema.get("properties"):
        return "route_only"
    return "typed_body"


def _model_schema(model: type[BaseModel] | None) -> dict[str, Any] | None:
    return None if model is None else model.model_json_schema()


def _type_name(annotation: Any) -> str | None:
    if annotation is None:
        return None
    return str(annotation).replace("<class '", "").replace("'>", "")


def _tool_record(layer: str, spec: Any) -> dict[str, Any]:
    return {
        "layer": layer,
        "name": spec.name,
        "description": spec.description,
        "capability": spec.capability.value,
        "args_model": spec.args_model.__name__,
        "args_schema": spec.args_model.model_json_schema(),
        "result_view": spec.result_view,
        "required_context": sorted(spec.required_context),
        "tool_groups": sorted(spec.tool_groups),
        "allowed_roles": sorted(str(role) for role in spec.allowed_roles),
        "submit_behavior": spec.submit_behavior.value,
        "backing_service": spec.backing_service,
        "backing_component": spec.backing_component,
        "backing_method": spec.backing_method,
    }


def _resolved_tool_views(layer: str, views: Iterable[Any], groups: Iterable[Any]) -> list[dict[str, Any]]:
    group_map = {group.key: group for group in groups}
    records: list[dict[str, Any]] = []
    for view in views:
        tool_names = set(view.extra_tool_names)
        for group_key in view.group_keys:
            group = group_map.get(group_key)
            if group is not None:
                tool_names.update(group.tool_names)
        records.append(
            {
                "layer": layer,
                **view.model_dump(mode="json"),
                "resolved_tool_names": sorted(tool_names),
            }
        )
    return records


def _render_http_markdown(catalog: dict[str, Any]) -> str:
    title = "Operator 数据 API 自动参考" if catalog["surface"] == "operator" else "Admin API 自动参考"
    lines = [
        f"# {title}",
        "",
        "> 本文件由当前代码注册表自动生成，请勿手工编辑。人工语义与运行流程见同目录对应手册。",
        "",
        f"- Catalog version：`{catalog['catalog_version']}`",
        f"- Operation count：`{catalog['operation_count']}`",
        "",
        "## 覆盖边界",
        "",
    ]
    lines.extend(f"- {note}" for note in catalog["coverage_notes"])
    lines.extend(["", "## 路由总表", "", "| Method | Path | Operation | Input | Schema |", "| --- | --- | --- | --- | --- |"])
    for item in catalog["operations"]:
        lines.append(
            f"| `{item['method']}` | `{item['path']}` | `{item['operation_id']}` | "
            f"`{item.get('input_model') or 'none/raw'}` | `{item.get('schema_status', 'typed')}` |"
        )
    for item in catalog["operations"]:
        lines.extend(
            [
                "",
                f"## `{item['method']} {item['path']}`",
                "",
                f"- Operation：`{item['operation_id']}`",
                f"- Input model：`{item.get('input_model') or 'none/raw; see handwritten manual'}`",
                f"- Response：{item['response_contract']}",
            ]
        )
        route_owned_fields = item.get("route_owned_fields") or []
        if route_owned_fields:
            lines.append(
                "- Route-owned fields（不得放入 body）："
                + ", ".join(f"`{field_name}`" for field_name in route_owned_fields)
            )
        lines.extend(_schema_markdown(item.get("input_schema")))
    return "\n".join(lines).rstrip() + "\n"


def _render_agent_tools_markdown(catalog: dict[str, Any]) -> str:
    lines = [
        "# Agent Tool/View 自动参考",
        "",
        "> 本文件由 ToolSpec、ToolGroupSpec 与 ToolViewSpec 注册表自动生成，请勿手工编辑。",
        "",
        f"- Tools：`{catalog['tool_count']}`",
        f"- Groups：`{catalog['group_count']}`",
        f"- Views：`{catalog['view_count']}`",
        "",
        "## 覆盖边界",
        "",
    ]
    lines.extend(f"- {note}" for note in catalog["coverage_notes"])
    lines.extend(["", "## ToolView 总表", "", "| Layer | View | Agent types | Groups | Resolved tools |", "| --- | --- | --- | ---: | ---: |"])
    for view in catalog["views"]:
        lines.append(
            f"| `{view['layer']}` | `{view['key']}` | {', '.join(view['allowed_agent_types'])} | "
            f"{len(view['group_keys'])} | {len(view['resolved_tool_names'])} |"
        )
    lines.extend(["", "## Tool 总表", "", "| Layer | Tool | Capability | Roles | Result view |", "| --- | --- | --- | --- | --- |"])
    for tool in catalog["tools"]:
        lines.append(
            f"| `{tool['layer']}` | `{tool['name']}` | `{tool['capability']}` | "
            f"{', '.join(tool['allowed_roles'])} | `{tool['result_view']}` |"
        )
    for tool in catalog["tools"]:
        lines.extend(
            [
                "",
                f"## `{tool['name']}`",
                "",
                tool["description"],
                "",
                f"- Layer / capability：`{tool['layer']}` / `{tool['capability']}`",
                f"- Roles：{', '.join(f'`{role}`' for role in tool['allowed_roles'])}",
                f"- Required context：{', '.join(f'`{value}`' for value in tool['required_context']) or 'none'}",
                f"- Groups：{', '.join(f'`{value}`' for value in tool['tool_groups'])}",
                f"- Result view：`{tool['result_view']}`",
                f"- Submit behavior：`{tool['submit_behavior']}`",
            ]
        )
        lines.extend(_schema_markdown(tool["args_schema"]))
    lines.extend(["", "## ToolView 展开", ""])
    for view in catalog["views"]:
        lines.extend(
            [
                f"### `{view['key']}`",
                "",
                f"- Layer：`{view['layer']}`",
                f"- Agent types：{', '.join(f'`{value}`' for value in view['allowed_agent_types'])}",
                f"- Groups：{', '.join(f'`{value}`' for value in view['group_keys'])}",
                f"- Resolved tools：{', '.join(f'`{value}`' for value in view['resolved_tool_names'])}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _schema_markdown(schema: dict[str, Any] | None) -> list[str]:
    if schema is None:
        return ["", "Request body：无 typed schema；请查阅人工手册或 endpoint 实现。"]
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    lines = ["", "### Request fields", ""]
    if not properties:
        return [*lines, "无字段（空 JSON 对象）。"]
    lines.extend(["| Field | Required | Type | Default / description |", "| --- | --- | --- | --- |"])
    for name, field_schema in properties.items():
        description = field_schema.get("description") or ""
        if "default" in field_schema:
            description = f"default={field_schema['default']!r}; {description}".strip()
        description = str(description).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{name}` | {'yes' if name in required else 'no'} | `{_schema_type(field_schema)}` | {description} |"
        )
    return lines


def _schema_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if "anyOf" in schema:
        return " | ".join(_schema_type(item) for item in schema["anyOf"])
    if "oneOf" in schema:
        return " | ".join(_schema_type(item) for item in schema["oneOf"])
    if schema.get("type") == "array":
        return f"array[{_schema_type(schema.get('items', {}))}]"
    if "enum" in schema:
        return "enum(" + ", ".join(map(str, schema["enum"])) + ")"
    return str(schema.get("type") or "object")


__all__ = [
    "CATALOG_VERSION",
    "SURFACES",
    "build_admin_catalog",
    "build_agent_tools_catalog",
    "build_interface_catalog",
    "build_operator_catalog",
    "export_interface_docs",
    "render_catalog_markdown",
]
