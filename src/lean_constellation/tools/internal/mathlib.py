"""Mathlib index, toolkit ingestion, and current-node hint tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    CurrentMathlibDeclUseArgs,
    CurrentMathlibModuleUseArgs,
    MathlibCandidateArgs,
    MathlibCandidateIngestArgs,
    MathlibDeclArgs,
    MathlibDeclNameArgs,
    MathlibDeclRecordArgs,
    MathlibExternalSearchArgs,
    MathlibIndexSearchArgs,
    MathlibInspectModuleArgs,
    MathlibModuleArgs,
    MathlibModuleDeclArgs,
    MathlibModuleRecordArgs,
    MathlibSemanticSearchArgs,
    NoArgs,
)
from lean_constellation.tools.specs import actor_for_write, current_node_path, direct_tool, handler_tool


def _current_hint_view(runtime, ctx, args):
    del args
    return runtime.mathlib.get_node_mathlib_hint_view(ctx.repo_root, node_path=current_node_path(ctx))


def _add_current_module_hint(runtime, ctx, args: CurrentMathlibModuleUseArgs):
    return runtime.mathlib.add_mathlib_module_use(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        module=args.module,
        reason=args.reason,
        actor=actor_for_write(ctx),
    )


def _remove_current_module_hint(runtime, ctx, args: CurrentMathlibModuleUseArgs):
    return runtime.mathlib.remove_mathlib_module_use(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        module=args.module,
        actor=actor_for_write(ctx),
    )


def _add_current_decl_hint(runtime, ctx, args: CurrentMathlibDeclUseArgs):
    return runtime.mathlib.add_mathlib_decl_use(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        decl_name=args.decl_name,
        reason=args.reason,
        actor=actor_for_write(ctx),
    )


def _remove_current_decl_hint(runtime, ctx, args: CurrentMathlibDeclUseArgs):
    return runtime.mathlib.remove_mathlib_decl_use(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        decl_name=args.decl_name,
        actor=actor_for_write(ctx),
    )


def _validate_current_mathlib_hints(runtime, ctx, args):
    del args
    return runtime.mathlib.validate_node_mathlib_uses(ctx.repo_root, node_path=current_node_path(ctx))


def _search_arxiv_theorems(runtime, ctx, args: MathlibSemanticSearchArgs):
    del ctx
    result = runtime.external.lean_mcp_toolkit.search_arxiv_theorems(args.query, limit=args.limit)
    if not result.ok:
        return runtime.foundation.fail(runtime.foundation.issue(result.issue_code or "arxiv_theorem_search_failed", result.summary))
    return runtime.foundation.ok(result)


def build_tool_specs() -> list[ToolSpec]:
    roles = {"coordinator", "plan", "worker", "reviewer", "admin"}
    write_roles = {"coordinator", "plan", "worker", "admin"}
    return [
        direct_tool(
            name="search_mathlib_index",
            description="Search the repo-level MathlibIndex modules and declarations.",
            args_model=MathlibIndexSearchArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="search_mathlib_index",
            result_view="mathlib_search",
            groups={"mathlib_index_read"},
            roles=roles,
        ),
        direct_tool(
            name="get_mathlib_module_entry",
            description="Read one recorded Mathlib module entry from the repo-level MathlibIndex.",
            args_model=MathlibModuleArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="get_mathlib_module_entry",
            result_view="mathlib_module_entry",
            groups={"mathlib_index_read"},
            roles=roles,
        ),
        direct_tool(
            name="get_mathlib_decl_entry",
            description="Read one recorded Mathlib declaration entry from the repo-level MathlibIndex.",
            args_model=MathlibDeclArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="get_mathlib_decl_entry",
            result_view="mathlib_decl_entry",
            groups={"mathlib_index_read"},
            roles=roles,
        ),
        direct_tool(
            name="record_mathlib_module",
            description="Check and record a Mathlib module in the repo-level MathlibIndex.",
            args_model=MathlibModuleRecordArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="record_mathlib_module_checked",
            result_view="mathlib_module_entry",
            groups={"mathlib_index_write"},
            roles=write_roles,
        ),
        direct_tool(
            name="record_mathlib_decl",
            description="Check and record a Mathlib declaration in the repo-level MathlibIndex.",
            args_model=MathlibDeclRecordArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="record_mathlib_decl_checked",
            result_view="mathlib_decl_entry",
            groups={"mathlib_index_write"},
            roles=write_roles,
        ),
        direct_tool(
            name="add_mathlib_module_important_decl",
            description="Record an important declaration under a Mathlib module entry.",
            args_model=MathlibModuleDeclArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="add_module_important_decl",
            result_view="mathlib_module_entry",
            groups={"mathlib_index_write"},
            roles=write_roles,
        ),
        direct_tool(
            name="search_external_mathlib",
            description="Search external Mathlib/theorem tools through the Lean toolkit integration.",
            args_model=MathlibExternalSearchArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="search_external_mathlib",
            result_view="mathlib_external_search",
            groups={"mathlib_semantic_search"},
            roles=roles,
        ),
        direct_tool(
            name="search_mathlib_declarations",
            description="Search Mathlib declarations semantically through Lean Explore/toolkit.",
            args_model=MathlibSemanticSearchArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="search_mathlib_declarations",
            result_view="mathlib_semantic_search",
            groups={"mathlib_semantic_search"},
            roles=roles,
        ),
        direct_tool(
            name="inspect_mathlib_search_candidate",
            description="Inspect and enrich a cached Mathlib search candidate.",
            args_model=MathlibCandidateArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="inspect_mathlib_search_candidate",
            result_view="mathlib_candidate",
            groups={"mathlib_navigation"},
            roles=roles,
        ),
        direct_tool(
            name="inspect_mathlib_declaration",
            description="Inspect one Mathlib declaration through the toolkit navigation layer.",
            args_model=MathlibDeclNameArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="inspect_mathlib_declaration",
            result_view="mathlib_navigation",
            groups={"mathlib_navigation"},
            roles=roles,
        ),
        direct_tool(
            name="inspect_mathlib_module",
            description="Inspect one Mathlib module and optionally filter declarations by pattern.",
            args_model=MathlibInspectModuleArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="inspect_mathlib_module",
            result_view="mathlib_module_navigation",
            groups={"mathlib_navigation"},
            roles=roles,
        ),
        direct_tool(
            name="check_mathlib_name",
            description="Check whether a Mathlib declaration name is accessible from the current repo.",
            args_model=MathlibModuleDeclArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="check_mathlib_name",
            result_view="mathlib_check",
            groups={"mathlib_navigation"},
            roles=roles,
        ),
        direct_tool(
            name="ingest_mathlib_candidate",
            description="Turn a cached Mathlib search candidate into a checked MathlibIndex declaration entry.",
            args_model=MathlibCandidateIngestArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="ingest_mathlib_candidate",
            result_view="mathlib_decl_entry",
            groups={"mathlib_index_write"},
            roles=write_roles,
        ),
        handler_tool(
            name="search_arxiv_theorems",
            description="Search arXiv theorem-like statements through the Lean toolkit integration.",
            args_model=MathlibSemanticSearchArgs,
            capability=ToolCapability.READ,
            result_view="arxiv_theorem_search",
            groups={"external_theorem_search"},
            roles=roles,
            handler=_search_arxiv_theorems,
            required_context=set(),
        ),
        handler_tool(
            name="get_current_node_mathlib_hints",
            description="Read Mathlib module/declaration hints on the current node contract.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="node_mathlib_hints",
            groups={"node_mathlib_hint_read"},
            roles=roles,
            handler=_current_hint_view,
        ),
        handler_tool(
            name="add_current_mathlib_module_hint",
            description="Add a Mathlib module hint to the current node contract.",
            args_model=CurrentMathlibModuleUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={"node_mathlib_hint_write"},
            roles=write_roles,
            handler=_add_current_module_hint,
        ),
        handler_tool(
            name="remove_current_mathlib_module_hint",
            description="Remove a Mathlib module hint from the current node contract.",
            args_model=CurrentMathlibModuleUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={"node_mathlib_hint_write"},
            roles=write_roles,
            handler=_remove_current_module_hint,
        ),
        handler_tool(
            name="add_current_mathlib_decl_hint",
            description="Add a Mathlib declaration hint to the current node contract.",
            args_model=CurrentMathlibDeclUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={"node_mathlib_hint_write"},
            roles=write_roles,
            handler=_add_current_decl_hint,
        ),
        handler_tool(
            name="remove_current_mathlib_decl_hint",
            description="Remove a Mathlib declaration hint from the current node contract.",
            args_model=CurrentMathlibDeclUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={"node_mathlib_hint_write"},
            roles=write_roles,
            handler=_remove_current_decl_hint,
        ),
        handler_tool(
            name="validate_current_node_mathlib_hints",
            description="Validate current node Mathlib hints against the repo-level MathlibIndex.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={"node_mathlib_hint_read"},
            roles=roles,
            handler=_validate_current_mathlib_hints,
        ),
    ]
