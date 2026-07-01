from __future__ import annotations

import inspect

from lean_constellation.services import create_test_runtime_services
from lean_constellation.services.tool_facade import SubmitBehavior
from lean_constellation.tools import (
    build_application_tool_groups,
    build_application_tool_specs,
    build_application_tool_views,
    register_application_tooling,
)


def _missing_schema_descriptions(schema: dict, *, path: str) -> list[str]:
    missing: list[str] = []
    if schema.get("type") == "object":
        for field_name, field_schema in schema.get("properties", {}).items():
            if "$ref" not in field_schema and "description" not in field_schema:
                missing.append(f"{path}.{field_name}")
            missing.extend(_missing_schema_descriptions(field_schema, path=f"{path}.{field_name}"))
    for defs_key in ("$defs", "definitions"):
        for name, nested_schema in schema.get(defs_key, {}).items():
            missing.extend(_missing_schema_descriptions(nested_schema, path=f"{path}.{defs_key}.{name}"))
    for union_key in ("anyOf", "oneOf", "allOf"):
        for nested_schema in schema.get(union_key, []):
            missing.extend(_missing_schema_descriptions(nested_schema, path=path))
    if "items" in schema:
        missing.extend(_missing_schema_descriptions(schema["items"], path=f"{path}[]"))
    return missing


def test_direct_tool_backing_methods_exist_on_runtime() -> None:
    runtime = create_test_runtime_services()

    for spec in build_application_tool_specs():
        if spec.backing_handler is not None or spec.toolkit_proxy_name:
            continue
        service = getattr(runtime, spec.backing_service)
        target = getattr(service, spec.backing_component) if spec.backing_component else service
        assert hasattr(target, spec.backing_method), f"{spec.name} backing method is missing"


def test_direct_tool_arg_fields_match_backing_signatures() -> None:
    runtime = create_test_runtime_services()

    for spec in build_application_tool_specs():
        if spec.backing_handler is not None or spec.toolkit_proxy_name:
            continue
        service = getattr(runtime, spec.backing_service)
        target = getattr(service, spec.backing_component) if spec.backing_component else service
        method = getattr(target, spec.backing_method)
        signature = inspect.signature(method)
        accepts_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values())
        if accepts_kwargs:
            continue
        keyword_params = {
            name
            for name, parameter in signature.parameters.items()
            if parameter.kind
            in {
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        }
        # ToolFacade injects repo_root/current root as the first positional
        # argument for repo-bound tools, so args_model fields must match only
        # the remaining service keyword parameters.
        keyword_params.discard("repo_root")
        keyword_params.discard("current_repo_root")
        keyword_params.discard("workspace_root")
        fields = set(spec.args_model.model_fields)
        missing = fields - keyword_params
        assert not missing, f"{spec.name} fields not accepted by backing method: {sorted(missing)}"


def test_application_tool_specs_are_unique_non_submit_and_schema_described() -> None:
    specs = build_application_tool_specs()

    names = [spec.name for spec in specs]
    assert len(names) == len(set(names))
    assert specs
    assert all(spec.submit_behavior == SubmitBehavior.NONE for spec in specs)
    assert not any(spec.name.startswith("submit_") for spec in specs)
    assert all(spec.tool_groups for spec in specs)
    assert all(spec.allowed_roles for spec in specs)

    for spec in specs:
        missing = _missing_schema_descriptions(spec.args_model.model_json_schema(), path=spec.name)
        assert not missing, f"{spec.name} has undocumented schema fields: {missing}"


def test_application_read_groups_do_not_expose_write_tools() -> None:
    specs = {spec.name: spec for spec in build_application_tool_specs()}
    groups = build_application_tool_groups(list(specs.values()))

    violations: dict[str, list[str]] = {}
    for group in groups:
        if "_read" not in group.key:
            continue
        write_tools = [tool_name for tool_name in group.tool_names if specs[tool_name].capability.value == "write"]
        if write_tools:
            violations[group.key] = sorted(write_tools)

    assert not violations


def test_application_tool_groups_do_not_reverse_bind_skills() -> None:
    specs = build_application_tool_specs()
    groups = build_application_tool_groups(specs)

    assert groups
    assert all(group.skill_keys == [] for group in groups)


def test_arxiv_theorem_search_has_dedicated_agent_schema() -> None:
    spec = next(spec for spec in build_application_tool_specs() if spec.name == "search_arxiv_theorems")
    schema = spec.args_model.model_json_schema()

    assert spec.args_model.__name__ == "ArxivTheoremSearchArgs"
    assert "arXiv theorem-like statements" in spec.description
    assert "Mathlib semantic search" not in schema["properties"]["query"]["description"]
    assert "arXiv ids" in schema["properties"]["query"]["description"]
    assert "theorem-like arXiv candidates" in schema["properties"]["limit"]["description"]


def test_application_tooling_registers_on_real_tool_facade() -> None:
    runtime = create_test_runtime_services()

    registered = register_application_tooling(runtime)

    assert registered.ok
    listed = runtime.tool_facade.list_registered_tools()
    assert listed.ok
    assert listed.value is not None
    assert len(listed.value) == len(build_application_tool_specs())


def test_factory_can_register_application_tools_explicitly() -> None:
    runtime = create_test_runtime_services(register_application_tools=True)

    listed = runtime.tool_facade.list_registered_tools()

    assert listed.ok
    assert listed.value is not None
    assert len(listed.value) == len(build_application_tool_specs())


def test_registry_builds_groups_and_views_from_specs() -> None:
    specs = build_application_tool_specs()
    groups = build_application_tool_groups(specs)
    views = build_application_tool_views(groups)

    tool_names = {spec.name for spec in specs}
    group_keys = {group.key for group in groups}
    assert groups
    assert views
    for group in groups:
        assert set(group.tool_names) <= tool_names
    for view in views:
        assert set(view.group_keys) <= group_keys
