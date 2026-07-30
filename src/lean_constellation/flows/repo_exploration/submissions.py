"""Terminal submissions produced by repository exploration agents."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.flows.common.submissions import LeanBaseSubmission


RepoExplorationOutcome = Literal["completed", "no_useful_findings", "incomplete"]


class RepoResourceCandidate(StrictModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    resource_kind: str
    canonical_locator: str
    version: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    relevance: str
    support_expected: str
    reliability: str
    risks_or_gaps: list[str] = Field(default_factory=list)
    recommendation: Literal["request", "inspect_later", "ignore"]


class RepoLeanProviderCandidate(StrictModel):
    git_url: str
    resolved_revision: str
    subdir: str | None = None
    package_name: str | None = None
    likely_import_modules: list[str] = Field(default_factory=list)
    relevant_interfaces: list[str] = Field(default_factory=list)
    lean_evidence: list[str] = Field(default_factory=list)
    adapter_feasibility: Literal["ready", "plausible", "unsuitable"]
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendation: Literal["direct_adapter_requirement", "generic_requirement", "ignore"]


class RepoResourceDiscoverySubmission(LeanBaseSubmission):
    submission_type: Literal["repo_resource_discovery_result"] = "repo_resource_discovery_result"
    outcome: RepoExplorationOutcome
    candidates: list[RepoResourceCandidate] = Field(default_factory=list)


class RepoLeanProviderDiscoverySubmission(LeanBaseSubmission):
    submission_type: Literal["repo_lean_provider_discovery_result"] = "repo_lean_provider_discovery_result"
    outcome: RepoExplorationOutcome
    candidates: list[RepoLeanProviderCandidate] = Field(default_factory=list)


class RepoMathlibReconSubmission(LeanBaseSubmission):
    submission_type: Literal["repo_mathlib_recon_result"] = "repo_mathlib_recon_result"
    outcome: RepoExplorationOutcome
    created_modules: list[str] = Field(default_factory=list)
    reused_modules: list[str] = Field(default_factory=list)
    created_declarations: list[str] = Field(default_factory=list)
    reused_declarations: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    usage_notes: list[str] = Field(default_factory=list)
