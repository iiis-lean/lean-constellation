"""Adapter repo catalog and upstream navigation tools."""

from __future__ import annotations

from typing import Literal

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclKind
from lean_constellation.domain.refs import DeclRef
from lean_constellation.services.decl_graph.models import (
    DeclOriginRef,
    DeclProof,
    DeclStatement,
    RepoDeclDep,
)
from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    AdapterDeclCreateArgs,
    AdapterDeclListArgs,
    AdapterDeclNameArgs,
    AdapterDeclOptionalNameArgs,
    AdapterDeclUpstreamFindArgs,
    AdapterDepArgs,
    AdapterDepRemoveArgs,
    AdapterFormalArgs,
    AdapterInterfaceBindArgs,
    AdapterInterfaceUnbindArgs,
    AdapterNlArgs,
    AdapterOriginArgs,
    NoArgs,
    QueryLimitArgs,
    UpstreamCaptureArgs,
    UpstreamDeclInspectArgs,
    UpstreamDeclSearchArgs,
    UpstreamModuleDeclsArgs,
    UpstreamModuleArgs,
    UpstreamSourceContextArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.specs import direct_tool, handler_tool


class AdapterDeclInspectView(StrictModel):
    name: str
    kind: DeclKind
    module: str
    lean_decl_name: str
    state: str
    status: str
    finalized: bool
    public: bool
    released_state: str | None = None
    release_protected: bool = False
    summary: str
    statement: DeclStatement
    proof: DeclProof | None = None


class AdapterDeclCreateReceipt(StrictModel):
    name: str
    kind: DeclKind
    module: str
    lean_decl_name: str
    state: str
    status: str
    summary: str


class AdapterDeclFieldMutationReceipt(StrictModel):
    name: str
    section: Literal["statement_nl", "statement_formal", "proof_nl", "proof_formal"]
    changed: bool
    resulting_state: str
    summary: str


class AdapterDeclOriginMutationReceipt(StrictModel):
    name: str
    section: Literal["statement", "proof"]
    operation: Literal["add"]
    origin: DeclOriginRef
    summary: str


class AdapterDeclDependencyMutationReceipt(StrictModel):
    name: str
    section: Literal["statement", "proof"]
    operation: Literal["add", "remove"]
    dependency: RepoDeclDep
    summary: str


class AdapterDeclFinalizeReceipt(StrictModel):
    name: str
    finalized: bool
    released_state: str | None = None
    state: str
    status: str
    summary: str


def _adapter_decl_inspect_view(value) -> AdapterDeclInspectView:
    return AdapterDeclInspectView(
        name=value.name,
        kind=value.kind,
        module=value.module,
        lean_decl_name=value.lean_decl_name,
        state=value.state,
        status=value.status,
        finalized=value.finalized,
        public=value.public,
        released_state=value.released_state,
        release_protected=value.release_protected,
        summary=value.summary,
        statement=value.revision.statement,
        proof=value.revision.proof,
    )


def _create_adapter_decl(runtime, ctx, args: AdapterDeclCreateArgs):
    created = runtime.adapter.create_adapter_decl(
        ctx.repo_root, **args.model_dump(exclude_unset=True)
    )
    if not created.ok or created.value is None:
        return runtime.foundation.fail(created.issues)
    return runtime.foundation.ok(
        AdapterDeclCreateReceipt(
            name=created.value.name,
            kind=created.value.kind,
            module=created.value.module,
            lean_decl_name=created.value.lean_decl_name,
            state=created.value.state,
            status=created.value.status,
            summary="Created adapter declaration.",
        ),
        warnings=created.issues,
    )


def _adapter_field_mutation(section: str, method_name: str):
    def handler(runtime, ctx, args):  # noqa: ANN001
        before = runtime.adapter.inspect_adapter_decl(ctx.repo_root, name=args.name)
        previous = before.value if before.ok else None
        updated = getattr(runtime.adapter, method_name)(
            ctx.repo_root, **args.model_dump(exclude_unset=True)
        )
        if not updated.ok or updated.value is None:
            return runtime.foundation.fail(updated.issues)
        previous_revision = previous.revision if previous is not None else None
        changed = previous_revision is None or previous_revision.model_dump(mode="json") != updated.value.revision.model_dump(mode="json")
        return runtime.foundation.ok(
            AdapterDeclFieldMutationReceipt(
                name=updated.value.name,
                section=section,
                changed=changed,
                resulting_state=updated.value.state,
                summary=f"Updated adapter declaration {section}.",
            ),
            warnings=updated.issues,
        )

    return handler


def _adapter_origin_mutation(section: Literal["statement", "proof"], method_name: str):
    def handler(runtime, ctx, args: AdapterOriginArgs):
        updated = getattr(runtime.adapter, method_name)(
            ctx.repo_root, **args.model_dump(exclude_unset=True)
        )
        if not updated.ok or updated.value is None:
            return runtime.foundation.fail(updated.issues)
        natural = (
            updated.value.revision.statement.nl
            if section == "statement"
            else updated.value.revision.proof.nl
        )
        if natural is None or not natural.origin:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "adapter_origin_receipt_missing",
                    "Updated adapter origin was not present after mutation.",
                    object_ref=args.name,
                )
            )
        return runtime.foundation.ok(
            AdapterDeclOriginMutationReceipt(
                name=args.name,
                section=section,
                operation="add",
                origin=natural.origin[-1],
                summary=f"Added adapter {section} origin.",
            ),
            warnings=updated.issues,
        )

    return handler


def _adapter_dependency_mutation(
    section: Literal["statement", "proof"],
    operation: Literal["add", "remove"],
    method_name: str,
):
    def handler(runtime, ctx, args):  # noqa: ANN001
        updated = getattr(runtime.adapter, method_name)(
            ctx.repo_root, **args.model_dump(exclude_unset=True)
        )
        if not updated.ok or updated.value is None:
            return runtime.foundation.fail(updated.issues)
        deps = (
            updated.value.revision.statement.deps
            if section == "statement"
            else (updated.value.revision.proof.deps if updated.value.revision.proof else [])
        )
        dep = next(
            (
                item
                for item in deps
                if isinstance(item, RepoDeclDep) and item.ref.name == args.dep_name
            ),
            None,
        )
        if operation == "remove":
            dep = RepoDeclDep(
                ref=DeclRef(repo=None, node="Main", name=args.dep_name, revision=1),
            )
        if dep is None:
            return runtime.foundation.fail(
                runtime.foundation.issue(
                    "adapter_dependency_receipt_missing",
                    "Updated adapter dependency was not present after mutation.",
                    object_ref=args.name,
                )
            )
        return runtime.foundation.ok(
            AdapterDeclDependencyMutationReceipt(
                name=args.name,
                section=section,
                operation=operation,
                dependency=dep,
                summary=(
                    f"Added adapter {section} dependency."
                    if operation == "add"
                    else f"Removed adapter {section} dependency."
                ),
            ),
            warnings=updated.issues,
        )

    return handler


def _inspect_adapter_decl(runtime, ctx, args: AdapterDeclNameArgs):
    inspected = runtime.adapter.inspect_adapter_decl(ctx.repo_root, name=args.name)
    if not inspected.ok or inspected.value is None:
        return runtime.foundation.fail(inspected.issues)
    return runtime.foundation.ok(
        _adapter_decl_inspect_view(inspected.value),
        warnings=inspected.issues,
    )


def _finalize_adapter_decl(runtime, ctx, args: AdapterDeclNameArgs):
    finalized = runtime.adapter.finalize_adapter_decl(ctx.repo_root, name=args.name)
    if not finalized.ok or finalized.value is None:
        return runtime.foundation.fail(finalized.issues)
    return runtime.foundation.ok(
        AdapterDeclFinalizeReceipt(
            name=finalized.value.name,
            finalized=finalized.value.finalized,
            released_state=finalized.value.released_state,
            state=finalized.value.state,
            status=finalized.value.status,
            summary=finalized.value.summary,
        ),
        warnings=finalized.issues,
    )


def build_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "worker", "reviewer", "admin"}
    write_roles = {"worker", "admin"}
    return [
        direct_tool(
            name="inspect_adapter_input",
            description="Read adapter preparation input and expected interfaces.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="inspect_adapter_input",
            result_view="adapter_input",
            groups={AppGroup.ADAPTER_INPUT_READ},
            roles=roles,
        ),
        direct_tool(
            name="get_adapter_upstream_metadata",
            description="Read adapter upstream metadata.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="get_adapter_upstream_metadata",
            result_view="adapter_upstream",
            groups={AppGroup.UPSTREAM_METADATA_READ},
            roles=roles,
        ),
        direct_tool(
            name="get_adapter_upstream_status",
            description="Read adapter upstream build and module visibility status.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="get_adapter_upstream_status",
            result_view="adapter_upstream_status",
            groups={AppGroup.UPSTREAM_METADATA_READ},
            roles=roles,
        ),
        direct_tool(
            name="search_upstream_declarations",
            description="Search declarations in the adapter upstream Lean repo.",
            args_model=UpstreamDeclSearchArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="search_upstream_declarations",
            result_view="upstream_decl_search",
            groups={AppGroup.UPSTREAM_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="search_upstream_modules",
            description="Search modules in the adapter upstream Lean repo.",
            args_model=QueryLimitArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="search_upstream_modules",
            result_view="upstream_module_search",
            groups={AppGroup.UPSTREAM_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="list_upstream_module_declarations",
            description="List declarations in one upstream module.",
            args_model=UpstreamModuleDeclsArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="list_upstream_module_declarations",
            result_view="upstream_module_decls",
            groups={AppGroup.UPSTREAM_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="inspect_upstream_declaration",
            description="Inspect one upstream declaration.",
            args_model=UpstreamDeclInspectArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="inspect_upstream_declaration",
            result_view="upstream_decl_detail",
            groups={AppGroup.UPSTREAM_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="read_upstream_source_context",
            description="Read upstream source context around a module or declaration.",
            args_model=UpstreamSourceContextArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="read_upstream_source_context",
            result_view="upstream_source_context",
            groups={AppGroup.UPSTREAM_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="capture_upstream_declaration_code",
            description="Capture upstream declaration code for adapter declaration fields.",
            args_model=UpstreamCaptureArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="capture_upstream_declaration_code",
            result_view="upstream_capture",
            groups={AppGroup.UPSTREAM_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="inspect_upstream_module_imports",
            description="Inspect imports of one upstream module.",
            args_model=UpstreamModuleArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="inspect_upstream_module_imports",
            result_view="upstream_module_imports",
            groups={AppGroup.UPSTREAM_NAVIGATION},
            roles=roles,
        ),
        handler_tool(
            name="create_adapter_decl",
            description="Create an adapter declaration catalog entry.",
            args_model=AdapterDeclCreateArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_create_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_create_adapter_decl,
        ),
        handler_tool(
            name="set_adapter_statement_formal",
            description="Set adapter declaration formal statement code.",
            args_model=AdapterFormalArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_field_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_field_mutation("statement_formal", "set_adapter_statement_formal"),
        ),
        handler_tool(
            name="set_adapter_statement_nl",
            description="Set adapter declaration natural-language statement.",
            args_model=AdapterNlArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_field_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_field_mutation("statement_nl", "set_adapter_statement_nl"),
        ),
        handler_tool(
            name="add_adapter_statement_origin",
            description="Add statement origin text to an adapter declaration.",
            args_model=AdapterOriginArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_origin_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_origin_mutation("statement", "add_adapter_statement_origin"),
        ),
        handler_tool(
            name="add_adapter_statement_dep",
            description="Add a statement dependency to an adapter declaration.",
            args_model=AdapterDepArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_dependency_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_dependency_mutation("statement", "add", "add_adapter_statement_dep"),
        ),
        handler_tool(
            name="remove_adapter_statement_dep",
            description="Remove a statement dependency from an adapter declaration.",
            args_model=AdapterDepRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_dependency_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_dependency_mutation("statement", "remove", "remove_adapter_statement_dep"),
        ),
        handler_tool(
            name="set_adapter_proof_formal",
            description="Set adapter declaration formal proof code.",
            args_model=AdapterFormalArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_field_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_field_mutation("proof_formal", "set_adapter_proof_formal"),
        ),
        handler_tool(
            name="set_adapter_proof_nl",
            description="Set adapter declaration natural-language proof.",
            args_model=AdapterNlArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_field_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_field_mutation("proof_nl", "set_adapter_proof_nl"),
        ),
        handler_tool(
            name="add_adapter_proof_origin",
            description="Add proof origin text to an adapter declaration.",
            args_model=AdapterOriginArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_origin_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_origin_mutation("proof", "add_adapter_proof_origin"),
        ),
        handler_tool(
            name="add_adapter_proof_dep",
            description="Add a proof dependency to an adapter declaration.",
            args_model=AdapterDepArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_dependency_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_dependency_mutation("proof", "add", "add_adapter_proof_dep"),
        ),
        handler_tool(
            name="remove_adapter_proof_dep",
            description="Remove a proof dependency from an adapter declaration.",
            args_model=AdapterDepRemoveArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_dependency_mutation_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_adapter_dependency_mutation("proof", "remove", "remove_adapter_proof_dep"),
        ),
        direct_tool(
            name="list_adapter_decls",
            description="List adapter declarations with optional filters.",
            args_model=AdapterDeclListArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="list_adapter_decls",
            result_view="adapter_decl_list",
            groups={AppGroup.ADAPTER_DECL_CATALOG_READ},
            roles=roles,
        ),
        handler_tool(
            name="inspect_adapter_decl",
            description="Inspect one adapter declaration.",
            args_model=AdapterDeclNameArgs,
            capability=ToolCapability.READ,
            result_view="adapter_decl_inspect",
            groups={AppGroup.ADAPTER_DECL_CATALOG_READ},
            roles=roles,
            handler=_inspect_adapter_decl,
        ),
        direct_tool(
            name="list_registered_adapter_modules",
            description="List upstream modules represented in the adapter declaration catalog.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="list_registered_adapter_modules",
            result_view="adapter_modules",
            groups={AppGroup.ADAPTER_DECL_CATALOG_READ},
            roles=roles,
        ),
        direct_tool(
            name="check_adapter_decl_completeness",
            description="Check whether one or all adapter declarations have required fields.",
            args_model=AdapterDeclOptionalNameArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="check_adapter_decl_completeness",
            result_view="adapter_decl_completeness",
            groups={AppGroup.ADAPTER_DECL_CATALOG_READ},
            roles=roles,
        ),
        direct_tool(
            name="find_adapter_decl_by_upstream",
            description="Find existing adapter declarations for an upstream module and optional upstream declaration/name query.",
            args_model=AdapterDeclUpstreamFindArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="find_adapter_decl_by_upstream",
            result_view="adapter_decl_match",
            groups={AppGroup.ADAPTER_DECL_CATALOG_READ},
            roles=roles,
        ),
        handler_tool(
            name="finalize_adapter_decl",
            description="Finalize one complete adapter declaration entry.",
            args_model=AdapterDeclNameArgs,
            capability=ToolCapability.WRITE,
            result_view="adapter_decl_finalize_receipt",
            groups={AppGroup.ADAPTER_DECL_CATALOG_WRITE},
            roles=write_roles,
            handler=_finalize_adapter_decl,
        ),
        direct_tool(
            name="bind_adapter_interface",
            description="Bind a preparation interface to an adapter declaration.",
            args_model=AdapterInterfaceBindArgs,
            capability=ToolCapability.WRITE,
            backing_service="adapter",
            backing_method="bind_adapter_interface",
            result_view="adapter_interface_binding",
            groups={AppGroup.ADAPTER_INTERFACE_BINDING_WRITE},
            roles=write_roles,
        ),
        direct_tool(
            name="unbind_adapter_interface",
            description="Unbind an adapter interface.",
            args_model=AdapterInterfaceUnbindArgs,
            capability=ToolCapability.WRITE,
            backing_service="adapter",
            backing_method="unbind_adapter_interface",
            result_view="adapter_interface_binding",
            groups={AppGroup.ADAPTER_INTERFACE_BINDING_WRITE},
            roles=write_roles,
        ),
        direct_tool(
            name="list_unbound_adapter_interfaces",
            description="List required adapter interfaces that are not yet bound.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="list_unbound_adapter_interfaces",
            result_view="adapter_unbound_interfaces",
            groups={AppGroup.ADAPTER_INTERFACE_BINDING_READ},
            roles=roles,
        ),
        direct_tool(
            name="validate_adapter_interface_bindings",
            description="Validate adapter interface bindings.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="validate_adapter_interface_bindings",
            result_view="gate_report",
            groups={AppGroup.ADAPTER_INTERFACE_BINDING_READ},
            roles=roles,
        ),
        direct_tool(
            name="preview_adapter_import_modules",
            description="Preview modules that will be imported by the adapter facade.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="preview_adapter_import_modules",
            result_view="adapter_import_preview",
            groups={AppGroup.ADAPTER_PROJECTION_CHECK},
            roles=roles,
        ),
        direct_tool(
            name="check_adapter_projection",
            description="Check adapter projection consistency.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="check_adapter_projection",
            result_view="gate_report",
            groups={AppGroup.ADAPTER_PROJECTION_CHECK},
            roles=roles,
        ),
        direct_tool(
            name="check_adapter_ready",
            description="Check adapter ready gate without submitting.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="check_adapter_ready",
            result_view="gate_report",
            groups={AppGroup.ADAPTER_READY_READ},
            roles=roles,
        ),
        direct_tool(
            name="check_adapter_catalog_ready_preflight",
            description="Check adapter catalog readiness before submitting, excluding Flow-owned projection finalization.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            backing_service="adapter",
            backing_method="check_adapter_catalog_ready_preflight",
            result_view="gate_report",
            groups={AppGroup.ADAPTER_READY_READ},
            roles=roles,
        ),
    ]
