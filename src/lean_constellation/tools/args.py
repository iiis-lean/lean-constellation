"""Reusable Pydantic argument models for Agent-facing tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel


class NoArgs(StrictModel):
    """Tool takes no Agent-visible arguments."""


class ExpectedFormatArgs(StrictModel):
    expected_format: str | None = Field(default=None, description="Expected repo format, such as native or adapter.")


class QueryLimitArgs(StrictModel):
    query: str = Field(description="Text or pattern to search for.")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of results to return.")


class MaxCountArgs(StrictModel):
    max_count: int = Field(default=20, ge=1, le=100, description="Maximum number of items to return.")


class UrlOrSlugArgs(StrictModel):
    url_or_slug: str = Field(description="GitHub repository URL or owner/name slug to inspect.")


class GitHubRepoArgs(StrictModel):
    git_url: str = Field(description="GitHub repository URL or owner/name slug.")


class GitHubTreeArgs(GitHubRepoArgs):
    revision: str | None = Field(default=None, description="Optional branch, tag, or commit to inspect.")
    recursive: bool = Field(default=True, description="Whether to request the recursive git tree.")
    path_prefix: str | None = Field(default=None, description="Optional repository-relative path prefix filter.")
    limit: int = Field(default=5000, ge=1, le=5000, description="Maximum tree entries returned to the Agent.")


class GitHubFileReadArgs(GitHubRepoArgs):
    path: str = Field(description="Repository-relative remote file path.")
    revision: str | None = Field(default=None, description="Optional branch, tag, or commit to inspect.")
    max_chars: int = Field(default=20000, ge=1, le=50000, description="Maximum characters returned from the file.")


class GitHubCodeSearchArgs(StrictModel):
    query: str = Field(description="Code search query.")
    repo: str | None = Field(default=None, description="Optional GitHub repository URL or owner/name slug used to scope the query.")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum code search results returned.")


class GitHubLeanRepoProbeArgs(GitHubRepoArgs):
    revision: str | None = Field(default=None, description="Optional branch, tag, or commit to inspect.")
    subdir: str | None = Field(default=None, description="Optional repository-relative Lean project subdirectory to probe.")
    max_tree_entries: int = Field(default=5000, ge=1, le=5000, description="Maximum tree entries inspected.")
    max_file_chars: int = Field(default=20000, ge=1, le=50000, description="Maximum characters read from each remote file.")


class PreparationRequirementRefArgs(StrictModel):
    consumer_repo: str = Field(description="Consumer repo key from the current preparation input requirement_refs.")
    requirement_name: str = Field(description="Requirement name from the current preparation input requirement_refs.")


class ContentPreparationResultListArgs(StrictModel):
    kind: Literal["node_dir_dependency", "mathlib", "resource"] | None = Field(
        default=None,
        description="Optional preparation kind filter.",
    )


class ContentPreparationResultArgs(StrictModel):
    kind: Literal["node_dir_dependency", "mathlib", "resource"] = Field(
        description="Preparation result kind to inspect."
    )
    attempt: int | None = Field(
        default=None,
        ge=1,
        description="One-based attempt number. Omit to inspect the latest result of this kind.",
    )


class TargetRepoArgs(StrictModel):
    target_repo: str = Field(description="Target provider repo key inside the current workspace.")


class ProviderRepoArgs(StrictModel):
    provider_repo: str = Field(description="Provider repo key inside the current workspace.")


class RepoKeyArgs(StrictModel):
    repo_key: str = Field(description="Repo key inside the current workspace.")


class RepoRelativeFileArgs(StrictModel):
    file_path: str = Field(description="File path relative to the current repo root.")


class FormalPolicyCheckArgs(RepoRelativeFileArgs):
    decl_kind: str = Field(description="Expected Lean declaration kind for statement-formal policy checks, such as def, theorem, lemma, or instance.")


class RequirementNameArgs(StrictModel):
    requirement_name: str = Field(description="Repo dependency requirement name in the current repo.")


class RequirementObservedArgs(RequirementNameArgs):
    note: str | None = Field(default=None, description="Optional note for observing the provider result.")


class SourceCorpusCheckArgs(StrictModel):
    relpath: str = Field(default=".lean_constellation/source", description="Source corpus path relative to the repo root.")
    entry_path: str | None = Field(default=None, description="Optional entry file path relative to the source corpus root.")


class SourceCorpusScanArgs(StrictModel):
    relpath: str = Field(default=".lean_constellation/source", description="Source corpus path relative to the repo root.")


class SourceMaterialAcquireArgs(StrictModel):
    target: str = Field(description="Source target to acquire, such as an arXiv id, URL, local file, or local directory.")
    preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None = Field(
        default=None,
        description="Optional acquisition kind override.",
    )


class SourceArtifactExtractArgs(StrictModel):
    artifact_ref: str = Field(description="Artifact reference returned by acquire_source_material.")
    extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"] | None = Field(
        default=None,
        description="Optional extraction kind override.",
    )


class SourceMaterialImportArgs(StrictModel):
    source_path: str = Field(description="Local source file or directory path to import into the source draft area.")
    as_name: str | None = Field(default=None, description="Optional normalized file or directory name.")


class SourceMaterialNormalizeArgs(StrictModel):
    material_ref: str = Field(description="Source draft material reference to normalize into readable text.")


class ResourceMaterialAcquireArgs(StrictModel):
    target: str = Field(description="Resource target to acquire into the current active resource draft, such as an arXiv id, URL, local file, or local directory.")
    preferred_kind: Literal["arxiv_source", "arxiv_pdf", "web_page", "local_file", "local_dir"] | None = Field(
        default=None,
        description="Optional acquisition kind override for the resource target.",
    )


class ResourceArtifactExtractArgs(StrictModel):
    artifact_ref: str = Field(description="Artifact reference returned by acquire_resource_material.")
    extraction_kind: Literal["pdf_text", "html_main_text", "tex_source", "text_normalize"] | None = Field(
        default=None,
        description="Optional extraction kind override for resource draft normalization.",
    )


class ResourceMaterialImportArgs(StrictModel):
    source_path: str = Field(description="Local file or directory path to import into the current resource draft original material area.")
    as_name: str | None = Field(default=None, description="Optional normalized file or directory name inside the resource draft.")


class ResourceMaterialNormalizeArgs(StrictModel):
    material_ref: str = Field(description="Resource draft material reference to normalize into readable text.")


class TextSearchArgs(StrictModel):
    query: str = Field(description="Text or regex pattern to search.")
    regex: bool = Field(default=False, description="Whether query should be treated as a regular expression.")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum number of matches to return.")


class MaterialContextArgs(StrictModel):
    query: str | None = Field(default=None, description="Optional text or regex query for narrowing material context.")
    scope: Literal["current_node", "source", "resource", "all"] = Field(
        default="current_node",
        description="Evidence scope. current_node returns only evidence assigned to the active node.",
    )
    regex: bool = Field(default=False, description="Whether query should be treated as a regular expression.")
    limit: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="Optional maximum number of compact matches. Omit to return every match in the selected scope.",
    )


class SourceRangeArgs(StrictModel):
    path: str = Field(description="Path relative to the source corpus root.")
    start_line: int = Field(ge=1, description="First source line to read, 1-based.")
    end_line: int = Field(ge=1, description="Last source line to read, inclusive.")
    context_lines: int = Field(default=2, ge=0, le=20, description="Extra context lines around the requested range.")


class SourceRangeValidateArgs(StrictModel):
    path: str = Field(description="Path relative to the source corpus root.")
    start_line: int = Field(ge=1, description="First source line to validate, 1-based.")
    end_line: int = Field(ge=1, description="Last source line to validate, inclusive.")


class ResourceRangeArgs(StrictModel):
    resource_key: str = Field(description="Resource key in the current repo resource library.")
    start_line: int = Field(ge=1, description="First resource line to read, 1-based.")
    end_line: int = Field(ge=1, description="Last resource line to read, inclusive.")
    context_lines: int = Field(default=2, ge=0, le=20, description="Extra context lines around the requested range.")


class ResourceKeyArgs(StrictModel):
    resource_key: str = Field(description="Resource key in the current repo resource library.")


class ResourceListArgs(StrictModel):
    query: str | None = Field(default=None, description="Optional text filter for resource list entries.")


class ResourceTargetArgs(StrictModel):
    target: str = Field(description="Canonical resource target, usually a URL, arXiv id, DOI, or local path.")


class ResourceDraftTargetArgs(StrictModel):
    target: str = Field(description="Canonical resource target for the draft, usually a URL, arXiv id, DOI, or local-path target.")
    resource_kind: str | None = Field(default=None, description="Resource kind hint such as arxiv, web, pdf, local, or notes.")
    title_hint: str | None = Field(default=None, description="Optional human-readable title hint used to initialize draft metadata.")


class DraftIdArgs(StrictModel):
    draft_id: str = Field(description="Resource draft id returned by allocate_resource_draft.")


class DraftIdReasonArgs(DraftIdArgs):
    reason: str = Field(description="Reason for abandoning the resource draft.")


class SummaryArgs(StrictModel):
    summary: str = Field(description="Summary for this action.")


class SourceIndexOverviewArgs(StrictModel):
    overview: str = Field(description="Natural-language overview for the draft SourceIndex.")


class SourceIndexFileListArgs(StrictModel):
    status: Literal["pending", "surveyed", "indexed", "skipped", "committed"] | None = Field(
        default=None,
        description="Optional lifecycle filter. Omit to list every indexed source file.",
    )


class SourceBlockListArgs(StrictModel):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive filter over block id, kind, subtype, title, and summary.",
    )
    kind: str | None = Field(default=None, description="Optional exact semantic block kind.")
    subtype: str | None = Field(default=None, description="Optional exact semantic block subtype.")
    path: str | None = Field(
        default=None,
        description="Optional source corpus path; only blocks with a ref to this path are returned.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="Optional maximum result count. Omit to return all compact matching entries.",
    )


class SourceBlockCreateArgs(StrictModel):
    parent_id: str = Field(description="Draft-local parent block id; root block is root.")
    kind: str = Field(description="Semantic block kind, such as definition, theorem, proof, section, or overview.")
    title: str = Field(description="Human-readable block title.")
    summary: str = Field(description="Concise semantic summary for the block.")
    subtype: str | None = Field(default=None, description="Optional more specific block subtype.")


class SourceBlockUpdateArgs(StrictModel):
    block_id: str = Field(description="Draft-local block id to update.")
    title: str | None = Field(default=None, description="Updated title, if changing it.")
    summary: str | None = Field(default=None, description="Updated summary, if changing it.")
    kind: str | None = Field(default=None, description="Updated block kind, if changing it.")
    subtype: str | None = Field(default=None, description="Updated block subtype, if changing it.")


class SourceBlockRefArgs(StrictModel):
    block_id: str = Field(description="Draft-local block id that owns the source range.")
    path: str = Field(description="Path relative to the source corpus root.")
    start_line: int = Field(ge=1, description="First referenced line, 1-based.")
    end_line: int = Field(ge=1, description="Last referenced line, inclusive.")
    role: str = Field(description="Role of this source range for the block.")


class SourceBlockRefRemoveArgs(StrictModel):
    block_id: str = Field(description="Draft-local block id.")
    ref_id: str = Field(description="Draft-local ref id returned by add_source_block_ref.")


class SourceBlockIdArgs(StrictModel):
    block_id: str = Field(description="Draft-local block id.")


class SourceLinkCreateArgs(StrictModel):
    source_block_id: str = Field(description="Draft-local source block id.")
    link_kind: str = Field(description="Semantic relationship kind.")
    evidence_ref_ids: list[str] = Field(description="Draft-local source ref ids supporting this link.")
    target_block_id: str | None = Field(default=None, description="Draft-local target block id, if already known.")
    target_hint: str | None = Field(default=None, description="Natural-language target hint when target_block_id is unknown.")


class FileStatusArgs(StrictModel):
    path: str = Field(description="Path relative to the source corpus root.")
    status: str = Field(description="New status value accepted by the SourceIndex service.")
    summary: str | None = Field(default=None, description="Optional explanation for the status.")


class FileIndexingStatusArgs(StrictModel):
    path: str = Field(description="Path relative to the source corpus root.")
    status: str = Field(description="New indexing status value accepted by the SourceIndex service.")


class NodePathArgs(StrictModel):
    node_path: str = Field(description="Node path in the current repo.")


class NodeContractCommitArgs(NodePathArgs):
    summary: str = Field(description="Coordinator summary to write into the committed node contract version.")


class ContentTaskResultListArgs(StrictModel):
    node_path: str | None = Field(default=None, description="Optional Content node path filter.")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum terminal Content task results to return.")


class ContentTaskResultInspectArgs(NodePathArgs):
    contract_version: int | None = Field(default=None, ge=1, description="Optional contract version filter; omit for the latest terminal result for the node.")


class NodePathQueryArgs(NodePathArgs):
    include_public_decl_preview: bool = Field(default=False, description="Whether to include a compact public declaration preview.")


class CreateScopeNodeArgs(StrictModel):
    path: str = Field(description="New scope node path.")
    goal: str = Field(description="Long-term goal of the scope node.")
    boundary: str = Field(description="Boundary of content owned by this scope node.")
    objective: str | None = Field(default=None, description="Current version objective for this scope contract.")
    constraints: str | None = Field(default=None, description="Optional constraints for the scope node.")
    success_criteria: str | None = Field(default=None, description="Optional success criteria for closing this scope.")


class CreateContentNodeArgs(StrictModel):
    path: str = Field(description="New content node path.")
    goal: str = Field(description="Long-term goal of the content node.")
    boundary: str = Field(description="Boundary of content owned by this content node.")
    objective: str = Field(description="Current task objective for this content contract.")
    success_criteria: str = Field(description="Criteria the content node task should satisfy.")
    constraints: str | None = Field(default=None, description="Optional constraints for this content node.")


class ContractCoreUpdateArgs(NodePathArgs):
    goal: str | None = Field(default=None, description="Updated node goal, if changing it.")
    boundary: str | None = Field(default=None, description="Updated node boundary, if changing it.")
    objective: str | None = Field(default=None, description="Updated current contract objective, if changing it.")
    success_criteria: str | None = Field(default=None, description="Updated success criteria, if changing it.")
    constraints: str | None = Field(default=None, description="Updated constraints, if changing them.")


class NodeDeleteArgs(NodePathArgs):
    reason: str = Field(description="Reason for deleting or deprecating the node.")


class ScopePathArgs(StrictModel):
    scope_path: str = Field(description="Scope node path in the current repo.")


class ScopeExportAddArgs(ScopePathArgs):
    decl_node: str = Field(description="Provider node path containing the declaration to export.")
    decl_name: str = Field(description="Declaration name to export.")
    decl_repo: str | None = Field(default=None, description="Provider repo key; omit for current repo.")
    revision: int = Field(default=1, ge=1, description="Declaration revision to export.")
    bind_interface_name: str | None = Field(default=None, description="Optional interface name to bind to this export.")


class ScopeExportRemoveArgs(ScopePathArgs):
    index: int = Field(ge=0, description="0-based export index from list_scope_exports.")


class IndexArgs(StrictModel):
    index: int = Field(ge=0, description="0-based index from the most recent list view.")


class ContentNodeBatchArgs(StrictModel):
    node_paths: list[str] = Field(description="Content node paths to check as one runnable batch.")


class InterfaceAddArgs(NodePathArgs):
    name: str = Field(description="Interface name.")
    kind: str = Field(description="Interface declaration kind.")
    summary: str = Field(description="Interface summary.")
    statement_hint: str | None = Field(default=None, description="Optional statement hint.")


class InterfaceUpdateArgs(NodePathArgs):
    name: str = Field(description="Interface name.")
    summary: str | None = Field(default=None, description="Updated summary, if changing it.")
    statement_hint: str | None = Field(default=None, description="Updated statement hint, if changing it.")


class InterfaceNameArgs(NodePathArgs):
    name: str = Field(description="Interface name.")


class RootInterfaceAddArgs(StrictModel):
    name: str = Field(description="Root Main interface name.")
    kind: str = Field(description="Root Main interface declaration kind.")
    summary: str = Field(description="Root Main interface summary.")
    statement_hint: str | None = Field(default=None, description="Optional statement hint.")


class RootInterfaceUpdateArgs(StrictModel):
    name: str = Field(description="Root Main interface name.")
    summary: str | None = Field(default=None, description="Updated summary, if changing it.")
    statement_hint: str | None = Field(default=None, description="Updated statement hint, if changing it.")


class RootInterfaceNameArgs(StrictModel):
    name: str = Field(description="Root Main interface name.")


class InterfaceBindArgs(NodePathArgs):
    interface_name: str = Field(description="Interface name to bind.")
    decl_name: str = Field(description="Declaration name to bind to the interface.")
    decl_node: str | None = Field(default=None, description="Node containing the declaration; omit to use the current node.")


class CurrentNodeInterfaceBindArgs(StrictModel):
    interface_name: str = Field(description="Current content-node interface name to bind.")
    decl_name: str = Field(description="Current content-node public declaration name to bind to the interface.")


class CurrentNodeDependencyAddArgs(StrictModel):
    target_node: str = Field(description="Provider node path to add as a dependency.")
    reason: str = Field(description="Why the current node needs this dependency.")
    target_repo: str | None = Field(default=None, description="Provider repo key; omit for current repo.")
    expected_public_decl_names: list[str] | None = Field(
        default=None,
        description="Names of provider public declarations expected to be used.",
    )


class NodeDependencyAddArgs(NodePathArgs):
    target_node: str = Field(description="Provider node path to add as a dependency of the target node.")
    reason: str = Field(description="Why the target node needs this dependency.")
    target_repo: str | None = Field(default=None, description="Provider repo key; omit for current repo.")
    expected_public_decl_names: list[str] | None = Field(
        default=None,
        description="Names of provider public declarations expected to be used.",
    )


class NodeDependencyRemoveArgs(NodePathArgs):
    index: int = Field(ge=0, description="0-based dependency index from list_node_deps or get_node_contract.")
    reason: str | None = Field(default=None, description="Optional reason for removing this dependency.")


class IndexReasonArgs(StrictModel):
    index: int = Field(ge=0, description="0-based index from the most recent list view.")
    reason: str | None = Field(default=None, description="Optional reason for this removal.")


class CurrentMaterialRefAddArgs(StrictModel):
    ref_scope: Literal["owned", "context"] = Field(description="Whether to add the material to owned_refs or context_refs.")
    material_kind: Literal["source", "resource"] = Field(description="Whether locator identifies source text or a resource.")
    locator: str = Field(description="Source path or resource key.")
    start_line: int | None = Field(default=None, ge=1, description="Optional first line, 1-based.")
    end_line: int | None = Field(default=None, ge=1, description="Optional last line, inclusive.")
    reason: str | None = Field(default=None, description="Why this material is relevant.")


class CurrentMaterialRefRemoveArgs(StrictModel):
    ref_scope: Literal["owned", "context"] = Field(description="Which material ref list to remove from.")
    index: int = Field(ge=0, description="0-based index from the current material ref list.")
    reason: str | None = Field(default=None, description="Optional removal reason.")


class NodeMaterialRefAddArgs(NodePathArgs):
    ref_scope: Literal["owned", "context"] = Field(description="Whether to add the material to owned_refs or context_refs.")
    material_kind: Literal["source", "resource"] = Field(description="Whether locator identifies source text or a resource.")
    locator: str = Field(description="Source path or resource key.")
    start_line: int | None = Field(default=None, ge=1, description="Optional first line, 1-based.")
    end_line: int | None = Field(default=None, ge=1, description="Optional last line, inclusive.")
    reason: str | None = Field(default=None, description="Why this material is relevant to the target node.")


class NodeMaterialRefRemoveArgs(NodePathArgs):
    ref_scope: Literal["owned", "context"] = Field(description="Which material ref list to remove from.")
    index: int = Field(ge=0, description="0-based index from the target node material ref list.")
    reason: str | None = Field(default=None, description="Optional removal reason.")


class MathlibIndexSearchArgs(StrictModel):
    query: str = Field(description="Text or regex to search across recorded MathlibIndex module names, declaration names, summaries, signatures, and notes.")
    regex: bool = Field(default=False, description="Whether query should be interpreted as a regular expression.")
    entry_kind: str = Field(default="all", description="Entry kind filter: all, module, declaration, or a Lean declaration kind such as def, theorem, lemma, or instance.")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum MathlibIndex entries to return.")


class MathlibModuleArgs(StrictModel):
    module: str = Field(description="Mathlib module name, for example Mathlib.Data.Nat.Basic.")


class MathlibDeclArgs(StrictModel):
    name: str = Field(description="Mathlib declaration name recorded in the repo-level MathlibIndex.")


class MathlibDeclNameArgs(StrictModel):
    decl_name: str = Field(description="Fully qualified Mathlib declaration name to inspect or check.")


class MathlibModuleDeclArgs(StrictModel):
    module: str = Field(description="Mathlib module to import before checking or associating the declaration.")
    decl_name: str = Field(description="Fully qualified Mathlib declaration name.")


class MathlibCandidateArgs(StrictModel):
    candidate_id: str = Field(description="Candidate id returned by a Mathlib search tool in the current tool session.")
    include_source_excerpt: bool = Field(
        default=False,
        description="Whether to include a bounded source/context excerpt in the candidate detail.",
    )


class MathlibCandidateIngestArgs(StrictModel):
    candidate_id: str = Field(description="Candidate id returned by a Mathlib search tool in the current tool session.")
    summary: str = Field(description="Concise reusable-purpose summary to store for the ingested Mathlib declaration.")
    note: str | None = Field(default=None, description="Optional usage note or search provenance to store with the ingested declaration.")


class MathlibModuleRecordArgs(StrictModel):
    module_name: str = Field(description="Mathlib module name to verify as importable from the current repo before recording.")
    summary: str | None = Field(default=None, description="Short summary of why the module is reusable for this repo or current node.")
    source: str | None = Field(default=None, description="Search result, document, or reasoning source that led to this module candidate.")


class MathlibDeclRecordArgs(StrictModel):
    decl_name: str = Field(description="Mathlib declaration name to verify as accessible from the current repo before recording.")
    module_name: str | None = Field(
        default=None,
        description=(
            "Known defining Mathlib module for the declaration, if discovered. Exact declaration inspection may canonicalize "
            "a broader import module to the defining module; record broader reusable imports as module entries or node module hints."
        ),
    )
    summary: str | None = Field(default=None, description="Short summary of why the declaration is reusable for this repo or current node.")
    source: str | None = Field(default=None, description="Search result, source inspection, or reasoning source that led to this declaration candidate.")
    kind: str | None = Field(default=None, description="Lean declaration kind if known, such as def, theorem, lemma, or instance.")
    signature: str | None = Field(default=None, description="Lean signature or type of the declaration if known.")
    snippet: str | None = Field(default=None, description="Relevant source or documentation snippet supporting the declaration entry.")


class MathlibBatchRecordArgs(StrictModel):
    modules: list[MathlibModuleRecordArgs] = Field(
        default_factory=list,
        description="Mathlib modules to verify together and record.",
    )
    declarations: list[MathlibDeclRecordArgs] = Field(
        default_factory=list,
        description="Mathlib declarations to verify together and record.",
    )

    @model_validator(mode="after")
    def _bounded_non_empty_batch(self) -> "MathlibBatchRecordArgs":
        size = len(self.modules) + len(self.declarations)
        if size == 0:
            raise ValueError("at least one Mathlib module or declaration is required")
        if size > 25:
            raise ValueError("at most 25 Mathlib entries may be recorded in one batch")
        return self


class MathlibSemanticSearchArgs(StrictModel):
    query: str = Field(description="Natural-language mathematical concept, theorem statement, or Lean-name query for LeanExplore Mathlib semantic search.")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of ranked Mathlib semantic-search candidates to return.")


class ArxivTheoremSearchArgs(StrictModel):
    query: str = Field(
        description=(
            "arXiv theorem search query. Natural-language queries use the configured remote theorem provider; "
            "explicit arXiv ids such as math/0001001 or 2401.00001 can fall back to parsing the paper e-print source."
        ),
    )
    limit: int = Field(default=20, ge=1, le=50, description="Maximum number of theorem-like arXiv candidates to return.")


class MathlibExternalSearchArgs(StrictModel):
    query: str = Field(description="Search query for configured external Mathlib or theorem search backends.")
    search_kinds: list[str] = Field(default_factory=lambda: ["lean_explore"], description="Toolkit search backends or result kinds to use, such as lean_explore.")
    limit: int = Field(default=20, ge=1, le=50, description="Maximum external search results to return.")


class MathlibInspectModuleArgs(StrictModel):
    module: str = Field(description="Mathlib module name to inspect through toolkit navigation.")
    pattern: str | None = Field(default=None, description="Optional declaration name or source text pattern to filter module contents.")
    limit: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum number of compact declaration entries to return.",
    )
    include_imports: bool = Field(
        default=False,
        description="Whether to include up to 50 direct imports.",
    )
    include_source_excerpt: bool = Field(
        default=False,
        description="Whether to include a bounded module source/context excerpt.",
    )


class CurrentMathlibModuleUseArgs(StrictModel):
    module: str = Field(description="Recorded Mathlib module name to add to or remove from the current node contract hints.")
    reason: str | None = Field(default=None, description="Why this module is useful for the current node objective.")


class CurrentMathlibDeclUseArgs(StrictModel):
    decl_name: str = Field(description="Recorded Mathlib declaration name to add to or remove from the current node contract hints.")
    reason: str | None = Field(default=None, description="Why this declaration is useful for the current node objective.")


class CurrentMathlibModuleHintInput(StrictModel):
    name: str = Field(description="Mathlib module name.")
    reason: str | None = Field(default=None, description="Why this module is useful for the current node objective.")


class CurrentMathlibDeclHintInput(StrictModel):
    name: str = Field(description="Mathlib declaration name.")
    reason: str | None = Field(default=None, description="Why this declaration is useful for the current node objective.")


class CurrentMathlibHintsAddArgs(StrictModel):
    modules: list[CurrentMathlibModuleHintInput] = Field(
        default_factory=list,
        max_length=25,
        description="Mathlib module hints to add atomically to the current node contract.",
    )
    declarations: list[CurrentMathlibDeclHintInput] = Field(
        default_factory=list,
        max_length=25,
        description="Mathlib declaration hints to add atomically to the current node contract.",
    )

    @model_validator(mode="after")
    def _require_hint(self) -> "CurrentMathlibHintsAddArgs":
        if not self.modules and not self.declarations:
            raise ValueError("At least one Mathlib module or declaration hint is required.")
        return self


class NodeMathlibModuleHintArgs(NodePathArgs):
    module: str = Field(description="Recorded Mathlib module name to add to or remove from the target node contract hints.")
    reason: str | None = Field(default=None, description="Why this module is useful for the target node objective.")


class NodeMathlibDeclHintArgs(NodePathArgs):
    decl_name: str = Field(description="Recorded Mathlib declaration name to add to or remove from the target node contract hints.")
    reason: str | None = Field(default=None, description="Why this declaration is useful for the target node objective.")


class StrategyEnsureArgs(StrictModel):
    objective: str = Field(description="Objective of the open declaration strategy.")
    rationale: str | None = Field(default=None, description="Optional rationale for the strategy.")


class StrategyCloseArgs(StrictModel):
    strategy_id: str = Field(description="Strategy id to close.")
    summary: str = Field(description="Closeout summary.")
    reason: str | None = Field(default=None, description="Optional reason for closing.")
    failed: bool = Field(default=False, description="Whether the strategy is being closed as failed.")


class RoundDraftArgs(StrictModel):
    strategy_id: str = Field(description="Open strategy id for this round.")
    objective: str = Field(description="Round objective.")


class RoundIdArgs(StrictModel):
    round_id: str | None = Field(default=None, description="Declaration round id; omit to use the current stage round.")


class StrategyIdArgs(StrictModel):
    strategy_id: str = Field(description="Declaration strategy id.")


class ChangeIdArgs(StrictModel):
    change_id: str = Field(description="Declaration change id.")


class ChangeSummaryArgs(RoundIdArgs):
    change_id: str = Field(description="Declaration change id.")
    summary: str = Field(description="Summary for this declaration change.")


class RoundSummaryArgs(RoundIdArgs):
    summary: str = Field(description="Round closeout summary.")


class RoundTerminalArgs(RoundIdArgs):
    result_kind: str = Field(description="Round terminal result kind.")
    reason: str | None = Field(default=None, description="Optional reason for the terminal result.")


class DeclCreateArgs(StrictModel):
    round_id: str = Field(description="Round id in which to plan this declaration creation.")
    name: str = Field(description="Flat Lean Constellation declaration key and native module filename segment; dots and path separators are not allowed.")
    kind: str = Field(description="Declaration kind.")
    objective: str = Field(description="Mathematical objective for this declaration change in the current round.")
    summary: str = Field(description="Concise stable catalog summary of the new declaration.")
    public: bool = Field(default=False, description="Whether the declaration should be public.")
    target_state: str = Field(default="declared", description="Target state after this round: declared or proved.")
    require_target_state_satisfied: bool = Field(
        default=True,
        description="Whether round final audit must verify that the target state also satisfies the current proof policy.",
    )
    anticipated_statement_dep_names: list[str] = Field(
        default_factory=list,
        description="Current-node declarations already known at planning time to be needed by this statement.",
    )
    anticipated_proof_dep_names: list[str] = Field(
        default_factory=list,
        description="Current-node declarations already known at planning time to be needed by this proof.",
    )


class DeclUpdateArgs(StrictModel):
    round_id: str = Field(description="Round id in which to plan this update.")
    name: str = Field(description="Existing declaration name.")
    objective: str = Field(description="Objective for this update.")
    target_state: str = Field(description="Target state after this update: declared or proved.")
    base_revision: int | None = Field(
        default=None,
        ge=1,
        description="Optional committed revision to copy; defaults to the current committed head.",
    )
    reset_to_state: str | None = Field(
        default=None,
        description="Optional state boundary to retain before this round; execution begins at the next pipeline stage.",
    )
    require_target_state_satisfied: bool = Field(
        default=True,
        description="Whether round final audit must verify that the target state also satisfies the current proof policy.",
    )
    anticipated_statement_dep_names: list[str] = Field(
        default_factory=list,
        description="Current-node declarations already known at planning time to be needed by this statement.",
    )
    anticipated_proof_dep_names: list[str] = Field(
        default_factory=list,
        description="Current-node declarations already known at planning time to be needed by this proof.",
    )


class DeclDeleteArgs(StrictModel):
    round_id: str = Field(description="Round id in which to plan deletion.")
    name: str = Field(description="Declaration name to delete.")
    objective: str = Field(description="Reason/objective for deletion.")


class DeclNameArgs(StrictModel):
    decl_name: str = Field(description="Declaration name in the current content node.")


class DeclRevisionArgs(DeclNameArgs):
    revision: int = Field(ge=1, description="Revision number to inspect.")


class DeclInspectArgs(DeclNameArgs):
    revision: int | None = Field(default=None, ge=1, description="Optional revision number; omit to inspect the current revision.")


class DeclFormalReadArgs(DeclNameArgs):
    include_docstring: bool = Field(
        default=False,
        description="Whether the returned formal Lean source should retain its managed declaration docstring.",
    )


class NodePublicDeclListArgs(NodePathArgs):
    pass


class NodePublicDeclInspectArgs(NodePathArgs, DeclInspectArgs):
    pass


class NodeDeclListArgs(NodePathArgs):
    pass


class NodeDeclInspectArgs(NodePathArgs, DeclInspectArgs):
    pass


class RepoPublicDeclListArgs(RepoKeyArgs):
    pass


class RepoPublicDeclInspectArgs(RepoKeyArgs, DeclInspectArgs):
    pass


class VisibleDeclLeanFileArgs(NodePathArgs, DeclNameArgs):
    repo_key: str | None = Field(default=None, description="Optional visible provider repo key; omit for the current repo.")
    revision: int | None = Field(default=None, ge=1, description="Optional visible revision; omit to use the effective current/public revision.")


class DeclNamesArgs(StrictModel):
    decl_names: list[str] = Field(description="Declaration names in the current content node.")


class DeclReadyArgs(DeclNameArgs):
    policy: str | None = Field(default=None, description="Optional readiness policy override.")


class StatementNlSetArgs(StrictModel):
    decl_name: str = Field(description="Declaration name to update in the current Statement NL stage batch.")
    text: str = Field(description="Complete natural-language statement text; may contain multiple Markdown paragraphs.")


class StatementSourceOriginAddArgs(StrictModel):
    decl_name: str = Field(description="Declaration name to update in the current Statement NL stage batch.")
    source_path: str = Field(description="Stable source material path or source id already present in the committed source corpus.")
    start_line: int = Field(ge=1, description="1-based first source line supporting the statement.")
    end_line: int = Field(ge=1, description="1-based final source line supporting the statement.")
    note: str | None = Field(default=None, description="Optional note explaining how this range supports the statement.")


class StatementResourceOriginAddArgs(StrictModel):
    decl_name: str = Field(description="Declaration name to update in the current Statement NL stage batch.")
    resource_key: str = Field(description="Stable resource key already present in the resource library.")
    start_locator: str | None = Field(default=None, description="Optional resource-local start locator, such as section, page, or paragraph.")
    end_locator: str | None = Field(default=None, description="Optional resource-local end locator.")
    note: str | None = Field(default=None, description="Optional note explaining how this resource supports the statement.")


class StatementOriginRemoveArgs(StrictModel):
    decl_name: str = Field(description="Declaration name to update in the current Statement NL stage batch.")
    index: int = Field(ge=0, description="0-based origin index from the current declaration origin list.")


class StatementOriginsClearArgs(StrictModel):
    decl_name: str = Field(description="Declaration name to update in the current Statement NL stage batch.")
    reason: str | None = Field(default=None, description="Optional reason for clearing statement origins.")


class RepoDeclDependencyInput(StrictModel):
    name: str = Field(description="Project declaration name.")
    node: str | None = Field(default=None, description="Provider node path; omit for the current node.")
    repository: str | None = Field(default=None, description="Provider repo key; omit for the current repo.")
    revision: int | None = Field(default=None, ge=1, description="Optional provider declaration revision.")
    reason: str | None = Field(default=None, description="Why this declaration is required.")


class MathlibDeclDependencyInput(StrictModel):
    name: str = Field(description="Mathlib declaration name.")
    module: str | None = Field(default=None, description="Mathlib module containing the declaration, when known.")
    reason: str | None = Field(default=None, description="Why this Mathlib declaration is required.")


class StatementDependenciesAddArgs(StrictModel):
    decl_name: str = Field(description="Declaration name to update in the current statement stage batch.")
    repo_declarations: list[RepoDeclDependencyInput] = Field(
        default_factory=list,
        max_length=25,
        description="Project declaration dependencies to add atomically to the statement dependency set.",
    )
    mathlib_declarations: list[MathlibDeclDependencyInput] = Field(
        default_factory=list,
        max_length=25,
        description="Mathlib declaration dependencies to add atomically to the statement dependency set.",
    )

    @model_validator(mode="after")
    def _require_dependency(self) -> "StatementDependenciesAddArgs":
        if not self.repo_declarations and not self.mathlib_declarations:
            raise ValueError("At least one project or Mathlib dependency is required.")
        return self


class StatementDepRemoveArgs(StrictModel):
    decl_name: str = Field(description="Declaration name to update in the current statement stage batch.")
    index: int = Field(ge=0, description="0-based dependency index from the current statement dependency list.")


class StatementDepsClearArgs(StrictModel):
    decl_name: str = Field(description="Declaration name to update in the current statement stage batch.")
    reason: str | None = Field(default=None, description="Optional reason for clearing statement dependencies.")


class ProofNlSetArgs(StrictModel):
    decl_name: str = Field(description="Theorem-like declaration name to update in the current Proof NL stage batch.")
    text: str = Field(description="Complete natural-language proof route; may contain multiple Markdown paragraphs.")


class ProofSourceOriginAddArgs(StrictModel):
    decl_name: str = Field(description="Theorem-like declaration name to update in the current Proof NL stage batch.")
    source_path: str = Field(description="Stable source material path or source id already present in the committed source corpus.")
    start_line: int = Field(ge=1, description="1-based first source line supporting the proof route.")
    end_line: int = Field(ge=1, description="1-based final source line supporting the proof route.")
    note: str | None = Field(default=None, description="Optional note explaining how this source range supports the proof route.")


class ProofResourceOriginAddArgs(StrictModel):
    decl_name: str = Field(description="Theorem-like declaration name to update in the current Proof NL stage batch.")
    resource_key: str = Field(description="Stable resource key already present in the resource library.")
    start_locator: str | None = Field(default=None, description="Optional resource-local start locator, such as section, page, or paragraph.")
    end_locator: str | None = Field(default=None, description="Optional resource-local end locator.")
    note: str | None = Field(default=None, description="Optional note explaining how this resource supports the proof route.")


class ProofOriginRemoveArgs(StrictModel):
    decl_name: str = Field(description="Theorem-like declaration name to update in the current Proof NL stage batch.")
    index: int = Field(ge=0, description="0-based origin index from the current proof origin list.")


class ProofOriginsClearArgs(StrictModel):
    decl_name: str = Field(description="Theorem-like declaration name to update in the current Proof NL stage batch.")
    reason: str | None = Field(default=None, description="Optional reason for clearing proof origins.")


class ProofDependenciesAddArgs(StrictModel):
    decl_name: str = Field(description="Theorem-like declaration name to update in the current proof stage batch.")
    repo_declarations: list[RepoDeclDependencyInput] = Field(
        default_factory=list,
        max_length=25,
        description="Project declaration dependencies to add atomically to the proof dependency set.",
    )
    mathlib_declarations: list[MathlibDeclDependencyInput] = Field(
        default_factory=list,
        max_length=25,
        description="Mathlib declaration dependencies to add atomically to the proof dependency set.",
    )

    @model_validator(mode="after")
    def _require_dependency(self) -> "ProofDependenciesAddArgs":
        if not self.repo_declarations and not self.mathlib_declarations:
            raise ValueError("At least one project or Mathlib dependency is required.")
        return self


class ProofDepRemoveArgs(StrictModel):
    decl_name: str = Field(description="Theorem-like declaration name to update in the current proof stage batch.")
    index: int = Field(ge=0, description="0-based dependency index from the current proof dependency list.")


class ProofDepsClearArgs(StrictModel):
    decl_name: str = Field(description="Theorem-like declaration name to update in the current proof stage batch.")
    reason: str | None = Field(default=None, description="Optional reason for clearing proof dependencies.")


class DeclStageFormalArgs(StrictModel):
    round_id: str | None = Field(default=None, description="Current declaration round id; omit to use the current stage round.")
    decl_name: str = Field(description="Declaration name to update.")
    lean_code: str = Field(description="Captured Lean code to store in the declaration revision.")
    lean_check: dict[str, object] = Field(description="LeanCheck view produced by capture/check tools.")
    deps: list[str] | None = Field(default=None, description="Optional declaration dependency names.")


class DeclStageFileArgs(DeclNameArgs):
    pass


class DeclStageFileCheckArgs(DeclNameArgs):
    stage: str = Field(description="Formal capture stage to check: statement or proof.")


class StatementNlReviewPassedArgs(StrictModel):
    decl_name: str = Field(description="Declaration name under Statement NL review.")
    summary: str = Field(description="Why the current Statement NL candidate is ready for statement formalization.")


class StatementNlReviewRejectedArgs(StrictModel):
    decl_name: str = Field(description="Declaration name under Statement NL review.")
    summary: str = Field(description="Concise review summary for this rejected Statement NL candidate.")
    issue_categories: list[str] = Field(description="Concrete issue categories for the rejection.")
    required_changes: list[str] = Field(description="Actionable changes the worker must make before the next review.")


class StatementFormalReviewPassedArgs(StrictModel):
    decl_name: str = Field(description="Declaration name under Statement Formal review.")
    summary: str = Field(description="Why the current formal statement candidate preserves the accepted Statement NL meaning.")


class StatementFormalReviewRejectedArgs(StrictModel):
    decl_name: str = Field(description="Declaration name under Statement Formal review.")
    summary: str = Field(description="Concise review summary for this rejected formal statement candidate.")
    issue_categories: list[str] = Field(description="Concrete semantic, dependency, visibility, or evidence issue categories.")
    required_changes: list[str] = Field(description="Actionable formalization changes the worker must make before the next review.")


class ProofNlReviewPassedArgs(StrictModel):
    decl_name: str = Field(description="Declaration name under Proof NL review.")
    summary: str = Field(description="Why the current proof route is ready for proof formalization.")


class ProofNlReviewRejectedArgs(StrictModel):
    decl_name: str = Field(description="Declaration name under Proof NL review.")
    summary: str = Field(description="Concise review summary for this rejected proof route.")
    issue_categories: list[str] = Field(description="Concrete proof-route, origin, dependency, or planning issue categories.")
    required_changes: list[str] = Field(description="Actionable proof-route changes the worker must make before the next review.")
    recommended_next_action: str | None = Field(default=None, description="Optional recommended routing action, such as worker repair, helper declaration, or resource curation.")


class ProofFormalReviewPassedArgs(StrictModel):
    decl_name: str = Field(description="Declaration name under Proof Formal review.")
    summary: str = Field(description="Why the current formal proof implements the reviewed proof route and proves the accepted formal statement.")


class ProofFormalReviewRejectedArgs(StrictModel):
    decl_name: str = Field(description="Declaration name under Proof Formal review.")
    summary: str = Field(description="Concise review summary for this rejected formal proof.")
    issue_categories: list[str] = Field(description="Concrete proof-route alignment, source, dependency, helper, or semantic issue categories.")
    required_changes: list[str] = Field(description="Actionable proof formalization changes the worker must make before the next review.")
    recommended_next_action: str = Field(description="Recommended routing action for the retry, such as worker_repairable or needs_proof_nl_update.")


class StageReviewSubmitArgs(StrictModel):
    round_id: str = Field(description="Current declaration round id.")
    stage: str = Field(description="Stage under review.")
    summary: str = Field(description="Overall review summary for the stage.")


class AdapterUpstreamMetadataArgs(StrictModel):
    source_kind: Literal["git", "local_path"] = Field(default="git", description="Upstream source kind.")
    git_url: str | None = Field(default=None, description="Upstream git repository URL for git source_kind.")
    revision: str | None = Field(default=None, description="Optional upstream revision.")
    subdir: str | None = Field(default=None, description="Optional upstream repo subdirectory containing the Lake package.")
    local_path: str | None = Field(default=None, description="Local upstream path for local_path source_kind.")
    package_name: str = Field(description="Upstream Lake package name.")
    dependency_name: str = Field(description="Lake dependency name used by this adapter repo.")
    evidence_summary: str | None = Field(default=None, description="Evidence for choosing this upstream repo.")
    setup_summary: str | None = Field(default=None, description="Summary of setup/build status.")
    visible_modules: list[str] | None = Field(default=None, description="Optional visible upstream modules to record.")


class AdapterVisibleModulesArgs(StrictModel):
    modules: list[str] = Field(description="Visible upstream modules to record.")
    summary: str | None = Field(default=None, description="Optional summary.")


class UpstreamDeclSearchArgs(StrictModel):
    query: str = Field(description="Text or regex to search upstream declarations.")
    kind_filter: str | None = Field(default=None, description="Optional declaration kind filter.")
    module_filter: str | None = Field(default=None, description="Optional upstream module filter.")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum declarations to return.")


class UpstreamModuleDeclsArgs(StrictModel):
    module: str = Field(description="Upstream module name.")
    kind_filter: str | None = Field(default=None, description="Optional declaration kind filter.")


class UpstreamModuleArgs(StrictModel):
    module: str = Field(description="Upstream module name.")


class UpstreamDeclInspectArgs(StrictModel):
    module: str = Field(description="Upstream module name.")
    lean_decl_name: str = Field(description="Complete upstream Lean declaration name.")


class UpstreamSourceContextArgs(UpstreamDeclInspectArgs):
    line_window: int = Field(default=20, ge=1, le=200, description="Source context lines around the declaration.")


class UpstreamCaptureArgs(UpstreamDeclInspectArgs):
    capture_mode: Literal["statement_only", "full_declaration"] = Field(description="Whether to capture only statement code or full declaration code.")


class AdapterDeclCreateArgs(StrictModel):
    name: str = Field(description="Adapter declaration name.")
    kind: str = Field(description="Declaration kind.")
    module: str = Field(description="Upstream module containing the declaration.")
    lean_decl_name: str = Field(description="Complete upstream Lean declaration name.")
    summary: str = Field(description="Short adapter catalog summary.")


class AdapterFormalArgs(StrictModel):
    name: str = Field(description="Adapter declaration name.")
    code: str = Field(description="Lean code captured from the upstream repo.")


class AdapterNlArgs(StrictModel):
    name: str = Field(description="Adapter declaration name.")
    text: str = Field(description="Complete natural-language statement or proof text; may contain multiple Markdown paragraphs.")


class AdapterOriginArgs(StrictModel):
    name: str = Field(description="Adapter declaration name.")
    origin_text: str = Field(description="Origin text to append.")
    source_hint: str | None = Field(default=None, description="Optional origin source hint.")


class AdapterDepArgs(StrictModel):
    name: str = Field(description="Adapter declaration name.")
    dep_name: str = Field(description="Adapter declaration dependency name.")
    reason: str = Field(description="Why this dependency is needed.")


class AdapterDepRemoveArgs(StrictModel):
    name: str = Field(description="Adapter declaration name.")
    dep_name: str = Field(description="Adapter declaration dependency name to remove.")


class AdapterInterfaceBindArgs(StrictModel):
    interface_name: str = Field(description="Preparation interface name.")
    decl_name: str = Field(description="Adapter declaration name to bind.")
    binding_summary: str = Field(description="Summary explaining the binding.")


class AdapterInterfaceUnbindArgs(StrictModel):
    interface_name: str = Field(description="Preparation interface name.")
    reason: str = Field(description="Reason for unbinding this interface.")


class AdapterDeclOptionalNameArgs(StrictModel):
    name: str | None = Field(default=None, description="Optional adapter declaration name; omit to check all declarations.")


class AdapterDeclUpstreamFindArgs(StrictModel):
    module: str = Field(description="Upstream module containing the declaration.")
    lean_decl_name: str | None = Field(default=None, description="Optional complete upstream Lean declaration name to match.")
    adapter_name_query: str | None = Field(default=None, description="Optional adapter declaration name text query.")


class AdapterDeclNameArgs(StrictModel):
    name: str = Field(description="Adapter declaration name.")


class AdapterDeclListArgs(StrictModel):
    module_filter: str | None = Field(default=None, description="Optional upstream module filter.")
    kind_filter: str | None = Field(default=None, description="Optional declaration kind filter.")
    name_query: str | None = Field(default=None, description="Optional declaration name text filter.")
