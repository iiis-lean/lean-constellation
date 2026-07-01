"""Submit-only ToolViewSpec definitions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lean_constellation.services.tool_facade import ToolGroupSpec, ToolViewSpec
from lean_constellation.tools.keys import SubmitToolGroupKey as SubmitGroup
from lean_constellation.tools.keys import SubmitToolViewKey as SubmitView
from lean_constellation.tools.views import StringKey, _aliases, _key


def _view(
    key: StringKey,
    group_keys: Sequence[StringKey],
    allowed_agent_types: Sequence[str],
    *,
    flow_kind: str | None = None,
    stage: str | None = None,
) -> ToolViewSpec:
    return ToolViewSpec(
        key=_key(key),
        group_keys=[_key(group_key) for group_key in group_keys],
        allowed_agent_types=list(allowed_agent_types),
        flow_kind=flow_kind,
        stage=stage,
    )


def build_submit_tool_views(group_specs: Iterable[ToolGroupSpec] | None = None) -> list[ToolViewSpec]:
    del group_specs
    return [
        _view(SubmitView.REPO_FORMAT_DISCOVERY_SUBMIT, [SubmitGroup.REPO_FORMAT_DISCOVERY_SUBMIT], _aliases("repo_format_discovery", "RepoFormatDiscoveryAgent")),
        _view(SubmitView.SOURCE_CORPUS_PREPARE_SUBMIT, [SubmitGroup.SOURCE_CORPUS_PREPARE_SUBMIT], _aliases("source_corpus_prepare", "SourceCorpusPrepareAgent")),
        _view(SubmitView.SOURCE_INDEX_BUILDER_SUBMIT, [SubmitGroup.SOURCE_INDEX_BUILDER_SUBMIT], _aliases("source_index_builder", "SourceIndexBuilderAgent")),
        _view(SubmitView.SOURCE_INDEX_REVIEWER_SUBMIT, [SubmitGroup.SOURCE_INDEX_REVIEWER_SUBMIT], _aliases("source_index_reviewer", "SourceIndexReviewerAgent")),
        _view(SubmitView.ROOT_INTERFACE_PREPARE_SUBMIT, [SubmitGroup.ROOT_INTERFACE_PREPARE_SUBMIT], _aliases("root_interface_prepare", "RootInterfacePrepareAgent")),
        _view(SubmitView.ADAPTER_REPO_IMPORT_SUBMIT, [SubmitGroup.ADAPTER_READY_SUBMIT], _aliases("adapter_repo_import", "AdapterRepoImportAgent", "AdapterDeclCatalogAgent")),
        _view(SubmitView.RESOURCE_CURATOR_SUBMIT, [SubmitGroup.RESOURCE_CURATOR_SUBMIT], _aliases("resource_curator", "ResourceCuratorAgent")),
        _view(
            SubmitView.NATIVE_REPO_COORDINATOR_SUBMIT,
            [SubmitGroup.COORDINATOR_SUBMIT, SubmitGroup.RESOURCE_REQUEST_SUBMIT],
            _aliases("native_repo_coordinator", "NativeRepoCoordinatorAgent", "CoordinatorAgent", "coordinator"),
        ),
        _view(
            SubmitView.CONTENT_PLAN_SUBMIT,
            [SubmitGroup.CONTENT_PLAN_SUBMIT, SubmitGroup.CONTENT_COMPLETION_SUBMIT, SubmitGroup.RESOURCE_REQUEST_SUBMIT],
            _aliases("content_plan", "ContentPlanAgent", "plan"),
        ),
        _view(SubmitView.NODE_DIR_DEPENDENCY_RECON_SUBMIT, [SubmitGroup.PREPARATION_RECON_SUBMIT], _aliases("node_dir_dependency_recon", "NodeDirDependencyReconAgent")),
        _view(SubmitView.MATHLIB_RECON_SUBMIT, [SubmitGroup.PREPARATION_RECON_SUBMIT], _aliases("mathlib_recon", "MathlibReconAgent")),
        _view(SubmitView.RESOURCE_RECON_SUBMIT, [SubmitGroup.PREPARATION_RECON_SUBMIT, SubmitGroup.RESOURCE_REQUEST_SUBMIT], _aliases("resource_recon", "ResourceReconAgent")),
        _view(
            SubmitView.DECL_STAGE_WORKER_SUBMIT,
            [SubmitGroup.DECL_STAGE_WORKER_SUBMIT],
            _aliases(
                "statement_nl_worker",
                "StatementNLWorkerAgent",
                "statement_formal_worker",
                "StatementFormalWorkerAgent",
                "proof_nl_worker",
                "ProofNLWorkerAgent",
                "proof_formal_worker",
                "ProofFormalWorkerAgent",
                "DeclStageWorkerAgent",
            ),
            stage="decl_stage_worker",
        ),
        _view(
            SubmitView.DECL_STAGE_REVIEWER_SUBMIT,
            [SubmitGroup.DECL_STAGE_REVIEWER_SUBMIT],
            _aliases(
                "statement_nl_reviewer",
                "StatementNLReviewerAgent",
                "statement_formal_reviewer",
                "StatementFormalReviewerAgent",
                "proof_nl_reviewer",
                "ProofNLReviewerAgent",
                "proof_formal_reviewer",
                "ProofFormalReviewerAgent",
                "DeclStageReviewerAgent",
            ),
            stage="decl_stage_reviewer",
        ),
    ]
