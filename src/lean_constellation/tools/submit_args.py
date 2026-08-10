"""Pydantic argument models for Agent-facing submit_* tools."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclKind


def _normalized_non_empty_items(values: list[str], *, field_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            normalized.append(item)
    if not normalized:
        raise ValueError(f"{field_name} must contain at least one non-empty item")
    return normalized


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
    revision: str | None = Field(default=None, description="Optional immutable 40- or 64-character upstream commit; omit to resolve a compatible commit.")
    subdir: str | None = Field(default=None, description="Optional subdirectory containing the Lean project.")
    evidence_summary: str = Field(description="Concrete remote evidence supporting the adapter route.")
    known_risks: list[str] = Field(default_factory=list, description="Known risks that later preparation must verify.")


class SubmitNativeRepoChoiceArgs(SummarySubmitArgs):
    searched_targets: list[str] = Field(description="Concrete search queries, repository locators, or target names checked before choosing native.")

    @field_validator("searched_targets")
    @classmethod
    def _searched_targets_non_empty(cls, values: list[str]) -> list[str]:
        return _normalized_non_empty_items(values, field_name="searched_targets")


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
    missing_interfaces: list[str] = Field(
        default_factory=list,
        description="Exact current unbound required interfaces; leave empty only for a non-interface catalog preflight failure.",
    )
    evidence_summary: str | None = Field(default=None, description="Concrete evidence for the current catalog preflight failure; required when submitting blocked.")
    suggested_next_action: str | None = Field(default=None, description="Higher-level action needed to resolve the blocker; required when submitting blocked.")


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


class SubmitRepoExplorationArgs(SummarySubmitArgs):
    resource_objective: str | None = Field(
        default=None,
        description="Focused objective for supporting-resource discovery, when that category is needed.",
    )
    lean_provider_objective: str | None = Field(
        default=None,
        description="Focused objective for existing Lean-provider discovery, when that category is needed.",
    )
    mathlib_objective: str | None = Field(
        default=None,
        description="Focused objective for repository-level Mathlib reconnaissance, when that category is needed.",
    )
    context_summary: str | None = Field(
        default=None,
        description="Optional shared direction for the selected categories; do not copy SourceCorpus or SourceIndex contents.",
    )

    @field_validator(
        "resource_objective",
        "lean_provider_objective",
        "mathlib_objective",
        "context_summary",
    )
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def _at_least_one_objective(self):
        if not any(
            (
                self.resource_objective,
                self.lean_provider_objective,
                self.mathlib_objective,
            )
        ):
            raise ValueError("at least one repository exploration objective is required")
        return self


class RequirementInterfaceArg(StrictModel):
    name: str = Field(description="Stable public interface identity that the provider repository must expose.")
    kind: DeclKind = Field(description="Required public declaration kind that the provider repository must expose.")
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
        description="Optional immutable 40- or 64-character Git commit; omit to resolve bounded latest-first exact-compatible candidates."
    )
    subdir: str | None = Field(
        default=None,
        description="Optional repository-relative subdirectory containing the Lean project.",
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

    @field_validator("searched_targets")
    @classmethod
    def _searched_targets_non_empty(cls, values: list[str]) -> list[str]:
        return _normalized_non_empty_items(values, field_name="searched_targets")


class SubmitRepoReadyArgs(SummarySubmitArgs):
    pass


class RepoResourceCandidateArg(StrictModel):
    target: str = Field(description="OpenAlex id, DOI, arXiv locator, or other locator accepted by resource inspection.")
    support_summary: str = Field(description="Concrete mathematical statement, construction, or evidence this resource may supply for the repository objective.")
    risks_or_gaps: list[str] = Field(default_factory=list, description="Known uncertainty, missing access, or scope gaps.")
    recommended_handling: Literal[
        "local_resource",
        "provider_requirement",
        "inspect_later",
    ] = Field(description="Use local_resource for supporting material owned here, provider_requirement for an independent reusable formal boundary, or inspect_later for a real inspected candidate whose usefulness or ownership remains unresolved.")
    consumer_need: str | None = Field(default=None, description="Concrete consumer need; required for provider_requirement and optional otherwise.")
    provider_scope: str | None = Field(default=None, description="Independent provider responsibility; required for provider_requirement and optional otherwise.")

    @field_validator("target", "support_summary")
    @classmethod
    def _required_text_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("resource candidate target and support_summary must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _handling_fields_consistent(self):
        if self.recommended_handling == "provider_requirement" and (
            not self.consumer_need
            or not self.consumer_need.strip()
            or not self.provider_scope
            or not self.provider_scope.strip()
        ):
            raise ValueError("provider_requirement candidates require consumer_need and provider_scope")
        return self


class SubmitRepoResourceDiscoveryResultArgs(SummarySubmitArgs):
    outcome: Literal["completed", "no_useful_findings", "incomplete"] = Field(description="Terminal discovery outcome.")
    candidates: list[RepoResourceCandidateArg] = Field(default_factory=list, max_length=5, description="Up to five promising targets in recommendation order; the backend re-inspects every target and supplies canonical metadata.")

    @model_validator(mode="after")
    def _candidate_consistency(self):
        if self.outcome == "no_useful_findings" and self.candidates:
            raise ValueError("no_useful_findings may not include resource candidates")
        if self.outcome == "completed" and not self.candidates:
            raise ValueError("completed resource discovery requires at least one candidate; use no_useful_findings otherwise")
        return self


class RepoLeanProviderCandidateArg(StrictModel):
    git_url: str = Field(description="GitHub repository URL or owner/name slug to probe.")
    revision: str | None = Field(default=None, description="Optional immutable 40- or 64-character commit; omit to let the backend resolve the inspected revision.")
    subdir: str | None = Field(default=None, description="Lean project subdirectory when the project is not at repository root.")
    capability_summary: str = Field(description="Mathematical capability this repository may provide for the current objective.")
    relevant_declarations: list[str] = Field(default_factory=list, description="Declaration names or precise semantic declaration clues relevant to the objective; required for direct_adapter_requirement.")
    gaps: list[str] = Field(default_factory=list, description="Missing evidence or capability gaps.")
    risks: list[str] = Field(default_factory=list, description="License, version, layout, maintenance, or adaptation risks.")
    recommendation: Literal[
        "direct_adapter_requirement",
        "generic_requirement",
        "inspect_later",
    ] = Field(description="Use direct_adapter_requirement only for a gap-free exact Lean/Lake candidate, generic_requirement for an independent provider need that still requires route discovery, or inspect_later for a real probed candidate needing more evidence.")

    @field_validator("git_url", "capability_summary")
    @classmethod
    def _provider_required_text_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider candidate git_url and capability_summary must be non-empty")
        return normalized

    @field_validator("revision")
    @classmethod
    def _immutable_revision_if_present(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", normalized) is None:
            raise ValueError("revision must be an immutable 40- or 64-character Git commit")
        return normalized

    @model_validator(mode="after")
    def _direct_adapter_consistency(self):
        if self.recommendation == "direct_adapter_requirement":
            if not any(item.strip() for item in self.relevant_declarations):
                raise ValueError("direct adapter candidates require at least one relevant declaration")
            if self.gaps:
                raise ValueError("direct adapter candidates may not retain unresolved evidence gaps")
        return self


class SubmitRepoLeanProviderDiscoveryResultArgs(SummarySubmitArgs):
    outcome: Literal["completed", "no_useful_findings", "incomplete"] = Field(description="Terminal provider-discovery outcome.")
    candidates: list[RepoLeanProviderCandidateArg] = Field(default_factory=list, max_length=8, description="Bounded verified Lean repository candidates.")

    @model_validator(mode="after")
    def _candidate_consistency(self):
        if self.outcome == "no_useful_findings" and self.candidates:
            raise ValueError("no_useful_findings may not include provider candidates")
        if self.outcome == "completed" and not self.candidates:
            raise ValueError("completed provider discovery requires at least one candidate; use no_useful_findings otherwise")
        return self


class SubmitRepoMathlibReconResultArgs(SummarySubmitArgs):
    outcome: Literal["completed", "no_useful_findings", "incomplete"] = Field(description="Terminal repository Mathlib recon outcome.")
    relevant_modules: list[str] = Field(default_factory=list, description="Objective-relevant module names already recorded in the current repository MathlibIndex.")
    relevant_declarations: list[str] = Field(default_factory=list, description="Objective-relevant declaration names already recorded in the current repository MathlibIndex.")
    unresolved: list[str] = Field(default_factory=list, description="Objective-relevant Mathlib questions left unresolved.")
    usage_notes: list[str] = Field(default_factory=list, description="Concise repository-wide usage guidance for the checked findings.")

    @field_validator("relevant_modules", "relevant_declarations", "unresolved", "usage_notes")
    @classmethod
    def _normalized_lists(cls, values: list[str], info: ValidationInfo) -> list[str]:
        if not values:
            return []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if item and item not in seen:
                seen.add(item)
                normalized.append(item)
        if not normalized:
            raise ValueError(f"{info.field_name} must contain at least one non-empty item")
        return normalized

    @model_validator(mode="after")
    def _outcome_matches_relevant_entries(self):
        relevant = bool(self.relevant_modules or self.relevant_declarations)
        if self.outcome == "no_useful_findings" and relevant:
            raise ValueError("no_useful_findings may not include relevant Mathlib entries")
        if self.outcome == "completed" and not relevant:
            raise ValueError("completed Mathlib recon requires a relevant indexed entry; use no_useful_findings otherwise")
        return self


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
