"""Submission types for repo discovery, native preparation, and adapter preparation."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission, LeanDispatchSubmission


class RepoFormatAdapterChoiceSubmission(LeanBaseSubmission):
    submission_type: Literal["repo_format_adapter_choice"] = "repo_format_adapter_choice"
    upstream_github_url: str
    upstream_revision: str | None = None
    upstream_subdir: str | None = None
    adapter_repo_name: str | None = None


class RepoFormatNativeChoiceSubmission(LeanBaseSubmission):
    submission_type: Literal["repo_format_native_choice"] = "repo_format_native_choice"
    native_repo_name: str | None = None
    source_corpus_mode: Literal["prepare", "existing"]


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
