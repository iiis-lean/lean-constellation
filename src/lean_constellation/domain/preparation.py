"""Repo preparation and dependency requirement models."""

from __future__ import annotations

from enum import StrEnum
from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.repo import RepoFormat


class SourceCorpusMode(StrEnum):
    EXISTING = "existing"
    PREPARE = "prepare"
    NONE = "none"


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


class RequirementWaitingState(StrictModel):
    waiting: bool = False
    provider_repo: str | None = None
    reason: str | None = None
    submitted_at: str | None = None
    result_observed: bool = False
    result_observed_at: str | None = None
    result_note: str | None = None


class RepoDependencyRequirement(StrictModel):
    name: str
    target_repo: str
    source_description: str | None = None
    reason: str | None = None
    interfaces: list[DeclInterface] = Field(default_factory=list)
    status: RepoDependencyRequirementStatus = RepoDependencyRequirementStatus.OPEN
    satisfaction_mode: RepoDependencySatisfactionMode = RepoDependencySatisfactionMode.REPO_READY
    provider_repo: str | None = None
    note: str | None = None
    waiting_state: RequirementWaitingState = Field(default_factory=RequirementWaitingState)


class RepoPreparationInput(StrictModel):
    goal: str
    source_corpus_mode: SourceCorpusMode
    source_corpus_relpath: str | None = ".lean_constellation/source"
    source_description: str | None = None
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
    requirements: list[RequirementGroupItem] = Field(default_factory=list)
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


class RepoRuntimeBootstrapView(StrictModel):
    repo_root: str
    runtime_root: str
    created: bool
    initialized_paths: list[str] = Field(default_factory=list)
    summary: str


class ProviderRepoRuntimeShellView(StrictModel):
    shell: RepoShellView
    preparation_input: RepoPreparationInputView
    runtime_bootstrap: RepoRuntimeBootstrapView
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


class RepoFormatChoice(StrictModel):
    repo_format: RepoFormat
    summary: str
