"""Submit-only ToolViewSpec definitions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lean_constellation.services.tool_facade import ToolGroupSpec, ToolViewSpec
from lean_constellation.tools.views import _aliases


def _view(
    key: str,
    group_keys: Sequence[str],
    allowed_agent_types: Sequence[str],
    *,
    flow_kind: str | None = None,
    stage: str | None = None,
) -> ToolViewSpec:
    return ToolViewSpec(key=key, group_keys=list(group_keys), allowed_agent_types=list(allowed_agent_types), flow_kind=flow_kind, stage=stage)


def build_submit_tool_views(group_specs: Iterable[ToolGroupSpec] | None = None) -> list[ToolViewSpec]:
    del group_specs
    return [
        _view("repo_format_discovery_submit", ["repo_format_discovery_submit"], _aliases("repo_format_discovery", "RepoFormatDiscoveryAgent")),
        _view("source_corpus_prepare_submit", ["source_corpus_prepare_submit"], _aliases("source_corpus_prepare", "SourceCorpusPrepareAgent")),
        _view("source_index_builder_submit", ["source_index_builder_submit"], _aliases("source_index_builder", "SourceIndexBuilderAgent")),
        _view("source_index_reviewer_submit", ["source_index_reviewer_submit"], _aliases("source_index_reviewer", "SourceIndexReviewerAgent")),
        _view("root_interface_prepare_submit", ["root_interface_prepare_submit"], _aliases("root_interface_prepare", "RootInterfacePrepareAgent")),
        _view("adapter_repo_import_submit", ["adapter_ready_submit"], _aliases("adapter_repo_import", "AdapterRepoImportAgent", "AdapterDeclCatalogAgent")),
        _view("resource_curator_submit", ["resource_curator_submit"], _aliases("resource_curator", "ResourceCuratorAgent")),
        _view("native_repo_coordinator_submit", ["coordinator_submit", "resource_request_submit"], _aliases("native_repo_coordinator", "NativeRepoCoordinatorAgent", "CoordinatorAgent", "coordinator")),
        _view("content_plan_submit", ["content_plan_submit", "content_completion_submit", "resource_request_submit"], _aliases("content_plan", "ContentPlanAgent", "plan")),
        _view("node_dir_dependency_recon_submit", ["preparation_recon_submit"], _aliases("node_dir_dependency_recon", "NodeDirDependencyReconAgent")),
        _view("mathlib_recon_submit", ["preparation_recon_submit"], _aliases("mathlib_recon", "MathlibReconAgent")),
        _view("resource_recon_submit", ["preparation_recon_submit", "resource_request_submit"], _aliases("resource_recon", "ResourceReconAgent")),
        _view(
            "decl_stage_worker_submit",
            ["decl_stage_worker_submit"],
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
            "decl_stage_reviewer_submit",
            ["decl_stage_reviewer_submit"],
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
