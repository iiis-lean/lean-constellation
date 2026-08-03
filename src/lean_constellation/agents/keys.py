"""Canonical key catalogs for production Agent and Skill registries."""

from __future__ import annotations

from enum import StrEnum


class SkillKey(StrEnum):
    NODE_CONTRACT_DESIGN = "node-contract-design"
    CONTENT_CONTRACT_READING = "content-contract-reading"
    VISIBLE_NODE_DEPENDENCY_RECON = "visible-node-dependency-recon"
    SCOPE_EXPORT_INTERFACE_CURATION = "scope-export-interface-curation"
    SOURCE_MATERIAL_ACQUISITION = "source-material-acquisition"
    RESOURCE_MATERIAL_ACQUISITION = "resource-material-acquisition"
    EXTERNAL_RESOURCE_DISCOVERY = "external-resource-discovery"
    RESOURCE_REQUEST_SUBMISSION = "resource-request-submission"
    RESOURCE_RESULT_CLOSEOUT = "resource-result-closeout"
    RESOURCE_DRAFT_CURATION = "resource-draft-curation"
    COORDINATOR_REPO_EXPLORATION = "coordinator-repo-exploration"
    REPO_RESOURCE_DISCOVERY = "repo-resource-discovery"
    REPO_LEAN_PROVIDER_DISCOVERY = "repo-lean-provider-discovery"
    REPO_MATHLIB_RECON = "repo-mathlib-recon"
    COORDINATOR_CONTENT_RESULT_CLOSEOUT = "coordinator-content-result-closeout"
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


class AgentCapabilityKey(StrEnum):
    REPO_SEMANTIC_READ = "repo_semantic_read"
    REPO_SEMANTIC_WRITE = "repo_semantic_write"
    SOURCE_ROOT_FILE_READ_WRITE = "source_root_file_read_write"
    RESOURCE_DRAFT_FILE_READ_WRITE = "resource_draft_file_read_write"
    DECL_OWNED_LEAN_FILE_READ_WRITE = "decl_owned_lean_file_read_write"
    GITHUB_REMOTE_SEARCH_READ = "github_remote_search_read"
    GENERAL_WEB_READ = "general_web_read"
    RESOURCE_ACQUISITION = "resource_acquisition"
    MATHLIB_SEARCH_READ = "mathlib_search_read"
    MATHLIB_INDEX_WRITE = "mathlib_index_write"
    REQUIREMENT_SUBMIT = "requirement_submit"


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
    "AgentCapabilityKey",
    "ProductionAgentTypeKey",
    "SkillKey",
]
