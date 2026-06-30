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
    relpath: str = Field(default=".lean_constellation/source", description="Source corpus path relative to repo root.")


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


class SubmitResourceDuplicateArgs(ReasonSubmitArgs):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    existing_kind: Literal["resource", "source"]
    duplicate_reason: str = Field(description="Why the existing material is the same target.")
    existing_resource_key: str | None = None
    existing_source_path: str | None = None
    preview: str | None = None


class SubmitLocalResourceCreatedArgs(SummarySubmitArgs):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    draft_id: str = Field(description="Resource draft id to finalize.")


class SubmitExternalRepoRequiredArgs(ReasonSubmitArgs):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    source_description: str = Field(description="Source description to pass to a provider repo requirement.")
    suggested_repo_name: str | None = None
    required_interfaces_hint: str | None = None


class SubmitResourceRejectedArgs(ReasonSubmitArgs):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    details: list[str] = Field(default_factory=list)


class SubmitContentNodeTasksArgs(SummarySubmitArgs):
    node_paths: list[str] = Field(description="Runnable content node paths to dispatch.")
    task_mode: str = Field(default="run", description="Task mode for each content node child flow.")


class RequirementInterfaceArg(StrictModel):
    name: str
    kind: str
    summary: str
    statement_hint: str | None = None


class SubmitRepoRequirementArgs(SummarySubmitArgs):
    name: str = Field(description="Requirement name in the current consumer repo.")
    target_repo: str = Field(description="Provider repo key to request.")
    source_description: str | None = None
    reason: str | None = None
    interfaces: list[RequirementInterfaceArg] = Field(default_factory=list)


class SubmitRepoReadyArgs(SummarySubmitArgs):
    pass


class SubmitContentPreparationReconArgs(SummarySubmitArgs):
    recon_kind: Literal["node_dir_dependency", "mathlib", "resource"]
    objective: str | None = None
    context_summary: str | None = None


class SubmitCurrentDeclRoundArgs(SummarySubmitArgs):
    strategy_id: str
    round_id: str
    round_index: int | None = None


class SubmitContentNodeReadyArgs(SummarySubmitArgs):
    pass


class SubmitContentNodeBlockedArgs(ReasonSubmitArgs):
    pass


class SubmitContentNodeFailedArgs(ReasonSubmitArgs):
    pass


class SubmitNodeDirDependencyReconCompletedArgs(SummarySubmitArgs):
    added_node_deps: list[str] = Field(default_factory=list)
    removed_node_deps: list[str] = Field(default_factory=list)


class SubmitMathlibReconCompletedArgs(SummarySubmitArgs):
    added_modules: list[str] = Field(default_factory=list)
    added_decls: list[str] = Field(default_factory=list)


class SubmitResourceReconCompletedArgs(SummarySubmitArgs):
    added_owned_refs: list[str] = Field(default_factory=list)
    added_context_refs: list[str] = Field(default_factory=list)


class SubmitResourceReconBlockedArgs(ReasonSubmitArgs):
    missing_targets: list[str] = Field(default_factory=list)


class SubmitStageWorkerCompletedArgs(SummarySubmitArgs):
    completed_decl_names: list[str] = Field(default_factory=list)


class SubmitStageWorkerBlockedArgs(ReasonSubmitArgs):
    affected_decl_names: list[str] = Field(default_factory=list)


class SubmitStageReviewArgs(SummarySubmitArgs):
    pass
