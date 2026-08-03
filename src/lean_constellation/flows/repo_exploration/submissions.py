"""Terminal submissions produced by repository exploration agents."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

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
    recommended_handling: Literal[
        "local_resource",
        "provider_requirement",
        "inspect_later",
        "ignore",
    ]
    classification_reason: str
    consumer_need: str
    suggested_repo_name: str | None = None
    provider_scope: str | None = None
    required_interfaces_hint: str | None = None
    existing_lean_repo_signal: str | None = None
    lean_provider_search_recommended: bool = False


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

    @model_validator(mode="after")
    def _evidence_consistency(self):
        if self.adapter_feasibility == "unsuitable" and self.recommendation != "ignore":
            raise ValueError("unsuitable provider candidates must be ignored")
        if self.recommendation == "direct_adapter_requirement" and self.adapter_feasibility != "ready":
            raise ValueError("direct adapter candidates must be ready")
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
        if self.recommendation != "direct_adapter_requirement":
            return self
        normalized_url = self.git_url.strip().lower().removesuffix(".git").rstrip("/")
        if normalized_url in {
            "leanprover-community/mathlib",
            "leanprover-community/mathlib4",
            "https://github.com/leanprover-community/mathlib",
            "https://github.com/leanprover-community/mathlib4",
        }:
            raise ValueError("Mathlib is not a direct adapter/provider candidate")
        return self


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
