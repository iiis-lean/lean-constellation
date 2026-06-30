"""Submission types for MathlibReconAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission


class MathlibReconCompletedSubmission(LeanBaseSubmission):
    submission_type: Literal["mathlib_recon_completed"] = "mathlib_recon_completed"
    added_modules: list[str] = Field(default_factory=list)
    added_decls: list[str] = Field(default_factory=list)
