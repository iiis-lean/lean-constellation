"""Repo-level domain models."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.publication import (
    RepoPublicationOverride,
    WorkspacePublicationPolicy,
)


class RepoFormat(StrEnum):
    UNKNOWN = "unknown"
    NATIVE = "native"
    ADAPTER = "adapter"


class ProofAvailability(StrEnum):
    DECLARED = "declared"
    PROVED = "proved"


def proof_availability_satisfies(
    available: ProofAvailability | str,
    required: ProofAvailability | str,
) -> bool:
    order = {
        ProofAvailability.DECLARED: 0,
        ProofAvailability.PROVED: 1,
    }
    return order[ProofAvailability(available)] >= order[ProofAvailability(required)]


class RepoCompletionMode(StrEnum):
    INTERFACE_DECLARED = "interface_declared"
    GRAPH_DECLARED = "graph_declared"
    GRAPH_PROVED = "graph_proved"


def completion_mode_satisfies(
    available: RepoCompletionMode | str,
    required: RepoCompletionMode | str,
) -> bool:
    order = {
        RepoCompletionMode.INTERFACE_DECLARED: 0,
        RepoCompletionMode.GRAPH_DECLARED: 1,
        RepoCompletionMode.GRAPH_PROVED: 2,
    }
    return order[RepoCompletionMode(available)] >= order[RepoCompletionMode(required)]


def proof_availability_for_completion_mode(
    mode: RepoCompletionMode | str,
) -> ProofAvailability:
    if RepoCompletionMode(mode) == RepoCompletionMode.GRAPH_PROVED:
        return ProofAvailability.PROVED
    return ProofAvailability.DECLARED


class RepoPublicationStatus(StrEnum):
    DEVELOPING = "developing"
    STABLE = "stable"


class DocstringProjectionConfig(StrictModel):
    """Workspace policy for the managed declaration docstring projection."""

    include_statement_nl: bool = True
    include_proof_nl: bool = False
    include_sources: bool = False
    include_dependencies: bool = False

    @model_validator(mode="after")
    def _statement_is_required(self) -> "DocstringProjectionConfig":
        if not self.include_statement_nl:
            raise ValueError("managed docstrings must include the NL statement")
        return self

    @classmethod
    def full(cls) -> "DocstringProjectionConfig":
        """Return the explicit publication/diagnostic projection policy."""

        return cls(
            include_statement_nl=True,
            include_proof_nl=True,
            include_sources=True,
            include_dependencies=True,
        )

    def fingerprint(self, *, format_version: int = 1) -> str:
        """Return a stable fingerprint for managed-file freshness checks."""

        payload = {
            "format_version": format_version,
            "policy": self.model_dump(mode="json"),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class RepoModel(StrictModel):
    main_node: str = "Main"
    summary: str | None = None


class RepoFormatState(StrictModel):
    repo_format: RepoFormat = RepoFormat.UNKNOWN
    reason: str | None = None


class RepoConfig(StrictModel):
    completion_mode: RepoCompletionMode = RepoCompletionMode.GRAPH_PROVED
    default_requirement_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    publication: RepoPublicationOverride | None = None


class RepoPublicationState(StrictModel):
    status: RepoPublicationStatus = RepoPublicationStatus.DEVELOPING
    latest_release_id: str | None = None
    stable_at: str | None = None

    @model_validator(mode="after")
    def _stable_timestamp_consistency(self) -> "RepoPublicationState":
        if self.status == RepoPublicationStatus.STABLE and self.stable_at is None:
            self.stable_at = utc_now_iso()
        if self.status == RepoPublicationStatus.DEVELOPING:
            self.stable_at = None
        return self


class WorkspaceConfig(StrictModel):
    default_direct_repo_completion_mode: RepoCompletionMode = RepoCompletionMode.GRAPH_PROVED
    default_requirement_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    requirement_provider_completion_mode_by_proof_availability: dict[
        ProofAvailability, RepoCompletionMode
    ] = Field(
        default_factory=lambda: {
            ProofAvailability.DECLARED: RepoCompletionMode.INTERFACE_DECLARED,
            ProofAvailability.PROVED: RepoCompletionMode.GRAPH_PROVED,
        }
    )
    publication: WorkspacePublicationPolicy = Field(
        default_factory=WorkspacePublicationPolicy
    )
    docstring_projection: DocstringProjectionConfig = Field(
        default_factory=DocstringProjectionConfig
    )

    @model_validator(mode="after")
    def _legal_defaults(self) -> "WorkspaceConfig":
        required_keys = {ProofAvailability.DECLARED, ProofAvailability.PROVED}
        if set(self.requirement_provider_completion_mode_by_proof_availability) != required_keys:
            raise ValueError(
                "requirement_provider_completion_mode_by_proof_availability must define declared and proved"
            )
        return self


class RepoModelView(StrictModel):
    repo_root: str
    main_node: str
    summary: str | None = None
    created: bool = False


class RepoFormatView(StrictModel):
    repo_root: str
    repo_format: RepoFormat
    reason: str | None = None


class RepoConfigView(StrictModel):
    repo_root: str
    config: RepoConfig


class RepoPublicationView(StrictModel):
    repo_root: str
    publication: RepoPublicationState


class RepoCompletionPolicyView(StrictModel):
    repo_root: str
    repo_key: str
    completion_mode: RepoCompletionMode
    default_requirement_proof_availability: ProofAvailability
    summary: str


class RepoStateView(StrictModel):
    repo_root: str
    main_node: str | None = None
    repo_summary: str | None = None
    repo_format: RepoFormat = RepoFormat.UNKNOWN
    publication_status: RepoPublicationStatus = RepoPublicationStatus.DEVELOPING
    latest_release_id: str | None = None
    completion_mode: RepoCompletionMode = RepoCompletionMode.GRAPH_PROVED
    default_requirement_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    provider_ready: bool = False
    readiness_policy: str = "proved_closure"
    preparation_input_exists: bool = False
    open_requirement_count: int = 0
    summary: str | None = None


class WorkspaceRepoSummary(StrictModel):
    repo_key: str
    repo_root: str
    repo_summary: str | None = None
    repo_format: RepoFormat = RepoFormat.UNKNOWN
    publication_status: RepoPublicationStatus = RepoPublicationStatus.DEVELOPING
    latest_release_id: str | None = None
    completion_mode: RepoCompletionMode = RepoCompletionMode.GRAPH_PROVED
    provider_ready: bool = False
    open_requirement_count: int = 0


class WorkspaceCatalogView(StrictModel):
    workspace_root: str
    repos: list[WorkspaceRepoSummary] = Field(default_factory=list)


class WorkspaceCoordinatorView(StrictModel):
    current_repo_root: str
    catalog: WorkspaceCatalogView
    ready_provider_repos: list[WorkspaceRepoSummary] = Field(default_factory=list)
