"""Repo-level domain models."""

from __future__ import annotations

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
