"""Mathlib index, toolkit ingestion, and current-node hint tools."""

from __future__ import annotations

from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    ArxivTheoremSearchArgs,
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
    NodeMathlibDeclHintArgs,
    NodeMathlibModuleHintArgs,
    MathlibSemanticSearchArgs,
    NoArgs,
)
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
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


def _add_node_module_hint(runtime, ctx, args: NodeMathlibModuleHintArgs):
    return runtime.mathlib.add_node_mathlib_module_hint(
        ctx.repo_root,
        node_path=args.node_path,
        module=args.module,
        reason=args.reason,
        actor="coordinator",
    )


def _remove_node_module_hint(runtime, ctx, args: NodeMathlibModuleHintArgs):
    return runtime.mathlib.remove_node_mathlib_module_hint(
        ctx.repo_root,
        node_path=args.node_path,
        module=args.module,
        actor="coordinator",
    )


def _add_node_decl_hint(runtime, ctx, args: NodeMathlibDeclHintArgs):
    return runtime.mathlib.add_node_mathlib_decl_hint(
        ctx.repo_root,
        node_path=args.node_path,
        decl_name=args.decl_name,
        reason=args.reason,
        actor="coordinator",
    )


def _remove_node_decl_hint(runtime, ctx, args: NodeMathlibDeclHintArgs):
    return runtime.mathlib.remove_node_mathlib_decl_hint(
        ctx.repo_root,
        node_path=args.node_path,
        decl_name=args.decl_name,
        actor="coordinator",
    )


def _search_arxiv_theorems(runtime, ctx, args: ArxivTheoremSearchArgs):
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
            description="Search recorded repo-level MathlibIndex module and declaration entries by text, regex, or entry kind.",
            args_model=MathlibIndexSearchArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="search_mathlib_index",
            result_view="mathlib_search",
            groups={AppGroup.MATHLIB_INDEX_READ},
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
            groups={AppGroup.MATHLIB_INDEX_READ},
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
            groups={AppGroup.MATHLIB_INDEX_READ},
            roles=roles,
        ),
        direct_tool(
            name="record_mathlib_module",
            description="Verify that a Mathlib module is importable from the current repo and record its reusable purpose in the repo-level MathlibIndex.",
            args_model=MathlibModuleRecordArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="record_mathlib_module_checked",
            result_view="mathlib_module_entry",
            groups={AppGroup.MATHLIB_INDEX_WRITE},
            roles=write_roles,
        ),
        direct_tool(
            name="record_mathlib_decl",
            description="Verify that a Mathlib declaration is accessible from the current repo and record its statement, kind, module, and reuse notes in the repo-level MathlibIndex.",
            args_model=MathlibDeclRecordArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="record_mathlib_decl_checked",
            result_view="mathlib_decl_entry",
            groups={AppGroup.MATHLIB_INDEX_WRITE},
            roles=write_roles,
        ),
        direct_tool(
            name="add_mathlib_module_important_decl",
            description="Attach a recorded Mathlib declaration name as an important reusable declaration for a recorded Mathlib module entry.",
            args_model=MathlibModuleDeclArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="add_module_important_decl",
            result_view="mathlib_module_entry",
            groups={AppGroup.MATHLIB_INDEX_WRITE},
            roles=write_roles,
        ),
        direct_tool(
            name="search_external_mathlib",
            description="Run configured toolkit-backed Mathlib search backends and return candidate declarations or theorem references for the query.",
            args_model=MathlibExternalSearchArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="search_external_mathlib",
            result_view="mathlib_external_search",
            groups={AppGroup.MATHLIB_SEMANTIC_SEARCH},
            roles=roles,
        ),
        direct_tool(
            name="search_mathlib_declarations",
            description="Run LeanExplore-backed semantic search over Mathlib declarations and return ranked candidate ids for later inspection or ingestion.",
            args_model=MathlibSemanticSearchArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="search_mathlib_declarations",
            result_view="mathlib_semantic_search",
            groups={AppGroup.MATHLIB_SEMANTIC_SEARCH},
            roles=roles,
        ),
        direct_tool(
            name="inspect_mathlib_search_candidate",
            description="Inspect a cached Mathlib search candidate and enrich it with declaration metadata, module context, and source or type details when available.",
            args_model=MathlibCandidateArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="inspect_mathlib_search_candidate",
            result_view="mathlib_candidate",
            groups={AppGroup.MATHLIB_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="inspect_mathlib_declaration",
            description="Inspect one Mathlib declaration through toolkit navigation and return its module, kind, type, documentation, and source context when available.",
            args_model=MathlibDeclNameArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="inspect_mathlib_declaration",
            result_view="mathlib_navigation",
            groups={AppGroup.MATHLIB_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="inspect_mathlib_module",
            description="Inspect one Mathlib module and optionally filter its declarations or source context by a text pattern.",
            args_model=MathlibInspectModuleArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="inspect_mathlib_module",
            result_view="mathlib_module_navigation",
            groups={AppGroup.MATHLIB_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="check_mathlib_name",
            description="Check whether a Mathlib declaration name is available after importing the given module in the current repo context.",
            args_model=MathlibModuleDeclArgs,
            capability=ToolCapability.READ,
            backing_service="mathlib",
            backing_method="check_mathlib_name",
            result_view="mathlib_check",
            groups={AppGroup.MATHLIB_NAVIGATION},
            roles=roles,
        ),
        direct_tool(
            name="ingest_mathlib_candidate",
            description="Verify a cached Mathlib search candidate and store it as a checked declaration entry in the repo-level MathlibIndex.",
            args_model=MathlibCandidateIngestArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="ingest_mathlib_candidate",
            result_view="mathlib_decl_entry",
            groups={AppGroup.MATHLIB_INDEX_WRITE},
            roles=write_roles,
        ),
        handler_tool(
            name="search_arxiv_theorems",
            description=(
                "Search arXiv theorem-like statements through the Lean toolkit integration; explicit arXiv id queries "
                "can fall back to parsing real e-print TeX source when the remote theorem provider fails."
            ),
            args_model=ArxivTheoremSearchArgs,
            capability=ToolCapability.READ,
            result_view="arxiv_theorem_search",
            groups={AppGroup.EXTERNAL_RESOURCE_DISCOVERY, AppGroup.EXTERNAL_MATERIAL_SEARCH_READ},
            roles=roles,
            handler=_search_arxiv_theorems,
            required_context=set(),
        ),
        handler_tool(
            name="get_current_node_mathlib_hints",
            description="Read Mathlib module and declaration hints recorded on the current node contract.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="node_mathlib_hints",
            groups={AppGroup.NODE_MATHLIB_HINT_READ},
            roles=roles,
            handler=_current_hint_view,
        ),
        handler_tool(
            name="add_current_mathlib_module_hint",
            description="Add a recorded Mathlib module as a current-node hint with a reason tied to the node objective.",
            args_model=CurrentMathlibModuleUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={AppGroup.NODE_MATHLIB_HINT_WRITE},
            roles=write_roles,
            handler=_add_current_module_hint,
        ),
        handler_tool(
            name="remove_current_mathlib_module_hint",
            description="Remove a Mathlib module hint from the current node contract when it is stale or no longer useful.",
            args_model=CurrentMathlibModuleUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={AppGroup.NODE_MATHLIB_HINT_WRITE},
            roles=write_roles,
            handler=_remove_current_module_hint,
        ),
        handler_tool(
            name="add_current_mathlib_decl_hint",
            description="Add a recorded Mathlib declaration as a current-node hint with a reason tied to the node objective.",
            args_model=CurrentMathlibDeclUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={AppGroup.NODE_MATHLIB_HINT_WRITE},
            roles=write_roles,
            handler=_add_current_decl_hint,
        ),
        handler_tool(
            name="remove_current_mathlib_decl_hint",
            description="Remove a Mathlib declaration hint from the current node contract when it is stale or no longer useful.",
            args_model=CurrentMathlibDeclUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_contract",
            groups={AppGroup.NODE_MATHLIB_HINT_WRITE},
            roles=write_roles,
            handler=_remove_current_decl_hint,
        ),
        handler_tool(
            name="validate_current_node_mathlib_hints",
            description="Validate that current-node Mathlib hints refer to entries recorded in the repo-level MathlibIndex.",
            args_model=NoArgs,
            capability=ToolCapability.READ,
            result_view="gate_report",
            groups={AppGroup.NODE_MATHLIB_HINT_READ},
            roles=roles,
            handler=_validate_current_mathlib_hints,
        ),
        handler_tool(
            name="add_node_mathlib_module_hint",
            description="Add a recorded Mathlib module as a hint on the target node contract.",
            args_model=NodeMathlibModuleHintArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
            groups={AppGroup.NODE_CONTRACT_MATHLIB_COORDINATOR_WRITE},
            roles={"coordinator", "admin"},
            handler=_add_node_module_hint,
        ),
        handler_tool(
            name="remove_node_mathlib_module_hint",
            description="Remove a Mathlib module hint from the target node contract.",
            args_model=NodeMathlibModuleHintArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
            groups={AppGroup.NODE_CONTRACT_MATHLIB_COORDINATOR_WRITE},
            roles={"coordinator", "admin"},
            handler=_remove_node_module_hint,
        ),
        handler_tool(
            name="add_node_mathlib_decl_hint",
            description="Add a recorded Mathlib declaration as a hint on the target node contract.",
            args_model=NodeMathlibDeclHintArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
            groups={AppGroup.NODE_CONTRACT_MATHLIB_COORDINATOR_WRITE},
            roles={"coordinator", "admin"},
            handler=_add_node_decl_hint,
        ),
        handler_tool(
            name="remove_node_mathlib_decl_hint",
            description="Remove a Mathlib declaration hint from the target node contract.",
            args_model=NodeMathlibDeclHintArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
            groups={AppGroup.NODE_CONTRACT_MATHLIB_COORDINATOR_WRITE},
            roles={"coordinator", "admin"},
            handler=_remove_node_decl_hint,
        ),
    ]
