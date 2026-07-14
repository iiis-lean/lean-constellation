from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, get_args

from lean_constellation.app.operator_data import OperatorDataApi
from lean_constellation.app.operator_data.common import (
    FORBIDDEN_OPERATOR_INPUT_FIELDS,
    OperatorInputModel,
    OperatorIssueView,
)
from lean_constellation.app.operator_data import decl_projection, node, release, repo_material
from lean_constellation.domain.common import StrictModel
from lean_constellation.tools import build_application_tool_specs


def _input_models() -> list[type[OperatorInputModel]]:
    models: set[type[OperatorInputModel]] = set()
    for module in (repo_material, node, decl_projection, release):
        for _, value in inspect.getmembers(module, inspect.isclass):
            if value is not OperatorInputModel and issubclass(value, OperatorInputModel):
                models.add(value)
    return sorted(models, key=lambda model: model.__name__)


def _contains_any(annotation: object) -> bool:
    return annotation is Any or any(_contains_any(argument) for argument in get_args(annotation))


def test_operator_business_inputs_are_strict_typed_and_identity_free() -> None:
    models = _input_models()
    assert len(models) >= 45
    for model in models:
        assert model.model_config.get("extra") == "forbid"
        assert not FORBIDDEN_OPERATOR_INPUT_FIELDS.intersection(model.model_fields)
        for field in model.model_fields.values():
            assert not _contains_any(field.annotation), f"{model.__name__} contains Any"


def test_operator_public_output_models_are_strict_and_storage_identity_free() -> None:
    forbidden = {
        "repo_root",
        "workspace_root",
        "file_path",
        "resource_root",
        "draft_root",
        "graph_root",
        "index_path",
        "archive_path",
        "lakefile_path",
        "metadata_path",
        "manifest_path",
        "baseline_locator",
    }
    output_models: set[type[StrictModel]] = set()
    for module in (repo_material, node, decl_projection, release):
        for _, value in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(value, StrictModel)
                and not issubclass(value, OperatorInputModel)
                and value.__module__ == module.__name__
                and value.__name__.endswith("View")
            ):
                output_models.add(value)

    assert len(output_models) >= 30
    for model in output_models:
        assert model.model_config.get("extra") == "forbid"
        assert not forbidden.intersection(model.model_fields), model.__name__
        for field in model.model_fields.values():
            assert not _contains_any(field.annotation), f"{model.__name__} contains Any"

    assert set(OperatorIssueView.model_fields) == {"kind", "message", "severity"}


def test_operator_aggregate_public_methods_do_not_accept_runtime_identity_or_dispatch() -> None:
    forbidden = {
        "repo_root",
        "flow_id",
        "step_id",
        "agent_id",
        "scope_id",
        "ark_runtime_snapshot_id",
        "service_name",
        "method_name",
        "lock_policy",
    }
    for name, method in inspect.getmembers(OperatorDataApi, inspect.isfunction):
        if name.startswith("_"):
            continue
        parameters = set(inspect.signature(method).parameters)
        assert not forbidden.intersection(parameters)


def test_operator_package_has_no_truth_writer_or_agent_surface_registration() -> None:
    root = Path(repo_material.__file__).parent
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))

    assert "write_json(" not in text
    assert "json.dump(" not in text
    assert ".write_text(" not in text
    assert ".write_bytes(" not in text
    assert "copytree(" not in text
    assert "register_application_tooling" not in text
    assert "ToolFacade" not in text
    assert "ToolSpec" not in text
    assert "submission_gateway" not in text

    tool_names = [spec.name for spec in build_application_tool_specs()]
    assert not any(name.startswith("operator_") for name in tool_names)
