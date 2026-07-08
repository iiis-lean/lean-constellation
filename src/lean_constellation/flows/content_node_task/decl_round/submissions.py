"""Submission types for content decl rounds and decl stage Agents."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission, LeanDispatchSubmission
from lean_constellation.services.decl_graph.models import DeclReviewMarkRecord


class DeclRoundDispatchSubmission(LeanDispatchSubmission):
    submission_type: Literal["decl_round_dispatch"] = "decl_round_dispatch"
    strategy_id: str
    round_id: str
    round_index: int | None = None


class DeclStageWorkerCompletedSubmission(LeanBaseSubmission):
    submission_type: Literal["decl_stage_worker_completed"] = "decl_stage_worker_completed"
    stage: str
    round_id: str
    completed_decl_names: list[str] = Field(default_factory=list)
    changed_decl_names: list[str] = Field(default_factory=list)
    notes: str | None = None


class DeclStageWorkerBlockedSubmission(LeanBaseSubmission):
    submission_type: Literal["decl_stage_worker_blocked"] = "decl_stage_worker_blocked"
    stage: str
    round_id: str
    reason: str
    affected_decl_names: list[str] = Field(default_factory=list)
    checked_context_summary: str | None = None
    blocked_needs: list[str] = Field(default_factory=list)


class DeclStageReviewSubmittedSubmission(LeanBaseSubmission):
    submission_type: Literal["decl_stage_review_submitted"] = "decl_stage_review_submitted"
    stage: str
    round_id: str
    accepted: bool
    retry_required: bool
    reviewed_decl_names: list[str] = Field(default_factory=list)
    failed_decl_names: list[str] = Field(default_factory=list)
    missing_decl_names: list[str] = Field(default_factory=list)
    feedback: list[DeclReviewMarkRecord] = Field(default_factory=list)
