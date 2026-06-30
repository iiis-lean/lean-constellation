"""Submission types for content decl rounds and decl stage Agents."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission, LeanDispatchSubmission


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


class DeclStageWorkerBlockedSubmission(LeanBaseSubmission):
    submission_type: Literal["decl_stage_worker_blocked"] = "decl_stage_worker_blocked"
    stage: str
    round_id: str
    reason: str
    affected_decl_names: list[str] = Field(default_factory=list)


class DeclStageReviewSubmittedSubmission(LeanBaseSubmission):
    submission_type: Literal["decl_stage_review_submitted"] = "decl_stage_review_submitted"
    stage: str
    round_id: str
    accepted: bool
    retry_required: bool
