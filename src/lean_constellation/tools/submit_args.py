"""Pydantic argument models for Agent-facing submit_* tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel


class SummarySubmitArgs(StrictModel):
    summary: str = Field(description="Concise summary of the submitted result.")


class ReasonSubmitArgs(StrictModel):
    reason: str = Field(description="Reason for this terminal submission.")


class SubmitAdapterRepoChoiceArgs(SummarySubmitArgs):
    upstream_github_url: str = Field(description="GitHub URL for the existing Lean upstream repository.")
    upstream_revision: str | None = Field(default=None, description="Optional upstream revision, tag, or commit.")
    upstream_subdir: str | None = Field(default=None, description="Optional subdirectory containing the Lean project.")
    adapter_repo_name: str | None = Field(default=None, description="Optional preferred provider repo key.")


class SubmitNativeRepoChoiceArgs(SummarySubmitArgs):
    native_repo_name: str | None = Field(default=None, description="Optional preferred native provider repo key.")
    source_corpus_mode: Literal["prepare", "existing"] = Field(
        description="Source corpus mode for the native repo shell. Native repo preparation cannot use none.",
    )


class SubmitSourceCorpusPreparedArgs(SummarySubmitArgs):
    entry_path: str = Field(description="Entry document path relative to the source corpus root.")
    overview: str = Field(description="Overview of the prepared source corpus.")
    preparation_summary: str = Field(description="What was acquired, normalized, and organized.")
    relpath: str = Field(default=".lean_constellation/source", description="Compatibility field; must match the current preparation input source corpus root.")


class SubmitSourceCorpusBlockedArgs(ReasonSubmitArgs):
    attempted_targets: list[str] = Field(default_factory=list, description="Targets already attempted.")
    missing_materials: list[str] = Field(default_factory=list, description="Materials still missing.")
    suggested_next_action: str | None = Field(default=None, description="Suggested admin or upstream action.")


class SubmitSourceIndexBuilderRoundArgs(SummarySubmitArgs):
    pass


class SubmitSourceIndexReviewRoundArgs(SummarySubmitArgs):
    approved: bool = Field(description="Whether the reviewer accepts this SourceIndex draft.")
    feedback: str | None = Field(default=None, description="Required feedback when approved=false.")


class SubmitRootInterfacePrepareReadyArgs(SummarySubmitArgs):
    pass


class SubmitAdapterCatalogReadyArgs(SummarySubmitArgs):
    pass


class SubmitAdapterCatalogBlockedArgs(ReasonSubmitArgs):
    missing_interfaces: list[str] = Field(default_factory=list, description="Required interfaces that could not be bound.")
    evidence_summary: str | None = Field(default=None, description="Evidence gathered before blocking.")
    suggested_next_action: str | None = Field(default=None, description="Suggested follow-up action.")


class SubmitResourceRequestArgs(SummarySubmitArgs):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"] = Field(description="Kind of resource target.")
    target: str = Field(description="URL, arXiv id, or local path.")
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version.")
    context_summary: str | None = Field(default=None, description="Why this resource is needed.")


class SubmitResourceDuplicateArgs(StrictModel):
    summary: str | None = Field(default=None, description="Optional concise summary for this duplicate submission.")
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"] | None = Field(default=None, description="Compatibility field; when provided, must match the current resource request target kind.")
    target: str | None = Field(default=None, description="Compatibility field; when provided, must match the current resource request target.")
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version for the duplicate target.")
    existing_kind: Literal["resource", "source"] = Field(description="Whether the duplicate is an accepted resource or original source material.")
    duplicate_reason: str = Field(description="Why the existing material is the same target.")
    existing_resource_key: str | None = Field(default=None, description="Existing resource key when existing_kind is resource.")
    existing_source_path: str | None = Field(default=None, description="Existing source corpus path when existing_kind is source.")
    preview: str | None = Field(default=None, description="Short evidence excerpt showing the duplicate relationship.")


class SubmitLocalResourceCreatedArgs(SummarySubmitArgs):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"] | None = Field(default=None, description="Compatibility field; when provided, must match the current resource request target kind.")
    target: str | None = Field(default=None, description="Compatibility field; when provided, must match the current resource request target.")
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version for the finalized target.")
    draft_id: str = Field(description="Resource draft id to finalize.")


class SubmitExternalRepoRequiredArgs(ReasonSubmitArgs):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"] | None = Field(default=None, description="Compatibility field; when provided, must match the current resource request target kind.")
    target: str | None = Field(default=None, description="Compatibility field; when provided, must match the current resource request target.")
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version for the external provider target.")
    source_description: str = Field(description="Source description to pass to a provider repo requirement.")
    suggested_repo_name: str | None = Field(default=None, description="Optional suggested provider repo key for the external requirement.")
    required_interfaces_hint: str | None = Field(default=None, description="Optional description of interfaces the external provider repo should expose.")


class SubmitResourceRejectedArgs(ReasonSubmitArgs):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"] | None = Field(default=None, description="Compatibility field; when provided, must match the current resource request target kind.")
    target: str | None = Field(default=None, description="Compatibility field; when provided, must match the current resource request target.")
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version for the rejected target.")
    details: list[str] = Field(default_factory=list, description="Concrete reasons or evidence supporting the rejection.")


class SubmitContentNodeTasksArgs(SummarySubmitArgs):
    node_paths: list[str] = Field(description="Runnable content node paths to dispatch.")
    task_mode: str = Field(default="run", description="Task mode for each content node child flow.")


class RequirementInterfaceArg(StrictModel):
    name: str = Field(description="Interface name requested from the provider repo.")
    kind: str = Field(description="Interface kind expected from the provider repo, such as theorem, definition, or namespace.")
    summary: str = Field(description="Short summary of what this requested interface should provide.")
    statement_hint: str | None = Field(default=None, description="Optional informal statement or signature hint for the requested interface.")


class SubmitRepoRequirementArgs(SummarySubmitArgs):
    name: str = Field(description="Requirement name in the current consumer repo.")
    target_repo: str = Field(description="Provider repo key to request.")
    source_description: str | None = Field(default=None, description="Optional source or context description motivating the provider repo requirement.")
    reason: str | None = Field(default=None, description="Why the current repo needs this provider repo requirement.")
    interfaces: list[RequirementInterfaceArg] = Field(default_factory=list, description="Interfaces requested from the provider repo.")


class SubmitRepoReadyArgs(SummarySubmitArgs):
    pass


class SubmitContentPreparationReconArgs(SummarySubmitArgs):
    recon_kind: Literal["node_dir_dependency", "mathlib", "resource"] = Field(description="Preparation recon child flow kind to dispatch for the current content node.")
    objective: str = Field(description="Focused objective for the preparation recon child flow.")
    context_summary: str | None = Field(default=None, description="Optional context summary to pass to the preparation recon child flow.")


class SubmitCurrentDeclRoundArgs(SummarySubmitArgs):
    strategy_id: str = Field(description="Current declaration strategy id that owns the submitted round.")
    round_id: str = Field(description="Declaration round id to dispatch for child DeclGraphRoundFlow execution.")
    round_index: int | None = Field(default=None, description="Optional human-readable round index for diagnostics and review.")


class SubmitContentNodeReadyArgs(SummarySubmitArgs):
    pass


class SubmitContentNodeBlockedArgs(ReasonSubmitArgs):
    pass


class SubmitContentNodeFailedArgs(ReasonSubmitArgs):
    pass


class SubmitNodeDirDependencyReconCompletedArgs(SummarySubmitArgs):
    dependency_change_summary: str | None = Field(default=None, description="Summary of node dependency additions, removals, or confirmation that no changes were needed.")
    checked_boundary_summary: str | None = Field(default=None, description="Summary of visible same-repo or provider boundaries checked during recon.")
    useful_findings: list[str] = Field(default_factory=list, description="Useful dependency findings or candidate declarations found during recon.")
    unresolved_within_visible_boundaries: list[str] = Field(default_factory=list, description="Relevant dependency questions still unresolved within the visible boundaries.")


class SubmitMathlibReconCompletedArgs(SummarySubmitArgs):
    index_update_summary: str | None = Field(default=None, description="Summary of MathlibIndex records created, reused, or confirmed during recon.")
    node_mathlib_hint_summary: str | None = Field(default=None, description="Summary of current-node Mathlib module or declaration hint changes.")
    useful_findings: list[str] = Field(default_factory=list, description="Useful Mathlib modules, declarations, or search findings from recon.")
    unresolved_in_mathlib: list[str] = Field(default_factory=list, description="Mathlib questions or candidate searches that remain unresolved.")


class SubmitResourceReconCompletedArgs(SummarySubmitArgs):
    material_change_summary: str | None = Field(default=None, description="Summary of material references attached to the current node or confirmation that no changes were needed.")
    checked_material_summary: str | None = Field(default=None, description="Summary of source/resource material checked during recon.")
    useful_findings: list[str] = Field(default_factory=list, description="Useful source/resource findings from recon.")
    unresolved_material_needs: list[str] = Field(default_factory=list, description="Material needs still unresolved after recon.")


class SubmitResourceReconBlockedArgs(ReasonSubmitArgs):
    missing_targets: list[str] = Field(default_factory=list, description="Resource or material targets that are still missing after recon.")


class SubmitStageWorkerCompletedArgs(SummarySubmitArgs):
    changed_decl_names: list[str] = Field(default_factory=list, description="Optional declarations changed during this worker attempt.")
    notes: str | None = Field(default=None, description="Optional additional notes for the reviewer.")


class SubmitStageWorkerBlockedArgs(ReasonSubmitArgs):
    affected_decl_names: list[str] = Field(default_factory=list, description="Declaration names that remain blocked in this stage worker batch.")
    checked_context_summary: str | None = Field(default=None, description="What context and evidence were checked before blocking.")
    blocked_needs: list[str] = Field(default_factory=list, description="Concrete missing evidence, dependencies, resources, or planning inputs.")


class SubmitStageReviewArgs(SummarySubmitArgs):
    pass
