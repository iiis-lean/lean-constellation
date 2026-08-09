"""Submit ToolSpec registry bootstrap."""

from __future__ import annotations

from collections.abc import Sequence

from lean_constellation.services.foundation import MutationSummaryView, ServiceResult
from lean_constellation.services.runtime import LeanRuntimeServices
from lean_constellation.services.tool_facade import SubmitBehavior, ToolCapability, ToolGroupSpec, ToolSpec, ToolViewSpec
from lean_constellation.tools import submit_handlers as handlers
from lean_constellation.tools.keys import SubmitToolGroupKey as SubmitGroup
from lean_constellation.tools.submit_args import (
    SubmitAdapterCatalogBlockedArgs,
    SubmitAdapterCatalogReadyArgs,
    SubmitAdapterRepoChoiceArgs,
    SubmitAdapterRepoRequirementArgs,
    SubmitContentNodeBlockedArgs,
    SubmitContentNodeFailedArgs,
    SubmitContentNodeReadyArgs,
    SubmitContentNodeTasksArgs,
    SubmitContentPreparationReconArgs,
    SubmitCurrentDeclRoundArgs,
    SubmitExternalRepoRequiredArgs,
    SubmitLocalResourceCreatedArgs,
    SubmitMathlibReconCompletedArgs,
    SubmitNativeRepoChoiceArgs,
    SubmitNativeRepoRequirementArgs,
    SubmitNodeDirDependencyReconCompletedArgs,
    SubmitRepoReadyArgs,
    SubmitRepoExplorationArgs,
    SubmitRepoLeanProviderDiscoveryResultArgs,
    SubmitRepoMathlibReconResultArgs,
    SubmitRepoResourceDiscoveryResultArgs,
    SubmitRepoRequirementArgs,
    SubmitResourceDuplicateArgs,
    SubmitResourceReconBlockedArgs,
    SubmitResourceReconCompletedArgs,
    SubmitResourceRejectedArgs,
    SubmitResourceRequestArgs,
    SubmitRootInterfacePrepareReadyArgs,
    SubmitSourceCorpusBlockedArgs,
    SubmitSourceCorpusPreparedArgs,
    SubmitSourceIndexBuilderRoundArgs,
    SubmitSourceIndexReviewRoundArgs,
    SubmitStageReviewArgs,
    SubmitStageWorkerBlockedArgs,
    SubmitStageWorkerCompletedArgs,
)
from lean_constellation.tools.submit_groups import build_submit_tool_groups as _build_groups
from lean_constellation.tools.submit_views import build_submit_tool_views as _build_views
from lean_constellation.tools.specs import StringKey, submit_handler_tool


def _submit_tool(
    *,
    name: str,
    description: str,
    args_model: type,
    groups: set[StringKey],
    roles: set[str],
    handler,
    behavior: SubmitBehavior = SubmitBehavior.TERMINAL,
    result_view: str = "submit_submission",
    required_context: set[str] | None = None,
) -> ToolSpec:
    return submit_handler_tool(
        name=name,
        description=description,
        args_model=args_model,
        result_view=result_view,
        groups=groups,
        roles=roles,
        handler=handler,
        submit_behavior=behavior,
        required_context=required_context,
    )


def build_submit_tool_specs() -> list[ToolSpec]:
    """Collect every layer-3 submit ToolSpec."""

    specs = [
        _submit_tool(
            name="submit_adapter_repo_choice",
            description="Verify and submit an existing GitHub Lean project as the Adapter route; package, module, and exact compatible revision are derived by the backend.",
            args_model=SubmitAdapterRepoChoiceArgs,
            groups={SubmitGroup.REPO_FORMAT_DISCOVERY_SUBMIT},
            roles={"coordinator", "admin"},
            handler=handlers.submit_adapter_repo_choice,
        ),
        _submit_tool(
            name="submit_native_repo_choice",
            description="Submit a Native route after recording at least one concrete upstream search target that was checked.",
            args_model=SubmitNativeRepoChoiceArgs,
            groups={SubmitGroup.REPO_FORMAT_DISCOVERY_SUBMIT},
            roles={"coordinator", "admin"},
            handler=handlers.submit_native_repo_choice,
        ),
        _submit_tool(
            name="submit_source_corpus_prepared",
            description="Submit that the source corpus has been organized and is ready for indexing.",
            args_model=SubmitSourceCorpusPreparedArgs,
            groups={SubmitGroup.SOURCE_CORPUS_PREPARE_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_source_corpus_prepared,
        ),
        _submit_tool(
            name="submit_source_corpus_blocked",
            description="Submit that source corpus preparation cannot continue without external action.",
            args_model=SubmitSourceCorpusBlockedArgs,
            groups={SubmitGroup.SOURCE_CORPUS_PREPARE_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_source_corpus_blocked,
        ),
        _submit_tool(
            name="submit_source_index_builder_round",
            description="Submit the current SourceIndex draft for reviewer inspection.",
            args_model=SubmitSourceIndexBuilderRoundArgs,
            groups={SubmitGroup.SOURCE_INDEX_BUILDER_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_source_index_builder_round,
        ),
        _submit_tool(
            name="submit_source_index_review_round",
            description="Submit the SourceIndex reviewer decision for this round.",
            args_model=SubmitSourceIndexReviewRoundArgs,
            groups={SubmitGroup.SOURCE_INDEX_REVIEWER_SUBMIT},
            roles={"reviewer", "admin"},
            handler=handlers.submit_source_index_review_round,
        ),
        _submit_tool(
            name="submit_root_interface_prepare_ready",
            description="Submit that the Main scope interface preparation is ready for native Coordinator handoff.",
            args_model=SubmitRootInterfacePrepareReadyArgs,
            groups={SubmitGroup.ROOT_INTERFACE_PREPARE_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_root_interface_prepare_ready,
        ),
        _submit_tool(
            name="submit_adapter_catalog_ready",
            description="Submit that adapter declaration catalog and interface bindings are ready.",
            args_model=SubmitAdapterCatalogReadyArgs,
            groups={SubmitGroup.ADAPTER_READY_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_adapter_catalog_ready,
        ),
        _submit_tool(
            name="submit_adapter_catalog_blocked",
            description="Submit a genuine current catalog-preflight blocker with exact unbound interfaces and evidence.",
            args_model=SubmitAdapterCatalogBlockedArgs,
            groups={SubmitGroup.ADAPTER_READY_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_adapter_catalog_blocked,
        ),
        _submit_tool(
            name="submit_resource_request",
            description="Submit a request to dispatch ResourceCurationFlow for a precise resource target.",
            args_model=SubmitResourceRequestArgs,
            groups={SubmitGroup.RESOURCE_REQUEST_SUBMIT},
            roles={"coordinator", "plan", "worker", "admin"},
            handler=handlers.submit_resource_request,
            behavior=SubmitBehavior.DISPATCH_CHILD_FLOWS,
        ),
        _submit_tool(
            name="submit_resource_duplicate",
            description="Submit that the requested resource duplicates existing source or resource material.",
            args_model=SubmitResourceDuplicateArgs,
            groups={SubmitGroup.RESOURCE_CURATOR_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_resource_duplicate,
        ),
        _submit_tool(
            name="submit_local_resource_created",
            description="Submit that a local Resource has been finalized from the current resource draft.",
            args_model=SubmitLocalResourceCreatedArgs,
            groups={SubmitGroup.RESOURCE_CURATOR_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_local_resource_created,
        ),
        _submit_tool(
            name="submit_external_repo_required",
            description="Submit that this resource target should become a separate provider repo requirement.",
            args_model=SubmitExternalRepoRequiredArgs,
            groups={SubmitGroup.RESOURCE_CURATOR_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_external_repo_required,
        ),
        _submit_tool(
            name="submit_resource_rejected",
            description="Submit that the requested resource target is invalid or not usable.",
            args_model=SubmitResourceRejectedArgs,
            groups={SubmitGroup.RESOURCE_CURATOR_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_resource_rejected,
        ),
        _submit_tool(
            name="submit_content_node_tasks",
            description="Submit runnable content node tasks for child ContentNodeTaskFlow dispatch.",
            args_model=SubmitContentNodeTasksArgs,
            groups={SubmitGroup.COORDINATOR_SUBMIT},
            roles={"coordinator", "admin"},
            handler=handlers.submit_content_node_tasks,
            behavior=SubmitBehavior.DISPATCH_CHILD_FLOWS,
        ),
        _submit_tool(
            name="submit_repo_exploration",
            description="Submit one bounded batch of distinct repository-level resource, Lean-provider, or Mathlib explorations.",
            args_model=SubmitRepoExplorationArgs,
            groups={SubmitGroup.COORDINATOR_SUBMIT},
            roles={"coordinator", "admin"},
            handler=handlers.submit_repo_exploration,
        ),
        _submit_tool(
            name="submit_repo_resource_discovery_result",
            description="Submit up to five inspected resource targets with mathematical-use and ownership judgment; the backend re-inspects and supplies canonical metadata before terminal acceptance.",
            args_model=SubmitRepoResourceDiscoveryResultArgs,
            groups={SubmitGroup.REPO_RESOURCE_DISCOVERY_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_repo_resource_discovery_result,
        ),
        _submit_tool(
            name="submit_repo_lean_provider_discovery_result",
            description="Submit bounded real GitHub Lean candidates with mathematical capability judgment; the backend probes and supplies canonical repository facts before terminal acceptance.",
            args_model=SubmitRepoLeanProviderDiscoveryResultArgs,
            groups={SubmitGroup.REPO_LEAN_PROVIDER_DISCOVERY_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_repo_lean_provider_discovery_result,
        ),
        _submit_tool(
            name="submit_repo_mathlib_recon_result",
            description="Submit the checked repository-level MathlibIndex delta and unresolved findings.",
            args_model=SubmitRepoMathlibReconResultArgs,
            groups={SubmitGroup.REPO_MATHLIB_RECON_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_repo_mathlib_recon_result,
        ),
        _submit_tool(
            name="submit_repo_requirement",
            description="Submit a provider repo dependency requirement from the current Coordinator.",
            args_model=SubmitRepoRequirementArgs,
            groups={SubmitGroup.COORDINATOR_SUBMIT},
            roles={"coordinator", "admin"},
            handler=handlers.submit_repo_requirement,
        ),
        _submit_tool(
            name="submit_adapter_repo_requirement",
            description="Verify and submit a direct Adapter provider requirement; the backend derives the exact compatible revision, package, and import module.",
            args_model=SubmitAdapterRepoRequirementArgs,
            groups={SubmitGroup.COORDINATOR_SUBMIT},
            roles={"coordinator", "admin"},
            handler=handlers.submit_adapter_repo_requirement,
        ),
        _submit_tool(
            name="submit_native_repo_requirement",
            description="Submit the rare confirmed-native provider requirement after concrete upstream searches found no suitable adapter.",
            args_model=SubmitNativeRepoRequirementArgs,
            groups={SubmitGroup.COORDINATOR_SUBMIT},
            roles={"coordinator", "admin"},
            handler=handlers.submit_native_repo_requirement,
        ),
        _submit_tool(
            name="submit_repo_ready",
            description="Submit repository-ready intent; the following deterministic Coordinator Step runs the authoritative audit and applies publication policy.",
            args_model=SubmitRepoReadyArgs,
            groups={SubmitGroup.COORDINATOR_SUBMIT},
            roles={"coordinator", "admin"},
            handler=handlers.submit_repo_ready,
        ),
        _submit_tool(
            name="submit_content_preparation_recon",
            description="Submit a preparation recon child flow request for the current content node.",
            args_model=SubmitContentPreparationReconArgs,
            groups={SubmitGroup.CONTENT_PLAN_SUBMIT},
            roles={"plan", "admin"},
            handler=handlers.submit_content_preparation_recon,
            behavior=SubmitBehavior.DISPATCH_CHILD_FLOWS,
        ),
        _submit_tool(
            name="submit_current_decl_round",
            description="Submit the current decl round for child DeclGraphRoundFlow dispatch.",
            args_model=SubmitCurrentDeclRoundArgs,
            groups={SubmitGroup.CONTENT_PLAN_SUBMIT},
            roles={"plan", "admin"},
            handler=handlers.submit_current_decl_round,
            behavior=SubmitBehavior.DISPATCH_CHILD_FLOWS,
        ),
        _submit_tool(
            name="submit_content_node_ready",
            description="Submit that the current content node task is ready.",
            args_model=SubmitContentNodeReadyArgs,
            groups={SubmitGroup.CONTENT_COMPLETION_SUBMIT},
            roles={"plan", "admin"},
            handler=handlers.submit_content_node_ready,
        ),
        _submit_tool(
            name="submit_content_node_blocked",
            description="Submit that the current content node task is blocked.",
            args_model=SubmitContentNodeBlockedArgs,
            groups={SubmitGroup.CONTENT_COMPLETION_SUBMIT},
            roles={"plan", "admin"},
            handler=handlers.submit_content_node_blocked,
        ),
        _submit_tool(
            name="submit_content_node_failed",
            description="Submit that the current content node task failed after allowed retries.",
            args_model=SubmitContentNodeFailedArgs,
            groups={SubmitGroup.CONTENT_COMPLETION_SUBMIT},
            roles={"plan", "admin"},
            handler=handlers.submit_content_node_failed,
        ),
        _submit_tool(
            name="submit_node_dir_dependency_recon_completed",
            description="Submit completion of node directory dependency recon.",
            args_model=SubmitNodeDirDependencyReconCompletedArgs,
            groups={SubmitGroup.NODE_DIR_DEPENDENCY_RECON_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_node_dir_dependency_recon_completed,
        ),
        _submit_tool(
            name="submit_mathlib_recon_completed",
            description="Submit completion of Mathlib dependency recon.",
            args_model=SubmitMathlibReconCompletedArgs,
            groups={SubmitGroup.MATHLIB_RECON_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_mathlib_recon_completed,
        ),
        _submit_tool(
            name="submit_resource_recon_completed",
            description="Submit completion of resource recon.",
            args_model=SubmitResourceReconCompletedArgs,
            groups={SubmitGroup.RESOURCE_RECON_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_resource_recon_completed,
        ),
        _submit_tool(
            name="submit_resource_recon_blocked",
            description="Submit that resource recon is blocked.",
            args_model=SubmitResourceReconBlockedArgs,
            groups={SubmitGroup.RESOURCE_RECON_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_resource_recon_blocked,
        ),
        _submit_tool(
            name="submit_stage_worker_completed",
            description="Submit that the current decl stage worker batch is complete.",
            args_model=SubmitStageWorkerCompletedArgs,
            groups={SubmitGroup.DECL_STAGE_WORKER_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_stage_worker_completed,
        ),
        _submit_tool(
            name="submit_stage_worker_blocked",
            description="Submit that the current decl stage worker batch is blocked.",
            args_model=SubmitStageWorkerBlockedArgs,
            groups={SubmitGroup.DECL_STAGE_WORKER_SUBMIT},
            roles={"worker", "admin"},
            handler=handlers.submit_stage_worker_blocked,
        ),
        _submit_tool(
            name="submit_stage_review",
            description="Submit the current decl stage review decision after per-decl review marks are recorded.",
            args_model=SubmitStageReviewArgs,
            groups={SubmitGroup.DECL_STAGE_REVIEWER_SUBMIT},
            roles={"reviewer", "admin"},
            handler=handlers.submit_stage_review,
        ),
    ]
    _validate_submit_tool_specs(specs)
    return specs


def build_submit_tool_groups(tool_specs: Sequence[ToolSpec] | None = None) -> list[ToolGroupSpec]:
    specs = list(tool_specs) if tool_specs is not None else build_submit_tool_specs()
    return _build_groups(specs)


def build_submit_tool_views(group_specs: Sequence[ToolGroupSpec] | None = None) -> list[ToolViewSpec]:
    groups = list(group_specs) if group_specs is not None else build_submit_tool_groups()
    return _build_views(groups)


def register_submit_tooling(runtime: LeanRuntimeServices) -> ServiceResult[MutationSummaryView]:
    specs = build_submit_tool_specs()
    groups = build_submit_tool_groups(specs)
    views = build_submit_tool_views(groups)
    tools_result = runtime.tool_facade.register_application_tools(specs)
    if not tools_result.ok:
        return runtime.foundation.fail(tools_result.issues)
    groups_result = runtime.tool_facade.register_tool_groups(groups)
    if not groups_result.ok:
        return runtime.foundation.fail(groups_result.issues)
    views_result = runtime.tool_facade.register_tool_views(views)
    if not views_result.ok:
        return runtime.foundation.fail(views_result.issues)
    return runtime.foundation.ok(
        runtime.foundation.mutation_view(
            object_ref="submit_tooling",
            changed=True,
            summary=f"Registered {len(specs)} submit tools, {len(groups)} groups, and {len(views)} views.",
            changed_items=["tools", "groups", "views"],
        )
    )


def _validate_submit_tool_specs(specs: Sequence[ToolSpec]) -> None:
    names = [spec.name for spec in specs]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"Duplicate submit tool names: {', '.join(duplicates)}")
    invalid = [
        spec.name
        for spec in specs
        if not spec.name.startswith("submit_")
        or spec.capability != ToolCapability.SUBMIT
        or spec.submit_behavior == SubmitBehavior.NONE
    ]
    if invalid:
        raise ValueError(f"Invalid submit tool specs: {', '.join(sorted(invalid))}")
    missing_groups = [spec.name for spec in specs if not spec.tool_groups]
    if missing_groups:
        raise ValueError(f"Every submit tool must declare at least one group: {', '.join(sorted(missing_groups))}")
    missing_roles = [spec.name for spec in specs if not spec.allowed_roles]
    if missing_roles:
        raise ValueError(f"Every submit tool must declare allowed roles: {', '.join(sorted(missing_roles))}")
