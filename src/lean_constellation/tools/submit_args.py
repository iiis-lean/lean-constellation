"""Pydantic argument models for Agent-facing submit_* tools."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel


class SummarySubmitArgs(StrictModel):
    summary: str = Field(description="Concise summary of the submitted result.")

    @field_validator("summary")
    @classmethod
    def _summary_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("summary must be non-empty")
        return value


class ReasonSubmitArgs(StrictModel):
    reason: str = Field(description="Reason for this terminal submission.")

    @field_validator("reason")
    @classmethod
    def _reason_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must be non-empty")
        return value


class SubmitAdapterRepoChoiceArgs(StrictModel):
    git_url: str = Field(description="GitHub URL or owner/name slug for the existing Lean upstream repository.")
    revision: str | None = Field(default=None, description="Optional upstream revision, tag, or commit.")
    subdir: str | None = Field(default=None, description="Optional subdirectory containing the Lean project.")
    package_name: str | None = Field(default=None, description="Optional upstream Lake package name.")
    likely_import_module: str | None = Field(default=None, description="Optional likely Lean module to import from the upstream package.")
    evidence_summary: str = Field(description="Concrete remote evidence supporting the adapter route.")
    known_risks: list[str] = Field(default_factory=list, description="Known risks that later preparation must verify.")


class RejectedUpstreamCandidateArgs(StrictModel):
    git_url: str | None = Field(default=None, description="Rejected GitHub candidate URL or slug, if known.")
    name: str | None = Field(default=None, description="Rejected candidate name or pattern, if no URL is known.")
    reason: str = Field(description="Reason this candidate should not be used as adapter upstream.")
    evidence_summary: str | None = Field(default=None, description="Optional evidence gathered for the rejection.")


class SubmitNativeRepoChoiceArgs(SummarySubmitArgs):
    searched_targets: list[str] = Field(default_factory=list, description="Search queries or target names checked before choosing native.")
    rejected_candidates: list[RejectedUpstreamCandidateArgs] = Field(default_factory=list, description="Upstream candidates rejected before choosing native.")


class SubmitSourceCorpusPreparedArgs(SummarySubmitArgs):
    entry_path: str = Field(description="Entry document path relative to the source corpus root.")
    overview: str = Field(description="Overview of the prepared source corpus.")
    preparation_summary: str = Field(description="What was acquired, normalized, and organized.")


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
    requested_use: Literal["supporting_material", "formal_dependency", "unknown"] = Field(
        description="Requested ownership use; formal_dependency must not be silently curated as a local Resource."
    )
    consumer_need: str = Field(description="Concrete statement, construction, evidence, or API the caller needs.")
    context_summary: str | None = Field(default=None, description="Why this resource is needed.")


class SubmitResourceDuplicateArgs(StrictModel):
    summary: str | None = Field(default=None, description="Optional concise summary for this duplicate submission.")
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version for the duplicate target.")
    existing_kind: Literal["resource", "source"] = Field(description="Whether the duplicate is an accepted resource or original source material.")
    duplicate_reason: str = Field(description="Why the existing material is the same target.")
    existing_resource_key: str | None = Field(default=None, description="Existing resource key when existing_kind is resource.")
    existing_source_path: str | None = Field(default=None, description="Existing source corpus path when existing_kind is source.")
    preview: str | None = Field(default=None, description="Short evidence excerpt showing the duplicate relationship.")


class SubmitLocalResourceCreatedArgs(SummarySubmitArgs):
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version for the finalized target.")
    draft_id: str = Field(description="Resource draft id to finalize.")
    classification_reason: str = Field(description="Why this target is supporting material owned by the current repo.")
    resource_role: str = Field(description="Narrow role the finalized Resource serves for the current repo.")
    consumer_formalization_scope: str = Field(
        description="What mathematical formalization responsibility remains in the current repo."
    )


class SubmitExternalRepoRequiredArgs(ReasonSubmitArgs):
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version for the external provider target.")
    source_description: str = Field(description="Source description to pass to a provider repo requirement.")
    classification_reason: str = Field(description="Why this target belongs to an independent provider boundary.")
    relation_to_current_repo_or_node: str = Field(description="How the provider result will be consumed here.")
    consumer_need: str = Field(description="Concrete provider capability required by the consumer.")
    provider_scope: str = Field(description="Independent mathematical responsibility the provider should own.")
    suggested_repo_name: str | None = Field(default=None, description="Optional suggested provider repo key for the external requirement.")
    required_interfaces_hint: str | None = Field(default=None, description="Optional description of interfaces the external provider repo should expose.")
    existing_lean_repo_signal: str | None = Field(
        default=None,
        description="Evidence for or against an existing Lean implementation.",
    )


class SubmitResourceRejectedArgs(ReasonSubmitArgs):
    arxiv_version: str | None = Field(default=None, description="Optional arXiv version for the rejected target.")
    details: list[str] = Field(default_factory=list, description="Concrete reasons or evidence supporting the rejection.")


class SubmitContentNodeTasksArgs(SummarySubmitArgs):
    node_paths: list[str] = Field(description="Runnable content node paths to dispatch.")


class RepoExplorationRequestArg(StrictModel):
    kind: Literal["resource", "lean_provider", "mathlib"] = Field(
        description="Distinct repository exploration category."
    )
    objective: str = Field(description="Focused, verifiable objective for this exploration category.")
    context_summary: str | None = Field(
        default=None,
        description="Optional direction that does not duplicate the SourceCorpus or SourceIndex.",
    )

    @field_validator("objective")
    @classmethod
    def _objective_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("exploration objective must be non-empty")
        return value


class SubmitRepoExplorationArgs(SummarySubmitArgs):
    explorations: list[RepoExplorationRequestArg] = Field(
        min_length=1,
        max_length=3,
        description="One to three distinct focused repository exploration requests.",
    )

    @model_validator(mode="after")
    def _unique_kinds(self):
        kinds = [item.kind for item in self.explorations]
        if len(kinds) != len(set(kinds)):
            raise ValueError("repo exploration kinds must be unique within one batch")
        return self


class RequirementInterfaceArg(StrictModel):
    name: str = Field(description="Stable public interface identity that the provider repository must expose.")
    kind: str = Field(description="Required public declaration kind, using one of the supported DeclKind values.")
    summary: str = Field(description="Concise mathematical meaning of the public interface required from the provider.")
    statement_hint: str | None = Field(default=None, description="Optional informal statement or signature guidance when no exact Lean header is required.")
    expected_statement_lean_code: str | None = Field(
        default=None,
        description="Optional exact Lean theorem declaration whose header the provider must preserve and satisfy verbatim.",
    )


class SubmitRepoRequirementArgs(SummarySubmitArgs):
    name: str = Field(
        description="Unique consumer-local requirement identity in lower_snake_case within the current repository."
    )
    target_repo: str = Field(
        description="Independent UpperCamelCase mathematical repo key to request; do not add role suffixes such as Provider, Repo, or Dependency."
    )
    source_description: str | None = Field(
        default=None,
        description="Optional description of the mathematical source, target material, or independent scope the provider repository should cover.",
    )
    reason: str | None = Field(
        default=None,
        description="Why the consumer needs this capability as an independent repository dependency instead of current-repo, Mathlib, or Resource work.",
    )
    interfaces: list[RequirementInterfaceArg] = Field(
        default_factory=list,
        description="Minimal stable public API boundary that the provider repository must expose.",
    )


class SubmitAdapterRepoRequirementArgs(SubmitRepoRequirementArgs):
    git_url: str = Field(
        description="GitHub URL or owner/name slug for the confirmed Lean provider repository."
    )
    revision: str | None = Field(
        default=None,
        description="Optional explicit immutable 40- or 64-character Git commit; when omitted the system resolves bounded latest-first exact-compatible candidates."
    )
    subdir: str | None = Field(
        default=None,
        description="Optional repository-relative subdirectory containing the Lean project.",
    )
    package_name: str | None = Field(
        default=None,
        description="Optional verified Lake package name.",
    )
    likely_import_module: str | None = Field(
        default=None,
        description="Optional likely Lean module to import from the provider package.",
    )
    evidence_summary: str = Field(
        description="Concrete evidence confirming this exact adapter route."
    )
    known_risks: list[str] = Field(
        default_factory=list,
        description="Known risks later adapter preparation must verify.",
    )


class SubmitNativeRepoRequirementArgs(SubmitRepoRequirementArgs):
    evidence_summary: str = Field(
        description="Concrete evidence that a new native provider is the appropriate route."
    )
    searched_targets: list[str] = Field(
        description="Non-empty search queries or upstream targets checked before choosing native."
    )
    rejected_candidates: list[RejectedUpstreamCandidateArgs] = Field(
        default_factory=list,
        description="Specific upstream candidates rejected before choosing native.",
    )


class SubmitRepoReadyArgs(SummarySubmitArgs):
    pass


class RepoResourceCandidateArg(StrictModel):
    title: str = Field(description="Resource title from verified metadata.")
    authors: list[str] = Field(default_factory=list, description="Verified author names.")
    resource_kind: str = Field(description="Resource kind such as paper, book, documentation, or web.")
    canonical_locator: str = Field(description="Canonical DOI, arXiv id/version, or stable URL.")
    version: str | None = Field(default=None, description="Exact resource version when relevant.")
    source_urls: list[str] = Field(default_factory=list, description="URLs supporting this metadata and recommendation.")
    relevance: str = Field(description="Specific relevance to the repository objective.")
    support_expected: str = Field(description="Mathematical statement, construction, or evidence expected from this resource.")
    reliability: str = Field(description="Source reliability assessment.")
    risks_or_gaps: list[str] = Field(default_factory=list, description="Known uncertainty, missing access, or scope gaps.")
    recommended_handling: Literal[
        "local_resource",
        "provider_requirement",
        "inspect_later",
        "ignore",
    ] = Field(description="Recommended material ownership handling.")
    classification_reason: str = Field(description="Why this ownership classification fits the candidate.")
    consumer_need: str = Field(description="Concrete statement, construction, evidence, or API the consumer needs.")
    suggested_repo_name: str | None = Field(default=None, description="Optional provider repo key suggestion.")
    provider_scope: str | None = Field(default=None, description="Independent provider responsibility when recommended.")
    required_interfaces_hint: str | None = Field(default=None, description="Minimal provider API hint when recommended.")
    existing_lean_repo_signal: str | None = Field(default=None, description="Evidence for an existing Lean implementation.")
    lean_provider_search_recommended: bool = Field(
        default=False,
        description="Whether RepoLeanProviderDiscovery should inspect existing Lean candidates.",
    )


class SubmitRepoResourceDiscoveryResultArgs(SummarySubmitArgs):
    outcome: Literal["completed", "no_useful_findings", "incomplete"] = Field(description="Terminal discovery outcome.")
    candidates: list[RepoResourceCandidateArg] = Field(default_factory=list, max_length=10, description="Bounded verified candidates, recommended first.")

    @model_validator(mode="after")
    def _candidate_consistency(self):
        if self.outcome == "no_useful_findings" and self.candidates:
            raise ValueError("no_useful_findings may not include resource candidates")
        for candidate in self.candidates:
            if candidate.recommended_handling == "local_resource" and (
                not candidate.canonical_locator.strip() or not candidate.source_urls
            ):
                raise ValueError("local_resource candidates require a canonical locator and source URL")
            if candidate.recommended_handling == "provider_requirement" and (
                not candidate.provider_scope or not candidate.provider_scope.strip()
            ):
                raise ValueError("provider_requirement candidates require provider_scope")
        return self


class RepoLeanProviderCandidateArg(StrictModel):
    git_url: str = Field(description="Canonical GitHub repository URL.")
    resolved_revision: str = Field(description="Resolved immutable commit for direct candidates, or best inspected revision otherwise.")
    subdir: str | None = Field(default=None, description="Lean project subdirectory when the project is not at repository root.")
    package_name: str | None = Field(default=None, description="Verified Lake package name when known.")
    likely_import_modules: list[str] = Field(default_factory=list, description="Likely importable modules supported by repository evidence.")
    relevant_interfaces: list[str] = Field(default_factory=list, description="Relevant public Lean declarations or interface descriptions.")
    lean_evidence: list[str] = Field(default_factory=list, description="Concrete Lean file, module, declaration, or build-layout evidence.")
    adapter_feasibility: Literal["ready", "plausible", "unsuitable"] = Field(description="Evidence-based adapter feasibility.")
    gaps: list[str] = Field(default_factory=list, description="Missing evidence or capability gaps.")
    risks: list[str] = Field(default_factory=list, description="License, version, layout, maintenance, or adaptation risks.")
    recommendation: Literal["direct_adapter_requirement", "generic_requirement", "ignore"] = Field(description="Recommended Coordinator handling.")

    @model_validator(mode="after")
    def _direct_adapter_consistency(self):
        if self.adapter_feasibility == "unsuitable" and self.recommendation != "ignore":
            raise ValueError("unsuitable provider candidates must be ignored")
        if self.adapter_feasibility == "ready":
            if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", self.resolved_revision) is None:
                raise ValueError("ready adapter candidates require an immutable commit")
            if not self.package_name or not self.package_name.strip():
                raise ValueError("ready adapter candidates require a verified package name")
            if not any(item.strip() for item in self.likely_import_modules):
                raise ValueError("ready adapter candidates require a verified import module")
            if not any(item.strip() for item in self.relevant_interfaces):
                raise ValueError("ready adapter candidates require relevant Lean interfaces")
            if not any(item.strip() for item in self.lean_evidence):
                raise ValueError("ready adapter candidates require Lean file or declaration evidence")
            if not any(
                ".lean" in item.casefold()
                or re.search(
                    r"\b(module|declaration|theorem|lemma|def|namespace)\b",
                    item,
                    re.IGNORECASE,
                )
                for item in self.lean_evidence
            ):
                raise ValueError("ready adapter evidence must name a Lean path, module, or declaration")
            if self.gaps:
                raise ValueError("ready adapter candidates may not retain unresolved evidence gaps")
        if self.recommendation == "direct_adapter_requirement":
            if self.adapter_feasibility != "ready":
                raise ValueError("direct adapter candidates must be ready")
            normalized_url = self.git_url.strip().lower().removesuffix(".git").rstrip("/")
            if normalized_url in {
                "leanprover-community/mathlib",
                "leanprover-community/mathlib4",
                "https://github.com/leanprover-community/mathlib",
                "https://github.com/leanprover-community/mathlib4",
            }:
                raise ValueError("Mathlib is not a direct adapter/provider candidate")
        return self


class SubmitRepoLeanProviderDiscoveryResultArgs(SummarySubmitArgs):
    outcome: Literal["completed", "no_useful_findings", "incomplete"] = Field(description="Terminal provider-discovery outcome.")
    candidates: list[RepoLeanProviderCandidateArg] = Field(default_factory=list, max_length=8, description="Bounded verified Lean repository candidates.")

    @model_validator(mode="after")
    def _candidate_consistency(self):
        if self.outcome == "no_useful_findings" and self.candidates:
            raise ValueError("no_useful_findings may not include provider candidates")
        return self


class SubmitRepoMathlibReconResultArgs(SummarySubmitArgs):
    outcome: Literal["completed", "no_useful_findings", "incomplete"] = Field(description="Terminal repository Mathlib recon outcome.")
    created_modules: list[str] = Field(default_factory=list, description="Checked Mathlib modules newly recorded in the repository index.")
    reused_modules: list[str] = Field(default_factory=list, description="Existing checked Mathlib modules reused for the objective.")
    created_declarations: list[str] = Field(default_factory=list, description="Checked Mathlib declarations newly recorded in the repository index.")
    reused_declarations: list[str] = Field(default_factory=list, description="Existing checked Mathlib declarations reused for the objective.")
    unresolved: list[str] = Field(default_factory=list, description="Objective-relevant Mathlib questions left unresolved.")
    usage_notes: list[str] = Field(default_factory=list, description="Concise repository-wide usage guidance for the checked findings.")


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
