"""Submission types produced by ResourceCuratorAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission


class ResourceDuplicateSubmission(LeanBaseSubmission):
    submission_type: Literal["resource_duplicate"] = "resource_duplicate"
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    existing_kind: Literal["resource", "source"]
    duplicate_reason: str
    existing_resource_key: str | None = None
    existing_source_path: str | None = None
    preview: str | None = None


class LocalResourceCreatedSubmission(LeanBaseSubmission):
    submission_type: Literal["local_resource_created"] = "local_resource_created"
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    draft_id: str
    resource_key: str


class ExternalRepoRequiredSubmission(LeanBaseSubmission):
    submission_type: Literal["external_repo_required"] = "external_repo_required"
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    reason: str
    source_description: str
    suggested_repo_name: str | None = None
    required_interfaces_hint: str | None = None


class ResourceRejectedSubmission(LeanBaseSubmission):
    submission_type: Literal["resource_rejected"] = "resource_rejected"
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    reason: str
    details: list[str] = Field(default_factory=list)
