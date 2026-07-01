"""Node tree, contract, dependency, refs, interface, and export tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    ContractCoreUpdateArgs,
    ContentNodeBatchArgs,
    CreateContentNodeArgs,
    CreateScopeNodeArgs,
    CurrentMaterialRefAddArgs,
    CurrentMaterialRefRemoveArgs,
    CurrentNodeDependencyAddArgs,
    IndexArgs,
    InterfaceAddArgs,
    InterfaceBindArgs,
    InterfaceNameArgs,
    InterfaceUpdateArgs,
    MaxCountArgs,
    NodeDeleteArgs,
    NodePathArgs,
    NoArgs,
    ScopeExportAddArgs,
    ScopeExportRemoveArgs,
    ScopePathArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import actor_for_write, current_node_path, direct_tool, handler_tool


def _current_contract(runtime, ctx, args):
    del args
    return runtime.node.get_current_contract_view(ctx.repo_root, node_path=current_node_path(ctx))


def _current_visible_boundaries(runtime, ctx, args):
    del args
    return runtime.node.dependency.list_visible_node_boundaries(ctx.repo_root, node_path=current_node_path(ctx))


def _current_node_deps(runtime, ctx, args):
    del args
    return runtime.node.dependency.list_node_deps(ctx.repo_root, node_path=current_node_path(ctx))


def _get_node(runtime, ctx, args: NodePathArgs):
    return runtime.node.node_tree.get_node(ctx.repo_root, path=args.node_path)


def _preview_delete_node(runtime, ctx, args: NodePathArgs):
    return runtime.node.node_tree.preview_delete_node(ctx.repo_root, path=args.node_path)


def _delete_node(runtime, ctx, args: NodeDeleteArgs):
    return runtime.node.node_tree.mark_node_deleted(ctx.repo_root, path=args.node_path, reason=args.reason)


def _add_current_node_dep(runtime, ctx, args: CurrentNodeDependencyAddArgs):
    return runtime.node.add_current_node_dep(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        target_node=args.target_node,
        reason=args.reason,
        actor=actor_for_write(ctx),
        expected_public_decl_names=args.expected_public_decl_names,
        target_repo=args.target_repo,
    )


def _remove_current_node_dep(runtime, ctx, args: IndexArgs):
    return runtime.node.remove_current_node_dep(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        index=args.index,
        actor=actor_for_write(ctx),
    )


def _add_current_material_ref(runtime, ctx, args: CurrentMaterialRefAddArgs):
    return runtime.node.add_current_material_ref(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        ref_scope=args.ref_scope,
        material_kind=args.material_kind,
        locator=args.locator,
        start_line=args.start_line,
        end_line=args.end_line,
        reason=args.reason,
        actor=actor_for_write(ctx),
    )


def _remove_current_material_ref(runtime, ctx, args: CurrentMaterialRefRemoveArgs):
    return runtime.node.remove_current_material_ref(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        ref_scope=args.ref_scope,
        index=args.index,
        actor=actor_for_write(ctx),
    )


def _add_interface(runtime, ctx, args: InterfaceAddArgs):
    return runtime.node.interface.add_interface(
        ctx.repo_root,
        node_path=args.node_path,
        name=args.name,
        kind=args.kind,
        summary=args.summary,
        statement_hint=args.statement_hint,
        actor=actor_for_write(ctx),
    )


def _update_interface(runtime, ctx, args: InterfaceUpdateArgs):
    return runtime.node.interface.update_interface(
        ctx.repo_root,
        node_path=args.node_path,
        name=args.name,
        summary=args.summary,
        statement_hint=args.statement_hint,
        actor=actor_for_write(ctx),
    )


def _remove_interface(runtime, ctx, args: InterfaceNameArgs):
    return runtime.node.interface.remove_interface(
        ctx.repo_root,
        node_path=args.node_path,
        name=args.name,
        actor=actor_for_write(ctx),
    )


def _bind_node_interface(runtime, ctx, args: InterfaceBindArgs):
    return runtime.node.interface.bind_interface_to_decl(
        ctx.repo_root,
        node_path=args.node_path,
        interface_name=args.interface_name,
        decl_name=args.decl_name,
        decl_node=args.decl_node,
    )


def _unbind_node_interface(runtime, ctx, args: InterfaceNameArgs):
    return runtime.node.interface.unbind_interface(
        ctx.repo_root,
        node_path=args.node_path,
        interface_name=args.name,
    )


def build_tool_specs() -> list[ToolSpec]:
    all_roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    write_roles = {"coordinator", "plan", "worker", "admin"}
    coordinator_roles = {"coordinator", "admin"}
    return [
        handler_tool(
            name="get_current_node_contract",
            description="Read the current node contract view, including deps and material refs.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="current_node_contract",
            groups={AppGroup.NODE_CONTRACT_READ_CURRENT},
            roles=all_roles,
            handler=_current_contract,
        ),
        direct_tool(
            name="get_node_contract",
            description="Read a node contract view by node path.",
            args_model=NodePathArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_method="get_current_contract_view",
            result_view="current_node_contract",
            groups={AppGroup.NODE_CONTRACT_READ_COORDINATOR},
            roles=all_roles,
        ),
        direct_tool(
            name="get_node_tree",
            description="Read the active Scope/Content node tree.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_component="node_tree",
            backing_method="get_node_tree",
            result_view="node_tree",
            groups={AppGroup.NODE_TREE_COORDINATOR_READ},
            roles=all_roles,
        ),
        handler_tool(
            name="get_node",
            description="Read one Scope or Content node metadata entry.",
            args_model=NodePathArgs,
            capability=ToolCapability.READ,
            result_view="node",
            groups={AppGroup.NODE_TREE_COORDINATOR_READ},
            roles=all_roles,
            handler=_get_node,
        ),
        direct_tool(
            name="list_runnable_content_nodes",
            description="List Content nodes whose current contract can be admitted for task scheduling.",
            args_model=MaxCountArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_component="node_tree",
            backing_method="list_runnable_content_candidates",
            result_view="runnable_content_nodes",
            groups={AppGroup.CONTENT_TASK_ADMISSION_READ},
            roles=coordinator_roles,
        ),
        direct_tool(
            name="create_scope_node",
            description="Create a Scope node and its initial open contract.",
            args_model=CreateScopeNodeArgs,
            capability=ToolCapability.WRITE,
            backing_service="node",
            backing_method="create_scope_node",
            result_view="node",
            groups={AppGroup.NODE_TREE_COORDINATOR_WRITE},
            roles=coordinator_roles,
        ),
        direct_tool(
            name="create_content_node",
            description="Create a Content node and its initial open contract.",
            args_model=CreateContentNodeArgs,
            capability=ToolCapability.WRITE,
            backing_service="node",
            backing_method="create_content_node",
            result_view="node",
            groups={AppGroup.NODE_TREE_COORDINATOR_WRITE},
            roles=coordinator_roles,
        ),
        direct_tool(
            name="update_node_contract_text",
            description="Update goal, boundary, objective, success criteria, or constraints for a node contract.",
            args_model=ContractCoreUpdateArgs,
            capability=ToolCapability.WRITE,
            backing_service="node",
            backing_component="contract",
            backing_method="update_contract_text_fields",
            result_view="node_contract",
            groups={AppGroup.NODE_CONTRACT_CORE_COORDINATOR_WRITE},
            roles=coordinator_roles,
        ),
        handler_tool(
            name="preview_delete_node",
            description="Preview whether a node can be safely deprecated.",
            args_model=NodePathArgs,
            capability=ToolCapability.READ,
            result_view="node_delete_impact",
            groups={AppGroup.NODE_TREE_COORDINATOR_WRITE},
            roles=coordinator_roles,
            handler=_preview_delete_node,
        ),
        handler_tool(
            name="delete_node",
            description="Mark a node obsolete after the delete preflight passes.",
            args_model=NodeDeleteArgs,
            capability=ToolCapability.WRITE,
            result_view="mutation",
            groups={AppGroup.NODE_TREE_COORDINATOR_WRITE},
            roles=coordinator_roles,
            handler=_delete_node,
        ),
        handler_tool(
            name="list_current_visible_node_boundaries",
            description="List ready node boundaries visible to the current node.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="visible_node_boundaries",
            groups={AppGroup.NODE_BOUNDARY_READ_CURRENT},
            roles=all_roles,
            handler=_current_visible_boundaries,
        ),
        handler_tool(
            name="list_current_node_deps",
            description="List node dependencies already recorded on the current node contract.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="node_deps",
            groups={AppGroup.NODE_CONTRACT_READ_CURRENT},
            roles=all_roles,
            handler=_current_node_deps,
        ),
        handler_tool(
            name="add_current_node_dep",
            description="Add a visible ready node boundary as a dependency of the current node.",
            args_model=CurrentNodeDependencyAddArgs,
            capability=ToolCapability.WRITE,
            result_view="current_node_contract",
            groups={AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE},
            roles=write_roles,
            handler=_add_current_node_dep,
        ),
        handler_tool(
            name="remove_current_node_dep",
            description="Remove a current-node dependency by list index if the actor is allowed to remove it.",
            args_model=IndexArgs,
            capability=ToolCapability.WRITE,
            result_view="current_node_contract",
            groups={AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE},
            roles=write_roles,
            handler=_remove_current_node_dep,
        ),
        handler_tool(
            name="add_current_material_ref",
            description="Add a source or resource ref to the current node contract.",
            args_model=CurrentMaterialRefAddArgs,
            capability=ToolCapability.WRITE,
            result_view="current_node_contract",
            groups={AppGroup.NODE_CONTRACT_MATERIAL_CURRENT_WRITE},
            roles=write_roles,
            handler=_add_current_material_ref,
        ),
        handler_tool(
            name="remove_current_material_ref",
            description="Remove a material ref from the current node contract by list index.",
            args_model=CurrentMaterialRefRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="current_node_contract",
            groups={AppGroup.NODE_CONTRACT_MATERIAL_CURRENT_WRITE},
            roles=write_roles,
            handler=_remove_current_material_ref,
        ),
        direct_tool(
            name="list_node_material_refs",
            description="List owned/context material refs for a node contract.",
            args_model=NodePathArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_component="material_ref",
            backing_method="list_node_material_refs",
            result_view="node_material_refs",
            groups={AppGroup.NODE_CONTRACT_READ_CURRENT},
            roles=all_roles,
        ),
        direct_tool(
            name="list_node_interfaces",
            description="List interfaces declared on a node contract.",
            args_model=NodePathArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_component="interface",
            backing_method="list_interfaces",
            result_view="node_interfaces",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_READ},
            roles=all_roles,
        ),
        handler_tool(
            name="add_node_interface",
            description="Add a non-protected interface to a node contract.",
            args_model=InterfaceAddArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_add_interface,
        ),
        handler_tool(
            name="update_node_interface",
            description="Update a non-protected interface summary or statement hint.",
            args_model=InterfaceUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_update_interface,
        ),
        handler_tool(
            name="remove_node_interface",
            description="Remove a non-protected unbound interface from a node contract.",
            args_model=InterfaceNameArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_remove_interface,
        ),
        handler_tool(
            name="bind_node_interface",
            description="Bind an interface to a visible declaration reference.",
            args_model=InterfaceBindArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_binding",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_bind_node_interface,
        ),
        handler_tool(
            name="unbind_node_interface",
            description="Remove a node interface binding.",
            args_model=InterfaceNameArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_binding",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_unbind_node_interface,
        ),
        direct_tool(
            name="list_scope_export_candidates",
            description="List public declarations visible for export from a Scope child boundary.",
            args_model=ScopePathArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_component="export",
            backing_method="list_scope_export_candidates",
            result_view="scope_export_candidates",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_READ},
            roles=all_roles,
        ),
        direct_tool(
            name="list_scope_exports",
            description="List current exports on a Scope contract.",
            args_model=ScopePathArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_component="export",
            backing_method="list_scope_exports",
            result_view="scope_exports",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_READ},
            roles=all_roles,
        ),
        direct_tool(
            name="add_scope_export",
            description="Add a declaration to a Scope export list and optionally bind an interface.",
            args_model=ScopeExportAddArgs,
            capability=ToolCapability.WRITE,
            backing_service="node",
            backing_component="export",
            backing_method="add_scope_export",
            result_view="scope_exports",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
        ),
        direct_tool(
            name="remove_scope_export",
            description="Remove a Scope export by list index when no interface still depends on it.",
            args_model=ScopeExportRemoveArgs,
            capability=ToolCapability.WRITE,
            backing_service="node",
            backing_component="export",
            backing_method="remove_scope_export",
            result_view="scope_exports",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
        ),
        direct_tool(
            name="get_scope_close_view",
            description="Read scope close readiness, child readiness, exports, and interface state.",
            args_model=ScopePathArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_method="get_scope_close_view",
            result_view="scope_close_view",
            groups={AppGroup.SCOPE_CLOSE_READ},
            roles=coordinator_roles,
        ),
        direct_tool(
            name="get_repo_ready_node_view",
            description="Read repository ready state through the Main scope and repo ready gates.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_method="get_repo_ready_node_view",
            result_view="repo_ready_node_view",
            groups={AppGroup.REPO_READY_READ},
            roles=coordinator_roles,
        ),
        direct_tool(
            name="check_content_task_admission",
            description="Check whether a Content node contract can be launched as a task.",
            args_model=NodePathArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_method="prepare_content_task_admission",
            result_view="gate_report",
            groups={AppGroup.CONTENT_TASK_ADMISSION_READ},
            roles=coordinator_roles,
        ),
        direct_tool(
            name="check_content_node_batch",
            description="Check whether a batch of Content nodes can run together without dependency conflicts.",
            args_model=ContentNodeBatchArgs,
            capability=ToolCapability.READ,
            backing_service="node",
            backing_method="submit_content_node_batch_preflight",
            result_view="gate_report",
            groups={AppGroup.CONTENT_TASK_ADMISSION_READ},
            roles=coordinator_roles,
        ),
    ]
