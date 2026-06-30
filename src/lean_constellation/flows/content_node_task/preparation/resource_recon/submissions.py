"""Submission types for ResourceReconAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission, LeanDispatchSubmission


class ResourceReconCompletedSubmission(LeanBaseSubmission):
    submission_type: Literal["resource_recon_completed"] = "resource_recon_completed"
    added_owned_refs: list[str] = Field(default_factory=list)
    added_context_refs: list[str] = Field(default_factory=list)


class ResourceReconBlockedSubmission(LeanBaseSubmission):
    submission_type: Literal["resource_recon_blocked"] = "resource_recon_blocked"
    reason: str
    missing_targets: list[str] = Field(default_factory=list)


class ResourceReconRequestResourceSubmission(LeanDispatchSubmission):
    submission_type: Literal["resource_recon_request_resource"] = "resource_recon_request_resource"
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    context_summary: str | None = None
