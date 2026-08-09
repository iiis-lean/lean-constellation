"""Canonical key catalogs for production Agent and Skill registries."""

from __future__ import annotations

from enum import StrEnum


class SkillKey(StrEnum):
    REPO_FORMAT_DISCOVERY = "repo-format-discovery"
    NODE_CONTRACT_DESIGN = "node-contract-design"
    CONTENT_CONTRACT_READING = "content-contract-reading"
    VISIBLE_NODE_DEPENDENCY_RECON = "visible-node-dependency-recon"
    SCOPE_EXPORT_INTERFACE_CURATION = "scope-export-interface-curation"
    SOURCE_MATERIAL_ACQUISITION = "source-material-acquisition"
    SOURCE_CORPUS_FAITHFUL_PREPARATION = "source-corpus-faithful-preparation"
    RESOURCE_MATERIAL_ACQUISITION = "resource-material-acquisition"
    EXTERNAL_RESOURCE_DISCOVERY = "external-resource-discovery"
    RESOURCE_REQUEST_SUBMISSION = "resource-request-submission"
    RESOURCE_RESULT_CLOSEOUT = "resource-result-closeout"
    MATERIAL_BOUNDARY_CLASSIFICATION = "material-boundary-classification"
    FAITHFUL_MATERIAL_PRESERVATION = "faithful-material-preservation"
    RESOURCE_DRAFT_CURATION = "resource-draft-curation"
    COORDINATOR_REPO_EXPLORATION = "coordinator-repo-exploration"
    REPO_RESOURCE_DISCOVERY = "repo-resource-discovery"
    REPO_LEAN_PROVIDER_DISCOVERY = "repo-lean-provider-discovery"
    REPO_MATHLIB_RECON = "repo-mathlib-recon"
    COORDINATOR_CONTENT_RESULT_CLOSEOUT = "coordinator-content-result-closeout"
    COORDINATOR_BLOCKED_CONSUMER_REPLAN = "coordinator-blocked-consumer-replan"
    COORDINATOR_REQUIREMENT_RESULT_CLOSEOUT = "coordinator-requirement-result-closeout"
    COORDINATOR_DEPENDENCY_READINESS = "coordinator-dependency-readiness"
    COORDINATOR_NODE_DECOMPOSITION = "coordinator-node-decomposition"
    COORDINATOR_SCOPE_LIFECYCLE = "coordinator-scope-lifecycle"
    COORDINATOR_CONTENT_TASK_DISPATCH = "coordinator-content-task-dispatch"
    COORDINATOR_PROVIDER_DEPENDENCY_LIFECYCLE = "coordinator-provider-dependency-lifecycle"
    COORDINATOR_REPO_READY_LIFECYCLE = "coordinator-repo-ready-lifecycle"
    COORDINATOR_COMPLETION_POLICY = "coordinator-completion-policy"
    MATHLIB_INDEX_FIRST_RECON = "mathlib-index-first-recon"
    MATHLIB_SEMANTIC_SEARCH_NAVIGATION = "mathlib-semantic-search-navigation"
    MATHLIB_INDEX_ENTRY_CURATION = "mathlib-index-entry-curation"
    CURRENT_NODE_MATHLIB_HINT_MAINTENANCE = "current-node-mathlib-hint-maintenance"
    CONTENT_PREPARATION_ORCHESTRATION = "content-preparation-orchestration"
    DECL_STRATEGY_PLANNING = "decl-strategy-planning"
    DECL_ROUND_CHANGE_PLANNING = "decl-round-change-planning"
    DECL_ROUND_CLOSEOUT = "decl-round-closeout"
    CURRENT_NODE_PUBLIC_BOUNDARY_CURATION = "current-node-public-boundary-curation"
    CONTENT_NODE_COMPLETION_DECISION = "content-node-completion-decision"
    CONTENT_PLAN_COMPLETION_POLICY = "content-plan-completion-policy"
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
    REPO_RESOURCE_DISCOVERY = "RepoResourceDiscoveryAgent"
    REPO_LEAN_PROVIDER_DISCOVERY = "RepoLeanProviderDiscoveryAgent"
    REPO_MATHLIB_RECON = "RepoMathlibReconAgent"
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
