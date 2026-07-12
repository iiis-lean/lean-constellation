"""Application ToolViewSpec definitions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from lean_constellation.services.tool_facade import ToolGroupSpec, ToolViewSpec
from lean_constellation.tools.keys import ApplicationToolGroupKey as AppGroup
from lean_constellation.tools.keys import ApplicationToolViewKey as AppView


StringKey = str | StrEnum


def _key(value: StringKey) -> str:
    return value.value if isinstance(value, StrEnum) else str(value)


def _aliases(*names: str) -> list[str]:
    """Return stable snake_case and class-style aliases for an Agent type."""

    aliases: set[str] = set()
    for name in names:
        aliases.add(name)
        if "_" in name:
            aliases.add("".join(part.capitalize() for part in name.split("_")))
            aliases.add("".join(part.capitalize() for part in name.split("_")) + "Agent")
        elif name.endswith("Agent"):
            aliases.add(name.removesuffix("Agent"))
    return sorted(aliases)


def _view(
    key: StringKey,
    group_keys: Sequence[StringKey],
    allowed_agent_types: Sequence[str],
    *,
    extra_tool_names: Sequence[str] = (),
    flow_kind: str | None = None,
    stage: str | None = None,
) -> ToolViewSpec:
    return ToolViewSpec(
        key=_key(key),
        group_keys=[_key(group_key) for group_key in group_keys],
        extra_tool_names=list(extra_tool_names),
        allowed_agent_types=list(allowed_agent_types),
        flow_kind=flow_kind,
        stage=stage,
    )


def build_application_tool_views(group_specs: Iterable[ToolGroupSpec] | None = None) -> list[ToolViewSpec]:
    """Build default application tool views.

    The definitions intentionally avoid including overlapping groups in one
    view. Some tools belong to multiple groups because they are shared between
    source/resource workflows or statement/proof formal workflows; such groups
    must be selected separately for concrete Agent views.
    """

    del group_specs
    return [
        _view(
            AppView.REPO_FORMAT_DISCOVERY,
            [
                AppGroup.REPO_PREPARATION_INPUT_READ,
                AppGroup.REPO_PREPARATION_START_PREFLIGHT_READ,
                AppGroup.REPO_PREPARATION_REQUIREMENT_READ,
                AppGroup.WORKSPACE_REPO_CATALOG_READ,
                AppGroup.UPSTREAM_REPO_SEARCH,
                AppGroup.GITHUB_REPOSITORY_READ,
            ],
            _aliases("repo_format_discovery", "RepoFormatDiscoveryAgent"),
        ),
        _view(
            AppView.SOURCE_CORPUS_PREPARE,
            [AppGroup.REPO_PREPARATION_INPUT_READ, AppGroup.SOURCE_CORPUS_READ, AppGroup.SOURCE_ACQUISITION],
            _aliases("source_corpus_prepare", "SourceCorpusPrepareAgent"),
        ),
        _view(
            AppView.SOURCE_INDEX_BUILDER,
            [AppGroup.SOURCE_CORPUS_READ, AppGroup.SOURCE_MATERIAL_TEXT_READ, AppGroup.SOURCE_INDEX_DRAFT_READ, AppGroup.SOURCE_INDEX_DRAFT_WRITE],
            _aliases("source_index_builder", "SourceIndexBuilderAgent"),
        ),
        _view(
            AppView.SOURCE_INDEX_REVIEWER,
            [AppGroup.SOURCE_CORPUS_READ, AppGroup.SOURCE_MATERIAL_TEXT_READ, AppGroup.SOURCE_INDEX_DRAFT_READ],
            _aliases("source_index_reviewer", "SourceIndexReviewerAgent"),
        ),
        _view(
            AppView.ROOT_INTERFACE_PREPARE,
            [
                AppGroup.REPO_PREPARATION_INPUT_READ,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.ROOT_INTERFACE_STATE_READ,
                AppGroup.ROOT_INTERFACE_PREPARE_READ,
            ],
            _aliases("root_interface_prepare", "RootInterfacePrepareAgent"),
            extra_tool_names=["add_root_interface"],
        ),
        _view(
            AppView.ADAPTER_REPO_IMPORT,
            [
                AppGroup.REPO_PREPARATION_INPUT_READ,
                AppGroup.REPO_PREPARATION_REQUIREMENT_READ,
                AppGroup.ADAPTER_INPUT_READ,
                AppGroup.ROOT_INTERFACE_STATE_READ,
                AppGroup.UPSTREAM_METADATA_READ,
                AppGroup.UPSTREAM_NAVIGATION,
                AppGroup.ADAPTER_DECL_CATALOG_READ,
                AppGroup.ADAPTER_DECL_CATALOG_WRITE,
                AppGroup.ADAPTER_INTERFACE_BINDING_READ,
                AppGroup.ADAPTER_INTERFACE_BINDING_WRITE,
                AppGroup.ADAPTER_PROJECTION_CHECK,
                AppGroup.ADAPTER_READY_READ,
            ],
            _aliases("adapter_repo_import", "AdapterRepoImportAgent", "AdapterDeclCatalogAgent"),
        ),
        _view(
            AppView.RESOURCE_CURATOR,
            [
                AppGroup.MATERIAL_CONTEXT_READ,
                AppGroup.RESOURCE_TARGET_PREFLIGHT_READ,
                AppGroup.RESOURCE_ACQUISITION,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.RESOURCE_DRAFT_CURRENT_READ,
                AppGroup.SOURCE_CORPUS_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
            ],
            _aliases("resource_curator", "ResourceCuratorAgent"),
        ),
        _view(
            AppView.NATIVE_REPO_COORDINATOR,
            [
                AppGroup.REPO_PREPARATION_INPUT_READ,
                AppGroup.REPO_WORK_CONFIG_READ,
                AppGroup.WORKSPACE_PROVIDER_CATALOG_READ,
                AppGroup.WORKSPACE_REQUIREMENT_READ,
                AppGroup.LAKE_DEPENDENCY_READ,
                AppGroup.LAKE_DEPENDENCY_WRITE,
                AppGroup.NODE_CONTRACT_READ_COORDINATOR,
                AppGroup.NODE_TREE_COORDINATOR_READ,
                AppGroup.NODE_TREE_COORDINATOR_WRITE,
                AppGroup.PUBLIC_DECL_READ_COORDINATOR,
                AppGroup.DECL_GRAPH_READ_COORDINATOR,
                AppGroup.NODE_CONTRACT_CORE_COORDINATOR_WRITE,
                AppGroup.NODE_CONTRACT_DEPENDENCY_COORDINATOR_WRITE,
                AppGroup.NODE_CONTRACT_MATERIAL_COORDINATOR_WRITE,
                AppGroup.NODE_CONTRACT_MATHLIB_COORDINATOR_WRITE,
                AppGroup.SCOPE_EXPORT_INTERFACE_READ,
                AppGroup.SCOPE_EXPORT_INTERFACE_WRITE,
                AppGroup.SCOPE_CONTRACT_COORDINATOR_COMMIT,
                AppGroup.SCOPE_CLOSE_READ,
                AppGroup.CONTENT_TASK_RESULT_COORDINATOR_FINALIZE,
                AppGroup.REPO_READY_READ,
                AppGroup.CONTENT_TASK_ADMISSION_READ,
                AppGroup.SOURCE_CORPUS_READ,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.MATERIAL_CONTEXT_READ,
                AppGroup.RESOURCE_TARGET_PREFLIGHT_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_INDEX_WRITE,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.EXTERNAL_RESOURCE_DISCOVERY,
            ],
            _aliases("native_repo_coordinator", "NativeRepoCoordinatorAgent", "CoordinatorAgent", "coordinator"),
        ),
        _view(
            AppView.CONTENT_PLAN,
            [
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.REPO_WORK_CONFIG_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
                AppGroup.NODE_CONTRACT_MATERIAL_CURRENT_WRITE,
                AppGroup.NODE_MATHLIB_HINT_READ,
                AppGroup.NODE_MATHLIB_HINT_WRITE,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.MATERIAL_CONTEXT_READ,
                AppGroup.RESOURCE_TARGET_PREFLIGHT_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_INDEX_WRITE,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.EXTERNAL_RESOURCE_DISCOVERY,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.DECL_DEPENDENCY_ANALYSIS_READ,
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.DECL_GRAPH_CURRENT_WRITE,
                AppGroup.DECL_STRATEGY_WRITE,
                AppGroup.DECL_ROUND_CHANGE_WRITE,
                AppGroup.DECL_ROUND_CLOSEOUT_WRITE,
                AppGroup.CONTENT_COMPLETION_GATE_READ,
            ],
            _aliases("content_plan", "ContentPlanAgent", "plan"),
        ),
        _view(
            AppView.NODE_DIR_DEPENDENCY_RECON,
            [
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
            ],
            _aliases("node_dir_dependency_recon", "NodeDirDependencyReconAgent"),
        ),
        _view(
            AppView.MATHLIB_RECON,
            [
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.NODE_MATHLIB_HINT_READ,
                AppGroup.NODE_MATHLIB_HINT_WRITE,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_INDEX_WRITE,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
            ],
            _aliases("mathlib_recon", "MathlibReconAgent"),
        ),
        _view(
            AppView.RESOURCE_RECON,
            [
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.NODE_CONTRACT_MATERIAL_CURRENT_WRITE,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.MATERIAL_CONTEXT_READ,
                AppGroup.RESOURCE_TARGET_PREFLIGHT_READ,
                AppGroup.EXTERNAL_RESOURCE_DISCOVERY,
            ],
            _aliases("resource_recon", "ResourceReconAgent"),
        ),
        _view(
            AppView.STATEMENT_NL_WORKER,
            [
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.DECL_STAGE_STATEMENT_NL_WRITE,
            ],
            _aliases("statement_nl_worker", "StatementNlWorkerAgent", "StatementNLWorkerAgent"),
            stage="statement_nl",
        ),
        _view(
            AppView.STATEMENT_FORMAL_WORKER,
            [
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
                AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE,
                AppGroup.DECL_STAGE_STATEMENT_FORMAL_FILE_WRITE,
                AppGroup.DECL_STAGE_STATEMENT_FORMAL_DEP_WRITE,
                AppGroup.STATEMENT_FORMAL_DIAGNOSTICS_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_INDEX_WRITE,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.NODE_MATHLIB_HINT_READ,
                AppGroup.NODE_MATHLIB_HINT_WRITE,
            ],
            _aliases("statement_formal_worker", "StatementFormalWorkerAgent"),
            stage="statement_formal",
        ),
        _view(
            AppView.PROOF_NL_WORKER,
            [
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_INDEX_WRITE,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.NODE_MATHLIB_HINT_READ,
                AppGroup.NODE_MATHLIB_HINT_WRITE,
                AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
                AppGroup.EXTERNAL_MATERIAL_SEARCH_READ,
                AppGroup.DECL_STAGE_PROOF_NL_WRITE,
            ],
            _aliases("proof_nl_worker", "ProofNlWorkerAgent", "ProofNLWorkerAgent"),
            stage="proof_nl",
        ),
        _view(
            AppView.PROOF_FORMAL_WORKER,
            [
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.DECL_STAGE_PROOF_FORMAL_FILE,
                AppGroup.DECL_STAGE_PROOF_FORMAL_FILE_WRITE,
                AppGroup.DECL_STAGE_PROOF_FORMAL_DEP_WRITE,
                AppGroup.PROOF_FORMAL_DIAGNOSTICS_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_INDEX_WRITE,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.NODE_MATHLIB_HINT_READ,
                AppGroup.NODE_MATHLIB_HINT_WRITE,
                AppGroup.NODE_CONTRACT_DEPENDENCY_CURRENT_WRITE,
            ],
            _aliases("proof_formal_worker", "ProofFormalWorkerAgent"),
            stage="proof_formal",
        ),
        _view(
            AppView.STATEMENT_NL_REVIEWER,
            [
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.DECL_STAGE_REVIEW_STATUS_READ,
                AppGroup.DECL_STAGE_STATEMENT_NL_REVIEW_MARK_WRITE,
            ],
            _aliases("statement_nl_reviewer", "StatementNlReviewerAgent", "StatementNLReviewerAgent"),
            stage="statement_nl",
        ),
        _view(
            AppView.STATEMENT_FORMAL_REVIEWER,
            [
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.DECL_STAGE_REVIEW_STATUS_READ,
                AppGroup.DECL_STAGE_STATEMENT_FORMAL_REVIEW_MARK_WRITE,
            ],
            _aliases("statement_formal_reviewer", "StatementFormalReviewerAgent"),
            stage="statement_formal",
        ),
        _view(
            AppView.PROOF_NL_REVIEWER,
            [
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.EXTERNAL_MATERIAL_SEARCH_READ,
                AppGroup.DECL_STAGE_REVIEW_STATUS_READ,
                AppGroup.DECL_STAGE_PROOF_NL_REVIEW_MARK_WRITE,
            ],
            _aliases("proof_nl_reviewer", "ProofNlReviewerAgent", "ProofNLReviewerAgent"),
            stage="proof_nl",
        ),
        _view(
            AppView.PROOF_FORMAL_REVIEWER,
            [
                AppGroup.DECL_GRAPH_READ_CURRENT,
                AppGroup.CURRENT_NODE_DECL_READ,
                AppGroup.NODE_VISIBILITY_READ_CURRENT,
                AppGroup.PUBLIC_DECL_READ,
                AppGroup.NODE_CONTRACT_READ_CURRENT,
                AppGroup.SOURCE_INDEX_COMMITTED_READ,
                AppGroup.SOURCE_MATERIAL_TEXT_READ,
                AppGroup.RESOURCE_LIBRARY_READ,
                AppGroup.MATHLIB_INDEX_READ,
                AppGroup.MATHLIB_SEMANTIC_SEARCH,
                AppGroup.MATHLIB_NAVIGATION,
                AppGroup.DECL_STAGE_REVIEW_STATUS_READ,
                AppGroup.DECL_STAGE_PROOF_FORMAL_REVIEW_MARK_WRITE,
            ],
            _aliases("proof_formal_reviewer", "ProofFormalReviewerAgent"),
            stage="proof_formal",
        ),
    ]
