"""Canonical key catalogs for production Agent and Skill registries."""

from __future__ import annotations

from enum import StrEnum


class SkillKey(StrEnum):
    NODE_CONTRACT_DESIGN = "node-contract-design"
    CONTENT_CONTRACT_READING = "content-contract-reading"
    VISIBLE_NODE_DEPENDENCY_RECON = "visible-node-dependency-recon"
    SCOPE_EXPORT_INTERFACE_CURATION = "scope-export-interface-curation"
    MATERIAL_ACQUISITION = "material-acquisition"
    EXTERNAL_RESOURCE_DISCOVERY = "external-resource-discovery"
    RESOURCE_REQUEST_HANDLING = "resource-request-handling"
    RESOURCE_DRAFT_CURATION = "resource-draft-curation"
    COORDINATOR_NODE_DECOMPOSITION = "coordinator-node-decomposition"
    COORDINATOR_SCOPE_LIFECYCLE = "coordinator-scope-lifecycle"
    COORDINATOR_CONTENT_TASK_LIFECYCLE = "coordinator-content-task-lifecycle"
    MATHLIB_INDEX_FIRST_RECON = "mathlib-index-first-recon"
    MATHLIB_SEMANTIC_SEARCH_NAVIGATION = "mathlib-semantic-search-navigation"
    MATHLIB_INDEX_ENTRY_CURATION = "mathlib-index-entry-curation"
    CURRENT_NODE_MATHLIB_HINT_MAINTENANCE = "current-node-mathlib-hint-maintenance"
    CONTENT_PREPARATION_ORCHESTRATION = "content-preparation-orchestration"
    DECL_STRATEGY_PLANNING = "decl-strategy-planning"
    DECL_ROUND_CHANGE_PLANNING = "decl-round-change-planning"
    DECL_ROUND_CLOSEOUT = "decl-round-closeout"
    CONTENT_NODE_COMPLETION_DECISION = "content-node-completion-decision"
    DECL_DEPENDENCY_ORIGIN_CURATION = "decl-dependency-origin-curation"
    DECL_OWNED_LEAN_FILE_CAPTURE_CHECK = "decl-owned-lean-file-capture-check"
    LEAN_STATEMENT_FORMALIZATION = "lean-statement-formalization"
    LEAN_PROOF_FORMALIZATION = "lean-proof-formalization"


class ProductionAgentTypeKey(StrEnum):
    REPO_FORMAT_DISCOVERY = "RepoFormatDiscoveryAgent"
    SOURCE_CORPUS_PREPARE = "SourceCorpusPrepareAgent"
    SOURCE_INDEX_BUILDER = "SourceIndexBuilderAgent"
    SOURCE_INDEX_REVIEWER = "SourceIndexReviewerAgent"
    ROOT_INTERFACE_PREPARE = "RootInterfacePrepareAgent"
    ADAPTER_DECL_CATALOG = "AdapterDeclCatalogAgent"
    RESOURCE_CURATOR = "ResourceCuratorAgent"
    COORDINATOR = "CoordinatorAgent"
    CONTENT_PLAN = "ContentPlanAgent"
    NODE_DIR_DEPENDENCY_RECON = "NodeDirDependencyReconAgent"
    MATHLIB_RECON = "MathlibReconAgent"
    RESOURCE_RECON = "ResourceReconAgent"
    STATEMENT_NL_WORKER = "StatementNLWorkerAgent"
    STATEMENT_NL_REVIEWER = "StatementNLReviewerAgent"
    STATEMENT_FORMAL_WORKER = "StatementFormalWorkerAgent"
    STATEMENT_FORMAL_REVIEWER = "StatementFormalReviewerAgent"
    PROOF_NL_WORKER = "ProofNLWorkerAgent"
    PROOF_NL_REVIEWER = "ProofNLReviewerAgent"
    PROOF_FORMAL_WORKER = "ProofFormalWorkerAgent"
    PROOF_FORMAL_REVIEWER = "ProofFormalReviewerAgent"


__all__ = [
    "ProductionAgentTypeKey",
    "SkillKey",
]
