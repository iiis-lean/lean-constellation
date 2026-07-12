"""Native repository release truth and derived read models."""

from __future__ import annotations

import re
from typing import Literal, TypeAlias

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.refs import DeclRef
from lean_constellation.domain.repo import ProofAvailability


_SAFE_RELEASE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

DeclStateValue: TypeAlias = Literal["planned", "specified", "declared", "proof_planned", "proved", "obsolete"]


class RepoRelease(StrictModel):
    release_id: str
    parent_release_id: str | None = None
    node_contract_versions: dict[str, int]
    target_proof_availability: ProofAvailability
    repo_checkpoint_id: str
    summary: str
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("release_id", "repo_checkpoint_id")
    @classmethod
    def _safe_required_key(cls, value: str) -> str:
        normalized = value.strip()
        if not _SAFE_RELEASE_KEY_RE.fullmatch(normalized):
            raise ValueError("release and checkpoint identifiers must be safe non-empty keys")
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

    @model_validator(mode="after")
    def _parent_is_not_self(self) -> "RepoRelease":
        if self.parent_release_id == self.release_id:
            raise ValueError("parent_release_id must not equal release_id")
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
    "RepoRelease",
    "RepoReleaseBaselineView",
    "RepoReleaseListView",
    "RepoReleaseView",
    "ResolvedDeclRefView",
]
