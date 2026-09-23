"""Submissions for Restructure AgentSteps."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission


class RestructureContentSubmission(LeanBaseSubmission):
    submission_type: Literal["restructure_content"] = "restructure_content"
    outcome: Literal["declared", "proved", "blocked"]
    stage: Literal["declared", "proved"]
    changed_decl_names: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class RestructureRepoPlanSubmission(LeanBaseSubmission):
    submission_type: Literal["restructure_repo_plan"] = "restructure_repo_plan"
    outcome: Literal["planned", "blocked"]
    plan_version: int | None = None
    issues: list[str] = Field(default_factory=list)


class RestructureRepoRepairSubmission(LeanBaseSubmission):
    submission_type: Literal["restructure_repo_repair"] = "restructure_repo_repair"
    outcome: Literal["repaired", "blocked"]
    issues: list[str] = Field(default_factory=list)


class RestructureReviewSubmission(LeanBaseSubmission):
    submission_type: Literal["restructure_review"] = "restructure_review"
    outcome: Literal["passed", "needs_content_fix", "needs_replan", "blocked"]
    artifact_id: str
    findings: list[str] = Field(default_factory=list)
