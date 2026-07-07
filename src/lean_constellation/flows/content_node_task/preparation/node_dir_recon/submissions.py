"""Submission types for NodeDirDependencyReconAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission


class NodeDirDependencyReconCompletedSubmission(LeanBaseSubmission):
    submission_type: Literal["node_dir_dependency_recon_completed"] = "node_dir_dependency_recon_completed"
    dependency_change_summary: str | None = None
    checked_boundary_summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_within_visible_boundaries: list[str] = Field(default_factory=list)
