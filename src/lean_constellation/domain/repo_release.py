"""Native repository release truth and derived read models."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import RepoCompletionMode


_SAFE_RELEASE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

DeclStateValue: TypeAlias = Literal["planned", "specified", "declared", "proof_planned", "proved", "obsolete"]


class RepoReleaseKind(StrEnum):
    SEMANTIC = "semantic"
    DEPENDENCY_MAINTENANCE = "dependency_maintenance"


class RepoReleaseValidationProfile(StrEnum):
    SEMANTIC_FULL = "semantic_full"
    DEPENDENCY_MINIMAL = "dependency_minimal"
    DEPENDENCY_PLUS_POLICY = "dependency_plus_policy"


class RepoDependencyChangeKind(StrEnum):
    LOCATOR_REBIND = "locator_rebind"
    PROVIDER_PIN_UPGRADE = "provider_pin_upgrade"


class RepoDependencyReleaseChange(StrictModel):
    kind: RepoDependencyChangeKind
    provider_repo_key: str
    previous_release_id: str | None = None
    release_id: str
    previous_commit: str | None = None
    commit: str
    previous_git_url: str | None = None
    git_url: str

    @field_validator("provider_repo_key", "release_id")
    @classmethod
    def _safe_required_key(cls, value: str) -> str:
        normalized = value.strip()
        if not _SAFE_RELEASE_KEY_RE.fullmatch(normalized):
            raise ValueError("dependency release identifiers must be safe non-empty keys")
        return normalized

    @field_validator("previous_release_id")
    @classmethod
    def _safe_optional_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _SAFE_RELEASE_KEY_RE.fullmatch(normalized):
            raise ValueError("previous_release_id must be a safe non-empty key")
        return normalized

    @field_validator("previous_commit", "commit")
    @classmethod
    def _valid_commit(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _GIT_OBJECT_ID_RE.fullmatch(normalized):
            raise ValueError("dependency commits must be full lowercase Git object ids")
        return normalized

    @field_validator("previous_git_url", "git_url")
    @classmethod
    def _non_empty_git_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("dependency Git URLs must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _change_shape_matches_kind(self) -> "RepoDependencyReleaseChange":
        if self.kind == RepoDependencyChangeKind.LOCATOR_REBIND:
            if self.previous_release_id != self.release_id:
                raise ValueError("locator_rebind must preserve the provider release id")
            if self.previous_commit != self.commit:
                raise ValueError("locator_rebind must preserve the provider commit")
            if self.previous_git_url == self.git_url:
                raise ValueError("locator_rebind must change the provider Git URL")
        elif self.previous_commit == self.commit:
            raise ValueError("provider_pin_upgrade must change the provider commit")
        return self


class RepoRelease(StrictModel):
    schema_version: int = 2
    release_id: str
    parent_release_id: str | None = None
    release_kind: RepoReleaseKind = RepoReleaseKind.SEMANTIC
    validation_profile: RepoReleaseValidationProfile = RepoReleaseValidationProfile.SEMANTIC_FULL
    node_contract_versions: dict[str, int]
    completion_mode: RepoCompletionMode
    semantic_manifest_digest: str
    dependency_lock_digest: str
    dependency_change: RepoDependencyReleaseChange | None = None
    summary: str
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("release_id")
    @classmethod
    def _safe_required_key(cls, value: str) -> str:
        normalized = value.strip()
        if not _SAFE_RELEASE_KEY_RE.fullmatch(normalized):
            raise ValueError("release identifiers must be safe non-empty keys")
        return normalized

    @field_validator("parent_release_id")
    @classmethod
    def _safe_optional_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _SAFE_RELEASE_KEY_RE.fullmatch(normalized):
            raise ValueError("parent_release_id must be a safe non-empty key")
        return normalized

    @field_validator("node_contract_versions")
    @classmethod
    def _valid_node_contract_versions(cls, value: dict[str, int]) -> dict[str, int]:
        if not value:
            raise ValueError("node_contract_versions must be non-empty")
        for node_id, version in value.items():
            if not _SAFE_RELEASE_KEY_RE.fullmatch(node_id):
                raise ValueError(f"node_contract_versions contains an unsafe node id: {node_id}")
            if version < 1:
                raise ValueError("node contract versions must be >= 1")
        return value

    @field_validator("summary")
    @classmethod
    def _summary_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("release summary must be non-empty")
        return normalized

    @field_validator("semantic_manifest_digest", "dependency_lock_digest")
    @classmethod
    def _valid_digest(cls, value: str) -> str:
        normalized = value.strip()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("release digests must be lowercase SHA-256 hex")
        return normalized

    @model_validator(mode="after")
    def _valid_release_shape(self) -> "RepoRelease":
        if self.parent_release_id == self.release_id:
            raise ValueError("parent_release_id must not equal release_id")
        if self.release_kind == RepoReleaseKind.SEMANTIC:
            if self.validation_profile != RepoReleaseValidationProfile.SEMANTIC_FULL:
                raise ValueError("semantic releases require the semantic_full validation profile")
            if self.dependency_change is not None:
                raise ValueError("semantic releases must not carry a dependency maintenance change")
        else:
            if self.validation_profile == RepoReleaseValidationProfile.SEMANTIC_FULL:
                raise ValueError(
                    "dependency maintenance releases require a dependency validation profile"
                )
            if self.dependency_change is None:
                raise ValueError("dependency maintenance releases require dependency_change")
        return self


class RepoReleaseView(StrictModel):
    repo_root: str
    release: RepoRelease
    summary: str


class RepoReleaseListView(StrictModel):
    repo_root: str
    releases: list[RepoReleaseView] = Field(default_factory=list)
    summary: str


class ReleasedDeclProtectionView(StrictModel):
    node_id: str
    node_path: str
    decl_name: str
    released_state: DeclStateValue
    first_release_id: str
    last_release_id: str
    summary: str


class RepoReleaseBaselineView(StrictModel):
    release_id: str
    lineage_release_ids: list[str] = Field(default_factory=list)
    released_node_contract_versions: dict[str, int] = Field(default_factory=dict)
    protected_decl_views: list[ReleasedDeclProtectionView] = Field(default_factory=list)
    protected_node_ids: list[str] = Field(default_factory=list)
    protected_scope_paths: list[str] = Field(default_factory=list)
    summary: str


class DeclReleaseStatusView(StrictModel):
    current_state: DeclStateValue
    released_state: DeclStateValue | None = None
    release_protected: bool = False
    summary: str


class ResolvedDeclRefView(StrictModel):
    anchor: DeclRef
    resolved_revision: int | None = None
    compatible: bool
    current_state: DeclStateValue | None = None
    reason: str | None = None


__all__ = [
    "DeclReleaseStatusView",
    "DeclStateValue",
    "ReleasedDeclProtectionView",
    "RepoDependencyChangeKind",
    "RepoDependencyReleaseChange",
    "RepoRelease",
    "RepoReleaseBaselineView",
    "RepoReleaseKind",
    "RepoReleaseListView",
    "RepoReleaseValidationProfile",
    "RepoReleaseView",
    "ResolvedDeclRefView",
]
