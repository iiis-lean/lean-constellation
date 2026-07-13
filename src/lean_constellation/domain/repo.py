"""Repo-level domain models."""

from __future__ import annotations

from enum import StrEnum
from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso


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


class RepoWorkMode(StrEnum):
    DECLARED_INTERFACE = "declared_interface"
    DECLARED_FULL_GRAPH = "declared_full_graph"
    PROVED_FULL_GRAPH = "proved_full_graph"


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
    target_proof_availability: ProofAvailability = ProofAvailability.PROVED
    work_mode: RepoWorkMode = RepoWorkMode.PROVED_FULL_GRAPH
    default_requirement_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    max_parallel_content_node_tasks: int = 1

    @field_validator("max_parallel_content_node_tasks")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_parallel_content_node_tasks must be >= 1")
        return value

    @model_validator(mode="after")
    def _legal_work_mode_for_target(self) -> "RepoConfig":
        legal = {
            ProofAvailability.DECLARED: {
                RepoWorkMode.DECLARED_INTERFACE,
                RepoWorkMode.DECLARED_FULL_GRAPH,
            },
            ProofAvailability.PROVED: {RepoWorkMode.PROVED_FULL_GRAPH},
        }
        if self.work_mode not in legal[self.target_proof_availability]:
            raise ValueError(
                "work_mode is not compatible with target_proof_availability: "
                f"{self.work_mode.value} for {self.target_proof_availability.value}"
            )
        return self


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
    default_direct_repo_proof_availability: ProofAvailability = ProofAvailability.PROVED
    default_direct_repo_work_mode: RepoWorkMode = RepoWorkMode.PROVED_FULL_GRAPH
    default_requirement_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    requirement_provider_work_mode_by_proof_availability: dict[ProofAvailability, RepoWorkMode] = Field(
        default_factory=lambda: {
            ProofAvailability.DECLARED: RepoWorkMode.DECLARED_INTERFACE,
            ProofAvailability.PROVED: RepoWorkMode.PROVED_FULL_GRAPH,
        }
    )

    @model_validator(mode="after")
    def _legal_defaults(self) -> "WorkspaceConfig":
        RepoConfig(
            target_proof_availability=self.default_direct_repo_proof_availability,
            work_mode=self.default_direct_repo_work_mode,
            default_requirement_proof_availability=self.default_requirement_proof_availability,
        )
        required_keys = {ProofAvailability.DECLARED, ProofAvailability.PROVED}
        if set(self.requirement_provider_work_mode_by_proof_availability) != required_keys:
            raise ValueError("requirement_provider_work_mode_by_proof_availability must define declared and proved")
        for availability, mode in self.requirement_provider_work_mode_by_proof_availability.items():
            RepoConfig(
                target_proof_availability=availability,
                work_mode=mode,
                default_requirement_proof_availability=self.default_requirement_proof_availability,
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


class RepoWorkConfigView(StrictModel):
    repo_root: str
    repo_key: str
    target_proof_availability: ProofAvailability
    work_mode: RepoWorkMode
    summary: str


class RepoStateView(StrictModel):
    repo_root: str
    main_node: str | None = None
    repo_summary: str | None = None
    repo_format: RepoFormat = RepoFormat.UNKNOWN
    publication_status: RepoPublicationStatus = RepoPublicationStatus.DEVELOPING
    latest_release_id: str | None = None
    target_proof_availability: ProofAvailability = ProofAvailability.PROVED
    work_mode: RepoWorkMode = RepoWorkMode.PROVED_FULL_GRAPH
    default_requirement_proof_availability: ProofAvailability = ProofAvailability.DECLARED
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
    publication_status: RepoPublicationStatus = RepoPublicationStatus.DEVELOPING
    latest_release_id: str | None = None
    target_proof_availability: ProofAvailability = ProofAvailability.PROVED
    work_mode: RepoWorkMode = RepoWorkMode.PROVED_FULL_GRAPH
    provider_ready: bool = False
    open_requirement_count: int = 0


class WorkspaceCatalogView(StrictModel):
    workspace_root: str
    repos: list[WorkspaceRepoSummary] = Field(default_factory=list)


class WorkspaceCoordinatorView(StrictModel):
    current_repo_root: str
    catalog: WorkspaceCatalogView
    ready_provider_repos: list[WorkspaceRepoSummary] = Field(default_factory=list)
