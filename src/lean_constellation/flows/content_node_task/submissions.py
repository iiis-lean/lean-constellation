"""Submission types produced by ContentPlanAgent."""

from __future__ import annotations

from typing import Literal

from lean_constellation.flows.common.submissions import LeanBaseSubmission, LeanDispatchSubmission


class ContentPreparationDispatchSubmission(LeanDispatchSubmission):
    submission_type: Literal["content_preparation_dispatch"] = "content_preparation_dispatch"
    recon_kind: Literal["node_dir_dependency", "mathlib", "resource"]
    objective: str | None = None
    context_summary: str | None = None


class ContentResourceRequestSubmission(LeanDispatchSubmission):
    submission_type: Literal["content_resource_request"] = "content_resource_request"
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    requested_use: Literal["supporting_material", "formal_dependency", "unknown"]
    consumer_need: str
    context_summary: str | None = None


class ContentNodeReadySubmission(LeanBaseSubmission):
    submission_type: Literal["content_node_ready"] = "content_node_ready"


class ContentNodeBlockedSubmission(LeanBaseSubmission):
    submission_type: Literal["content_node_blocked"] = "content_node_blocked"
    reason: str


class ContentNodeFailedSubmission(LeanBaseSubmission):
    submission_type: Literal["content_node_failed"] = "content_node_failed"
    reason: str
