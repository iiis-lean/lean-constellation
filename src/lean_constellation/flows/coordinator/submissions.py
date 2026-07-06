"""Submission types produced by NativeRepoCoordinatorAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.flows.common.submissions import LeanBaseSubmission, LeanDispatchSubmission


class CoordinatorContentTasksSubmission(LeanDispatchSubmission):
    submission_type: Literal["coordinator_content_tasks"] = "coordinator_content_tasks"
    node_paths: list[str]
    task_mode: str = "run"


class CoordinatorResourceRequestSubmission(LeanDispatchSubmission):
    submission_type: Literal["coordinator_resource_request"] = "coordinator_resource_request"
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    context_summary: str | None = None


class CoordinatorRepoRequirementSubmission(LeanBaseSubmission):
    submission_type: Literal["coordinator_repo_requirement"] = "coordinator_repo_requirement"
    requirement_name: str
    target_repo: str
    required_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    source_description: str | None = None
    reason: str | None = None
    interfaces: list[dict[str, str]] = Field(default_factory=list)


class CoordinatorRepoReadySubmission(LeanBaseSubmission):
    submission_type: Literal["coordinator_repo_ready"] = "coordinator_repo_ready"
