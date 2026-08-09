"""Mathlib index, toolkit ingestion, and current-node hint tools."""

from __future__ import annotations

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.tool_facade import ToolCapability, ToolSpec
from lean_constellation.tools.args import (
    ArxivTheoremSearchArgs,
    CurrentMathlibDeclUseArgs,
    CurrentMathlibHintsAddArgs,
    CurrentMathlibModuleUseArgs,
    MathlibCandidateArgs,
    MathlibCandidateIngestArgs,
    MathlibBatchRecordArgs,
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


class MathlibEntryMutationReceipt(StrictModel):
    operation: str
    changed: bool
    entry_kind: str
    module: str | None = None
    declaration: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    added_important_declaration: str | None = None
    summary: str


def _changed_fields(before, after) -> list[str]:  # noqa: ANN001
    before_data = before.model_dump(mode="json") if before is not None else {}
    after_data = after.model_dump(mode="json")
    return sorted(key for key, value in after_data.items() if before_data.get(key) != value)


def _record_mathlib_module(runtime, ctx, args: MathlibModuleRecordArgs):
    before = runtime.mathlib.get_mathlib_module_entry(ctx.repo_root, module=args.module_name)
    previous = before.value if before.ok else None
    recorded = runtime.mathlib.record_mathlib_module_checked(
        ctx.repo_root,
        module_name=args.module_name,
        summary=args.summary,
        source=args.source,
    )
    if not recorded.ok or recorded.value is None:
        return runtime.foundation.fail(recorded.issues)
    fields = _changed_fields(previous, recorded.value)
    return runtime.foundation.ok(
        MathlibEntryMutationReceipt(
            operation="record",
            changed=bool(fields),
            entry_kind="module",
            module=recorded.value.module,
            changed_fields=fields,
            summary=("Recorded Mathlib module changes." if fields else "Mathlib module entry was already current."),
        ),
        warnings=recorded.issues,
    )


def _record_mathlib_decl(runtime, ctx, args: MathlibDeclRecordArgs):
    before = runtime.mathlib.get_mathlib_decl_entry(ctx.repo_root, name=args.decl_name)
    previous = before.value if before.ok else None
    recorded = runtime.mathlib.record_mathlib_decl_checked(
        ctx.repo_root,
        decl_name=args.decl_name,
        summary=args.summary,
        source=args.source,
    )
    if not recorded.ok or recorded.value is None:
        return runtime.foundation.fail(recorded.issues)
    fields = _changed_fields(previous, recorded.value)
    return runtime.foundation.ok(
        MathlibEntryMutationReceipt(
            operation="record",
            changed=bool(fields),
            entry_kind="declaration",
            module=recorded.value.module,
            declaration=recorded.value.name,
            changed_fields=fields,
            summary=("Recorded Mathlib declaration changes." if fields else "Mathlib declaration entry was already current."),
        ),
        warnings=recorded.issues,
    )


def _add_mathlib_module_important_decl(runtime, ctx, args: MathlibModuleDeclArgs):
    before = runtime.mathlib.get_mathlib_module_entry(ctx.repo_root, module=args.module)
    previous_names = set(before.value.important_decl_names) if before.ok and before.value is not None else set()
    updated = runtime.mathlib.add_module_important_decl(ctx.repo_root, module=args.module, decl_name=args.decl_name)
    if not updated.ok or updated.value is None:
        return runtime.foundation.fail(updated.issues)
    changed = args.decl_name not in previous_names
    return runtime.foundation.ok(
        MathlibEntryMutationReceipt(
            operation="add_important_declaration",
            changed=changed,
            entry_kind="module",
            module=updated.value.module,
            added_important_declaration=args.decl_name,
            summary=(
                "Added an important declaration to the Mathlib module entry."
                if changed
                else "The important declaration was already recorded for this module."
            ),
        ),
        warnings=updated.issues,
    )


def _current_hint_view(runtime, ctx, args):
    del args
    return runtime.mathlib.get_node_mathlib_hint_view(ctx.repo_root, node_path=current_node_path(ctx))


def _remove_current_module_hint(runtime, ctx, args: CurrentMathlibModuleUseArgs):
    return runtime.mathlib.remove_mathlib_module_use(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        module=args.module,
        actor=actor_for_write(ctx),
    )


def _add_current_mathlib_hints(runtime, ctx, args: CurrentMathlibHintsAddArgs):
    return runtime.mathlib.add_mathlib_hints(
        ctx.repo_root,
        node_path=current_node_path(ctx),
        modules=[(item.name, item.reason) for item in args.modules],
        declarations=[(item.name, item.reason) for item in args.declarations],
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
        handler_tool(
            name="record_mathlib_module",
            description="Verify and record one Mathlib module, returning only the changed fields receipt.",
            args_model=MathlibModuleRecordArgs,
            capability=ToolCapability.WRITE,
            result_view="mathlib_entry_mutation_receipt",
            groups={AppGroup.MATHLIB_INDEX_WRITE},
            roles=write_roles,
            handler=_record_mathlib_module,
        ),
        handler_tool(
            name="record_mathlib_decl",
            description=(
                "Resolve compiler/index metadata for one exact Mathlib declaration name, verify accessibility from the "
                "current repo, record it, and return only the changed fields receipt."
            ),
            args_model=MathlibDeclRecordArgs,
            capability=ToolCapability.WRITE,
            result_view="mathlib_entry_mutation_receipt",
            groups={AppGroup.MATHLIB_INDEX_WRITE},
            roles=write_roles,
            handler=_record_mathlib_decl,
        ),
        direct_tool(
            name="record_mathlib_batch",
            description=(
                "Resolve declaration metadata, verify up to 25 understood Mathlib modules and declarations in one Lean "
                "snippet, then record the checked entries in the repo-level MathlibIndex."
            ),
            args_model=MathlibBatchRecordArgs,
            capability=ToolCapability.WRITE,
            backing_service="mathlib",
            backing_method="record_mathlib_batch_checked",
            result_view="mathlib_batch_record",
            groups={AppGroup.MATHLIB_INDEX_WRITE},
            roles=write_roles,
        ),
        handler_tool(
            name="add_mathlib_module_important_decl",
            description="Attach one important declaration to a Mathlib module and return only the mutation receipt.",
            args_model=MathlibModuleDeclArgs,
            capability=ToolCapability.WRITE,
            result_view="mathlib_entry_mutation_receipt",
            groups={AppGroup.MATHLIB_INDEX_WRITE},
            roles=write_roles,
            handler=_add_mathlib_module_important_decl,
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
            description="Use LeanExplore-backed semantic search to return compact ranked Mathlib declaration handles. Inspect only plausible candidates.",
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
            description="Inspect one cached Mathlib candidate. Source excerpts are omitted unless explicitly requested.",
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
            description="Navigate one Mathlib module with bounded compact declarations; imports and source excerpts are opt-in.",
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
            groups={AppGroup.EXTERNAL_THEOREM_SEARCH_READ},
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
            name="add_current_mathlib_hints",
            description="Add one batch of current-node Mathlib module and declaration hints and return only the delta.",
            args_model=CurrentMathlibHintsAddArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_delta",
            groups={AppGroup.NODE_MATHLIB_HINT_WRITE},
            roles=write_roles,
            handler=_add_current_mathlib_hints,
        ),
        handler_tool(
            name="remove_current_mathlib_module_hint",
            description="Remove a Mathlib module hint from the current node contract and report any generated Prelude file change.",
            args_model=CurrentMathlibModuleUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
            groups={AppGroup.NODE_MATHLIB_HINT_WRITE},
            roles=write_roles,
            handler=_remove_current_module_hint,
        ),
        handler_tool(
            name="remove_current_mathlib_decl_hint",
            description="Remove a Mathlib declaration hint from the current node contract and report any generated Prelude file change.",
            args_model=CurrentMathlibDeclUseArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
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
            groups={AppGroup.NODE_CONTRACT_MATHLIB_WRITE_BY_NODE},
            roles={"coordinator", "admin"},
            handler=_add_node_module_hint,
        ),
        handler_tool(
            name="remove_node_mathlib_module_hint",
            description="Remove a Mathlib module hint from the target node contract.",
            args_model=NodeMathlibModuleHintArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
            groups={AppGroup.NODE_CONTRACT_MATHLIB_WRITE_BY_NODE},
            roles={"coordinator", "admin"},
            handler=_remove_node_module_hint,
        ),
        handler_tool(
            name="add_node_mathlib_decl_hint",
            description="Add a recorded Mathlib declaration as a hint on the target node contract.",
            args_model=NodeMathlibDeclHintArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
            groups={AppGroup.NODE_CONTRACT_MATHLIB_WRITE_BY_NODE},
            roles={"coordinator", "admin"},
            handler=_add_node_decl_hint,
        ),
        handler_tool(
            name="remove_node_mathlib_decl_hint",
            description="Remove a Mathlib declaration hint from the target node contract.",
            args_model=NodeMathlibDeclHintArgs,
            capability=ToolCapability.WRITE,
            result_view="node_mathlib_hint_mutation",
            groups={AppGroup.NODE_CONTRACT_MATHLIB_WRITE_BY_NODE},
            roles={"coordinator", "admin"},
            handler=_remove_node_decl_hint,
        ),
    ]
