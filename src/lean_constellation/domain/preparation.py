"""Repo preparation and dependency requirement models."""

from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath
import re
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.repo import ProofAvailability, RepoCompletionMode, RepoFormat


class SourceCorpusMode(StrEnum):
    EXISTING = "existing"
    PREPARE = "prepare"
    NONE = "none"


class SourceMaterialInput(StrictModel):
    """Exact external or supplied material boundary authorized for Source preparation."""

    target: str
    included_scope: str
    role: Literal["primary_source", "formal_target", "solution", "proof_reference", "asset"]

    @field_validator("target", "included_scope", mode="before")
    @classmethod
    def _strip_required_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("target", "included_scope")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("source material target and included_scope must be non-empty")
        return value


class RepoDependencyRequirementStatus(StrEnum):
    OPEN = "open"
    SATISFIED = "satisfied"
    HANDLED = "handled"
    OBSOLETE = "obsolete"


class RepoDependencySatisfactionMode(StrEnum):
    REPO_READY = "repo_ready"
    INTERFACES_READY = "interfaces_ready"


class RepoRequirementRef(StrictModel):
    consumer_repo: str
    requirement_name: str


class AutoProviderRoute(StrictModel):
    kind: Literal["auto"] = "auto"


class AdapterProviderRoute(StrictModel):
    kind: Literal["adapter"] = "adapter"
    git_url: str
    revision: str | None = None
    subdir: str | None = None
    package_name: str | None = None
    likely_import_module: str | None = None
    evidence_summary: str
    known_risks: list[str] = Field(default_factory=list)

    @field_validator(
        "git_url",
        "package_name",
        "likely_import_module",
        "evidence_summary",
        mode="before",
    )
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("git_url", "evidence_summary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value:
            raise ValueError("value must be non-empty")
        return value

    @field_validator("revision", mode="before")
    @classmethod
    def _immutable_revision(cls, value: object) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        if not value:
            return None
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", value):
            raise ValueError("revision must be an immutable 40- or 64-character Git commit identity")
        return value.lower()

    @field_validator("subdir", mode="before")
    @classmethod
    def _safe_subdir(cls, value: object) -> object:
        if value is None:
            return None
        normalized = str(value).strip().strip("/")
        if not normalized:
            return None
        path = PurePosixPath(normalized)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("subdir must be a safe repository-relative path")
        return path.as_posix()

    @field_validator("package_name", "likely_import_module")
    @classmethod
    def _empty_optional_text(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("known_risks")
    @classmethod
    def _normalize_risks(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            if item and item not in seen:
                seen.add(item)
                normalized.append(item)
        return normalized


class RejectedUpstreamCandidate(StrictModel):
    git_url: str | None = None
    name: str | None = None
    reason: str
    evidence_summary: str | None = None

    @field_validator("git_url", "name", "reason", "evidence_summary", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _validate_candidate(self) -> RejectedUpstreamCandidate:
        if not self.git_url and not self.name:
            raise ValueError("rejected candidate requires git_url or name")
        if not self.reason:
            raise ValueError("rejected candidate reason must be non-empty")
        return self


class NativeProviderRoute(StrictModel):
    kind: Literal["native"] = "native"
    evidence_summary: str
    searched_targets: list[str]
    rejected_candidates: list[RejectedUpstreamCandidate] = Field(default_factory=list)

    @field_validator("evidence_summary", mode="before")
    @classmethod
    def _strip_evidence(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("evidence_summary")
    @classmethod
    def _required_evidence(cls, value: str) -> str:
        if not value:
            raise ValueError("evidence_summary must be non-empty")
        return value

    @field_validator("searched_targets")
    @classmethod
    def _normalize_targets(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            item = value.strip()
            key = item.casefold()
            if item and key not in seen:
                seen.add(key)
                normalized.append(item)
        if not normalized:
            raise ValueError("searched_targets must contain at least one non-empty target")
        return normalized


ProviderRoute = Annotated[
    AutoProviderRoute | AdapterProviderRoute | NativeProviderRoute,
    Field(discriminator="kind"),
]


class VerifiedAdapterRouteReceipt(StrictModel):
    git_url: str
    revision: str
    subdir: str | None = None
    package_name: str
    likely_import_module: str
    lean_toolchain: str
    mathlib_source: str | None = None
    mathlib_revision: str | None = None
    expected_lean_toolchain: str
    expected_mathlib_revision: str | None = None
    revision_resolution: Literal["explicit", "latest", "history"]
    candidates_checked: list[str] = Field(default_factory=list)
    evidence_summary: str

    @field_validator("revision")
    @classmethod
    def _verified_immutable_revision(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", normalized):
            raise ValueError("verified revision must be an immutable 40- or 64-character Git commit identity")
        return normalized

    @field_validator(
        "git_url",
        "package_name",
        "likely_import_module",
        "lean_toolchain",
        "expected_lean_toolchain",
        "evidence_summary",
    )
    @classmethod
    def _verified_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("verified adapter route fields must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _verified_candidate_contains_revision(self) -> "VerifiedAdapterRouteReceipt":
        checked = [candidate.strip().lower() for candidate in self.candidates_checked if candidate.strip()]
        if self.revision not in checked:
            raise ValueError("verified adapter route candidates_checked must include the selected revision")
        self.candidates_checked = checked
        return self


class RepoDependencyRequirement(StrictModel):
    name: str
    target_repo: str
    provider_route: ProviderRoute
    required_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    source_description: str | None = None
    reason: str | None = None
    interfaces: list[DeclInterface] = Field(default_factory=list)
    status: RepoDependencyRequirementStatus = RepoDependencyRequirementStatus.OPEN
    satisfaction_mode: RepoDependencySatisfactionMode = RepoDependencySatisfactionMode.REPO_READY
    provider_repo: str | None = None
    provider_release_id: str | None = None
    provider_commit: str | None = None
    provider_git_url: str | None = None
    provider_request_submitted_at: str | None = None
    provider_result_observed_at: str | None = None
    note: str | None = None


class RepoPreparationInput(StrictModel):
    goal: str
    source_corpus_mode: SourceCorpusMode
    source_corpus_relpath: str | None = ".lean_constellation/source"
    source_description: str | None = None
    source_material_inputs: list[SourceMaterialInput] = Field(default_factory=list)
    interface_inputs: list[DeclInterface] = Field(default_factory=list)
    allow_interface_supplement: bool = True
    requirement_refs: list[RepoRequirementRef] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("goal")
    @classmethod
    def _goal_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must be non-empty")
        return value.strip()

    @field_validator("source_material_inputs")
    @classmethod
    def _unique_source_material_inputs(cls, values: list[SourceMaterialInput]) -> list[SourceMaterialInput]:
        seen: set[tuple[str, str, str]] = set()
        for value in values:
            key = (value.target, value.included_scope, value.role)
            if key in seen:
                raise ValueError("source_material_inputs must not contain duplicate exact boundaries")
            seen.add(key)
        return values


class RequirementView(StrictModel):
    repo_root: str
    requirement: RepoDependencyRequirement


class RequirementWaitingView(StrictModel):
    repo_root: str
    requirement_name: str
    target_repo: str
    provider_repo: str
    status: RepoDependencyRequirementStatus
    waiting: bool
    result_observed: bool
    summary: str


class RequirementResumeCandidateView(StrictModel):
    consumer_repo: str
    consumer_repo_root: str
    requirement_name: str
    target_repo: str
    provider_repo: str
    status: RepoDependencyRequirementStatus
    result_observed: bool
    summary: str


class RequirementGroupItem(StrictModel):
    consumer_repo: str
    consumer_repo_root: str
    requirement: RepoDependencyRequirement


class RequirementGroupView(StrictModel):
    target_repo: str
    resolved_provider_route: ProviderRoute
    route_resolution_summary: str
    required_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    provider_completion_mode: RepoCompletionMode = RepoCompletionMode.INTERFACE_DECLARED
    requirements: list[RequirementGroupItem] = Field(default_factory=list)
    summary: str


class RepoPreparationRequirementsView(StrictModel):
    repo_root: str
    target_repo: str
    requirement_refs: list[RepoRequirementRef] = Field(default_factory=list)
    requirements: list[RequirementGroupItem] = Field(default_factory=list)
    missing_refs: list[RepoRequirementRef] = Field(default_factory=list)
    summary: str


class RepoPreparationInputView(StrictModel):
    repo_root: str | None = None
    input: RepoPreparationInput
    summary: str


class RepoPreparationInputDraftView(StrictModel):
    input: RepoPreparationInput
    requirement_group: RequirementGroupView
    warnings: list[str] = Field(default_factory=list)
    summary: str


class RepoShellView(StrictModel):
    repo_root: str
    repo_name: str
    project_name: str | None = None
    created: bool
    summary: str


class ProviderRepoShellView(StrictModel):
    shell: RepoShellView
    preparation_input: RepoPreparationInputView
    requirement_group: RequirementGroupView
    summary: str


class ProviderRepoPreparationView(StrictModel):
    shell: RepoShellView
    preparation_input: RepoPreparationInputView
    summary: str


class BootstrapInputValidationView(StrictModel):
    passed: bool
    requirement_count: int
    source_corpus_mode: SourceCorpusMode | None = None
    issue_code: str | None = None
    summary: str
    suggested_fix: str | None = None


class ProviderReadyView(StrictModel):
    provider_ready_marked: bool
    satisfied_requirement_count: int = 0
    repo_summary: str | None = None
    summary: str
    issue_code: str | None = None


class UpstreamDependencyInput(StrictModel):
    git_url: str
    revision: str | None = None
    subdir: str | None = None
    package_name: str | None = None
    module_name: str | None = None
    evidence_summary: str | None = None
    known_risks: list[str] = Field(default_factory=list)


class RepoFormatChoice(StrictModel):
    repo_format: RepoFormat
    summary: str
