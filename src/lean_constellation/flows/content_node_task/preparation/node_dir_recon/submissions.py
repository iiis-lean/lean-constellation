"""Submission types for NodeDirDependencyReconAgent."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lean_constellation.flows.common.submissions import LeanBaseSubmission


class NodeDirDependencyReconCompletedSubmission(LeanBaseSubmission):
    submission_type: Literal["node_dir_dependency_recon_completed"] = "node_dir_dependency_recon_completed"
    added_node_deps: list[str] = Field(default_factory=list)
    removed_node_deps: list[str] = Field(default_factory=list)
