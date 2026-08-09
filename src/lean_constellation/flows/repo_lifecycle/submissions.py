"""Submission types for repo discovery, native preparation, and adapter preparation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.preparation import VerifiedAdapterRouteReceipt
from lean_constellation.flows.common.submissions import LeanBaseSubmission, LeanDispatchSubmission


class RepoFormatAdapterChoiceSubmission(LeanBaseSubmission):
    submission_type: Literal["repo_format_adapter_choice"] = "repo_format_adapter_choice"
    git_url: str
    revision: str
    subdir: str | None = None
    evidence_summary: str
    known_risks: list[str] = Field(default_factory=list)
    verified_route: VerifiedAdapterRouteReceipt

    @model_validator(mode="after")
    def _verified_route_matches_choice(self) -> "RepoFormatAdapterChoiceSubmission":
        receipt = self.verified_route
        if (self.git_url, self.revision, self.subdir) != (
            receipt.git_url,
            receipt.revision,
            receipt.subdir,
        ):
            raise ValueError("verified adapter route does not match the submitted canonical choice")
        return self


class RepoFormatNativeChoiceSubmission(LeanBaseSubmission):
    submission_type: Literal["repo_format_native_choice"] = "repo_format_native_choice"
    searched_targets: list[str]

    @field_validator("searched_targets")
    @classmethod
    def _searched_targets_non_empty(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if not normalized:
            raise ValueError("searched_targets must contain at least one non-empty target")
        return list(dict.fromkeys(normalized))


class SourceCorpusPreparedSubmission(LeanBaseSubmission):
    submission_type: Literal["source_corpus_prepared"] = "source_corpus_prepared"
    relpath: str = ".lean_constellation/source"
    entry_path: str
    overview: str
    preparation_summary: str


class SourceCorpusBlockedSubmission(LeanBaseSubmission):
    submission_type: Literal["source_corpus_blocked"] = "source_corpus_blocked"
    reason: str
    attempted_targets: list[str] = Field(default_factory=list)
    missing_materials: list[str] = Field(default_factory=list)
    suggested_next_action: str | None = None


class SourceIndexBuilderRoundSubmission(LeanBaseSubmission):
    submission_type: Literal["source_index_builder_round"] = "source_index_builder_round"
    validation_summary: str | None = None


class SourceIndexReviewerRoundSubmission(LeanBaseSubmission):
    submission_type: Literal["source_index_reviewer_round"] = "source_index_reviewer_round"
    approved: bool
    feedback: str | None = None


class RootInterfacePrepareReadySubmission(LeanBaseSubmission):
    submission_type: Literal["root_interface_prepare_ready"] = "root_interface_prepare_ready"


class NativeCoordinatorHandoffSubmission(LeanDispatchSubmission):
    submission_type: Literal["native_coordinator_handoff"] = "native_coordinator_handoff"
    handoff_summary: str


class AdapterCatalogReadySubmission(LeanBaseSubmission):
    submission_type: Literal["adapter_catalog_ready"] = "adapter_catalog_ready"


class AdapterCatalogBlockedSubmission(LeanBaseSubmission):
    submission_type: Literal["adapter_catalog_blocked"] = "adapter_catalog_blocked"
    reason: str
    missing_interfaces: list[str] = Field(default_factory=list)
    evidence_summary: str | None = None
    suggested_next_action: str | None = None
