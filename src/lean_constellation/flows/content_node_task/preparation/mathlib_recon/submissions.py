"""Submission types for MathlibReconAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission


class MathlibReconCompletedSubmission(LeanBaseSubmission):
    submission_type: Literal["mathlib_recon_completed"] = "mathlib_recon_completed"
    index_update_summary: str | None = None
    node_mathlib_hint_summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_in_mathlib: list[str] = Field(default_factory=list)
