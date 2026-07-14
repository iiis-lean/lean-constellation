"""Thin Starlette route mapping for the typed Operator Data API."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Literal

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from lean_constellation.app.operator_data.api import OperatorDataApi
from lean_constellation.app.operator_data.common import OperatorInputModel
from lean_constellation.app.operator_data.decl_projection_http import DECL_PROJECTION_ROUTES
from lean_constellation.app.operator_data.http_support import (
    parse_operator_body,
    service_result_json,
    validation_error_json,
)
from lean_constellation.app.operator_data.node_http import NODE_HTTP_ROUTE_NAMES, NodeHttpHandlers
from lean_constellation.app.operator_data.repo_material_http import REPO_MATERIAL_HTTP_ROUTES
from lean_constellation.app.operator_data.release_http import RELEASE_HTTP_ROUTES
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry


@dataclass(frozen=True, slots=True)
class _NodeRoute:
    method: Literal["GET", "POST", "PATCH", "DELETE"]
    path: str
    handler_name: str


_NODE_ROUTES = (
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/get", "get_node"),
    _NodeRoute("GET", "/admin/operator/repos/{repo_key}/nodes", "list_nodes"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/children", "list_children"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/contracts/get", "get_contract"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/public-boundary", "get_public_boundary"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/interfaces/list", "list_interfaces"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/exports/candidates", "list_scope_export_candidates"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/exports/list", "list_scope_exports"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/mathlib/list", "list_mathlib_uses"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/scopes", "create_scope_node"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/content", "create_content_node"),
    _NodeRoute("PATCH", "/admin/operator/repos/{repo_key}/nodes/contracts", "update_contract_text"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/contracts/commit-scope", "commit_scope_contract"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/contracts/commit-content", "commit_content_contract"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/dependencies", "add_node_dep"),
    _NodeRoute("DELETE", "/admin/operator/repos/{repo_key}/nodes/dependencies", "remove_node_dep"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/material-refs", "add_material_ref"),
    _NodeRoute("DELETE", "/admin/operator/repos/{repo_key}/nodes/material-refs", "remove_material_ref"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/mathlib/modules", "add_mathlib_module"),
    _NodeRoute("DELETE", "/admin/operator/repos/{repo_key}/nodes/mathlib/modules", "remove_mathlib_module"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/mathlib/declarations", "add_mathlib_decl"),
    _NodeRoute("DELETE", "/admin/operator/repos/{repo_key}/nodes/mathlib/declarations", "remove_mathlib_decl"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/interfaces", "add_interface"),
    _NodeRoute("PATCH", "/admin/operator/repos/{repo_key}/nodes/interfaces", "update_interface"),
    _NodeRoute("DELETE", "/admin/operator/repos/{repo_key}/nodes/interfaces", "remove_interface"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/interfaces/bind", "bind_interface"),
    _NodeRoute("DELETE", "/admin/operator/repos/{repo_key}/nodes/interfaces/bind", "unbind_interface"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/interfaces/sync-root", "sync_root_interfaces"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/exports", "add_scope_export"),
    _NodeRoute("DELETE", "/admin/operator/repos/{repo_key}/nodes/exports", "remove_scope_export"),
    _NodeRoute("POST", "/admin/operator/repos/{repo_key}/nodes/delete-preview", "preview_delete_node"),
    _NodeRoute("DELETE", "/admin/operator/repos/{repo_key}/nodes", "delete_node"),
)

if tuple(route.handler_name for route in _NODE_ROUTES) != NODE_HTTP_ROUTE_NAMES:
    raise RuntimeError("Node Operator HTTP route mapping is incomplete or out of order.")


def create_operator_data_http_routes(
    registry: RepoRuntimeRegistry,
    *,
    api: OperatorDataApi | None = None,
) -> list[Route]:
    """Create fixed routes; all admission and business gates remain in the API."""

    operator_api = api or OperatorDataApi(registry)
    node_handlers = NodeHttpHandlers(operator_api.node)
    routes: list[Route] = []

    async def prepare_management(request: Request) -> JSONResponse:
        rejected = await _reject_query_or_nonempty_body(request)
        if rejected is not None:
            return rejected
        return _result_response(
            service_result_json(
                operator_api.prepare_repo_management(request.path_params["repo_key"])
            )
        )

    routes.append(
        Route(
            "/admin/operator/repos/{repo_key}/management/prepare",
            prepare_management,
            methods=["POST"],
            name="operator_prepare_repo_management",
        )
    )

    for declaration in REPO_MATERIAL_HTTP_ROUTES:
        routes.append(
            Route(
                declaration.path,
                _repo_material_endpoint(operator_api, declaration),
                methods=[declaration.method],
                name=f"operator_repo_material_{declaration.api_method}",
            )
        )
    for declaration in _NODE_ROUTES:
        routes.append(
            Route(
                declaration.path,
                _node_endpoint(node_handlers, declaration.handler_name),
                methods=[declaration.method],
                name=f"operator_node_{declaration.handler_name}",
            )
        )
    for declaration in DECL_PROJECTION_ROUTES:
        path = declaration.path
        if not path.startswith("/admin/operator"):
            path = f"/admin/operator{path}"
        routes.append(
            Route(
                path,
                _decl_endpoint(operator_api, declaration.handler_name, declaration.input_model),
                methods=[declaration.method],
                name=f"operator_decl_projection_{declaration.handler_name}",
            )
        )
    for declaration in RELEASE_HTTP_ROUTES:
        routes.append(
            Route(
                declaration.path,
                _release_endpoint(operator_api, declaration.api_method, declaration.input_model),
                methods=[declaration.method],
                name=f"operator_release_{declaration.api_method}",
            )
        )
    return routes


def _repo_material_endpoint(api: OperatorDataApi, declaration):  # noqa: ANN001, ANN202
    async def endpoint(request: Request) -> JSONResponse:
        if request.query_params:
            return _validation_response(ValueError("Operator routes do not accept query parameters."))
        try:
            body = await _json_body(request)
            if declaration.input_model is None:
                if body != {}:
                    raise ValueError("This operator route does not accept a request body.")
                input_model = None
            else:
                input_model = parse_operator_body(declaration.input_model, body)
        except (ValueError, TypeError) as exc:
            return _validation_response(exc)
        if input_model is None:
            result = getattr(api.repo_material, declaration.api_method)(request.path_params["repo_key"])
        elif declaration.api_method == "create_native_repo":
            result = api.create_native_repo(request.path_params["repo_key"], input_model)
        else:
            result = getattr(api.repo_material, declaration.api_method)(
                request.path_params["repo_key"], input_model
            )
        return _result_response(service_result_json(result))

    return endpoint


def _node_endpoint(handlers: NodeHttpHandlers, handler_name: str):  # noqa: ANN202
    async def endpoint(request: Request) -> JSONResponse:
        if request.query_params:
            return _validation_response(ValueError("Operator routes do not accept query parameters."))
        try:
            body = await _json_body(request)
        except (ValueError, TypeError) as exc:
            return _validation_response(exc)
        envelope = getattr(handlers, handler_name)(request.path_params["repo_key"], body)
        return _result_response(envelope)

    return endpoint


def _decl_endpoint(api: OperatorDataApi, handler_name: str, input_model: type[OperatorInputModel]):  # noqa: ANN202
    async def endpoint(request: Request) -> JSONResponse:
        if request.query_params:
            return _validation_response(ValueError("Operator routes do not accept query parameters."))
        try:
            body = await _json_body(request)
            parsed = parse_operator_body(input_model, body)
        except (ValueError, TypeError) as exc:
            return _validation_response(exc)
        result = getattr(api.decl_projection, handler_name)(request.path_params["repo_key"], parsed)
        return _result_response(service_result_json(result))

    return endpoint


def _release_endpoint(
    api: OperatorDataApi,
    handler_name: str,
    input_model: type[OperatorInputModel] | None,
):  # noqa: ANN202
    async def endpoint(request: Request) -> JSONResponse:
        if request.query_params:
            return _validation_response(ValueError("Operator routes do not accept query parameters."))
        try:
            body = await _json_body(request)
            if input_model is None:
                if body != {}:
                    raise ValueError("This operator route does not accept a request body.")
                parsed = None
            else:
                parsed = parse_operator_body(input_model, body)
        except (ValueError, TypeError) as exc:
            return _validation_response(exc)
        method = getattr(api.release_checkpoint, handler_name)
        if parsed is None:
            result = method(request.path_params["repo_key"])
        else:
            result = method(request.path_params["repo_key"], parsed)
        return _result_response(service_result_json(result))

    return endpoint


async def _json_body(request: Request) -> object:
    raw = await request.body()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Operator request body must be valid JSON.") from exc


async def _reject_query_or_nonempty_body(request: Request) -> JSONResponse | None:
    if request.query_params:
        return _validation_response(ValueError("Operator routes do not accept query parameters."))
    try:
        body = await _json_body(request)
    except ValueError as exc:
        return _validation_response(exc)
    if body != {}:
        return _validation_response(ValueError("This operator route does not accept a request body."))
    return None


def _validation_response(exc: ValueError | TypeError) -> JSONResponse:
    value_error = exc if isinstance(exc, ValueError) else ValueError(str(exc))
    return JSONResponse(validation_error_json(value_error), status_code=422)


def _result_response(envelope: dict[str, object]) -> JSONResponse:
    if envelope.get("ok") is True:
        status = 200
    else:
        issues = envelope.get("issues")
        first = issues[0] if isinstance(issues, list) and issues else {}
        status = 422 if isinstance(first, dict) and first.get("kind") == "operator_request_validation_failed" else 400
    return JSONResponse(envelope, status_code=status)


__all__ = ["create_operator_data_http_routes"]
