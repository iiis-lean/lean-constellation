"""Node tree, contract, dependency, refs, interface, and export tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    ContractCoreUpdateArgs,
    ContentNodeBatchArgs,
    ContentTaskResultInspectArgs,
    ContentTaskResultListArgs,
    CreateContentNodeArgs,
    CreateScopeNodeArgs,
    CurrentNodeInterfaceBindArgs,
    CurrentMaterialRefAddArgs,
    CurrentMaterialRefRemoveArgs,
    CurrentNodeDependencyAddArgs,
    IndexArgs,
    InterfaceAddArgs,
    InterfaceBindArgs,
    InterfaceNameArgs,
    InterfaceUpdateArgs,
    MaxCountArgs,
    NodeContractCommitArgs,
    NodeDeleteArgs,
    NodeDependencyAddArgs,
    NodeDependencyRemoveArgs,
    NodeMaterialRefAddArgs,
    NodeMaterialRefRemoveArgs,
    NodePathArgs,
    NoArgs,
    RootInterfaceAddArgs,
    RootInterfaceNameArgs,
    RootInterfaceUpdateArgs,
    ScopeExportAddArgs,
    ScopeExportRemoveArgs,
    ScopePathArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import actor_for_write, current_node_path, direct_tool, handler_tool
from lean_constellation.domain.common import StrictModel
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.flows.common.flow_requests import node_scope_id
from lean_constellation.services.foundation import GateReport, ServiceIssue
from lean_constellation.services.node import (
    ContentTaskResultView,
    ContractVersionStatus,
    InterfaceView,
    NodeKind,
    NodeLifecycle,
)
from pydantic import Field


class ContentTaskResultItemView(StrictModel):
    node_path: str
    repo_key: str
    outcome: str
    contract_version: int | None = None
    reason: str | None = None
    summary: str | None = None


class ContentTaskResultListView(StrictModel):
    count: int
    items: list[ContentTaskResultItemView]
    summary: str


class ContentTaskResultInspectView(StrictModel):
    result: ContentTaskResultItemView
    summary: str


class ScopeContractCommitReceipt(StrictModel):
    scope_path: str
    operation: str = "commit"
    changed: bool
    contract_version: int
    contract_status: ContractVersionStatus
    gate: GateReport
    summary: str


class ScopeExportDeclView(StrictModel):
    index: int
    repository: str | None = None
    node_path: str
    declaration_name: str
    requested_revision: int
    resolved_revision: int | None = None
    resolution_reason: str | None = None
    valid: bool
    source_node: str | None = None
    issues: list[ServiceIssue] = Field(default_factory=list)


class ScopeExportListView(StrictModel):
    scope_path: str
    count: int
    exports: list[ScopeExportDeclView]
    summary: str


class ScopeExportMutationReceipt(StrictModel):
    scope_path: str
    operation: str
    changed: bool
    export: ScopeExportDeclView
    bound_interface_name: str | None = None
    summary: str


class InterfaceListAgentView(StrictModel):
    node_path: str
    interfaces: list[InterfaceView]
    summary: str


class NodeAgentView(StrictModel):
    node_path: str
    node_kind: NodeKind
    lifecycle: NodeLifecycle
    contract_status: str | None = None
    parent_path: str | None = None
    child_count: int = 0


class NodeTreeAgentView(StrictModel):
    root_path: str | None = None
    nodes: list[NodeAgentView]
    active_count: int
    summary: str


class RootInterfaceRunContextView(StrictModel):
    start_kind: str
    run_objective: str
    root_interface_policy: str
    source_files_in_run: list[str]
    source_index_delta_summary: str | None = None
    explicit_required_additions: list[str]
    prior_interface_names: list[str]
    summary: str


def _current_contract(runtime, ctx, args):
    del args
    return runtime.node.get_current_contract_view(ctx.repo_root, node_path=current_node_path(ctx))


def _current_visible_boundaries(runtime, ctx, args):
    del args
    role = ctx.actor.role
    return runtime.node.public_decl_access.list_visible_nodes(
        ctx.repo_root,
        actor_role=role.value if hasattr(role, "value") else str(role),
        current_node_path=current_node_path(ctx),
    )


def _current_node_deps(runtime, ctx, args):
    del args
    return runtime.node.dependency.list_node_deps(ctx.repo_root, node_path=current_node_path(ctx))


def _current_material_refs(runtime, ctx, args):
    del args
    return runtime.node.material_ref.list_node_material_refs(ctx.repo_root, node_path=current_node_path(ctx))


def _get_node(runtime, ctx, args: NodePathArgs):
    found = runtime.node.node_tree.get_node(ctx.repo_root, path=args.node_path)
    if not found.ok or found.value is None:
        return runtime.foundation.fail(found.issues)
    return runtime.foundation.ok(_node_agent_view(found.value), warnings=found.issues)


def _node_agent_view(item) -> NodeAgentView:
    return NodeAgentView(
        node_path=item.path,
        node_kind=item.kind,
        lifecycle=item.lifecycle,
        contract_status=item.contract_status,
        parent_path=item.parent_path,
        child_count=item.child_count,
    )


def _get_node_tree(runtime, ctx, args: NoArgs):
    del args
    tree = runtime.node.node_tree.get_node_tree(ctx.repo_root)
    if not tree.ok or tree.value is None:
        return runtime.foundation.fail(tree.issues)
    return runtime.foundation.ok(
        NodeTreeAgentView(
            root_path=tree.value.root_path,
            nodes=[_node_agent_view(item) for item in tree.value.nodes],
            active_count=tree.value.active_count,
            summary=tree.value.summary,
        ),
        warnings=tree.issues,
    )


def _update_node_contract_text(runtime, ctx, args: ContractCoreUpdateArgs):
    return runtime.node.contract.update_contract_text_fields_receipt(
        ctx.repo_root,
        node_path=args.node_path,
        goal=args.goal,
        boundary=args.boundary,
        objective=args.objective,
        success_criteria=args.success_criteria,
        constraints=args.constraints,
    )


def _node_delete_runtime_blockers(runtime, ctx, *, node_path: str):
    node = runtime.node.node_tree.get_node(ctx.repo_root, path=node_path)
    if not node.ok or node.value is None:
        return runtime.foundation.fail(node.issues)
    scope_id = node_scope_id(ctx.repo.repo_key, node.value.node_id)
    try:
        flows = runtime.list_flows(scope_id=scope_id)
        steps = runtime.list_steps(scope_id=scope_id)
        agent_service = runtime.ark.agent_service
        if agent_service is None or not hasattr(agent_service, "list_agents"):
            raise RuntimeError("ARK agent service does not expose list_agents.")
        agents = list(agent_service.list_agents(scope_id=scope_id))
    except Exception as exc:  # noqa: BLE001 - fail closed at the runtime boundary
        return runtime.foundation.ok([f"runtime_inspection_failed:{exc}"])

    blockers = [
        f"running_content_task:{getattr(flow, 'flow_id', '')}"
        for flow in flows
        if getattr(flow, "flow_type", None) == "content_node_task"
        and str(getattr(getattr(flow, "status", None), "value", getattr(flow, "status", "")))
        not in {"completed", "failed"}
    ]
    blockers.extend(
        f"running_step:{getattr(step, 'step_id', '')}"
        for step in steps
        if str(getattr(getattr(step, "status", None), "value", getattr(step, "status", ""))) == "running"
        and getattr(step, "step_id", None) != ctx.runtime.step_id
    )
    blockers.extend(
        f"running_agent:{getattr(agent, 'agent_id', '')}"
        for agent in agents
        if getattr(agent, "status", None) == "running"
        and getattr(agent, "agent_id", None) != ctx.runtime.agent_id
    )
    return runtime.foundation.ok(sorted(set(blockers)))


def _preview_delete_node(runtime, ctx, args: NodePathArgs):
    runtime_blockers = _node_delete_runtime_blockers(runtime, ctx, node_path=args.node_path)
    if not runtime_blockers.ok or runtime_blockers.value is None:
        return runtime.foundation.fail(runtime_blockers.issues)
    data = runtime.node.preview_delete_node(ctx.repo_root, path=args.node_path)
    if not data.ok or data.value is None:
        return runtime.foundation.fail(data.issues)
    blockers = sorted(set([*data.value.blocking_reasons, *runtime_blockers.value]))
    return runtime.foundation.ok(data.value.model_copy(update={
        "deletable": not blockers,
        "blocking_reasons": blockers,
        "summary": "Node deletion is blocked." if blockers else "Node can be deleted.",
    }))


def _delete_node(runtime, ctx, args: NodeDeleteArgs):
    try:
        with runtime.repo_workspace.lifecycle_lock.locked(ctx.repo_root):
            first = _node_delete_runtime_blockers(runtime, ctx, node_path=args.node_path)
            if not first.ok or first.value is None:
                return runtime.foundation.fail(first.issues)
            data = runtime.node.preview_delete_node(ctx.repo_root, path=args.node_path)
            if not data.ok or data.value is None:
                return runtime.foundation.fail(data.issues)
            second = _node_delete_runtime_blockers(runtime, ctx, node_path=args.node_path)
            if not second.ok or second.value is None:
                return runtime.foundation.fail(second.issues)
            blockers = sorted(set([*data.value.blocking_reasons, *first.value, *second.value]))
            if blockers:
                return runtime.foundation.fail(runtime.foundation.issue(
                    "node_delete_blocked",
                    "Node deletion is blocked by current data or runtime impacts.",
                    object_ref=args.node_path,
                    details={"blocking_reasons": ",".join(blockers)},
                ))
            return runtime.node.mark_node_deleted(ctx.repo_root, path=args.node_path, reason=args.reason)
    except Exception as exc:  # lifecycle lock busy and runtime boundary failures are fail-closed
        return runtime.foundation.fail(runtime.foundation.issue(
            "node_delete_runtime_guard_failed",
            f"Node deletion runtime guard failed: {exc}",
            object_ref=args.node_path,
        ))


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


def _add_node_dep(runtime, ctx, args: NodeDependencyAddArgs):
    return runtime.node.add_current_node_dep(
        ctx.repo_root,
        node_path=args.node_path,
        target_node=args.target_node,
        reason=args.reason,
        actor="coordinator",
        expected_public_decl_names=args.expected_public_decl_names,
        target_repo=args.target_repo,
    )


def _remove_node_dep(runtime, ctx, args: NodeDependencyRemoveArgs):
    return runtime.node.remove_current_node_dep(
        ctx.repo_root,
        node_path=args.node_path,
        index=args.index,
        actor="coordinator",
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


def _add_node_material_ref(runtime, ctx, args: NodeMaterialRefAddArgs):
    return runtime.node.add_current_material_ref(
        ctx.repo_root,
        node_path=args.node_path,
        ref_scope=args.ref_scope,
        material_kind=args.material_kind,
        locator=args.locator,
        start_line=args.start_line,
        end_line=args.end_line,
        reason=args.reason,
        actor="coordinator",
    )


def _remove_node_material_ref(runtime, ctx, args: NodeMaterialRefRemoveArgs):
    return runtime.node.remove_current_material_ref(
        ctx.repo_root,
        node_path=args.node_path,
        ref_scope=args.ref_scope,
        index=args.index,
        actor="coordinator",
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


def _list_root_interfaces(runtime, ctx, args: NoArgs):
    del args
    listed = runtime.node.interface.list_interfaces(ctx.repo_root, node_path="Main")
    if not listed.ok or listed.value is None:
        return runtime.foundation.fail(listed.issues)
    return runtime.foundation.ok(
        InterfaceListAgentView(
            node_path=listed.value.node_path,
            interfaces=listed.value.interfaces,
            summary=listed.value.summary,
        ),
        warnings=listed.issues,
    )


def _get_root_interface_run_context(runtime, ctx, args: NoArgs):
    del args
    flow_id = ctx.runtime.flow_id
    if not flow_id:
        return runtime.foundation.fail(
            runtime.foundation.issue("root_interface_flow_context_required", "Root-interface run context requires the current preparation Flow.")
        )
    flow = runtime.get_flow(flow_id)
    if getattr(flow, "flow_type", None) != "root_interface_preparation":
        return runtime.foundation.fail(
            runtime.foundation.issue("root_interface_flow_context_invalid", "Current Flow is not a root-interface preparation Flow.")
        )
    flow_input = getattr(flow, "input", None)
    if str(getattr(flow_input, "repo_root", "")) != str(ctx.repo_root):
        return runtime.foundation.fail(
            runtime.foundation.issue("root_interface_repo_context_mismatch", "Current root-interface Flow is bound to a different repository.")
        )
    listed = runtime.node.interface.list_interfaces(ctx.repo_root, node_path="Main")
    if not listed.ok or listed.value is None:
        return runtime.foundation.fail(listed.issues)
    run_context = flow_input.run_context
    state = getattr(flow, "state", None)
    return runtime.foundation.ok(
        RootInterfaceRunContextView(
            start_kind=run_context.start_kind,
            run_objective=run_context.run_spec.run_objective,
            root_interface_policy=run_context.run_spec.root_interface_policy,
            source_files_in_run=list(run_context.resolved_source_files),
            source_index_delta_summary=getattr(flow_input.source_index_delta, "coverage_summary", None),
            explicit_required_additions=[
                item.name for item in run_context.run_spec.additional_required_interfaces
            ],
            prior_interface_names=sorted(getattr(state, "previous_interfaces", {})),
            summary="Current root-interface responsibility.",
        ),
        warnings=listed.issues,
    )


def _add_root_interface(runtime, ctx, args: RootInterfaceAddArgs):
    return runtime.node.interface.add_interface(
        ctx.repo_root,
        node_path="Main",
        name=args.name,
        kind=args.kind,
        summary=args.summary,
        statement_hint=args.statement_hint,
        actor=actor_for_write(ctx),
    )


def _update_root_interface(runtime, ctx, args: RootInterfaceUpdateArgs):
    return runtime.node.interface.update_interface(
        ctx.repo_root,
        node_path="Main",
        name=args.name,
        summary=args.summary,
        statement_hint=args.statement_hint,
        actor=actor_for_write(ctx),
    )


def _remove_root_interface(runtime, ctx, args: RootInterfaceNameArgs):
    return runtime.node.interface.remove_interface(
        ctx.repo_root,
        node_path="Main",
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


def _bind_current_node_interface(runtime, ctx, args: CurrentNodeInterfaceBindArgs):
    node_path = current_node_path(ctx)
    return runtime.node.interface.bind_interface_to_decl(
        ctx.repo_root,
        node_path=node_path,
        interface_name=args.interface_name,
        decl_name=args.decl_name,
        decl_node=node_path,
    )


def _unbind_node_interface(runtime, ctx, args: InterfaceNameArgs):
    return runtime.node.interface.unbind_interface(
        ctx.repo_root,
        node_path=args.node_path,
        interface_name=args.name,
    )


def _commit_scope_contract(runtime, ctx, args: NodeContractCommitArgs):
    committed = runtime.node.commit_scope_contract(ctx.repo_root, scope_path=args.node_path, summary=args.summary)
    if not committed.ok or committed.value is None:
        return runtime.foundation.fail(committed.issues)
    gate = runtime.foundation.gate_passed(
        "scope_commit",
        summary="Scope commit guards passed.",
    )
    return runtime.foundation.ok(
        ScopeContractCommitReceipt(
            scope_path=args.node_path,
            changed=True,
            contract_version=committed.value.version,
            contract_status=committed.value.version_status,
            gate=gate,
            summary=f"Committed {args.node_path} contract v{committed.value.version}.",
        ),
        warnings=committed.issues,
    )


def _get_scope_close_view(runtime, ctx, args: ScopePathArgs):
    return runtime.validation_snapshot.get_scope_ready_view(ctx.repo_root, scope_path=args.scope_path)


def _scope_export_decl_view(item) -> ScopeExportDeclView:
    return ScopeExportDeclView(
        index=item.index,
        repository=item.repo,
        node_path=item.node,
        declaration_name=item.name,
        requested_revision=item.revision,
        resolved_revision=item.resolved_revision,
        resolution_reason=item.resolution_reason,
        valid=item.valid,
        source_node=item.source,
        issues=list(item.issues),
    )


def _list_scope_exports(runtime, ctx, args: ScopePathArgs):
    listed = runtime.node.export.list_scope_exports(ctx.repo_root, scope_path=args.scope_path)
    if not listed.ok or listed.value is None:
        return runtime.foundation.fail(listed.issues)
    exports = [_scope_export_decl_view(item) for item in listed.value]
    return runtime.foundation.ok(
        ScopeExportListView(
            scope_path=args.scope_path,
            count=len(exports),
            exports=exports,
            summary=f"Loaded {len(exports)} exports for {args.scope_path}.",
        ),
        warnings=listed.issues,
    )


def _add_scope_export(runtime, ctx, args: ScopeExportAddArgs):
    updated = runtime.node.export.add_scope_export(
        ctx.repo_root,
        scope_path=args.scope_path,
        decl_node=args.decl_node,
        decl_name=args.decl_name,
        decl_repo=args.decl_repo,
        revision=args.revision,
        bind_interface_name=args.bind_interface_name,
    )
    if not updated.ok or updated.value is None:
        return runtime.foundation.fail(updated.issues)
    exported = next(
        (
            item
            for item in updated.value.exports
            if item.node == args.decl_node
            and item.name == args.decl_name
            and item.revision == args.revision
            and item.repo == args.decl_repo
        ),
        None,
    )
    if exported is None:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "scope_export_receipt_missing",
                "Updated Scope export was not present in the post-mutation export view.",
                object_ref=args.scope_path,
            )
        )
    return runtime.foundation.ok(
        ScopeExportMutationReceipt(
            scope_path=args.scope_path,
            operation="add",
            changed=updated.value.changed,
            export=_scope_export_decl_view(exported),
            bound_interface_name=args.bind_interface_name,
            summary=updated.value.summary,
        ),
        warnings=updated.issues,
    )


def _remove_scope_export(runtime, ctx, args: ScopeExportRemoveArgs):
    before = runtime.node.export.list_scope_exports(ctx.repo_root, scope_path=args.scope_path)
    if not before.ok or before.value is None:
        return runtime.foundation.fail(before.issues)
    if args.index >= len(before.value):
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "scope_export_index_out_of_range",
                f"Scope export index is out of range: {args.index}",
                object_ref=args.scope_path,
                field="index",
            )
        )
    removed = _scope_export_decl_view(before.value[args.index])
    updated = runtime.node.export.remove_scope_export(ctx.repo_root, scope_path=args.scope_path, index=args.index)
    if not updated.ok or updated.value is None:
        return runtime.foundation.fail(updated.issues)
    return runtime.foundation.ok(
        ScopeExportMutationReceipt(
            scope_path=args.scope_path,
            operation="remove",
            changed=updated.value.changed,
            export=removed,
            summary=updated.value.summary,
        ),
        warnings=[*before.issues, *updated.issues],
    )


def _list_node_interfaces(runtime, ctx, args: NodePathArgs):
    listed = runtime.node.interface.list_interfaces(ctx.repo_root, node_path=args.node_path)
    if not listed.ok or listed.value is None:
        return runtime.foundation.fail(listed.issues)
    return runtime.foundation.ok(
        InterfaceListAgentView(
            node_path=listed.value.node_path,
            interfaces=listed.value.interfaces,
            summary=listed.value.summary,
        ),
        warnings=listed.issues,
    )


def _list_recent_content_task_results(runtime, ctx, args: ContentTaskResultListArgs):
    resolved = _resolve_content_task_result_items(runtime, ctx, node_path=args.node_path)
    if not resolved.ok or resolved.value is None:
        return runtime.foundation.fail(resolved.issues)
    items = resolved.value[: args.limit]
    return runtime.foundation.ok(
        ContentTaskResultListView(
            count=len(items),
            items=items,
            summary=f"Found {len(items)} terminal Content task result(s) in the current callback context.",
        )
    )


def _inspect_content_task_result(runtime, ctx, args: ContentTaskResultInspectArgs):
    selected = _select_content_task_result_item(runtime, ctx, node_path=args.node_path, contract_version=args.contract_version)
    if not selected.ok or selected.value is None:
        return runtime.foundation.fail(selected.issues)
    return runtime.foundation.ok(
        ContentTaskResultInspectView(
            result=selected.value,
            summary=f"Selected terminal Content task result for {args.node_path}.",
        )
    )


def _commit_content_contract(runtime, ctx, args: NodeContractCommitArgs):
    selected = _select_content_task_result(runtime, ctx, node_path=args.node_path, contract_version=None)
    if not selected.ok or selected.value is None:
        return runtime.foundation.fail(selected.issues)
    return runtime.node.finalize_content_task_result(
        ctx.repo_root,
        node_path=args.node_path,
        task_result=ContentTaskResultView(
            outcome=selected.value.outcome,
            contract_version=selected.value.contract_version,
            summary=selected.value.summary,
            reason=selected.value.reason,
        ),
        coordinator_summary=args.summary,
    )


def _select_content_task_result(runtime, ctx, *, node_path: str, contract_version: int | None):
    flow_results = _content_task_results_for_callback(runtime, ctx)
    if not flow_results.ok or flow_results.value is None:
        return runtime.foundation.fail(flow_results.issues)
    matches: list[ContentNodeTaskResult] = []
    for result in flow_results.value:
        if result.node_path != node_path:
            continue
        if contract_version is not None and result.contract_version != contract_version:
            continue
        matches.append(result)
    if not matches:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "content_task_result_not_found",
                "No terminal Content task result matched the requested node path and contract version.",
                object_ref=node_path,
                field="contract_version" if contract_version is not None else "node_path",
                expected=str(contract_version) if contract_version is not None else None,
            )
        )
    return runtime.foundation.ok(matches[0])


def _select_content_task_result_item(runtime, ctx, *, node_path: str, contract_version: int | None):
    selected = _select_content_task_result(runtime, ctx, node_path=node_path, contract_version=contract_version)
    if not selected.ok or selected.value is None:
        return runtime.foundation.fail(selected.issues)
    return runtime.foundation.ok(_content_task_result_item(selected.value))


def _resolve_content_task_result_items(runtime, ctx, *, node_path: str | None):
    flow_results = _content_task_results_for_callback(runtime, ctx)
    if not flow_results.ok or flow_results.value is None:
        return runtime.foundation.fail(flow_results.issues)
    items = [
        _content_task_result_item(result)
        for result in flow_results.value
        if node_path is None or result.node_path == node_path
    ]
    return runtime.foundation.ok(items)


def _coordinator_release_context(runtime, ctx):
    flow_id = ctx.runtime.flow_id
    if not flow_id:
        return runtime.foundation.fail(
            runtime.foundation.issue("coordinator_flow_context_required", "Release-candidate preview requires the current Coordinator Flow.")
        )
    flow = runtime.get_flow(flow_id)
    if getattr(flow, "flow_type", None) != "native_repo_coordinator":
        return runtime.foundation.fail(
            runtime.foundation.issue("coordinator_flow_context_invalid", "Current Flow is not a native repository Coordinator Flow.")
        )
    flow_input = getattr(flow, "input", None)
    if str(getattr(flow_input, "repo_root", "")) != str(ctx.repo_root):
        return runtime.foundation.fail(
            runtime.foundation.issue("coordinator_repo_context_mismatch", "Current Coordinator Flow is bound to a different repository.")
        )
    run_context = getattr(flow_input, "run_context", None)
    return runtime.foundation.ok((flow_id, getattr(run_context, "base_release_id", None)))


def _get_repo_ready_node_view(runtime, ctx, args: NoArgs):
    del args
    owner = _coordinator_release_context(runtime, ctx)
    if not owner.ok or owner.value is None:
        return runtime.foundation.fail(owner.issues)
    ready_view = runtime.validation_snapshot.get_repo_ready_view(ctx.repo_root)
    if not ready_view.ok or ready_view.value is None:
        return runtime.foundation.fail(ready_view.issues)
    flow_id, base_release_id = owner.value
    from lean_constellation.flows.coordinator.release_runtime import check_repo_release_runtime_closeout

    runtime_closeout = check_repo_release_runtime_closeout(
        runtime,
        ctx.repo_root,
        owner_flow_id=flow_id,
        phase="submission_preview",
        allowed_agent_id=ctx.runtime.agent_id,
    )
    if not runtime_closeout.ok or runtime_closeout.value is None:
        return runtime.foundation.fail(runtime_closeout.issues)
    if not runtime_closeout.value.passed:
        return runtime.foundation.fail(runtime_closeout.value.issues)
    preview = runtime.validation_snapshot.preview_candidate_release(
        ctx.repo_root,
        base_release_id=base_release_id,
        summary="Repository release candidate preview.",
    )
    if not preview.ok or preview.value is None:
        return runtime.foundation.fail(preview.issues)
    return runtime.foundation.ok(
        {
            "repo_readiness": ready_view.value.model_dump(mode="json"),
            "candidate_gate": preview.value.gate.model_dump(mode="json"),
            "blocking_issue_kinds": list(preview.value.blocking_issue_kinds),
            "ready": bool(preview.value.gate.passed),
            "summary": preview.value.summary,
        },
        warnings=ready_view.issues,
    )


def _content_task_results_for_callback(runtime, ctx):
    flow_service = getattr(runtime.ark, "flow_service", None)
    if flow_service is None:
        return runtime.foundation.fail(runtime.foundation.issue("flow_service_missing", "ARK flow service is not configured."))
    current_step_id = ctx.runtime.step_id
    parent_flow_id = ctx.runtime.flow_id
    if not current_step_id or not parent_flow_id:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "content_task_callback_context_missing",
                "Content task result tools require a Coordinator callback AgentStep context.",
            )
        )
    try:
        current_step = flow_service.get_step(current_step_id)
    except Exception as exc:  # noqa: BLE001 - runtime boundary
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "content_task_callback_context_missing",
                f"Could not read current AgentStep context: {exc}",
                object_ref=current_step_id,
            )
        )
    dispatch_step_id = getattr(getattr(current_step, "state", None), "callback_dispatch_step_id", None)
    if not dispatch_step_id:
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "content_task_callback_context_missing",
                "Content task result tools require a callback AgentStep with callback_dispatch_step_id.",
                object_ref=current_step_id,
            )
        )

    store = getattr(flow_service, "store", None)
    try:
        if store is not None and hasattr(store, "list_child_flows"):
            child_flows = list(store.list_child_flows(parent_flow_id=parent_flow_id, parent_dispatch_step_id=dispatch_step_id))
        else:
            child_flows = [
                flow
                for flow in flow_service.list_flows()
                if flow.parent_flow_id == parent_flow_id and flow.parent_dispatch_step_id == dispatch_step_id
            ]
    except Exception as exc:  # noqa: BLE001 - runtime boundary
        return runtime.foundation.fail(
            runtime.foundation.issue(
                "content_task_callback_children_unavailable",
                f"Could not read callback child flows: {exc}",
                object_ref=dispatch_step_id,
            )
        )

    results: list[ContentNodeTaskResult] = []
    for child_flow in child_flows:
        result = getattr(child_flow, "result", None)
        if isinstance(result, ContentNodeTaskResult):
            results.append(result)
    results.reverse()
    return runtime.foundation.ok(results)


def _content_task_result_item(result: ContentNodeTaskResult) -> ContentTaskResultItemView:
    return ContentTaskResultItemView(
        node_path=result.node_path,
        repo_key=result.repo_key,
        outcome=result.outcome,
        contract_version=result.contract_version,
        reason=result.reason,
        summary=result.summary,
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
        handler_tool(
            name="get_node_tree",
            description="Read the active node tree without internal node ids or contract-version bookkeeping.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="node_tree_overview",
            groups={AppGroup.NODE_TREE_COORDINATOR_READ},
            roles=all_roles,
            handler=_get_node_tree,
        ),
        handler_tool(
            name="get_node",
            description="Read one Scope or Content node metadata entry.",
            args_model=NodePathArgs,
            capability=ToolCapability.READ,
            result_view="node_overview",
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
        handler_tool(
            name="update_node_contract_text",
            description="Update goal, boundary, objective, success criteria, or constraints for a node contract.",
            args_model=ContractCoreUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="mutation",
            groups={AppGroup.NODE_CONTRACT_CORE_COORDINATOR_WRITE},
            roles=coordinator_roles,
            handler=_update_node_contract_text,
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
            description="List ready same-repo nodes visible to the current node with compact public declarations; contract interfaces are not exposed here.",
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
            description="Add a visible ready node boundary as a dependency of the current node and report any generated Prelude file change.",
            args_model=CurrentNodeDependencyAddArgs,
            capability=ToolCapability.WRITE,
            result_view="node_dependency_mutation",
            groups={AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE},
            roles=write_roles,
            handler=_add_current_node_dep,
        ),
        handler_tool(
            name="remove_current_node_dep",
            description="Remove a current-node dependency by list index and report any generated Prelude file change.",
            args_model=IndexArgs,
            capability=ToolCapability.WRITE,
            result_view="node_dependency_mutation",
            groups={AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE},
            roles=write_roles,
            handler=_remove_current_node_dep,
        ),
        handler_tool(
            name="add_node_dep",
            description="Add a visible ready node boundary as a dependency of the target node contract and report any generated Prelude file change.",
            args_model=NodeDependencyAddArgs,
            capability=ToolCapability.WRITE,
            result_view="node_dependency_mutation",
            groups={AppGroup.NODE_CONTRACT_DEPENDENCY_COORDINATOR_WRITE},
            roles=coordinator_roles,
            handler=_add_node_dep,
        ),
        handler_tool(
            name="remove_node_dep",
            description="Remove a dependency from the target node contract by list index and report any generated Prelude file change.",
            args_model=NodeDependencyRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="node_dependency_mutation",
            groups={AppGroup.NODE_CONTRACT_DEPENDENCY_COORDINATOR_WRITE},
            roles=coordinator_roles,
            handler=_remove_node_dep,
        ),
        handler_tool(
            name="add_current_material_ref",
            description="Add a source or resource ref to the current node contract.",
            args_model=CurrentMaterialRefAddArgs,
            capability=ToolCapability.WRITE,
            result_view="current_node_material_mutation",
            groups={AppGroup.NODE_CONTRACT_MATERIAL_CURRENT_WRITE},
            roles=write_roles,
            handler=_add_current_material_ref,
        ),
        handler_tool(
            name="remove_current_material_ref",
            description="Remove a material ref from the current node contract by list index.",
            args_model=CurrentMaterialRefRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="current_node_material_mutation",
            groups={AppGroup.NODE_CONTRACT_MATERIAL_CURRENT_WRITE},
            roles=write_roles,
            handler=_remove_current_material_ref,
        ),
        handler_tool(
            name="add_node_material_ref",
            description="Add a source or resource ref to the target node contract.",
            args_model=NodeMaterialRefAddArgs,
            capability=ToolCapability.WRITE,
            result_view="current_node_material_mutation",
            groups={AppGroup.NODE_CONTRACT_MATERIAL_COORDINATOR_WRITE},
            roles=coordinator_roles,
            handler=_add_node_material_ref,
        ),
        handler_tool(
            name="remove_node_material_ref",
            description="Remove a material ref from the target node contract by list index.",
            args_model=NodeMaterialRefRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="current_node_material_mutation",
            groups={AppGroup.NODE_CONTRACT_MATERIAL_COORDINATOR_WRITE},
            roles=coordinator_roles,
            handler=_remove_node_material_ref,
        ),
        handler_tool(
            name="list_current_node_material_refs",
            description="List owned/context material refs for the current node contract.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="node_material_refs",
            groups={AppGroup.NODE_CONTRACT_READ_CURRENT},
            roles=all_roles,
            handler=_current_material_refs,
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
            groups={AppGroup.NODE_CONTRACT_READ_COORDINATOR},
            roles=all_roles,
        ),
        handler_tool(
            name="list_node_interfaces",
            description="List interfaces declared on a node contract.",
            args_model=NodePathArgs,
            capability=ToolCapability.READ,
            result_view="interface_list",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_READ},
            roles=all_roles,
            handler=_list_node_interfaces,
        ),
        handler_tool(
            name="list_root_interfaces",
            description="List root Main interfaces with protected/supplement markers.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="node_interfaces",
            groups={AppGroup.ROOT_INTERFACE_STATE_READ},
            roles={"worker", "coordinator", "admin"},
            handler=_list_root_interfaces,
        ),
        handler_tool(
            name="get_root_interface_run_context",
            description="Read the current root-interface objective, source delta, protected baseline, required additions, and candidate interface names.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="root_interface_run_context",
            groups={AppGroup.ROOT_INTERFACE_PREPARE_READ},
            roles={"worker", "admin"},
            handler=_get_root_interface_run_context,
        ),
        handler_tool(
            name="add_root_interface",
            description="Add a supplement interface to root Main.",
            args_model=RootInterfaceAddArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_mutation",
            groups={AppGroup.ROOT_INTERFACE_WRITE},
            roles={"worker", "admin"},
            handler=_add_root_interface,
        ),
        handler_tool(
            name="update_root_interface",
            description="Update a supplement root Main interface summary or statement hint.",
            args_model=RootInterfaceUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_mutation",
            groups={AppGroup.ROOT_INTERFACE_WRITE},
            roles={"worker", "admin"},
            handler=_update_root_interface,
        ),
        handler_tool(
            name="remove_root_interface",
            description="Remove a supplement root Main interface.",
            args_model=RootInterfaceNameArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_mutation",
            groups={AppGroup.ROOT_INTERFACE_WRITE},
            roles={"worker", "admin"},
            handler=_remove_root_interface,
        ),
        handler_tool(
            name="add_node_interface",
            description="Add a non-protected interface to a node contract.",
            args_model=InterfaceAddArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_mutation",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_add_interface,
        ),
        handler_tool(
            name="update_node_interface",
            description="Update a non-protected interface summary or statement hint.",
            args_model=InterfaceUpdateArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_mutation",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_update_interface,
        ),
        handler_tool(
            name="remove_node_interface",
            description="Remove a non-protected unbound interface from a node contract.",
            args_model=InterfaceNameArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_mutation",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_remove_interface,
        ),
        handler_tool(
            name="bind_current_node_interface",
            description="Bind an interface on the current Content node to a public declaration on that same node.",
            args_model=CurrentNodeInterfaceBindArgs,
            capability=ToolCapability.WRITE,
            result_view="interface_binding",
            groups={AppGroup.CONTENT_INTERFACE_CURRENT_WRITE},
            roles={"plan", "admin"},
            handler=_bind_current_node_interface,
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
        handler_tool(
            name="list_recent_content_task_results",
            description="List terminal Content node task results from the current Coordinator callback context.",
            args_model=ContentTaskResultListArgs,
            capability=ToolCapability.READ,
            result_view="content_task_results",
            groups={AppGroup.CONTENT_TASK_RESULT_COORDINATOR_FINALIZE},
            roles=coordinator_roles,
            handler=_list_recent_content_task_results,
        ),
        handler_tool(
            name="inspect_content_task_result",
            description="Inspect one terminal Content node task result from the current Coordinator callback context.",
            args_model=ContentTaskResultInspectArgs,
            capability=ToolCapability.READ,
            result_view="content_task_result",
            groups={AppGroup.CONTENT_TASK_RESULT_COORDINATOR_FINALIZE},
            roles=coordinator_roles,
            handler=_inspect_content_task_result,
        ),
        handler_tool(
            name="commit_content_contract",
            description="Commit the current open Content node contract after its latest terminal task result has been reviewed.",
            args_model=NodeContractCommitArgs,
            capability=ToolCapability.WRITE,
            result_view="content_task_finalize",
            groups={AppGroup.CONTENT_TASK_RESULT_COORDINATOR_FINALIZE},
            roles=coordinator_roles,
            handler=_commit_content_contract,
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
        handler_tool(
            name="list_scope_exports",
            description="List current exports on a Scope contract.",
            args_model=ScopePathArgs,
            capability=ToolCapability.READ,
            result_view="scope_export_list",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_READ},
            roles=all_roles,
            handler=_list_scope_exports,
        ),
        handler_tool(
            name="add_scope_export",
            description="Add one declaration to a Scope export list and return only the mutation receipt.",
            args_model=ScopeExportAddArgs,
            capability=ToolCapability.WRITE,
            result_view="scope_export_mutation_receipt",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_add_scope_export,
        ),
        handler_tool(
            name="remove_scope_export",
            description="Remove one Scope export by list index and return only the mutation receipt.",
            args_model=ScopeExportRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="scope_export_mutation_receipt",
            groups={AppGroup.SCOPE_EXPORT_INTERFACE_WRITE},
            roles=coordinator_roles,
            handler=_remove_scope_export,
        ),
        handler_tool(
            name="commit_scope_contract",
            description="Run the Scope close gate and commit the current open Scope contract when exports and interface bindings are stable.",
            args_model=NodeContractCommitArgs,
            capability=ToolCapability.WRITE,
            result_view="scope_contract_commit_receipt",
            groups={AppGroup.SCOPE_CONTRACT_COORDINATOR_COMMIT},
            roles=coordinator_roles,
            handler=_commit_scope_contract,
        ),
        handler_tool(
            name="get_scope_close_view",
            description="Read compact Scope close readiness counts and blocking gate findings.",
            args_model=ScopePathArgs,
            capability=ToolCapability.READ,
            result_view="scope_ready_gate",
            groups={AppGroup.SCOPE_CLOSE_READ},
            roles=coordinator_roles,
            handler=_get_scope_close_view,
        ),
        handler_tool(
            name="get_repo_ready_node_view",
            description="Preview whether the current repository state is a valid release candidate and return its blocking findings.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="repo_release_candidate_readiness",
            groups={AppGroup.REPO_READY_READ},
            roles=coordinator_roles,
            handler=_get_repo_ready_node_view,
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
