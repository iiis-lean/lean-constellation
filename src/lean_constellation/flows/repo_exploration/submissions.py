"""Terminal submissions produced by repository exploration agents."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

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
    support_summary: str
    risks_or_gaps: list[str] = Field(default_factory=list)
    recommended_handling: Literal[
        "local_resource",
        "provider_requirement",
        "inspect_later",
    ]
    consumer_need: str | None = None
    provider_scope: str | None = None

    @model_validator(mode="after")
    def _canonical_handling_consistency(self):
        if self.recommended_handling == "local_resource" and not self.source_urls:
            raise ValueError("canonical local_resource candidates require a verified source URL")
        if self.recommended_handling == "provider_requirement" and (
            not self.consumer_need
            or not self.consumer_need.strip()
            or not self.provider_scope
            or not self.provider_scope.strip()
        ):
            raise ValueError("canonical provider_requirement candidates require consumer_need and provider_scope")
        return self


class RepoLeanProviderCandidate(StrictModel):
    git_url: str
    resolved_revision: str
    subdir: str | None = None
    package_name: str | None = None
    likely_import_modules: list[str] = Field(default_factory=list)
    lean_toolchain: str | None = None
    has_lakefile: bool
    has_lean_manifest: bool
    has_lean_files: bool
    capability_summary: str
    relevant_declarations: list[str] = Field(default_factory=list)
    lean_evidence: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendation: Literal[
        "direct_adapter_requirement",
        "generic_requirement",
        "inspect_later",
    ]

    @field_validator("resolved_revision")
    @classmethod
    def _resolved_revision_is_immutable(cls, value: str) -> str:
        normalized = value.strip().lower()
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", normalized) is None:
            raise ValueError("canonical provider candidate requires an immutable commit")
        return normalized

    @model_validator(mode="after")
    def _canonical_probe_consistency(self):
        if not self.has_lean_files or not self.lean_evidence:
            raise ValueError("canonical provider candidate requires verified Lean source evidence")
        if self.recommendation == "direct_adapter_requirement" and (
            not self.has_lakefile
            or not self.package_name
            or not self.likely_import_modules
            or not self.relevant_declarations
            or self.gaps
        ):
            raise ValueError("canonical direct Adapter candidate requires complete gap-free probe facts")
        return self


class RepoResourceDiscoverySubmission(LeanBaseSubmission):
    submission_type: Literal["repo_resource_discovery_result"] = "repo_resource_discovery_result"
    outcome: RepoExplorationOutcome
    candidates: list[RepoResourceCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outcome_matches_candidates(self):
        if self.outcome == "no_useful_findings" and self.candidates:
            raise ValueError("no_useful_findings may not include resource candidates")
        if self.outcome == "completed" and not self.candidates:
            raise ValueError("completed resource discovery requires candidates")
        return self


class RepoLeanProviderDiscoverySubmission(LeanBaseSubmission):
    submission_type: Literal["repo_lean_provider_discovery_result"] = "repo_lean_provider_discovery_result"
    outcome: RepoExplorationOutcome
    candidates: list[RepoLeanProviderCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _outcome_matches_candidates(self):
        if self.outcome == "no_useful_findings" and self.candidates:
            raise ValueError("no_useful_findings may not include provider candidates")
        if self.outcome == "completed" and not self.candidates:
            raise ValueError("completed provider discovery requires candidates")
        return self


class RepoMathlibReconSubmission(LeanBaseSubmission):
    submission_type: Literal["repo_mathlib_recon_result"] = "repo_mathlib_recon_result"
    outcome: RepoExplorationOutcome
    created_modules: list[str] = Field(default_factory=list)
    reused_modules: list[str] = Field(default_factory=list)
    created_declarations: list[str] = Field(default_factory=list)
    reused_declarations: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    usage_notes: list[str] = Field(default_factory=list)
