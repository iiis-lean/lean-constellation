"""Typed Operator facade for node, interface, export, and Mathlib truth."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, TypeVar

from pydantic import Field, model_validator

from lean_constellation.app.operator_data.common import (
    OperatorAccess,
    OperatorInputModel,
    OperatorLockPolicy,
    OperatorOperationSpec,
    project_operator_result,
)
from lean_constellation.app.operator_data.execution import (
    OperatorExecutionContext,
    OperatorExecutionService,
)
from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry
from lean_constellation.domain.interface import DeclKind
from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.node import InterfaceActor
from lean_constellation.services.node.contract_fields import (
    MaterialRefActor,
    MathlibUseActor,
    NodeDepActor,
)


T = TypeVar("T")


def _read(name: str, *, preview: bool = False) -> OperatorOperationSpec:
    return OperatorOperationSpec(
        name=f"node.{name}",
        access=OperatorAccess.PREVIEW if preview else OperatorAccess.READ,
        lock_policy=OperatorLockPolicy.NONE,
    )


def _mutation(name: str) -> OperatorOperationSpec:
    return OperatorOperationSpec(
        name=f"node.{name}",
        access=OperatorAccess.MUTATION,
        lock_policy=OperatorLockPolicy.OPERATOR,
        requires_stable_runtime=True,
    )


GET_NODE = _read("get")
LIST_NODES = _read("list")
LIST_CHILDREN = _read("list_children")
GET_CONTRACT = _read("get_contract")
GET_BOUNDARY = _read("get_boundary")
LIST_INTERFACES = _read("list_interfaces")
LIST_EXPORT_CANDIDATES = _read("list_export_candidates")
LIST_EXPORTS = _read("list_exports")
LIST_MATHLIB_USES = _read("list_mathlib_uses")
PREVIEW_DELETE = _read("preview_delete", preview=True)

CREATE_SCOPE = _mutation("create_scope")
CREATE_CONTENT = _mutation("create_content")
UPDATE_CONTRACT = _mutation("update_contract")
COMMIT_SCOPE = _mutation("commit_scope")
COMMIT_CONTENT = _mutation("commit_content")
ADD_DEP = _mutation("add_dep")
REMOVE_DEP = _mutation("remove_dep")
ADD_MATERIAL_REF = _mutation("add_material_ref")
REMOVE_MATERIAL_REF = _mutation("remove_material_ref")
ADD_MATHLIB_MODULE = _mutation("add_mathlib_module")
REMOVE_MATHLIB_MODULE = _mutation("remove_mathlib_module")
ADD_MATHLIB_DECL = _mutation("add_mathlib_decl")
REMOVE_MATHLIB_DECL = _mutation("remove_mathlib_decl")
ADD_INTERFACE = _mutation("add_interface")
UPDATE_INTERFACE = _mutation("update_interface")
REMOVE_INTERFACE = _mutation("remove_interface")
BIND_INTERFACE = _mutation("bind_interface")
UNBIND_INTERFACE = _mutation("unbind_interface")
SYNC_ROOT_INTERFACES = _mutation("sync_root_interfaces")
ADD_SCOPE_EXPORT = _mutation("add_scope_export")
REMOVE_SCOPE_EXPORT = _mutation("remove_scope_export")
DELETE_NODE = _mutation("delete")


class NodePathInput(OperatorInputModel):
    node_path: str


class ScopePathInput(OperatorInputModel):
    scope_path: str


class CreateScopeNodeInput(OperatorInputModel):
    path: str
    goal: str
    boundary: str
    objective: str | None = None
    constraints: str | None = None
    success_criteria: str | None = None
    expected_parent_contract_version: int | None = Field(default=None, ge=1)


class CreateContentNodeInput(OperatorInputModel):
    path: str
    goal: str
    boundary: str
    objective: str
    success_criteria: str
    constraints: str | None = None
    expected_parent_contract_version: int = Field(ge=1)


class ContractMutationInput(OperatorInputModel):
    node_path: str
    expected_contract_version: int = Field(ge=1)


class UpdateContractTextInput(ContractMutationInput):
    goal: str | None = None
    boundary: str | None = None
    objective: str | None = None
    success_criteria: str | None = None
    constraints: str | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "UpdateContractTextInput":
        if all(
            value is None
            for value in (self.goal, self.boundary, self.objective, self.success_criteria, self.constraints)
        ):
            raise ValueError("At least one contract text field is required.")
        return self


class CommitContractInput(ContractMutationInput):
    summary: str


class AddNodeDepInput(ContractMutationInput):
    target_node: str
    reason: str
    expected_public_decl_names: list[str] = Field(default_factory=list)
    target_repo: str | None = None


class RemoveIndexedInput(ContractMutationInput):
    index: int = Field(ge=0)


class AddMaterialRefInput(ContractMutationInput):
    ref_scope: Literal["owned", "context"]
    material_kind: Literal["source", "resource"]
    locator: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    reason: str | None = None


class RemoveMaterialRefInput(RemoveIndexedInput):
    ref_scope: Literal["owned", "context"]


class MathlibModuleMutationInput(ContractMutationInput):
    module: str
    reason: str | None = None


class MathlibDeclMutationInput(ContractMutationInput):
    decl_name: str
    reason: str | None = None


class AddInterfaceInput(ContractMutationInput):
    name: str
    kind: DeclKind
    summary: str
    statement_hint: str | None = None


class UpdateInterfaceInput(ContractMutationInput):
    name: str
    summary: str | None = None
    statement_hint: str | None = None


class RemoveInterfaceInput(ContractMutationInput):
    name: str


class BindInterfaceInput(ContractMutationInput):
    interface_name: str
    decl_name: str
    decl_node: str | None = None


class UnbindInterfaceInput(ContractMutationInput):
    interface_name: str


class SyncRootInterfacesInput(OperatorInputModel):
    expected_contract_version: int = Field(ge=1)


class AddScopeExportInput(OperatorInputModel):
    scope_path: str
    expected_contract_version: int = Field(ge=1)
    decl_node: str
    decl_name: str
    decl_repo: str | None = None
    revision: int = Field(default=1, ge=1)
    bind_interface_name: str | None = None


class RemoveScopeExportInput(OperatorInputModel):
    scope_path: str
    expected_contract_version: int = Field(ge=1)
    index: int = Field(ge=0)


class DeleteNodeInput(OperatorInputModel):
    path: str
    reason: str
    expected_impact_identity: str


class NodeOperatorApi:
    """Registry-bound typed facade. Actor and lock policy are never caller inputs."""

    def __init__(self, registry: RepoRuntimeRegistry) -> None:
        self.execution = OperatorExecutionService(registry)

    def get_node(self, repo_key: str, request: NodePathInput) -> ServiceResult[object]:
        return self._execute(repo_key, GET_NODE, lambda ctx: ctx.runtime.node.node_tree.get_node(ctx.repo_root, path=request.node_path))

    def list_nodes(self, repo_key: str) -> ServiceResult[object]:
        return self._execute(repo_key, LIST_NODES, lambda ctx: ctx.runtime.node.node_tree.get_node_tree(ctx.repo_root))

    def list_children(self, repo_key: str, request: ScopePathInput) -> ServiceResult[object]:
        return self._execute(
            repo_key,
            LIST_CHILDREN,
            lambda ctx: ctx.runtime.node.node_tree.list_children(ctx.repo_root, scope_path=request.scope_path),
        )

    def get_contract(self, repo_key: str, request: NodePathInput) -> ServiceResult[object]:
        return self._execute(
            repo_key,
            GET_CONTRACT,
            lambda ctx: ctx.runtime.node.get_current_contract_view(ctx.repo_root, node_path=request.node_path),
        )

    def get_public_boundary(self, repo_key: str, request: NodePathInput) -> ServiceResult[object]:
        return self._execute(
            repo_key,
            GET_BOUNDARY,
            lambda ctx: ctx.runtime.node.get_node_public_boundary(ctx.repo_root, node_path=request.node_path),
        )

    def list_interfaces(self, repo_key: str, request: NodePathInput) -> ServiceResult[object]:
        return self._execute(
            repo_key,
            LIST_INTERFACES,
            lambda ctx: ctx.runtime.node.interface.list_interfaces(ctx.repo_root, node_path=request.node_path),
        )

    def list_scope_export_candidates(self, repo_key: str, request: ScopePathInput) -> ServiceResult[object]:
        return self._execute(
            repo_key,
            LIST_EXPORT_CANDIDATES,
            lambda ctx: ctx.runtime.node.export.list_scope_export_candidates(ctx.repo_root, scope_path=request.scope_path),
        )

    def list_scope_exports(self, repo_key: str, request: ScopePathInput) -> ServiceResult[object]:
        return self._execute(
            repo_key,
            LIST_EXPORTS,
            lambda ctx: ctx.runtime.node.export.list_scope_exports(ctx.repo_root, scope_path=request.scope_path),
        )

    def list_mathlib_uses(self, repo_key: str, request: NodePathInput) -> ServiceResult[object]:
        return self._execute(
            repo_key,
            LIST_MATHLIB_USES,
            lambda ctx: ctx.runtime.mathlib.get_node_mathlib_hint_view(ctx.repo_root, node_path=request.node_path),
        )

    def create_scope_node(self, repo_key: str, request: CreateScopeNodeInput) -> ServiceResult[object]:
        def action(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            parent = request.path.rpartition(".")[0]
            if parent:
                checked = self._check_contract_version(ctx, parent, request.expected_parent_contract_version)
                if not checked.ok:
                    return checked
            return ctx.runtime.node.create_scope_node(
                ctx.repo_root,
                path=request.path,
                goal=request.goal,
                boundary=request.boundary,
                objective=request.objective,
                constraints=request.constraints,
                success_criteria=request.success_criteria,
            )

        return self._execute(repo_key, CREATE_SCOPE, action)

    def create_content_node(self, repo_key: str, request: CreateContentNodeInput) -> ServiceResult[object]:
        def action(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            parent = request.path.rpartition(".")[0]
            checked = self._check_contract_version(ctx, parent, request.expected_parent_contract_version)
            if not checked.ok:
                return checked
            return ctx.runtime.node.create_content_node(
                ctx.repo_root,
                path=request.path,
                goal=request.goal,
                boundary=request.boundary,
                objective=request.objective,
                success_criteria=request.success_criteria,
                constraints=request.constraints,
            )

        return self._execute(repo_key, CREATE_CONTENT, action)

    def update_contract_text(self, repo_key: str, request: UpdateContractTextInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            UPDATE_CONTRACT,
            request,
            lambda ctx: ctx.runtime.node.contract.update_contract_text_fields(
                ctx.repo_root,
                node_path=request.node_path,
                goal=request.goal,
                boundary=request.boundary,
                objective=request.objective,
                success_criteria=request.success_criteria,
                constraints=request.constraints,
            ),
        )

    def commit_scope_contract(self, repo_key: str, request: CommitContractInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            COMMIT_SCOPE,
            request,
            lambda ctx: ctx.runtime.node.commit_scope_contract(ctx.repo_root, scope_path=request.node_path, summary=request.summary),
        )

    def commit_content_contract(self, repo_key: str, request: CommitContractInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            COMMIT_CONTENT,
            request,
            lambda ctx: ctx.runtime.node.commit_content_contract(ctx.repo_root, node_path=request.node_path, summary=request.summary),
        )

    def add_node_dep(self, repo_key: str, request: AddNodeDepInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            ADD_DEP,
            request,
            lambda ctx: ctx.runtime.node.add_current_node_dep(
                ctx.repo_root,
                node_path=request.node_path,
                target_node=request.target_node,
                reason=request.reason,
                actor=NodeDepActor.OPERATOR,
                expected_public_decl_names=request.expected_public_decl_names,
                target_repo=request.target_repo,
            ),
        )

    def remove_node_dep(self, repo_key: str, request: RemoveIndexedInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            REMOVE_DEP,
            request,
            lambda ctx: ctx.runtime.node.remove_current_node_dep(
                ctx.repo_root, node_path=request.node_path, index=request.index, actor=NodeDepActor.OPERATOR
            ),
        )

    def add_material_ref(self, repo_key: str, request: AddMaterialRefInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            ADD_MATERIAL_REF,
            request,
            lambda ctx: ctx.runtime.node.add_current_material_ref(
                ctx.repo_root,
                node_path=request.node_path,
                ref_scope=request.ref_scope,
                material_kind=request.material_kind,
                locator=request.locator,
                start_line=request.start_line,
                end_line=request.end_line,
                reason=request.reason,
                actor=MaterialRefActor.OPERATOR,
            ),
        )

    def remove_material_ref(self, repo_key: str, request: RemoveMaterialRefInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            REMOVE_MATERIAL_REF,
            request,
            lambda ctx: ctx.runtime.node.remove_current_material_ref(
                ctx.repo_root,
                node_path=request.node_path,
                ref_scope=request.ref_scope,
                index=request.index,
                actor=MaterialRefActor.OPERATOR,
            ),
        )

    def add_mathlib_module(self, repo_key: str, request: MathlibModuleMutationInput) -> ServiceResult[object]:
        return self._mathlib_module_mutation(repo_key, ADD_MATHLIB_MODULE, request, remove=False)

    def remove_mathlib_module(self, repo_key: str, request: MathlibModuleMutationInput) -> ServiceResult[object]:
        return self._mathlib_module_mutation(repo_key, REMOVE_MATHLIB_MODULE, request, remove=True)

    def add_mathlib_decl(self, repo_key: str, request: MathlibDeclMutationInput) -> ServiceResult[object]:
        return self._mathlib_decl_mutation(repo_key, ADD_MATHLIB_DECL, request, remove=False)

    def remove_mathlib_decl(self, repo_key: str, request: MathlibDeclMutationInput) -> ServiceResult[object]:
        return self._mathlib_decl_mutation(repo_key, REMOVE_MATHLIB_DECL, request, remove=True)

    def add_interface(self, repo_key: str, request: AddInterfaceInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            ADD_INTERFACE,
            request,
            lambda ctx: ctx.runtime.node.interface.add_interface(
                ctx.repo_root,
                node_path=request.node_path,
                name=request.name,
                kind=request.kind,
                summary=request.summary,
                statement_hint=request.statement_hint,
                actor=InterfaceActor.SYSTEM,
            ),
        )

    def update_interface(self, repo_key: str, request: UpdateInterfaceInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            UPDATE_INTERFACE,
            request,
            lambda ctx: ctx.runtime.node.interface.update_interface(
                ctx.repo_root,
                node_path=request.node_path,
                name=request.name,
                summary=request.summary,
                statement_hint=request.statement_hint,
                actor=InterfaceActor.SYSTEM,
            ),
        )

    def remove_interface(self, repo_key: str, request: RemoveInterfaceInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            REMOVE_INTERFACE,
            request,
            lambda ctx: ctx.runtime.node.interface.remove_interface(
                ctx.repo_root, node_path=request.node_path, name=request.name, actor=InterfaceActor.SYSTEM
            ),
        )

    def bind_interface(self, repo_key: str, request: BindInterfaceInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            BIND_INTERFACE,
            request,
            lambda ctx: ctx.runtime.node.interface.bind_interface_to_decl(
                ctx.repo_root,
                node_path=request.node_path,
                interface_name=request.interface_name,
                decl_name=request.decl_name,
                decl_node=request.decl_node,
            ),
        )

    def unbind_interface(self, repo_key: str, request: UnbindInterfaceInput) -> ServiceResult[object]:
        return self._contract_mutation(
            repo_key,
            UNBIND_INTERFACE,
            request,
            lambda ctx: ctx.runtime.node.interface.unbind_interface(
                ctx.repo_root, node_path=request.node_path, interface_name=request.interface_name
            ),
        )

    def sync_root_interfaces(self, repo_key: str, request: SyncRootInterfacesInput) -> ServiceResult[object]:
        def action(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            checked = self._check_contract_version(ctx, "Main", request.expected_contract_version)
            if not checked.ok:
                return checked
            return ctx.runtime.node.interface.sync_protected_root_interfaces_from_preparation_input(ctx.repo_root)

        return self._execute(repo_key, SYNC_ROOT_INTERFACES, action)

    def add_scope_export(self, repo_key: str, request: AddScopeExportInput) -> ServiceResult[object]:
        def action(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            checked = self._check_contract_version(ctx, request.scope_path, request.expected_contract_version)
            if not checked.ok:
                return checked
            return ctx.runtime.node.export.add_scope_export(
                ctx.repo_root,
                scope_path=request.scope_path,
                decl_node=request.decl_node,
                decl_name=request.decl_name,
                decl_repo=request.decl_repo,
                revision=request.revision,
                bind_interface_name=request.bind_interface_name,
            )

        return self._execute(repo_key, ADD_SCOPE_EXPORT, action)

    def remove_scope_export(self, repo_key: str, request: RemoveScopeExportInput) -> ServiceResult[object]:
        def action(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            checked = self._check_contract_version(ctx, request.scope_path, request.expected_contract_version)
            if not checked.ok:
                return checked
            return ctx.runtime.node.export.remove_scope_export(ctx.repo_root, scope_path=request.scope_path, index=request.index)

        return self._execute(repo_key, REMOVE_SCOPE_EXPORT, action)

    def preview_delete_node(self, repo_key: str, request: NodePathInput) -> ServiceResult[object]:
        return self._execute(
            repo_key,
            PREVIEW_DELETE,
            lambda ctx: ctx.runtime.node.preview_delete_node(ctx.repo_root, path=request.node_path),
        )

    def delete_node(self, repo_key: str, request: DeleteNodeInput) -> ServiceResult[object]:
        def action(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            first = ctx.runtime.node.preview_delete_node(ctx.repo_root, path=request.path)
            checked = self._check_delete_preview(ctx, request.expected_impact_identity, first)
            if not checked.ok:
                return checked
            stable = self._recheck_runtime(ctx)
            if not stable.ok:
                return stable
            second = ctx.runtime.node.preview_delete_node(ctx.repo_root, path=request.path)
            checked = self._check_delete_preview(ctx, request.expected_impact_identity, second)
            if not checked.ok:
                return checked
            return ctx.runtime.node.mark_node_deleted(ctx.repo_root, path=request.path, reason=request.reason)

        return self._execute(repo_key, DELETE_NODE, action)

    def _mathlib_module_mutation(
        self,
        repo_key: str,
        operation: OperatorOperationSpec,
        request: MathlibModuleMutationInput,
        *,
        remove: bool,
    ) -> ServiceResult[object]:
        def mutate(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            if remove:
                return ctx.runtime.mathlib.remove_node_mathlib_module_hint(
                    ctx.repo_root, node_path=request.node_path, module=request.module, actor=MathlibUseActor.OPERATOR
                )
            return ctx.runtime.mathlib.add_node_mathlib_module_hint(
                ctx.repo_root,
                node_path=request.node_path,
                module=request.module,
                reason=request.reason,
                actor=MathlibUseActor.OPERATOR,
            )

        return self._contract_mutation(repo_key, operation, request, mutate)

    def _mathlib_decl_mutation(
        self,
        repo_key: str,
        operation: OperatorOperationSpec,
        request: MathlibDeclMutationInput,
        *,
        remove: bool,
    ) -> ServiceResult[object]:
        def mutate(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            if remove:
                return ctx.runtime.mathlib.remove_node_mathlib_decl_hint(
                    ctx.repo_root, node_path=request.node_path, decl_name=request.decl_name, actor=MathlibUseActor.OPERATOR
                )
            return ctx.runtime.mathlib.add_node_mathlib_decl_hint(
                ctx.repo_root,
                node_path=request.node_path,
                decl_name=request.decl_name,
                reason=request.reason,
                actor=MathlibUseActor.OPERATOR,
            )

        return self._contract_mutation(repo_key, operation, request, mutate)

    def _contract_mutation(
        self,
        repo_key: str,
        operation: OperatorOperationSpec,
        request: ContractMutationInput,
        mutate: Callable[[OperatorExecutionContext], ServiceResult[object]],
    ) -> ServiceResult[object]:
        def action(ctx: OperatorExecutionContext) -> ServiceResult[object]:
            checked = self._check_contract_version(ctx, request.node_path, request.expected_contract_version)
            if not checked.ok:
                return checked
            return mutate(ctx)

        return self._execute(repo_key, operation, action)

    def _check_contract_version(
        self,
        ctx: OperatorExecutionContext,
        node_path: str,
        expected: int | None,
    ) -> ServiceResult[object]:
        if expected is None:
            return ctx.runtime.foundation.fail(
                ctx.runtime.foundation.issue(
                    "operator_contract_version_required",
                    "A parent/current contract version is required for this mutation.",
                    object_ref=node_path,
                    field="expected_contract_version",
                )
            )
        current = ctx.runtime.node.contract.get_current_contract(ctx.repo_root, node_path=node_path)
        if not current.ok or current.value is None:
            return ctx.runtime.foundation.fail(current.issues)
        actual = current.value.contract.version
        if actual != expected:
            return ctx.runtime.foundation.fail(
                ctx.runtime.foundation.issue(
                    "operator_contract_version_stale",
                    "NodeContract changed after the operator request was prepared.",
                    object_ref=node_path,
                    field="expected_contract_version",
                    current=str(actual),
                    expected=str(expected),
                )
            )
        return ctx.runtime.foundation.ok(current.value)

    def _check_delete_preview(
        self,
        ctx: OperatorExecutionContext,
        expected_identity: str,
        preview: ServiceResult[object],
    ) -> ServiceResult[object]:
        if not preview.ok or preview.value is None:
            return ctx.runtime.foundation.fail(preview.issues)
        identity = getattr(preview.value, "impact_identity", None)
        if identity != expected_identity:
            return ctx.runtime.foundation.fail(
                ctx.runtime.foundation.issue(
                    "operator_node_delete_preview_stale",
                    "Node delete impact changed after preview.",
                    object_ref=getattr(preview.value, "path", None),
                    current=str(identity),
                    expected=expected_identity,
                )
            )
        if not getattr(preview.value, "deletable", False):
            return ctx.runtime.foundation.fail(
                ctx.runtime.foundation.issue(
                    "node_delete_blocked",
                    "Node deletion is blocked by current or released impacts.",
                    object_ref=getattr(preview.value, "path", None),
                    details={"blocking_reasons": ",".join(getattr(preview.value, "blocking_reasons", []))},
                )
            )
        return ctx.runtime.foundation.ok(preview.value)

    def _recheck_runtime(self, ctx: OperatorExecutionContext) -> ServiceResult[object]:
        record = self.execution.registry.discover_repo(ctx.repo_key)
        if not record.ok or record.value is None:
            return ctx.runtime.foundation.fail(record.issues)
        if ctx.admission.management_state == "data_only":
            if self.execution.registry.runtime_history_exists(record.value):
                return ctx.runtime.foundation.fail(
                    ctx.runtime.foundation.issue(
                        "operator_repo_runtime_history_changed",
                        "Runtime history appeared during a data-only destructive operation.",
                        object_ref=ctx.repo_key,
                    )
                )
            return ctx.runtime.foundation.ok(ctx.admission)
        stable = self.execution.registry.check_operator_runtime_stable(record.value)
        if not stable.ok:
            return ctx.runtime.foundation.fail(stable.issues)
        return ctx.runtime.foundation.ok(ctx.admission)

    def _execute(
        self,
        repo_key: str,
        operation: OperatorOperationSpec,
        action: Callable[[OperatorExecutionContext], ServiceResult[T]],
    ) -> ServiceResult[T]:
        return project_operator_result(self.execution.execute(repo_key, operation, action))


__all__ = [
    "AddInterfaceInput",
    "AddMaterialRefInput",
    "AddNodeDepInput",
    "AddScopeExportInput",
    "BindInterfaceInput",
    "CommitContractInput",
    "CreateContentNodeInput",
    "CreateScopeNodeInput",
    "DeleteNodeInput",
    "MathlibDeclMutationInput",
    "MathlibModuleMutationInput",
    "NodeOperatorApi",
    "NodePathInput",
    "RemoveIndexedInput",
    "RemoveInterfaceInput",
    "RemoveMaterialRefInput",
    "RemoveScopeExportInput",
    "ScopePathInput",
    "SyncRootInterfacesInput",
    "UnbindInterfaceInput",
    "UpdateContractTextInput",
    "UpdateInterfaceInput",
]
