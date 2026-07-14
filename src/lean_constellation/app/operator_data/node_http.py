"""HTTP-boundary handlers for the typed Node Operator facade.

This module declares handlers only. Production route registration belongs to
the aggregate Operator HTTP composition layer.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from lean_constellation.app.operator_data.http_support import (
    parse_operator_body,
    service_result_json,
    validation_error_json,
)
from lean_constellation.app.operator_data.node import (
    AddInterfaceInput,
    AddMaterialRefInput,
    AddNodeDepInput,
    AddScopeExportInput,
    BindInterfaceInput,
    CommitContractInput,
    CreateContentNodeInput,
    CreateScopeNodeInput,
    DeleteNodeInput,
    MathlibDeclMutationInput,
    MathlibModuleMutationInput,
    NodeOperatorApi,
    NodePathInput,
    RemoveIndexedInput,
    RemoveInterfaceInput,
    RemoveMaterialRefInput,
    RemoveScopeExportInput,
    ScopePathInput,
    SyncRootInterfacesInput,
    UnbindInterfaceInput,
    UpdateContractTextInput,
    UpdateInterfaceInput,
)
from lean_constellation.app.operator_data.common import OperatorInputModel
from lean_constellation.services.foundation import ServiceResult


TInput = TypeVar("TInput", bound=OperatorInputModel)


NODE_HTTP_ROUTE_NAMES = (
    "get_node",
    "list_nodes",
    "list_children",
    "get_contract",
    "get_public_boundary",
    "list_interfaces",
    "list_scope_export_candidates",
    "list_scope_exports",
    "list_mathlib_uses",
    "create_scope_node",
    "create_content_node",
    "update_contract_text",
    "commit_scope_contract",
    "commit_content_contract",
    "add_node_dep",
    "remove_node_dep",
    "add_material_ref",
    "remove_material_ref",
    "add_mathlib_module",
    "remove_mathlib_module",
    "add_mathlib_decl",
    "remove_mathlib_decl",
    "add_interface",
    "update_interface",
    "remove_interface",
    "bind_interface",
    "unbind_interface",
    "sync_root_interfaces",
    "add_scope_export",
    "remove_scope_export",
    "preview_delete_node",
    "delete_node",
)


class NodeHttpHandlers:
    """Strict body parsing and ServiceResult serialization; no domain gates."""

    def __init__(self, api: NodeOperatorApi) -> None:
        self.api = api

    def get_node(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(NodePathInput, body, lambda request: self.api.get_node(repo_key, request))

    def list_nodes(self, repo_key: str, body: object) -> dict[str, Any]:
        if body != {}:
            return validation_error_json(ValueError("list_nodes request body must be empty."))
        return service_result_json(self.api.list_nodes(repo_key))

    def list_children(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(ScopePathInput, body, lambda request: self.api.list_children(repo_key, request))

    def get_contract(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(NodePathInput, body, lambda request: self.api.get_contract(repo_key, request))

    def get_public_boundary(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(NodePathInput, body, lambda request: self.api.get_public_boundary(repo_key, request))

    def list_interfaces(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(NodePathInput, body, lambda request: self.api.list_interfaces(repo_key, request))

    def list_scope_export_candidates(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(ScopePathInput, body, lambda request: self.api.list_scope_export_candidates(repo_key, request))

    def list_scope_exports(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(ScopePathInput, body, lambda request: self.api.list_scope_exports(repo_key, request))

    def list_mathlib_uses(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(NodePathInput, body, lambda request: self.api.list_mathlib_uses(repo_key, request))

    def create_scope_node(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(CreateScopeNodeInput, body, lambda request: self.api.create_scope_node(repo_key, request))

    def create_content_node(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(CreateContentNodeInput, body, lambda request: self.api.create_content_node(repo_key, request))

    def update_contract_text(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(UpdateContractTextInput, body, lambda request: self.api.update_contract_text(repo_key, request))

    def commit_scope_contract(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(CommitContractInput, body, lambda request: self.api.commit_scope_contract(repo_key, request))

    def commit_content_contract(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(CommitContractInput, body, lambda request: self.api.commit_content_contract(repo_key, request))

    def add_node_dep(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(AddNodeDepInput, body, lambda request: self.api.add_node_dep(repo_key, request))

    def remove_node_dep(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(RemoveIndexedInput, body, lambda request: self.api.remove_node_dep(repo_key, request))

    def add_material_ref(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(AddMaterialRefInput, body, lambda request: self.api.add_material_ref(repo_key, request))

    def remove_material_ref(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(RemoveMaterialRefInput, body, lambda request: self.api.remove_material_ref(repo_key, request))

    def add_mathlib_module(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(MathlibModuleMutationInput, body, lambda request: self.api.add_mathlib_module(repo_key, request))

    def remove_mathlib_module(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(MathlibModuleMutationInput, body, lambda request: self.api.remove_mathlib_module(repo_key, request))

    def add_mathlib_decl(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(MathlibDeclMutationInput, body, lambda request: self.api.add_mathlib_decl(repo_key, request))

    def remove_mathlib_decl(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(MathlibDeclMutationInput, body, lambda request: self.api.remove_mathlib_decl(repo_key, request))

    def add_interface(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(AddInterfaceInput, body, lambda request: self.api.add_interface(repo_key, request))

    def update_interface(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(UpdateInterfaceInput, body, lambda request: self.api.update_interface(repo_key, request))

    def remove_interface(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(RemoveInterfaceInput, body, lambda request: self.api.remove_interface(repo_key, request))

    def bind_interface(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(BindInterfaceInput, body, lambda request: self.api.bind_interface(repo_key, request))

    def unbind_interface(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(UnbindInterfaceInput, body, lambda request: self.api.unbind_interface(repo_key, request))

    def sync_root_interfaces(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(SyncRootInterfacesInput, body, lambda request: self.api.sync_root_interfaces(repo_key, request))

    def add_scope_export(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(AddScopeExportInput, body, lambda request: self.api.add_scope_export(repo_key, request))

    def remove_scope_export(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(RemoveScopeExportInput, body, lambda request: self.api.remove_scope_export(repo_key, request))

    def preview_delete_node(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(NodePathInput, body, lambda request: self.api.preview_delete_node(repo_key, request))

    def delete_node(self, repo_key: str, body: object) -> dict[str, Any]:
        return self._call(DeleteNodeInput, body, lambda request: self.api.delete_node(repo_key, request))

    @staticmethod
    def _call(
        model: type[TInput],
        body: object,
        call: Callable[[TInput], ServiceResult[object]],
    ) -> dict[str, Any]:
        try:
            request = parse_operator_body(model, body)
        except (ValueError, TypeError) as exc:
            return validation_error_json(exc if isinstance(exc, ValueError) else ValueError(str(exc)))
        return service_result_json(call(request))


__all__ = ["NODE_HTTP_ROUTE_NAMES", "NodeHttpHandlers"]
