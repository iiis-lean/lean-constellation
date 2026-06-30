"""Repo-level domain models."""

from __future__ import annotations

from enum import StrEnum
from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel


class RepoFormat(StrEnum):
    UNKNOWN = "unknown"
    NATIVE = "native"
    ADAPTER = "adapter"


class RepoModel(StrictModel):
    main_node: str = "Main"
    summary: str | None = None


class RepoFormatState(StrictModel):
    repo_format: RepoFormat = RepoFormat.UNKNOWN
    reason: str | None = None


class RepoPolicy(StrictModel):
    readiness_policy: str = "proved_closure"
    max_parallel_content_node_tasks: int = 1

    @field_validator("max_parallel_content_node_tasks")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_parallel_content_node_tasks must be >= 1")
        return value


class ProviderReadyState(StrictModel):
    ready: bool = False


class RepoModelView(StrictModel):
    repo_root: str
    main_node: str
    summary: str | None = None
    created: bool = False


class RepoFormatView(StrictModel):
    repo_root: str
    repo_format: RepoFormat
    reason: str | None = None


class RepoPolicyView(StrictModel):
    repo_root: str
    policy: RepoPolicy


class RepoStateView(StrictModel):
    repo_root: str
    main_node: str | None = None
    repo_summary: str | None = None
    repo_format: RepoFormat = RepoFormat.UNKNOWN
    provider_ready: bool = False
    readiness_policy: str = "proved_closure"
    max_parallel_content_node_tasks: int = 1
    preparation_input_exists: bool = False
    open_requirement_count: int = 0
    summary: str | None = None


class WorkspaceRepoSummary(StrictModel):
    repo_key: str
    repo_root: str
    repo_summary: str | None = None
    repo_format: RepoFormat = RepoFormat.UNKNOWN
    provider_ready: bool = False
    open_requirement_count: int = 0


class WorkspaceCatalogView(StrictModel):
    workspace_root: str
    repos: list[WorkspaceRepoSummary] = Field(default_factory=list)


class WorkspaceCoordinatorView(StrictModel):
    current_repo_root: str
    catalog: WorkspaceCatalogView
    ready_provider_repos: list[WorkspaceRepoSummary] = Field(default_factory=list)
