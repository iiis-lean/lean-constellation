"""Host-neutral Git publication policy and receipts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso


class ReleasePolicy(StrEnum):
    MANUAL = "manual"
    ON_COMPLETION = "on_completion"


class PushPolicy(StrEnum):
    MANUAL = "manual"
    ON_RELEASE = "on_release"


class RepoPortability(StrEnum):
    PORTABLE = "portable"
    LOCAL_WORKSPACE = "local_workspace"


class GitCommitIdentity(StrictModel):
    name: str
    email: str

    @field_validator("name", "email")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Git commit identity fields must be non-empty")
        return normalized


class RemoteProfile(StrictModel):
    fetch_url_template: str
    push_url_template: str | None = None
    values: dict[str, str] = Field(default_factory=dict)

    @field_validator("fetch_url_template", "push_url_template")
    @classmethod
    def _non_empty_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("remote URL templates must be non-empty")
        return normalized


class RepoPublicationPolicy(StrictModel):
    release_policy: ReleasePolicy = ReleasePolicy.ON_COMPLETION
    push_policy: PushPolicy = PushPolicy.MANUAL
    remote_name: str = "origin"
    canonical_fetch_url: str | None = None
    canonical_push_url: str | None = None
    initial_branch: str = "main"
    commit_identity: GitCommitIdentity | None = None
    include_lake_manifest: bool = True
    post_release_checkpoint: bool = True

    @field_validator(
        "remote_name",
        "canonical_fetch_url",
        "canonical_push_url",
        "initial_branch",
    )
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("publication policy strings must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _push_requires_remote(self) -> "RepoPublicationPolicy":
        if self.push_policy == PushPolicy.ON_RELEASE and self.canonical_fetch_url is None:
            raise ValueError("on_release push requires a canonical fetch URL")
        return self


class RepoPublicationOverride(StrictModel):
    release_policy: ReleasePolicy | None = None
    push_policy: PushPolicy | None = None
    remote_name: str | None = None
    canonical_fetch_url: str | None = None
    canonical_push_url: str | None = None
    initial_branch: str | None = None
    commit_identity: GitCommitIdentity | None = None
    include_lake_manifest: bool | None = None
    post_release_checkpoint: bool | None = None


class RepoPublicationBadge(StrictModel):
    label: str
    message: str
    color: str = "blue"
    link: str | None = None

    @field_validator("label", "message", "color", "link")
    @classmethod
    def _strip_badge_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("publication badge fields must be non-empty")
        return normalized


class RepoPublicationPresentation(StrictModel):
    """Human-facing publication metadata, separate from runtime summaries."""

    schema_version: Literal[1] = 1
    title: str | None = None
    description: str | None = None
    topics: list[str] = Field(default_factory=list)
    badges: list[RepoPublicationBadge] = Field(default_factory=list)
    about_markdown: str | None = None
    citation_markdown: str | None = None
    licensing_markdown: str | None = None

    @field_validator(
        "title",
        "description",
        "about_markdown",
        "citation_markdown",
        "licensing_markdown",
    )
    @classmethod
    def _strip_presentation_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("topics")
    @classmethod
    def _normalize_topics(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            topic = value.strip().lower()
            if not topic:
                continue
            if topic not in normalized:
                normalized.append(topic)
        return normalized


class WorkspacePublicationPolicy(StrictModel):
    repo_defaults: RepoPublicationPolicy = Field(default_factory=RepoPublicationPolicy)
    repo_remote_profile: str | None = None
    repo_remote_name_template: str = "{repo_key}"
    superproject_remote_profile: str | None = None
    superproject_remote_name: str | None = None
    remote_profiles: dict[str, RemoteProfile] = Field(default_factory=dict)


class EffectiveRepoPublicationPolicy(StrictModel):
    repo_key: str
    policy: RepoPublicationPolicy
    source_by_field: dict[str, str] = Field(default_factory=dict)
    portability: RepoPortability
    summary: str


class RepoRemoteBinding(StrictModel):
    repo_key: str
    remote_name: str
    fetch_url: str
    push_url: str | None = None
    configured: bool = False
    summary: str


class RepoPublicationReceipt(StrictModel):
    repo_key: str
    release_id: str
    commit: str
    remote_name: str | None = None
    remote_url: str | None = None
    status: str
    verified_at: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    summary: str


__all__ = [
    "EffectiveRepoPublicationPolicy",
    "GitCommitIdentity",
    "PushPolicy",
    "ReleasePolicy",
    "RemoteProfile",
    "RepoPortability",
    "RepoPublicationBadge",
    "RepoPublicationOverride",
    "RepoPublicationPolicy",
    "RepoPublicationPresentation",
    "RepoPublicationReceipt",
    "RepoRemoteBinding",
    "WorkspacePublicationPolicy",
]
