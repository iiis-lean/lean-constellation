"""Application ToolViewSpec definitions."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from lean_constellation.services.tool_facade import ToolGroupSpec, ToolViewSpec


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
    key: str,
    group_keys: Sequence[str],
    allowed_agent_types: Sequence[str],
    *,
    flow_kind: str | None = None,
    stage: str | None = None,
) -> ToolViewSpec:
    return ToolViewSpec(
        key=key,
        group_keys=list(group_keys),
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
            "repo_format_discovery",
            ["repo_preparation_input_read", "workspace_repo_catalog_read", "upstream_repo_search"],
            _aliases("repo_format_discovery", "RepoFormatDiscoveryAgent"),
        ),
        _view(
            "source_corpus_prepare",
            ["repo_preparation_input_read", "source_corpus_read", "source_acquisition"],
            _aliases("source_corpus_prepare", "SourceCorpusPrepareAgent"),
        ),
        _view(
            "source_index_builder",
            ["source_corpus_read", "source_material_text_read", "source_index_draft_read", "source_index_draft_write"],
            _aliases("source_index_builder", "SourceIndexBuilderAgent"),
        ),
        _view(
            "source_index_reviewer",
            ["source_corpus_read", "source_material_text_read", "source_index_draft_read"],
            _aliases("source_index_reviewer", "SourceIndexReviewerAgent"),
        ),
        _view(
            "root_interface_prepare",
            ["repo_preparation_input_read", "source_index_committed_read", "source_material_text_read", "root_interface_prepare_read", "scope_export_interface_read", "scope_export_interface_write"],
            _aliases("root_interface_prepare", "RootInterfacePrepareAgent"),
        ),
        _view(
            "adapter_repo_import",
            [
                "repo_preparation_input_read",
                "adapter_input_read",
                "upstream_metadata_read",
                "upstream_metadata_write",
                "upstream_navigation",
                "adapter_decl_catalog_read",
                "adapter_decl_catalog_write",
                "adapter_interface_binding_read",
                "adapter_interface_binding_write",
                "adapter_projection_check",
                "adapter_projection_write",
                "adapter_ready_read",
            ],
            _aliases("adapter_repo_import", "AdapterRepoImportAgent"),
        ),
        _view(
            "resource_curator",
            ["resource_curation_context_read", "material_acquisition", "resource_library_read", "resource_draft_write"],
            _aliases("resource_curator", "ResourceCuratorAgent"),
        ),
        _view(
            "native_repo_coordinator",
            [
                "repo_preparation_input_read",
                "workspace_provider_catalog_read",
                "workspace_requirement_read",
                "workspace_requirement_write",
                "lake_dependency_read",
                "lake_dependency_write",
                "node_contract_read_coordinator",
                "node_tree_coordinator_read",
                "node_tree_coordinator_write",
                "node_contract_core_coordinator_write",
                "scope_export_interface_read",
                "scope_export_interface_write",
                "scope_close_read",
                "repo_ready_read",
                "content_task_admission_read",
                "resource_library_read",
                "mathlib_index_read",
                "mathlib_index_write",
                "mathlib_semantic_search",
                "mathlib_navigation",
                "external_resource_discovery",
            ],
            _aliases("native_repo_coordinator", "NativeRepoCoordinatorAgent", "CoordinatorAgent", "coordinator"),
        ),
        _view(
            "content_plan",
            [
                "node_contract_read_current",
                "node_boundary_read_current",
                "node_contract_dependency_current_write",
                "node_contract_material_current_write",
                "node_mathlib_hint_read",
                "node_mathlib_hint_write",
                "source_material_text_read",
                "resource_library_read",
                "resource_curation_context_read",
                "mathlib_index_read",
                "mathlib_index_write",
                "mathlib_semantic_search",
                "mathlib_navigation",
                "external_theorem_search",
                "decl_graph_read_current",
                "decl_strategy_write",
                "decl_round_change_write",
                "decl_round_closeout_write",
                "decl_detail_read",
                "decl_history_read",
                "decl_readiness_read",
            ],
            _aliases("content_plan", "ContentPlanAgent", "plan"),
        ),
        _view(
            "node_dir_dependency_recon",
            ["node_contract_read_current", "node_boundary_read_current", "node_contract_dependency_current_write", "decl_readiness_read"],
            _aliases("node_dir_dependency_recon", "NodeDirDependencyReconAgent"),
        ),
        _view(
            "mathlib_recon",
            ["node_contract_read_current", "node_mathlib_hint_read", "node_mathlib_hint_write", "mathlib_index_read", "mathlib_index_write", "mathlib_semantic_search", "mathlib_navigation", "external_theorem_search"],
            _aliases("mathlib_recon", "MathlibReconAgent"),
        ),
        _view(
            "resource_recon",
            ["node_contract_read_current", "node_contract_material_current_write", "source_material_text_read", "resource_library_read", "resource_curation_context_read"],
            _aliases("resource_recon", "ResourceReconAgent"),
        ),
        _view(
            "statement_nl_worker",
            ["decl_graph_read_current", "decl_detail_read", "source_material_text_read", "resource_library_read", "mathlib_index_read", "decl_stage_statement_nl_write"],
            _aliases("statement_nl_worker", "StatementNlWorkerAgent", "StatementNLWorkerAgent"),
            stage="statement_nl",
        ),
        _view(
            "statement_formal_worker",
            ["decl_graph_read_current", "decl_detail_read", "decl_stage_statement_formal_file", "formal_diagnostics_read", "node_boundary_read_current", "mathlib_index_read", "mathlib_index_write", "mathlib_semantic_search", "mathlib_navigation", "node_mathlib_hint_read", "node_mathlib_hint_write"],
            _aliases("statement_formal_worker", "StatementFormalWorkerAgent"),
            stage="statement_formal",
        ),
        _view(
            "proof_nl_worker",
            ["decl_graph_read_current", "decl_detail_read", "decl_history_read", "source_material_text_read", "resource_library_read", "mathlib_index_read", "external_theorem_search", "decl_stage_proof_nl_write"],
            _aliases("proof_nl_worker", "ProofNlWorkerAgent", "ProofNLWorkerAgent"),
            stage="proof_nl",
        ),
        _view(
            "proof_formal_worker",
            ["decl_graph_read_current", "decl_detail_read", "decl_history_read", "decl_stage_proof_formal_file", "formal_diagnostics_read", "node_boundary_read_current", "mathlib_index_read", "mathlib_index_write", "mathlib_semantic_search", "mathlib_navigation", "node_mathlib_hint_read", "node_mathlib_hint_write"],
            _aliases("proof_formal_worker", "ProofFormalWorkerAgent"),
            stage="proof_formal",
        ),
        _view(
            "statement_nl_reviewer",
            ["decl_graph_read_current", "decl_detail_read", "source_material_text_read", "resource_library_read", "mathlib_index_read", "decl_stage_review_mark_write"],
            _aliases("statement_nl_reviewer", "StatementNlReviewerAgent", "StatementNLReviewerAgent"),
            stage="statement_nl_review",
        ),
        _view(
            "statement_formal_reviewer",
            ["decl_graph_read_current", "decl_detail_read", "decl_history_read", "decl_stage_statement_formal_file", "formal_diagnostics_read", "node_boundary_read_current", "mathlib_index_read", "decl_stage_review_mark_write"],
            _aliases("statement_formal_reviewer", "StatementFormalReviewerAgent"),
            stage="statement_formal_review",
        ),
        _view(
            "proof_nl_reviewer",
            ["decl_graph_read_current", "decl_detail_read", "decl_history_read", "source_material_text_read", "resource_library_read", "mathlib_index_read", "decl_stage_review_mark_write"],
            _aliases("proof_nl_reviewer", "ProofNlReviewerAgent", "ProofNLReviewerAgent"),
            stage="proof_nl_review",
        ),
        _view(
            "proof_formal_reviewer",
            ["decl_graph_read_current", "decl_detail_read", "decl_history_read", "decl_stage_proof_formal_file", "formal_diagnostics_read", "node_boundary_read_current", "mathlib_index_read", "decl_stage_review_mark_write"],
            _aliases("proof_formal_reviewer", "ProofFormalReviewerAgent"),
            stage="proof_formal_review",
        ),
    ]
