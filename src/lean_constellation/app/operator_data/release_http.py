"""Strict route declarations for Release and LC-only Checkpoint operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from lean_constellation.app.operator_data.common import OperatorInputModel
from lean_constellation.app.operator_data.release import (
    CheckpointCreateInput,
    CheckpointIdInput,
    CheckpointKindInput,
    CheckpointListInput,
    CheckpointRestoreInput,
    ReleaseCandidateInput,
    ReleaseIdInput,
    ReleaseRestoreInput,
)


@dataclass(frozen=True, slots=True)
class ReleaseHttpRoute:
    method: Literal["GET", "POST"]
    path: str
    api_method: str
    input_model: type[OperatorInputModel] | None = None


RELEASE_HTTP_ROUTES = (
    ReleaseHttpRoute("GET", "/admin/operator/repos/{repo_key}/ready", "get_repo_ready_view"),
    ReleaseHttpRoute("GET", "/admin/operator/repos/{repo_key}/audit", "run_full_audit"),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/releases/preview", "preview_repo_release", ReleaseCandidateInput),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/releases/publish", "publish_repo_release", ReleaseCandidateInput),
    ReleaseHttpRoute("GET", "/admin/operator/repos/{repo_key}/releases", "list_repo_releases"),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/releases/get", "get_repo_release", ReleaseIdInput),
    ReleaseHttpRoute("GET", "/admin/operator/repos/{repo_key}/releases/latest", "get_latest_repo_release"),
    ReleaseHttpRoute("GET", "/admin/operator/repos/{repo_key}/releases/audit", "audit_repo_release_storage"),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/releases/restore", "restore_repo_release", ReleaseRestoreInput),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/checkpoints/gate", "check_checkpoint_gate", CheckpointKindInput),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/checkpoints", "create_checkpoint", CheckpointCreateInput),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/checkpoints/list", "list_checkpoints", CheckpointListInput),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/checkpoints/validate", "validate_checkpoint", CheckpointIdInput),
    ReleaseHttpRoute("POST", "/admin/operator/repos/{repo_key}/checkpoints/restore", "restore_checkpoint", CheckpointRestoreInput),
)


__all__ = ["RELEASE_HTTP_ROUTES", "ReleaseHttpRoute"]
