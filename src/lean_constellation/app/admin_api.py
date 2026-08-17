"""Admin-facing API for starting and controlling Lean Constellation runtime."""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
from pathlib import Path
import time
from typing import Any, Literal

from agent_runtime_kit.agent.models import to_jsonable
from agent_runtime_kit.flow import SchedulerRunBudget, SchedulerRunControlView, SchedulerRunLeaseView
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus, StepStatus, utc_now_iso
from agent_runtime_kit.flow.standard_steps import AgentStepState
from pydantic import Field, field_validator, model_validator

from lean_constellation.app.repo_runtime_registry import RepoRuntimeRegistry
from lean_constellation.app.semantic_scheduler import (
    RuntimeSemanticAdvanceInput,
    SemanticAdvancePolicyError,
    build_semantic_run_policy,
    get_semantic_lease_observation,
    register_semantic_lease_observation,
)
from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.preparation import (
    AdapterProviderRoute,
    RepoDependencyRequirement,
    RepoDependencyRequirementStatus,
    RepoPreparationInput,
    RepoPreparationInputView,
    RepoShellView,
    RequirementResumeCandidateView,
    SourceCorpusMode,
    VerifiedAdapterRouteReceipt,
)
from lean_constellation.domain.repo import (
    ProofAvailability,
    RepoCompletionMode,
    RepoConfigView,
    RepoPublicationView,
)
from lean_constellation.domain.publication import (
    RepoPublicationOverride,
    RepoPublicationPresentation,
)
from lean_constellation.domain.repo_run import RepoRunContext, RepoRunSpec, SourceScope
from lean_constellation.domain.repo_recovery import NativeSourceIndexRecoveryContract
from lean_constellation.domain.repo_release import RepoReleaseListView
from lean_constellation.domain.repo_release import RepoReleaseValidationProfile
from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildResult
from lean_constellation.services.validation_snapshot import RepoCheckpointKind
from lean_constellation.flows.testing import (
    CONTROLLED_AGENT_OVERRIDE_KEY,
    CONTROLLED_AGENT_RECORD_KEY,
    ControlledAgentOverrideSpec,
)
from lean_constellation.services.foundation import (
    FoundationContext,
    GateReport,
    ServiceIssue,
    ServiceResult,
    WriteMode,
)
from lean_constellation.services.repo_workspace import (
    DependencyReleaseMode,
    RepoSkeletonView,
)
from lean_constellation.services.repo_workspace.repo_lifecycle_lock import RepoLifecycleLockBusyError
from lean_constellation.services.repo_workspace.repo_preparation import (
    resolve_requirement_routes,
)
from lean_constellation.services.runtime import LeanRuntimeServices


class AdminFlowStartView(StrictModel):
    flow_id: str
    flow_type: str
    scope_id: str
    enqueued: bool
    repo_root: str | None = None
    summary: str


class RepoRunStatusView(StrictModel):
    repo_root: str
    publication_status: str
    latest_release_id: str | None = None
    active_flow_id: str | None = None
    active_flow_type: str | None = None
    run_spec: RepoRunSpec | None = None
    summary: str


class RuntimePauseView(StrictModel):
    paused: bool
    scope_id: str | None = None
    run_control: SchedulerRunControlView | None = None
    lease_id: str | None = None
    lease_version: int | None = None
    lease_status: str | None = None
    wait_url: str | None = None
    summary: str


class RuntimeResumeInput(StrictModel):
    scope_id: str | None = None
    budget: SchedulerRunBudget | None = None
    unbounded: bool = False
    skip_rebuild: bool = False

    @model_validator(mode="after")
    def validate_bounded_resume(self) -> "RuntimeResumeInput":
        if (self.budget is None) == (not self.unbounded):
            raise ValueError("runtime resume requires exactly one run plan: budget or unbounded=true")
        if self.budget is not None and self.scope_id is not None:
            raise ValueError("bounded scheduler resume is repo-global and cannot specify scope_id")
        if self.budget is not None and self.skip_rebuild:
            raise ValueError("bounded scheduler resume requires candidate queue rebuild")
        return self


class RuntimeStatusView(StrictModel):
    paused: bool
    test_control_enabled: bool
    run_control: SchedulerRunControlView | None = None
    flow_candidate_queue: list[str] = Field(default_factory=list)
    step_candidate_queue: list[str] = Field(default_factory=list)
    queued_flow_ids: list[str] = Field(default_factory=list)
    queued_step_ids: list[str] = Field(default_factory=list)
    active_flow_advances: list[str] = Field(default_factory=list)
    running_step_ids: list[str] = Field(default_factory=list)
    created_step_ids: list[str] = Field(default_factory=list)
    summary: str


RuntimeLeaseTerminalDisposition = Literal[
    "active",
    "normal_boundary",
    "cross_flow_handoff",
    "review_required",
    "business_blocked",
    "runtime_failure",
]


class RuntimeLeaseMonitorView(StrictModel):
    lease: SchedulerRunLeaseView
    runtime: RuntimeStatusView
    advanced_flows: list[FlowMonitorView] = Field(default_factory=list)
    started_steps: list[StepMonitorView] = Field(default_factory=list)
    current_content_task_flow_id: str | None = None
    current_content_task_phase: str | None = None
    current_agent_id: str | None = None
    checkpoint_ids: list[str] = Field(default_factory=list)
    truth_version: int
    observed_at: str
    timed_out: bool = False
    terminal_disposition: RuntimeLeaseTerminalDisposition = "active"
    requires_review: bool = False
    suggested_next_action: str = "wait_for_terminal"
    summary: str


class StepMonitorView(StrictModel):
    step_id: str
    flow_id: str
    scope_id: str
    step_type: str
    status: str
    state_type: str | None = None
    submission_type: str | None = None
    submit_tool: str | None = None
    result_type: str | None = None
    error_type: str | None = None
    agent_type: str | None = None
    bound_agent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    summary: str


class StepTerminalWaitView(StrictModel):
    step: StepMonitorView
    terminal: bool
    timed_out: bool
    runner_state: Literal["active", "not_started", "lost", "settled"]
    warning: str | None = None
    observed_at: str
    summary: str


class FlowMonitorView(StrictModel):
    flow_id: str
    flow_type: str
    scope_id: str
    status: str
    phase: str | None = None
    round_index: int | None = None
    current_step_id: str | None = None
    parent_flow_id: str | None = None
    parent_dispatch_step_id: str | None = None
    manual_pause_active: bool = False
    step_count: int
    child_flow_count: int
    result_type: str | None = None
    error_type: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    steps: list[StepMonitorView] = Field(default_factory=list)
    summary: str


def _classify_runtime_lease_terminal(
    lease: SchedulerRunLeaseView,
    advanced_flows: list[FlowMonitorView],
) -> tuple[RuntimeLeaseTerminalDisposition, bool, str]:
    if lease.status != "terminal":
        return "active", False, "wait_for_terminal"

    reason = lease.terminal_reason
    failed_flows = [flow for flow in advanced_flows if flow.status == "failed" or flow.error_type is not None]
    if failed_flows:
        return "business_blocked", True, "inspect_terminal_flow_error"

    if isinstance(reason, str) and reason.startswith("runtime_failure"):
        return "runtime_failure", True, "inspect_runtime_failure"

    if isinstance(reason, str) and reason.startswith("flow_terminal:"):
        return "cross_flow_handoff", False, "inspect_flow_result_and_start_next_lifecycle_entry"

    normal_prefixes = (
        "agent_step_created:",
        "agent_step_terminal:",
        "content_plan_step_terminal:",
        "content_child_closed:",
        "waiting_for_parent_callback:",
        "content_task_terminal:",
        "content_task_batch_checkpointed:",
        "coordinator_terminal:",
    )
    if reason == "semantic_boundary_reached" or (
        isinstance(reason, str) and reason.startswith(normal_prefixes)
    ):
        return "normal_boundary", False, "inspect_boundary_and_continue"

    if reason == "no_runnable_candidate":
        completed_flows = [flow for flow in advanced_flows if flow.status == "completed"]
        if completed_flows:
            return "cross_flow_handoff", False, "inspect_flow_result_and_start_next_lifecycle_entry"
        return "review_required", True, "audit_candidates_before_next_admission"

    if reason in {"semantic_safety_cap_exhausted", "run_control_cleared"}:
        return "review_required", True, "audit_lease_terminal_state"

    return "review_required", True, "audit_unknown_terminal_reason"


class FlowTreeMonitorView(StrictModel):
    scope_id: str | None = None
    include_terminal: bool = True
    total_flows: int
    total_steps: int
    root_count: int
    roots: list[dict[str, Any]] = Field(default_factory=list)
    summary: str


class WaitingRequirementMonitorView(StrictModel):
    consumer_repo: str
    consumer_repo_root: str
    requirement_name: str
    target_repo: str
    provider_repo: str | None = None
    status: RepoDependencyRequirementStatus
    waiting: bool
    result_observed: bool
    submitted_at: str | None = None
    result_observed_at: str | None = None
    reason: str | None = None
    summary: str


class WaitingRequirementsMonitorView(StrictModel):
    workspace_root: str | None = None
    repo_root: str | None = None
    provider_repo: str | None = None
    requirements: list[WaitingRequirementMonitorView] = Field(default_factory=list)
    summary: str


class RequirementResumeCandidatesMonitorView(StrictModel):
    workspace_root: str
    provider_repo: str
    candidates: list[RequirementResumeCandidateView] = Field(default_factory=list)
    summary: str


class AgentMonitorView(StrictModel):
    agent_id: str
    scope_id: str
    agent_type: str
    provider_type: str
    home_id: str
    status: str
    session_id: str | None = None
    artifact_ref: str | None = None
    artifact_exists: bool = False
    last_completion_status: str | None = None
    last_completion_turn_id: str | None = None
    latest_turn_duration_ms: int | None = None
    tool_call_count: int | None = None
    summary: str


class AgentListMonitorView(StrictModel):
    scope_id: str | None = None
    agent_type: str | None = None
    status: str | None = None
    agents: list[AgentMonitorView] = Field(default_factory=list)
    summary: str


class ContentTaskProgressView(StrictModel):
    flow_id: str
    node_path: str
    contract_version: int | None = None
    task_status: str
    phase: str | None = None
    plan_agent_id: str | None = None
    active_child_flow_id: str | None = None
    active_child_type: str | None = None
    active_round_id: str | None = None
    round_index: int | None = None
    round_status: str | None = None
    active_decl_names: list[str] = Field(default_factory=list)
    current_stage: str | None = None
    current_step_id: str | None = None
    current_agent_id: str | None = None
    latest_callback_outcome: str | None = None
    blocker_summary: str | None = None
    latest_content_progress_checkpoint_id: str | None = None
    candidate_summary: dict[str, Any] = Field(default_factory=dict)
    truth_version: str
    observed_at: str
    summary: str


class AgentLiveMonitorView(StrictModel):
    agent: AgentMonitorView
    wake_on: Literal["activity", "status", "response"] = "activity"
    owning_steps: list[StepMonitorView] = Field(default_factory=list)
    delta_turns: list[dict[str, Any]] = Field(default_factory=list)
    delta_events: list[dict[str, Any]] = Field(default_factory=list)
    delta_tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    latest_response_available: bool = False
    latest_response_summary: str | None = None
    report_index_url: str
    trace_report_url: str
    next_cursor: str
    timed_out: bool = False
    observed_at: str
    summary: str


class RunningAgentAuditItemView(StrictModel):
    agent_id: str
    scope_id: str
    classification: str
    session_id: str | None = None
    artifact_ref: str | None = None
    evidence: list[str] = Field(default_factory=list)


class RunningAgentAuditView(StrictModel):
    repo_key: str
    agents: list[RunningAgentAuditItemView] = Field(default_factory=list)
    summary: str


class RunningAgentRepairInput(StrictModel):
    expected_scope_id: str
    expected_session_id: str | None = None
    expected_artifact_ref: str | None = None
    action: Literal["mark_idle"] = "mark_idle"
    dry_run: bool = True


class RunningAgentRepairView(StrictModel):
    agent_id: str
    classification: str
    action: str
    dry_run: bool
    repaired: bool
    summary: str


class AgentReportIndexView(StrictModel):
    agent_id: str
    reports_root: str | None = None
    latest_json_path: str | None = None
    latest_markdown_path: str | None = None
    existing_report_paths: list[str] = Field(default_factory=list)
    summary: str


class ExternalHealthMonitorView(StrictModel):
    health: dict[str, Any]
    toolkit_process: dict[str, Any] | None = None
    summary: str


class MainRepoStatusView(StrictModel):
    repo_root: str
    repo_exists: bool
    constellation_exists: bool
    preparation_input_exists: bool
    source_corpus_exists: bool | None = None
    source_corpus_file_count: int | None = None
    repo_state: dict[str, Any] | None = None
    flow_count: int
    nonterminal_flow_count: int
    agent_count: int
    summary: str


class TestControlCandidateQueueView(StrictModel):
    flow_candidate_queue: list[str] = Field(default_factory=list)
    step_candidate_queue: list[str] = Field(default_factory=list)
    queued_flow_ids: list[str] = Field(default_factory=list)
    queued_step_ids: list[str] = Field(default_factory=list)
    active_flow_advances: list[str] = Field(default_factory=list)
    running_step_ids: list[str] = Field(default_factory=list)
    created_step_ids: list[str] = Field(default_factory=list)


class TestControlRuntimeView(StrictModel):
    test_control_enabled: bool
    paused: bool
    candidate_queues: TestControlCandidateQueueView
    summary: str


class AdminFlowAdvanceInput(StrictModel):
    flow_id: str


class AdminFlowAdvanceView(StrictModel):
    flow_id: str
    scope_id: str
    flow_status: str
    created_step_id: str | None = None
    summary: str


class AdminStepStartInput(StrictModel):
    step_id: str
    wait: bool = True
    timeout_s: float | None = None


class AdminStepRunView(StrictModel):
    step_id: str
    flow_id: str
    scope_id: str
    step_type: str
    status: str
    waited: bool
    summary: str


class AdminRunUntilStepCreatedInput(StrictModel):
    flow_id: str
    step_type: str | None = None
    max_advances: int = 20


class AgentStepControlView(StrictModel):
    step_id: str
    flow_id: str
    scope_id: str
    step_type: str
    status: str
    agent_role: str
    agent_type: str | None = None
    provider_type: str
    home_id: str | None = None
    tool_view_key: str | None = None
    step_bound_agent_id: str | None = None
    flow_bound_agent_id: str | None = None
    override: dict[str, Any] | None = None
    controlled_record: dict[str, Any] | None = None
    summary: str


class SetAgentStepOverrideInput(StrictModel):
    step_id: str
    override: ControlledAgentOverrideSpec


class ClearAgentStepOverrideInput(StrictModel):
    step_id: str


class RestartFailedAgentStepInput(StrictModel):
    step_id: str


class RestartFailedAgentStepView(StrictModel):
    failed_step_id: str
    replacement_step_id: str
    flow_id: str
    scope_id: str
    agent_id: str
    agent_reused: bool
    enqueued: bool
    reopened_round_id: str | None = None
    summary: str


class ManualCheckpointInput(StrictModel):
    repo_root: Path
    scope_ids: list[str]
    label: str | None = None
    node_paths: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class RequirementResumeView(StrictModel):
    requirement_name: str
    consumer_repo_root: str
    provider_repo: str
    observed: bool
    resume_flow: AdminFlowStartView
    summary: str


class StartRequirementGroupBootstrapInput(StrictModel):
    workspace_root: Path
    target_repo: str
    source_corpus_mode: SourceCorpusMode = SourceCorpusMode.PREPARE
    project_name: str | None = None
    admin_notes: str | None = None
    enqueue: bool = True

    @field_validator("workspace_root", mode="before")
    @classmethod
    def _coerce_workspace(cls, value: Any) -> Path:
        return Path(value).expanduser()


class StartPreparationInput(StrictModel):
    repo_root: Path
    repo_key: str | None = None
    start_reason: Literal["admin", "bootstrap", "repair_resume"] = "admin"
    admin_notes: str | None = None
    enqueue: bool = True
    run_request: "RepoRunOptions | None" = None

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class RepoRunOptions(StrictModel):
    run_objective: str | None = Field(
        default=None,
        description=(
            "Bounded responsibility and stopping boundary for this run; omit to use the stable repository goal."
        ),
    )
    completion_mode: RepoCompletionMode | None = None
    source_scope: SourceScope | None = None
    index_policy: Literal["auto", "update", "reuse"] | None = None
    root_interface_policy: Literal["auto", "prepare", "reuse"] | None = None
    max_parallel_content_node_tasks: int = Field(default=1, ge=1)
    additional_required_interfaces: list[DeclInterface] = Field(default_factory=list)


class RepoRunRequestInput(RepoRunOptions):
    repo_root: Path
    repo_key: str | None = None
    run_objective: str = Field(
        description="Bounded responsibility and stopping boundary for this continuation run."
    )
    enqueue: bool = True

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class RepoRunStartInput(StrictModel):
    repo_root: Path
    repo_key: str | None = None
    request: RepoRunOptions = Field(default_factory=RepoRunOptions)
    admin_notes: str | None = None
    enqueue: bool = True

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class NativeSourceIndexRecoveryPreviewInput(StrictModel):
    repo_root: Path
    repo_key: str | None = None
    failed_parent_flow_id: str

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class NativeSourceIndexRecoveryStartInput(NativeSourceIndexRecoveryPreviewInput):
    expected_recovery_token: str = Field(min_length=64, max_length=64)
    enqueue: bool = True


class StandaloneSourceIndexRunInput(StrictModel):
    repo_root: Path
    repo_key: str | None = None
    run_objective: str = Field(
        description="Bounded responsibility and stopping boundary for this SourceIndex run."
    )
    source_scope: SourceScope
    index_policy: Literal["auto", "update", "reuse"] = "auto"
    enqueue: bool = True


class StandaloneRootInterfaceRunInput(StrictModel):
    repo_root: Path
    repo_key: str | None = None
    run_objective: str = Field(
        description="Bounded responsibility and stopping boundary for this root-interface run."
    )
    root_interface_policy: Literal["auto", "prepare", "reuse"] = "auto"
    additional_required_interfaces: list[DeclInterface] = Field(default_factory=list)
    enqueue: bool = True


class RepoReleasePreviewInput(StrictModel):
    repo_root: Path
    summary: str = "Admin release preview."

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class RepoReleaseIdInput(StrictModel):
    repo_root: Path
    release_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class RepoReleaseRestoreApplyInput(RepoReleaseIdInput):
    expected_recovery_token: str = Field(min_length=64, max_length=64)


class RepoPublicationPrepareInput(StrictModel):
    repo_root: Path
    title: str | None = None
    presentation: RepoPublicationPresentation | None = None

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class RepoRemotePublicationInput(RepoReleaseIdInput):
    expected_recovery_token: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    push: bool = False


class RepoGitHubTopicsInput(StrictModel):
    repo_root: Path
    remote_name: str = Field(
        default="origin",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    expected_recovery_token: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class RepoDependencyChangeInput(StrictModel):
    repo_root: Path
    provider_repo_key: str
    target_provider_release_id: str
    target_git_url: str
    release_mode: DependencyReleaseMode = DependencyReleaseMode.DEFER
    validation_profile: RepoReleaseValidationProfile = (
        RepoReleaseValidationProfile.DEPENDENCY_MINIMAL
    )
    expected_recovery_token: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class WorkspacePublicationInput(StrictModel):
    workspace_root: Path
    repo_keys: list[str] | None = None
    output_root: Path | None = None
    push_children: bool = False
    push_superproject: bool = False
    expected_recovery_token: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )

    @field_validator("workspace_root", "output_root", mode="before")
    @classmethod
    def _coerce_paths(cls, value: Any) -> Path | None:
        return None if value is None else Path(value).expanduser()


class RepoReleaseOrphanCleanupInput(StrictModel):
    repo_root: Path
    expected_audit_digest: str = Field(min_length=64, max_length=64)

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class CreateMainRepoShellInput(StrictModel):
    workspace_root: Path
    repo_name: str
    project_name: str

    @field_validator("workspace_root", mode="before")
    @classmethod
    def _coerce_workspace(cls, value: Any) -> Path:
        return Path(value).expanduser()


class WriteMainRepoPreparationInput(StrictModel):
    repo_root: Path
    input: RepoPreparationInput

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class ValidateMainSourceCorpusInput(StrictModel):
    repo_root: Path
    require_files: bool = True
    check_draft_gate: bool = False
    entry_path: str | None = None

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class MainSourceCorpusValidationView(StrictModel):
    repo_root: str
    source_corpus_mode: SourceCorpusMode
    source_corpus_relpath: str | None = None
    source_corpus_path: str | None = None
    exists: bool
    file_count: int
    draft_gate: GateReport | None = None
    passed: bool
    issues: list[ServiceIssue] = Field(default_factory=list)
    summary: str


class InitializeMainNativeSkeletonInput(StrictModel):
    repo_root: Path
    project_name: str | None = None

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class BootstrapMainNativeRepoInput(StrictModel):
    workspace_root: Path
    repo_name: str
    project_name: str
    preparation_input: RepoPreparationInput
    validate_source_corpus: bool = True
    enqueue: bool = True
    run_request: RepoRunOptions | None = None

    @field_validator("workspace_root", mode="before")
    @classmethod
    def _coerce_workspace(cls, value: Any) -> Path:
        return Path(value).expanduser()


class MainNativeRepoBootstrapView(StrictModel):
    shell: RepoShellView
    preparation_input: RepoPreparationInputView
    source_corpus_validation: MainSourceCorpusValidationView | None = None
    skeleton: RepoSkeletonView
    preparation_flow: AdminFlowStartView
    summary: str


class RepoConfigUpdateInput(StrictModel):
    repo_root: Path
    completion_mode: RepoCompletionMode | None = None
    default_requirement_proof_availability: ProofAvailability | None = None
    publication: RepoPublicationOverride | None = None

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class StartFlowInput(StrictModel):
    flow_type: str
    scope_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    enqueue: bool = True


class SnapshotCreateInput(StrictModel):
    repo_root: Path
    checkpoint_kind: str = "requirement_bootstrap_terminal"
    label: str | None = None
    node_paths: list[str] = Field(default_factory=list)
    node_ids: list[str] = Field(default_factory=list)
    scope_ids: list[str] | None = None

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class SnapshotRestoreInput(StrictModel):
    repo_root: Path
    snapshot_id: str
    dry_run: bool = False
    leave_runtime_paused: bool = True
    prune_extra_files: bool = False

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class SnapshotListInput(StrictModel):
    repo_root: Path
    checkpoint_kind: str | None = None

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class RequirementResumeInput(StrictModel):
    consumer_repo_root: Path
    requirement_name: str
    provider_repo: str
    admin_note: str | None = None
    enqueue: bool = True

    @field_validator("consumer_repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class UpdateRepoRequirementInput(StrictModel):
    consumer_repo: str
    current_requirement_name: str
    expected_current_digest: str
    replacement: RepoDependencyRequirement
    reason: str
    dry_run: bool = True

    @field_validator(
        "consumer_repo",
        "current_requirement_name",
        "expected_current_digest",
        "reason",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must be non-empty")
        return normalized


class RequirementUpdateImpactView(StrictModel):
    changed: bool
    current_digest: str
    replacement_digest: str
    before_name: str
    after_name: str
    changed_fields: list[str] = Field(default_factory=list)
    affected_requirement_refs: list[str] = Field(default_factory=list)
    affected_preparation_inputs: list[str] = Field(default_factory=list)
    affected_runtime_flows: list[str] = Field(default_factory=list)
    affected_provider_groups: list[str] = Field(default_factory=list)
    blockers: list[ServiceIssue] = Field(default_factory=list)
    checkpoint_required: bool = False
    applied: bool = False
    checkpoint_id: str | None = None
    summary: str


class LeanAdminApi:
    """Small admin service that composes existing runtime services."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        workspace_root: Path | None = None,
        toolkit_state: object | None = None,
        repo_runtime_registry: RepoRuntimeRegistry | None = None,
    ) -> None:
        self.runtime = runtime
        self.workspace_root = Path(workspace_root).expanduser() if workspace_root is not None else None
        self.toolkit_state = toolkit_state
        self.repo_runtime_registry = repo_runtime_registry

    def start_requirement_group_bootstrap(
        self,
        input_model: StartRequirementGroupBootstrapInput,
    ) -> ServiceResult[AdminFlowStartView]:
        registry = self.repo_runtime_registry
        if registry is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_runtime_registry_required",
                    "Requirement-group bootstrap requires the workspace repo runtime registry.",
                )
            )
        if input_model.workspace_root.resolve() != registry.workspace_root.resolve():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_root_mismatch",
                    "Requirement-group bootstrap workspace_root must match the runtime registry workspace.",
                    field="workspace_root",
                    current=str(input_model.workspace_root),
                    expected=str(registry.workspace_root),
                )
            )
        draft = self.runtime.repo_workspace.preparation.build_preparation_input_from_group(
            input_model.workspace_root,
            target_repo=input_model.target_repo,
            source_corpus_mode=input_model.source_corpus_mode,
        )
        if not draft.ok or draft.value is None:
            return self.runtime.foundation.fail(draft.issues)
        verified_adapter_route = self._verify_requirement_adapter_route(
            draft.value.requirement_group.resolved_provider_route
        )
        if not verified_adapter_route.ok:
            return self.runtime.foundation.fail(verified_adapter_route.issues)
        prepared = self.runtime.repo_workspace.prepare_provider_repo_shell(
            input_model.workspace_root,
            target_repo=input_model.target_repo,
            preparation_input=draft.value.input,
            project_name=input_model.project_name,
        )
        if not prepared.ok or prepared.value is None:
            return self.runtime.foundation.fail(prepared.issues)
        loaded = registry.initialize_and_load(input_model.target_repo)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        refs = [
            f"{ref.consumer_repo}:{ref.requirement_name}"
            for ref in prepared.value.preparation_input.input.requirement_refs
        ]
        provider_admin = LeanAdminApi(
            loaded.value,
            workspace_root=registry.workspace_root,
            repo_runtime_registry=registry,
        )
        return provider_admin.start_arbitrary_flow(
            StartFlowInput(
                flow_type="requirement_group_repo_bootstrap",
                scope_id=f"repo:{input_model.target_repo}",
                enqueue=input_model.enqueue,
                params={
                    "target_repo": input_model.target_repo,
                    "repo_root": prepared.value.shell.repo_root,
                    "workspace_root": str(input_model.workspace_root),
                    "requirement_refs": refs,
                    "resolved_provider_route": draft.value.requirement_group.resolved_provider_route.model_dump(
                        mode="json"
                    ),
                    "verified_adapter_route": (
                        verified_adapter_route.value.model_dump(mode="json")
                        if verified_adapter_route.value is not None
                        else None
                    ),
                    "admin_notes": input_model.admin_notes,
                },
            ),
            repo_root=prepared.value.shell.repo_root,
        )

    def update_repo_requirement(
        self,
        input_model: UpdateRepoRequirementInput,
    ) -> ServiceResult[RequirementUpdateImpactView]:
        if self.workspace_root is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_root_required",
                    "Requirement update requires a workspace root.",
                )
            )
        try:
            consumer_repo = self.runtime.foundation.layout.ensure_safe_key(
                input_model.consumer_repo
            )
            current_name = self.runtime.foundation.layout.ensure_safe_key(
                input_model.current_requirement_name
            )
            replacement_name = self.runtime.foundation.layout.ensure_safe_key(
                input_model.replacement.name
            )
            replacement_target = self.runtime.foundation.layout.ensure_safe_key(
                input_model.replacement.target_repo
            )
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_update_identity_invalid",
                    str(exc),
                )
            )
        consumer_root = self.workspace_root / consumer_repo
        loaded = self.runtime.repo_workspace.requirement.get_requirement(
            consumer_root,
            name=current_name,
        )
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        current = loaded.value.requirement
        current_digest = (
            self.runtime.repo_workspace.requirement.requirement_digest(current)
        )
        if current_digest != input_model.expected_current_digest:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_digest_mismatch",
                    "Requirement changed after the operator preview.",
                    current=current_digest,
                    expected=input_model.expected_current_digest,
                    object_ref=f"{consumer_repo}:{current_name}",
                )
            )
        normalized_route = (
            self.runtime.repo_workspace.requirement.normalize_provider_route(
                input_model.replacement.provider_route
            )
        )
        if not normalized_route.ok or normalized_route.value is None:
            return self.runtime.foundation.fail(normalized_route.issues)
        replacement = input_model.replacement.model_copy(
            update={
                "name": replacement_name,
                "target_repo": replacement_target,
                "provider_route": normalized_route.value,
            }
        )
        before = current.model_dump(mode="json")
        after = replacement.model_dump(mode="json")
        changed_fields = sorted(
            key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
        )
        replacement_digest = (
            self.runtime.repo_workspace.requirement.requirement_digest(replacement)
        )
        identity_changed = (
            current.name != replacement.name
            or current.target_repo != replacement.target_repo
        )
        blockers: list[ServiceIssue] = []
        if identity_changed and current.status != RepoDependencyRequirementStatus.OPEN:
            blockers.append(
                self.runtime.foundation.issue(
                    "requirement_identity_change_after_open",
                    "Requirement identity can only change while the requirement is open.",
                    object_ref=f"{consumer_repo}:{current_name}",
                    current=current.status.value,
                    expected=RepoDependencyRequirementStatus.OPEN.value,
                )
            )
        if replacement.status in {
            RepoDependencyRequirementStatus.SATISFIED,
            RepoDependencyRequirementStatus.HANDLED,
        } and not replacement.provider_repo:
            blockers.append(
                self.runtime.foundation.issue(
                    "requirement_provider_missing",
                    "Satisfied or handled requirements require provider_repo.",
                    field="replacement.provider_repo",
                )
            )

        preparation_updates: dict[Path, RepoPreparationInput] = {}
        affected_refs: list[str] = []
        for repo_dir in sorted(
            path for path in self.workspace_root.iterdir() if path.is_dir()
        ):
            preparation = self.runtime.repo_workspace.preparation.get_preparation_input(
                repo_dir
            )
            if not preparation.ok or preparation.value is None:
                continue
            updated = preparation.value.input.model_copy(deep=True)
            changed = False
            refs = []
            for ref in updated.requirement_refs:
                if (
                    ref.consumer_repo == consumer_repo
                    and ref.requirement_name == current_name
                ):
                    affected_refs.append(
                        f"{repo_dir.name}:{consumer_repo}:{current_name}"
                    )
                    refs.append(
                        ref.model_copy(
                            update={"requirement_name": replacement.name}
                        )
                    )
                    changed = changed or current.name != replacement.name
                else:
                    refs.append(ref)
            if changed:
                updated.requirement_refs = refs
                preparation_updates[repo_dir] = updated

        group_requirements: dict[str, list[RepoDependencyRequirement]] = {}
        for repo_dir in sorted(
            path for path in self.workspace_root.iterdir() if path.is_dir()
        ):
            listed = self.runtime.repo_workspace.requirement.list_requirements(
                repo_dir
            )
            if not listed.ok or listed.value is None:
                continue
            for view in listed.value:
                requirement = view.requirement
                if repo_dir.name == consumer_repo and requirement.name == current_name:
                    requirement = replacement
                if requirement.status == RepoDependencyRequirementStatus.OPEN:
                    group_requirements.setdefault(
                        requirement.target_repo, []
                    ).append(requirement)
        affected_groups = sorted(
            {current.target_repo, replacement.target_repo}
        )
        for target_repo in affected_groups:
            route, _, conflicts = resolve_requirement_routes(
                group_requirements.get(target_repo, [])
            )
            if route is None:
                blockers.extend(
                    self.runtime.foundation.issue(
                        "requirement_provider_route_conflict",
                        conflict,
                        field="replacement.provider_route",
                        object_ref=target_repo,
                    )
                    for conflict in conflicts
                )
            interfaces: dict[str, DeclInterface] = {}
            for requirement in group_requirements.get(target_repo, []):
                for interface in requirement.interfaces:
                    previous = interfaces.get(interface.name)
                    if (
                        previous is not None
                        and previous.model_dump(mode="json")
                        != interface.model_dump(mode="json")
                    ):
                        blockers.append(
                            self.runtime.foundation.issue(
                                "requirement_interface_conflict",
                                "Requirements for one provider contain incompatible interfaces with the same name.",
                                field=interface.name,
                                object_ref=target_repo,
                            )
                        )
                    interfaces[interface.name] = interface

        affected_flows: list[str] = []
        if self.repo_runtime_registry is not None:
            for repo_dir in sorted(
                path for path in self.workspace_root.iterdir() if path.is_dir()
            ):
                repo_runtime = self.repo_runtime_registry.try_get_loaded(repo_dir.name)
                if repo_runtime is None:
                    continue
                flow_service = repo_runtime.ark.flow_service
                if flow_service is None:
                    continue
                for flow in flow_service.list_flows():
                    state = getattr(flow, "state", None)
                    input_value = getattr(flow, "input", None)
                    refs = list(getattr(input_value, "requirement_refs", []) or [])
                    matches_ref = f"{consumer_repo}:{current_name}" in refs
                    matches_state = current_name in {
                        getattr(state, "waiting_requirement_name", None),
                        getattr(state, "resuming_requirement_name", None),
                    }
                    if matches_ref or matches_state:
                        affected_flows.append(flow.flow_id)
                        if (
                            identity_changed
                            and flow.status
                            not in {FlowStatus.COMPLETED, FlowStatus.FAILED}
                        ):
                            blockers.append(
                                self.runtime.foundation.issue(
                                    "requirement_update_nonterminal_flow",
                                    "Identity changes require the affected Flow to reach a terminal repair boundary.",
                                    object_ref=flow.flow_id,
                                )
                            )

        consumer_runtime = (
            self.repo_runtime_registry.try_get_loaded(consumer_repo)
            if self.repo_runtime_registry is not None
            else None
        )
        if consumer_runtime is not None:
            paused = consumer_runtime.ark.pause_controller
            if (
                paused is None
                or not hasattr(paused, "is_paused")
                or not paused.is_paused()
            ):
                blockers.append(
                    self.runtime.foundation.issue(
                        "requirement_update_runtime_not_paused",
                        "Loaded consumer runtime must be paused before apply.",
                        object_ref=consumer_repo,
                    )
                )
            if consumer_runtime.ark.step_service.list_running_steps():
                blockers.append(
                    self.runtime.foundation.issue(
                        "requirement_update_running_steps",
                        "Loaded consumer runtime still has running Steps.",
                        object_ref=consumer_repo,
                    )
                )
            if consumer_runtime.ark.agent_service.list_running_agents():
                blockers.append(
                    self.runtime.foundation.issue(
                        "requirement_update_running_agents",
                        "Loaded consumer runtime still has running Agents.",
                        object_ref=consumer_repo,
                    )
                )
            if consumer_runtime.ark.schedule_service.active_flow_advances:
                blockers.append(
                    self.runtime.foundation.issue(
                        "requirement_update_active_advance",
                        "Loaded consumer runtime still has an active Flow advance.",
                        object_ref=consumer_repo,
                    )
                )

        impact = RequirementUpdateImpactView(
            changed=bool(changed_fields or preparation_updates),
            current_digest=current_digest,
            replacement_digest=replacement_digest,
            before_name=current.name,
            after_name=replacement.name,
            changed_fields=changed_fields,
            affected_requirement_refs=sorted(set(affected_refs)),
            affected_preparation_inputs=sorted(
                str(repo_root) for repo_root in preparation_updates
            ),
            affected_runtime_flows=sorted(set(affected_flows)),
            affected_provider_groups=affected_groups,
            blockers=blockers,
            checkpoint_required=consumer_runtime is not None,
            summary=(
                f"Requirement update preview found {len(changed_fields)} changed fields "
                f"and {len(blockers)} blockers."
            ),
        )
        if input_model.dry_run or not impact.changed:
            return self.runtime.foundation.ok(impact)
        if blockers:
            return self.runtime.foundation.fail(blockers)

        checkpoint_id: str | None = None
        if consumer_runtime is not None:
            checkpoint = (
                consumer_runtime.app.snapshot_runtime.create_repo_stable_point_snapshot(
                    consumer_root,
                    checkpoint_kind="manual_test_stable_point",
                    label=(
                        f"before requirement update {consumer_repo}:{current_name}"
                    ),
                    scope_ids=[f"repo:{consumer_repo}"],
                )
            )
            if not checkpoint.ok or checkpoint.value is None:
                return self.runtime.foundation.fail(checkpoint.issues)
            checkpoint_id = checkpoint.value.snapshot_id

        old_path = self.runtime.foundation.layout.requirement_path(
            FoundationContext(repo_root=consumer_root),
            current_name,
        )
        new_path = self.runtime.foundation.layout.requirement_path(
            FoundationContext(repo_root=consumer_root),
            replacement.name,
        )
        if new_path != old_path and new_path.exists():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_name_duplicate",
                    f"Requirement already exists: {replacement.name}",
                    object_ref=str(new_path),
                )
            )
        with self.runtime.foundation.store.mutation(
            "admin_update_repo_requirement"
        ) as mutation:
            mutation.stage_json(
                new_path,
                replacement,
                mode=(
                    WriteMode.UPDATE_EXISTING
                    if new_path == old_path
                    else WriteMode.CREATE_ONLY
                ),
            )
            if new_path != old_path:
                mutation.stage_delete(old_path)
            for repo_root, updated in preparation_updates.items():
                preparation_path = (
                    self.runtime.foundation.layout.preparation_input_path(
                        FoundationContext(repo_root=repo_root)
                    )
                )
                mutation.stage_json(
                    preparation_path,
                    updated,
                    mode=WriteMode.UPDATE_EXISTING,
                )
            committed = mutation.commit()
        if not committed.ok:
            return self.runtime.foundation.fail(committed.issues)
        reloaded = self.runtime.repo_workspace.requirement.get_requirement(
            consumer_root,
            name=replacement.name,
        )
        if (
            not reloaded.ok
            or reloaded.value is None
            or self.runtime.repo_workspace.requirement.requirement_digest(
                reloaded.value.requirement
            )
            != replacement_digest
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_update_post_validation_failed",
                    "Updated requirement did not reload with the expected digest.",
                    object_ref=f"{consumer_repo}:{replacement.name}",
                )
            )
        return self.runtime.foundation.ok(
            impact.model_copy(
                update={
                    "applied": True,
                    "checkpoint_id": checkpoint_id,
                    "summary": (
                        f"Updated requirement {consumer_repo}:{current_name} "
                        f"to {replacement.name}."
                    ),
                }
            )
        )

    def _verify_requirement_adapter_route(
        self,
        route,
    ) -> ServiceResult[VerifiedAdapterRouteReceipt | None]:
        if not isinstance(route, AdapterProviderRoute):
            return self.runtime.foundation.ok(None)
        verified = self.runtime.repo_workspace.verify_adapter_provider_route(route)
        if not verified.ok or verified.value is None:
            return self.runtime.foundation.fail(verified.issues)
        return self.runtime.foundation.ok(verified.value)

    def start_native_preparation(self, input_model: StartPreparationInput) -> ServiceResult[AdminFlowStartView]:
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(input_model.repo_root):
                return self._start_native_preparation_locked(input_model)
        except RepoLifecycleLockBusyError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_lifecycle_lock_busy", str(exc), object_ref=str(input_model.repo_root)
            ))

    def start_initial_native_repo_run(self, input_model: RepoRunStartInput) -> ServiceResult[AdminFlowStartView]:
        return self.start_native_preparation(StartPreparationInput(
            repo_root=input_model.repo_root,
            repo_key=input_model.repo_key,
            start_reason="admin",
            admin_notes=input_model.admin_notes,
            enqueue=input_model.enqueue,
            run_request=input_model.request,
        ))

    def preview_native_source_index_recovery(
        self,
        input_model: NativeSourceIndexRecoveryPreviewInput,
    ) -> ServiceResult[NativeSourceIndexRecoveryContract]:
        repo_key = input_model.repo_key or input_model.repo_root.name
        return self.runtime.repo_workspace.native_source_index_recovery.preview(
            input_model.repo_root,
            repo_key=repo_key,
            failed_parent_flow_id=input_model.failed_parent_flow_id,
        )

    def recover_native_source_index(
        self,
        input_model: NativeSourceIndexRecoveryStartInput,
    ) -> ServiceResult[AdminFlowStartView]:
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(input_model.repo_root):
                return self._recover_native_source_index_locked(input_model)
        except RepoLifecycleLockBusyError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "repo_lifecycle_lock_busy",
                    str(exc),
                    object_ref=str(input_model.repo_root),
                )
            )

    def _recover_native_source_index_locked(
        self,
        input_model: NativeSourceIndexRecoveryStartInput,
    ) -> ServiceResult[AdminFlowStartView]:
        repo_key = input_model.repo_key or input_model.repo_root.name
        preview = self.runtime.repo_workspace.native_source_index_recovery.preview(
            input_model.repo_root,
            repo_key=repo_key,
            failed_parent_flow_id=input_model.failed_parent_flow_id,
        )
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        recovery = preview.value
        if recovery.recovery_token != input_model.expected_recovery_token:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "native_source_index_recovery_token_mismatch",
                    "The failed lineage or rejected draft changed after recovery preview.",
                    object_ref=input_model.failed_parent_flow_id,
                    current=recovery.recovery_token,
                    expected=input_model.expected_recovery_token,
                )
            )
        parent = self.runtime.ark.flow_service.get_flow(input_model.failed_parent_flow_id)
        run_spec = getattr(getattr(parent, "input", None), "run_spec", None)
        if not isinstance(run_spec, RepoRunSpec):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "native_source_index_recovery_run_spec_missing",
                    "The failed native preparation parent does not retain its RepoRunSpec.",
                    object_ref=input_model.failed_parent_flow_id,
                )
            )
        return self.start_arbitrary_flow(
            StartFlowInput(
                flow_type="native_repo_preparation",
                scope_id=f"repo:{repo_key}",
                enqueue=input_model.enqueue,
                params={
                    "repo_key": repo_key,
                    "repo_root": str(Path(input_model.repo_root).resolve(strict=False)),
                    "start_reason": "repair_resume",
                    "admin_notes": (
                        "Fail-closed SourceIndex successor recovery from "
                        f"{recovery.failed_parent_flow_id}."
                    ),
                    "run_spec": run_spec.model_dump(mode="json"),
                    "recovery": recovery.model_dump(mode="json"),
                },
            ),
            repo_root=str(input_model.repo_root),
        )

    def start_native_repo_continuation(self, input_model: RepoRunRequestInput) -> ServiceResult[AdminFlowStartView]:
        return self.continue_native_repo(input_model)

    def start_source_index_run(self, input_model: StandaloneSourceIndexRunInput) -> ServiceResult[AdminFlowStartView]:
        return self.start_standalone_source_index(input_model)

    def start_root_interface_run(self, input_model: StandaloneRootInterfaceRunInput) -> ServiceResult[AdminFlowStartView]:
        return self.start_standalone_root_interfaces(input_model)

    def _start_native_preparation_locked(self, input_model: StartPreparationInput) -> ServiceResult[AdminFlowStartView]:
        repo_key = input_model.repo_key or input_model.repo_root.name
        active = [flow for flow in self.runtime.ark.flow_service.list_flows(scope_id=f"repo:{repo_key}")
                  if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}]
        if active:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_lifecycle_flow_conflict", "A repo lifecycle Flow is already active.", object_ref=active[0].flow_id
            ))
        preparation = self.runtime.repo_workspace.preparation.get_preparation_input(input_model.repo_root)
        origin = (
            "requirement_provider"
            if preparation.ok and preparation.value is not None and preparation.value.input.requirement_refs
            else "main"
        )
        request = input_model.run_request or RepoRunOptions()
        resolved = self.runtime.repo_workspace.run.resolve_initial_repo_run_spec(
            input_model.repo_root, origin=origin,
            run_objective=request.run_objective,
            completion_mode=request.completion_mode,
            source_scope=request.source_scope,
            index_policy=request.index_policy, root_interface_policy=request.root_interface_policy,
            max_parallel_content_node_tasks=request.max_parallel_content_node_tasks,
            additional_required_interfaces=request.additional_required_interfaces,
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        return self.start_arbitrary_flow(
            StartFlowInput(
                flow_type="native_repo_preparation", scope_id=f"repo:{repo_key}", enqueue=input_model.enqueue,
                params={"repo_key": repo_key, "repo_root": str(input_model.repo_root),
                        "start_reason": input_model.start_reason, "admin_notes": input_model.admin_notes,
                        "run_spec": resolved.value.model_dump(mode="json")},
            ), repo_root=str(input_model.repo_root),
        )

    def continue_native_repo(self, input_model: RepoRunRequestInput) -> ServiceResult[AdminFlowStartView]:
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(input_model.repo_root):
                return self._continue_native_repo_locked(input_model)
        except RepoLifecycleLockBusyError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_lifecycle_lock_busy", str(exc), object_ref=str(input_model.repo_root)
            ))

    def _continue_native_repo_locked(self, input_model: RepoRunRequestInput) -> ServiceResult[AdminFlowStartView]:
        repo_key = input_model.repo_key or input_model.repo_root.name
        active = [flow for flow in self.runtime.ark.flow_service.list_flows(scope_id=f"repo:{repo_key}")
                  if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}]
        if active:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_lifecycle_flow_conflict", "A repo lifecycle Flow is already active.", object_ref=active[0].flow_id
            ))
        resolved = self.runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
            input_model.repo_root, run_objective=input_model.run_objective,
            completion_mode=input_model.completion_mode,
            source_scope=input_model.source_scope,
            index_policy=input_model.index_policy, root_interface_policy=input_model.root_interface_policy,
            max_parallel_content_node_tasks=input_model.max_parallel_content_node_tasks,
            additional_required_interfaces=input_model.additional_required_interfaces,
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(input_model.repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        base = publication.value.publication.latest_release_id
        if base is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "continuation_release_baseline_missing", "Native continuation requires a latest release."
            ))
        return self.start_arbitrary_flow(StartFlowInput(
            flow_type="native_repo_continuation", scope_id=f"repo:{repo_key}", enqueue=input_model.enqueue,
            params={"repo_key": repo_key, "repo_root": str(input_model.repo_root),
                    "run_spec": resolved.value.model_dump(mode="json"), "base_release_id": base,
                    "start_reason": "admin_continue"}), repo_root=str(input_model.repo_root))

    def get_repo_run_status(self, repo_root: Path, *, repo_key: str | None = None) -> ServiceResult[RepoRunStatusView]:
        key = repo_key or Path(repo_root).name
        publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
        if not publication.ok or publication.value is None:
            return self.runtime.foundation.fail(publication.issues)
        active = [flow for flow in self.runtime.ark.flow_service.list_flows(scope_id=f"repo:{key}")
                  if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}]
        flow = sorted(active, key=lambda item: item.created_at or "")[-1] if active else None
        run_spec = getattr(getattr(flow, "input", None), "run_spec", None)
        return self.runtime.foundation.ok(RepoRunStatusView(
            repo_root=str(repo_root), publication_status=publication.value.publication.status.value,
            latest_release_id=publication.value.publication.latest_release_id,
            active_flow_id=flow.flow_id if flow else None, active_flow_type=flow.flow_type if flow else None,
            run_spec=run_spec, summary="Derived current repo run status.",
        ))

    def start_standalone_source_index(self, input_model: StandaloneSourceIndexRunInput) -> ServiceResult[AdminFlowStartView]:
        repo_key = input_model.repo_key or input_model.repo_root.name
        spec = self.runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
            input_model.repo_root, run_objective=input_model.run_objective,
            source_scope=input_model.source_scope, index_policy=input_model.index_policy,
            root_interface_policy="reuse",
        )
        if not spec.ok or spec.value is None:
            return self.runtime.foundation.fail(spec.issues)
        return self._start_standalone_native_run(
            input_model.repo_root,
            repo_key=repo_key,
            run_spec=spec.value,
            flow_type="source_index_build",
            enqueue=input_model.enqueue,
            params_factory=lambda _base, checkpoint_id: {
                "repo_key": repo_key, "repo_root": str(input_model.repo_root),
                "run_objective": spec.value.run_objective,
                "source_scope": spec.value.source_scope.model_dump(mode="json"),
                "index_policy": spec.value.index_policy, "start_reason": "admin_preprocess",
                "pre_update_checkpoint_id": checkpoint_id,
            },
        )

    def start_standalone_root_interfaces(self, input_model: StandaloneRootInterfaceRunInput) -> ServiceResult[AdminFlowStartView]:
        repo_key = input_model.repo_key or input_model.repo_root.name
        spec = self.runtime.repo_workspace.run.resolve_continuation_repo_run_spec(
            input_model.repo_root, run_objective=input_model.run_objective,
            source_scope=SourceScope(mode="none"), index_policy="reuse",
            root_interface_policy=input_model.root_interface_policy,
            additional_required_interfaces=input_model.additional_required_interfaces,
        )
        if not spec.ok or spec.value is None:
            return self.runtime.foundation.fail(spec.issues)
        source_delta = SourceIndexBuildResult(outcome="no_op", repo_key=repo_key, summary="Standalone root preparation reuses SourceIndex.")
        return self._start_standalone_native_run(
            input_model.repo_root,
            repo_key=repo_key,
            run_spec=spec.value,
            flow_type="root_interface_preparation",
            enqueue=input_model.enqueue,
            params_factory=lambda base, checkpoint_id: {
                "repo_key": repo_key, "repo_root": str(input_model.repo_root),
                "run_context": RepoRunContext(
                    start_kind="continuation", run_spec=spec.value, base_release_id=base
                ).model_dump(mode="json"),
                "source_index_delta": source_delta.model_dump(mode="json"),
                "start_reason": "admin_preprocess",
                "pre_run_mutation_checkpoint_id": checkpoint_id,
            },
        )

    def _start_standalone_native_run(
        self,
        repo_root: Path,
        *,
        repo_key: str,
        run_spec: RepoRunSpec,
        flow_type: str,
        enqueue: bool,
        params_factory: Callable[[str, str], dict[str, Any]],
    ) -> ServiceResult[AdminFlowStartView]:
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                active = [flow for flow in self.runtime.ark.flow_service.list_flows(scope_id=f"repo:{repo_key}")
                          if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}]
                if active:
                    return self.runtime.foundation.fail(self.runtime.foundation.issue(
                        "repo_lifecycle_flow_conflict", "A repo lifecycle Flow is already active.", object_ref=active[0].flow_id
                    ))
                publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
                if not publication.ok or publication.value is None or publication.value.publication.latest_release_id is None:
                    return self.runtime.foundation.fail(publication.issues or [self.runtime.foundation.issue(
                        "continuation_release_baseline_missing", "Standalone preprocessing requires a latest release."
                    )])
                base = publication.value.publication.latest_release_id
                gate = self.runtime.repo_workspace.run.validate_repo_run_transition(
                    repo_root, run_spec=run_spec, start_kind="standalone_preprocess", base_release_id=base
                )
                if not gate.ok or gate.value is None:
                    return self.runtime.foundation.fail(gate.issues)
                if not gate.value.passed:
                    return self.runtime.foundation.fail(gate.value.issues)
                snapshot = self.runtime.app.snapshot_runtime.create_repo_stable_point_snapshot(
                    repo_root, checkpoint_kind=RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
                    label=f"before standalone native preprocessing for {repo_key}", scope_ids=[f"repo:{repo_key}"],
                )
                if not snapshot.ok or snapshot.value is None:
                    return self.runtime.foundation.fail(snapshot.issues)
                if publication.value.publication.status.value == "stable":
                    transitioned = self.runtime.repo_workspace.metadata.mark_repo_developing(repo_root)
                    if not transitioned.ok:
                        return self.runtime.foundation.fail(transitioned.issues)
                # Flow truth is created before releasing the lifecycle lock. If creation
                # fails, no active owner exists; the developing repo remains safely
                # retryable from the same release baseline and checkpoint evidence.
                return self.start_arbitrary_flow(
                    StartFlowInput(
                        flow_type=flow_type,
                        scope_id=f"repo:{repo_key}",
                        enqueue=enqueue,
                        params=params_factory(base, snapshot.value.snapshot_id),
                    ),
                    repo_root=str(repo_root),
                )
        except RepoLifecycleLockBusyError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_lifecycle_lock_busy", str(exc), object_ref=str(repo_root)
            ))

    def list_repo_releases(self, repo_root: Path) -> ServiceResult[RepoReleaseListView]:
        listed = self.runtime.repo_workspace.release.list_releases(repo_root)
        if not listed.ok or listed.value is None:
            return self.runtime.foundation.fail(listed.issues)
        return self.runtime.foundation.ok(RepoReleaseListView(
            repo_root=str(repo_root),
            releases=listed.value,
            summary=f"Listed {len(listed.value)} repository releases.",
        ))

    def get_repo_release(self, repo_root: Path, *, release_id: str):  # noqa: ANN201
        return self.runtime.repo_workspace.release.get_release(
            repo_root, release_id=release_id
        )

    def preview_repo_release(self, repo_root: Path, *, summary: str = "Admin release preview."):  # noqa: ANN201
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                publication = self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)
                if not publication.ok or publication.value is None:
                    return self.runtime.foundation.fail(publication.issues)
                return self.runtime.validation_snapshot.release_finalizer.preview_candidate_release(
                    repo_root,
                    base_release_id=publication.value.publication.latest_release_id,
                    summary=summary,
                )
        except RepoLifecycleLockBusyError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_lifecycle_lock_busy", str(exc), object_ref=str(repo_root)
            ))

    def preview_repo_release_restore(
        self,
        input_model: RepoReleaseIdInput,
    ):  # noqa: ANN201
        safe = self._check_release_restore_runtime_safe()
        if not safe.ok:
            return self.runtime.foundation.fail(safe.issues)
        return (
            self.runtime.validation_snapshot.release_finalizer.preview_repo_release_restore(
                input_model.repo_root,
                release_id=input_model.release_id,
            )
        )

    def apply_repo_release_restore(
        self,
        input_model: RepoReleaseRestoreApplyInput,
    ):  # noqa: ANN201
        safe = self._check_release_restore_runtime_safe()
        if not safe.ok:
            return self.runtime.foundation.fail(safe.issues)
        preview = (
            self.runtime.validation_snapshot.release_finalizer.preview_repo_release_restore(
                input_model.repo_root,
                release_id=input_model.release_id,
            )
        )
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        return (
            self.runtime.validation_snapshot.release_finalizer.apply_repo_release_restore(
                input_model.repo_root,
                preview=preview.value,
                expected_recovery_token=input_model.expected_recovery_token,
            )
        )

    def prepare_repo_publication(
        self,
        input_model: RepoPublicationPrepareInput,
    ):  # noqa: ANN201
        latest = self.runtime.repo_workspace.release.get_latest_release(
            input_model.repo_root
        )
        release_id = (
            latest.value.release.release_id
            if latest.ok and latest.value is not None
            else None
        )
        semantic_digest = (
            latest.value.release.semantic_manifest_digest
            if latest.ok and latest.value is not None
            else None
        )
        generated_at = (
            latest.value.release.created_at
            if latest.ok and latest.value is not None
            else None
        )
        return self.runtime.repo_workspace.publication.prepare_publication(
            input_model.repo_root,
            title=input_model.title,
            presentation=input_model.presentation,
            release_id=release_id,
            semantic_manifest_digest=semantic_digest,
            generated_at=generated_at,
        )

    def preview_repo_remote_publication(
        self,
        input_model: RepoRemotePublicationInput,
    ):  # noqa: ANN201
        return self.runtime.repo_workspace.remote_publication.preview(
            input_model.repo_root,
            release_id=input_model.release_id,
        )

    def apply_repo_remote_publication(
        self,
        input_model: RepoRemotePublicationInput,
    ):  # noqa: ANN201
        if input_model.expected_recovery_token is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "remote_publication_token_required",
                    "Remote publication apply requires an exact preview token.",
                    object_ref=input_model.release_id,
                )
            )
        preview = self.runtime.repo_workspace.remote_publication.preview(
            input_model.repo_root,
            release_id=input_model.release_id,
        )
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        return self.runtime.repo_workspace.remote_publication.apply(
            input_model.repo_root,
            preview=preview.value,
            expected_recovery_token=input_model.expected_recovery_token,
            push=input_model.push,
        )

    def preview_repo_github_topics(
        self,
        input_model: RepoGitHubTopicsInput,
    ):  # noqa: ANN201
        return self.runtime.repo_workspace.github_topics.preview(
            input_model.repo_root,
            remote_name=input_model.remote_name,
        )

    def apply_repo_github_topics(
        self,
        input_model: RepoGitHubTopicsInput,
    ):  # noqa: ANN201
        if input_model.expected_recovery_token is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "github_topics_token_required",
                    "GitHub topics apply requires an exact preview token.",
                    object_ref=input_model.repo_root.name,
                )
            )
        return self.runtime.repo_workspace.github_topics.apply(
            input_model.repo_root,
            remote_name=input_model.remote_name,
            expected_recovery_token=input_model.expected_recovery_token,
        )

    def preview_repo_dependency_change(
        self,
        input_model: RepoDependencyChangeInput,
    ):  # noqa: ANN201
        return self.runtime.repo_workspace.dependency_release.preview(
            input_model.repo_root,
            provider_repo_key=input_model.provider_repo_key,
            target_provider_release_id=input_model.target_provider_release_id,
            target_git_url=input_model.target_git_url,
            release_mode=input_model.release_mode,
            validation_profile=input_model.validation_profile,
        )

    def apply_repo_dependency_change(
        self,
        input_model: RepoDependencyChangeInput,
    ):  # noqa: ANN201
        if input_model.expected_recovery_token is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "dependency_change_token_required",
                    "Dependency change apply requires an exact preview token.",
                    object_ref=input_model.provider_repo_key,
                )
            )
        preview = self.preview_repo_dependency_change(input_model)
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        return self.runtime.repo_workspace.dependency_release.apply(
            input_model.repo_root,
            preview=preview.value,
            expected_recovery_token=input_model.expected_recovery_token,
        )

    def preview_workspace_publication(
        self,
        input_model: WorkspacePublicationInput,
    ):  # noqa: ANN201
        return self.runtime.repo_workspace.workspace_publication.preview(
            input_model.workspace_root,
            repo_keys=input_model.repo_keys,
            output_root=input_model.output_root,
            push_children=input_model.push_children,
            push_superproject=input_model.push_superproject,
        )

    def apply_workspace_publication(
        self,
        input_model: WorkspacePublicationInput,
    ):  # noqa: ANN201
        if input_model.expected_recovery_token is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_publication_token_required",
                    "Workspace publication apply requires an exact preview token.",
                    object_ref=str(input_model.workspace_root),
                )
            )
        preview = self.preview_workspace_publication(input_model)
        if not preview.ok or preview.value is None:
            return self.runtime.foundation.fail(preview.issues)
        return self.runtime.repo_workspace.workspace_publication.apply(
            input_model.workspace_root,
            preview=preview.value,
            expected_recovery_token=input_model.expected_recovery_token,
        )

    def _check_release_restore_runtime_safe(self) -> ServiceResult[bool]:
        controller = self.runtime.ark.pause_controller
        if controller is None:
            return self.runtime.foundation.ok(True)
        if not hasattr(controller, "is_paused") or not controller.is_paused():
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_restore_runtime_not_paused",
                    "Release restore requires a paused or unloaded runtime.",
                )
            )
        queues = self._candidate_queue_view()
        if (
            queues.active_flow_advances
            or queues.running_step_ids
            or queues.created_step_ids
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "release_restore_runtime_not_quiescent",
                    "Release restore requires no active Flow advance or running/created Step.",
                    details={
                        "active_flow_advances": ",".join(
                            queues.active_flow_advances
                        ),
                        "running_step_ids": ",".join(queues.running_step_ids),
                        "created_step_ids": ",".join(queues.created_step_ids),
                    },
                )
            )
        return self.runtime.foundation.ok(True)

    def audit_repo_releases(self, repo_root: Path):  # noqa: ANN201
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                return self.runtime.validation_snapshot.release_finalizer.audit_repo_release_storage(repo_root)
        except RepoLifecycleLockBusyError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_lifecycle_lock_busy", str(exc), object_ref=str(repo_root)
            ))

    def cleanup_repo_release_orphans(self, input_model: RepoReleaseOrphanCleanupInput):  # noqa: ANN201
        return self.runtime.validation_snapshot.cleanup_repo_release_orphans(
            input_model.repo_root,
            expected_audit_digest=input_model.expected_audit_digest,
        )

    def reconcile_repo_requirements(self, repo_root: Path, *, release_id: str):  # noqa: ANN201
        try:
            with self.runtime.repo_workspace.lifecycle_lock.locked(repo_root):
                return self.runtime.validation_snapshot.release_finalizer.reconcile_provider_requirements(
                    repo_root, release_id=release_id
                )
        except RepoLifecycleLockBusyError as exc:
            return self.runtime.foundation.fail(self.runtime.foundation.issue(
                "repo_lifecycle_lock_busy", str(exc), object_ref=str(repo_root)
            ))

    def start_adapter_preparation(self, input_model: StartPreparationInput) -> ServiceResult[AdminFlowStartView]:
        repo_key = input_model.repo_key or input_model.repo_root.name
        return self.start_arbitrary_flow(
            StartFlowInput(
                flow_type="adapter_repo_preparation",
                scope_id=f"repo:{repo_key}",
                enqueue=input_model.enqueue,
                params={
                    "repo_key": repo_key,
                    "repo_root": str(input_model.repo_root),
                    "start_reason": input_model.start_reason,
                    "admin_notes": input_model.admin_notes,
                },
            ),
            repo_root=str(input_model.repo_root),
        )

    def create_main_repo_shell(self, input_model: CreateMainRepoShellInput) -> ServiceResult[RepoShellView]:
        return self.runtime.repo_workspace.create_main_repo_shell(
            input_model.workspace_root,
            repo_name=input_model.repo_name,
            project_name=input_model.project_name,
        )

    def write_main_repo_preparation_input(
        self,
        input_model: WriteMainRepoPreparationInput,
    ) -> ServiceResult[RepoPreparationInputView]:
        return self.runtime.repo_workspace.write_preparation_input(
            input_model.repo_root,
            input=input_model.input,
        )

    def validate_main_source_corpus(
        self,
        input_model: ValidateMainSourceCorpusInput,
    ) -> ServiceResult[MainSourceCorpusValidationView]:
        loaded = self.runtime.repo_workspace.preparation.get_preparation_input(input_model.repo_root)
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        preparation = loaded.value.input
        issues: list[ServiceIssue] = []
        source_path: Path | None = None
        exists = False
        file_count = 0
        draft_gate: GateReport | None = None
        if preparation.source_corpus_mode == SourceCorpusMode.NONE:
            issues.append(
                self.runtime.foundation.issue(
                    "main_source_corpus_none",
                    "Main native repo preparation requires source_corpus_mode to be existing or prepare.",
                    field="source_corpus_mode",
                    current=SourceCorpusMode.NONE.value,
                    expected="existing|prepare",
                )
            )
        else:
            try:
                source_path = self.runtime.foundation.layout.source_corpus_root(
                    FoundationContext(repo_root=input_model.repo_root),
                    preparation.source_corpus_relpath or ".lean_constellation/source",
                )
            except ValueError as exc:
                issues.append(
                    self.runtime.foundation.issue(
                        "main_source_corpus_path_invalid",
                        f"Main source corpus path is invalid: {exc}",
                        field="source_corpus_relpath",
                        current=preparation.source_corpus_relpath,
                    )
                )
            if source_path is not None:
                exists = source_path.exists() and source_path.is_dir()
                if not exists:
                    issues.append(
                        self.runtime.foundation.issue(
                            "main_source_corpus_missing",
                            "Main source corpus directory is missing.",
                            object_ref=str(source_path),
                        )
                    )
                else:
                    file_count = sum(1 for item in source_path.rglob("*") if item.is_file())
                    if input_model.require_files and file_count == 0:
                        issues.append(
                            self.runtime.foundation.issue(
                                "main_source_corpus_empty",
                                "Main source corpus directory contains no files.",
                                object_ref=str(source_path),
                            )
                        )
                    if input_model.check_draft_gate:
                        checked = self.runtime.material.check_source_corpus_draft(
                            input_model.repo_root,
                            relpath=preparation.source_corpus_relpath or ".lean_constellation/source",
                            entry_path=input_model.entry_path,
                        )
                        if not checked.ok or checked.value is None:
                            issues.extend(checked.issues)
                        else:
                            draft_gate = checked.value
                            if not draft_gate.passed:
                                issues.extend(draft_gate.issues)
        passed = not issues
        view = MainSourceCorpusValidationView(
            repo_root=str(input_model.repo_root),
            source_corpus_mode=preparation.source_corpus_mode,
            source_corpus_relpath=preparation.source_corpus_relpath,
            source_corpus_path=str(source_path) if source_path is not None else None,
            exists=exists,
            file_count=file_count,
            draft_gate=draft_gate,
            passed=passed,
            issues=issues,
            summary="Main source corpus validation passed." if passed else f"Main source corpus validation found {len(issues)} issues.",
        )
        if passed:
            return self.runtime.foundation.ok(view)
        return self.runtime.foundation.fail(issues)

    def initialize_main_native_skeleton(
        self,
        input_model: InitializeMainNativeSkeletonInput,
    ) -> ServiceResult[RepoSkeletonView]:
        return self.runtime.repo_workspace.initialize_repo_as_native(
            input_model.repo_root,
            project_name=input_model.project_name or input_model.repo_root.name,
        )

    def get_repo_config(self, repo_root: Path) -> ServiceResult[RepoConfigView]:
        return self.runtime.repo_workspace.metadata.get_repo_config(repo_root)

    def update_repo_config(self, input_model: RepoConfigUpdateInput) -> ServiceResult[RepoConfigView]:
        return self.runtime.repo_workspace.metadata.update_repo_config(
            input_model.repo_root,
            completion_mode=input_model.completion_mode,
            default_requirement_proof_availability=input_model.default_requirement_proof_availability,
            publication=input_model.publication,
        )

    def get_repo_publication(self, repo_root: Path) -> ServiceResult[RepoPublicationView]:
        return self.runtime.repo_workspace.metadata.get_repo_publication(repo_root)

    def bootstrap_main_native_repo(
        self,
        input_model: BootstrapMainNativeRepoInput,
    ) -> ServiceResult[MainNativeRepoBootstrapView]:
        shell = self.runtime.repo_workspace.create_main_repo_shell(
            input_model.workspace_root,
            repo_name=input_model.repo_name,
            project_name=input_model.project_name,
        )
        if not shell.ok or shell.value is None:
            return self.runtime.foundation.fail(shell.issues)
        repo_root = Path(shell.value.repo_root)
        written = self.write_main_repo_preparation_input(
            WriteMainRepoPreparationInput(repo_root=repo_root, input=input_model.preparation_input)
        )
        if not written.ok or written.value is None:
            return self.runtime.foundation.fail(written.issues)
        source_validation = None
        if input_model.validate_source_corpus:
            validated = self.validate_main_source_corpus(ValidateMainSourceCorpusInput(repo_root=repo_root))
            if not validated.ok or validated.value is None:
                return self.runtime.foundation.fail(validated.issues)
            source_validation = validated.value
        skeleton = self.initialize_main_native_skeleton(
            InitializeMainNativeSkeletonInput(repo_root=repo_root, project_name=input_model.project_name)
        )
        if not skeleton.ok or skeleton.value is None:
            return self.runtime.foundation.fail(skeleton.issues)
        preparation = self.start_native_preparation(
            StartPreparationInput(
                repo_root=repo_root,
                repo_key=input_model.repo_name,
                start_reason="admin",
                admin_notes="Started by main native repo bootstrap.",
                enqueue=input_model.enqueue,
                run_request=input_model.run_request,
            )
        )
        if not preparation.ok or preparation.value is None:
            return self.runtime.foundation.fail(preparation.issues)
        return self.runtime.foundation.ok(
            MainNativeRepoBootstrapView(
                shell=shell.value,
                preparation_input=written.value,
                source_corpus_validation=source_validation,
                skeleton=skeleton.value,
                preparation_flow=preparation.value,
                summary=f"Bootstrapped main native repo {input_model.repo_name}.",
            )
        )

    def start_arbitrary_flow(
        self,
        input_model: StartFlowInput,
        *,
        repo_root: str | None = None,
    ) -> ServiceResult[AdminFlowStartView]:
        try:
            request = FlowRequest(
                flow_type=input_model.flow_type,
                scope_id=input_model.scope_id,
                params=dict(input_model.params),
            )
            flow_id = self.runtime.ark.flow_service.start_flow(request, enqueue=input_model.enqueue)
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("admin_start_flow_failed", f"Failed to start flow: {exc}")
            )
        return self.runtime.foundation.ok(
            AdminFlowStartView(
                flow_id=flow_id,
                flow_type=input_model.flow_type,
                scope_id=input_model.scope_id,
                enqueued=input_model.enqueue,
                repo_root=repo_root,
                summary=f"Started flow {input_model.flow_type}.",
            )
        )

    def pause_runtime(self, *, scope_id: str | None = None) -> ServiceResult[RuntimePauseView]:
        controller = self.runtime.ark.pause_controller
        if controller is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("pause_controller_missing", "ARK pause controller is not configured."))
        controller.pause(scope_id)
        schedule_service = self.runtime.ark.schedule_service
        if scope_id is None and schedule_service is not None and hasattr(schedule_service, "clear_run_budget"):
            schedule_service.clear_run_budget(reason="manual_pause")
        return self.runtime.foundation.ok(
            RuntimePauseView(
                paused=True,
                scope_id=scope_id,
                run_control=self._run_control_view(),
                summary="Paused runtime scheduling.",
            )
        )

    def resume_runtime(
        self,
        input_model: RuntimeResumeInput | None = None,
        *,
        scope_id: str | None = None,
    ) -> ServiceResult[RuntimePauseView]:
        if input_model is not None and scope_id is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "runtime_resume_input_conflict",
                    "Provide either RuntimeResumeInput or scope_id, not both.",
                )
            )
        request = input_model or RuntimeResumeInput(scope_id=scope_id, unbounded=True)
        controller = self.runtime.ark.pause_controller
        if controller is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("pause_controller_missing", "ARK pause controller is not configured."))
        schedule_service = self.runtime.ark.schedule_service
        if schedule_service is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("schedule_service_missing", "ARK schedule service is not configured.")
            )
        if request.budget is not None:
            admission = self._validate_bounded_resume_admission()
            if not admission.ok:
                return self.runtime.foundation.fail(admission.issues)
        was_paused = bool(controller.is_paused(request.scope_id)) if hasattr(controller, "is_paused") else False
        try:
            if request.budget is None:
                schedule_service.clear_run_budget()
            else:
                schedule_service.configure_run_budget(request.budget)
            if not request.skip_rebuild:
                schedule_service.rebuild_candidate_queues(scope_id=request.scope_id)
            controller.resume(request.scope_id)
        except Exception as exc:  # noqa: BLE001 - admin mutation boundary.
            if was_paused:
                controller.pause(request.scope_id)
            else:
                controller.resume(request.scope_id)
            if hasattr(schedule_service, "clear_run_budget"):
                schedule_service.clear_run_budget(reason="resume_failed")
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "runtime_resume_failed",
                    f"Failed to resume runtime scheduling: {exc}",
                )
            )
        return self.runtime.foundation.ok(
            RuntimePauseView(
                paused=False,
                scope_id=request.scope_id,
                run_control=self._run_control_view(),
                summary="Resumed runtime scheduling.",
            )
        )

    def semantic_advance(self, input_model: RuntimeSemanticAdvanceInput) -> ServiceResult[RuntimePauseView]:
        controller = self.runtime.ark.pause_controller
        schedule_service = self.runtime.ark.schedule_service
        step_service = self.runtime.ark.step_service
        if controller is None or schedule_service is None or step_service is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "semantic_advance_runtime_missing",
                    "Semantic advance requires pause, schedule, and step services.",
                )
            )
        if not controller.is_paused(None):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "semantic_advance_requires_global_pause",
                    "Production semantic advance requires the repo runtime to be globally paused.",
                )
            )
        running_steps = step_service.list_running_steps()
        if running_steps:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "semantic_advance_running_steps",
                    "Production semantic advance cannot start while Steps are running.",
                    details={"step_ids": [step.step_id for step in running_steps]},
                )
            )
        try:
            policy = build_semantic_run_policy(self.runtime, input_model)
            run_control = schedule_service.configure_semantic_run(policy)
            if run_control.lease_id is not None:
                register_semantic_lease_observation(schedule_service, run_control.lease_id, input_model)
            schedule_service.rebuild_candidate_queues()
            controller.resume(None)
        except Exception as exc:  # noqa: BLE001 - Admin mutation boundary.
            schedule_service.clear_run_budget(reason="semantic_advance_admission_failed")
            controller.pause(None)
            kind = "semantic_advance_invalid" if isinstance(exc, SemanticAdvancePolicyError) else "semantic_advance_failed"
            return self.runtime.foundation.fail(self.runtime.foundation.issue(kind, str(exc)))
        run_control = schedule_service.get_run_control_view()
        lease = schedule_service.get_run_lease(run_control.lease_id) if run_control.lease_id is not None else None
        return self.runtime.foundation.ok(
            RuntimePauseView(
                paused=False,
                run_control=run_control,
                lease_id=run_control.lease_id,
                lease_version=lease.version if lease is not None else None,
                lease_status=lease.status if lease is not None else None,
                summary=f"Started production semantic advance {policy.name}.",
            )
        )

    def get_runtime_lease(self, lease_id: str) -> ServiceResult[RuntimeLeaseMonitorView]:
        schedule_service = self.runtime.ark.schedule_service
        if schedule_service is None or not hasattr(schedule_service, "get_run_lease"):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scheduler_lease_unavailable",
                    "Scheduler run lease inspection is unavailable.",
                )
            )
        try:
            lease = schedule_service.get_run_lease(lease_id)
        except KeyError:
            return self._lease_lost_result(lease_id)
        except Exception as exc:  # noqa: BLE001 - Admin observation boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("scheduler_lease_read_failed", f"Failed to read scheduler lease: {exc}")
            )
        return self.runtime.foundation.ok(self._runtime_lease_monitor_view(lease))

    def wait_runtime_lease(
        self,
        lease_id: str,
        *,
        after_version: int | None = None,
        timeout_s: float = 30.0,
    ) -> ServiceResult[RuntimeLeaseMonitorView]:
        schedule_service = self.runtime.ark.schedule_service
        if schedule_service is None or not hasattr(schedule_service, "wait_run_lease"):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "scheduler_lease_unavailable",
                    "Scheduler run lease waiting is unavailable.",
                )
            )
        try:
            waited = schedule_service.wait_run_lease(
                lease_id,
                after_version=after_version,
                timeout_s=timeout_s,
            )
        except KeyError:
            return self._lease_lost_result(lease_id)
        except Exception as exc:  # noqa: BLE001 - Admin observation boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("scheduler_lease_wait_failed", f"Failed to wait for scheduler lease: {exc}")
            )
        return self.runtime.foundation.ok(
            self._runtime_lease_monitor_view(waited.lease, timed_out=waited.timed_out)
        )

    def get_runtime_status(self) -> ServiceResult[RuntimeStatusView]:
        paused = False
        controller = self.runtime.ark.pause_controller
        if controller is not None and hasattr(controller, "is_paused"):
            paused = bool(controller.is_paused())
        queues = self._candidate_queue_view()
        return self.runtime.foundation.ok(
            RuntimeStatusView(
                paused=paused,
                test_control_enabled=self.runtime.test_control_enabled,
                run_control=self._run_control_view(),
                flow_candidate_queue=queues.flow_candidate_queue,
                step_candidate_queue=queues.step_candidate_queue,
                queued_flow_ids=queues.queued_flow_ids,
                queued_step_ids=queues.queued_step_ids,
                active_flow_advances=queues.active_flow_advances,
                running_step_ids=queues.running_step_ids,
                created_step_ids=queues.created_step_ids,
                summary="Loaded runtime status.",
            )
        )

    def wait_step_terminal(
        self,
        step_id: str,
        *,
        timeout_s: float = 30.0,
    ) -> ServiceResult[StepTerminalWaitView]:
        if timeout_s < 0 or timeout_s > 300:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "step_terminal_wait_invalid",
                    "Step terminal wait timeout_s must be between 0 and 300 seconds.",
                )
            )
        step_service = self.runtime.ark.step_service
        if step_service is None or not hasattr(step_service, "wait_step_terminal"):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "step_terminal_wait_unavailable",
                    "Production Step terminal waiting is unavailable.",
                )
            )
        try:
            waited = step_service.wait_step_terminal(step_id, timeout_s=timeout_s)
        except KeyError:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "step_not_found",
                    f"Step does not exist in this repo runtime: {step_id}",
                    object_ref=step_id,
                )
            )
        except Exception as exc:  # noqa: BLE001 - Admin observation boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "step_terminal_wait_failed",
                    f"Failed to wait for Step terminal state: {exc}",
                    object_ref=step_id,
                )
            )
        return self.runtime.foundation.ok(
            StepTerminalWaitView(
                step=self._step_monitor_view(waited.step),
                terminal=waited.terminal,
                timed_out=waited.timed_out,
                runner_state=waited.runner_state,
                warning=waited.warning,
                observed_at=waited.observed_at,
                summary=(
                    f"Step {step_id} reached settled terminal state."
                    if waited.terminal
                    else f"Step {step_id} observation returned {waited.runner_state}."
                ),
            )
        )

    def _validate_bounded_resume_admission(self) -> ServiceResult[None]:
        controller = self.runtime.ark.pause_controller
        if controller is None or not hasattr(controller, "is_paused") or not controller.is_paused(None):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "bounded_resume_requires_global_pause",
                    "Bounded scheduler resume requires a globally paused runtime.",
                )
            )
        try:
            agent_service = self.runtime.ark.agent_service
            step_service = self.runtime.ark.step_service
            schedule_service = self.runtime.ark.schedule_service
            if agent_service is None or not hasattr(agent_service, "list_running_agents"):
                raise RuntimeError("agent runtime inspection is unavailable")
            if step_service is None or not hasattr(step_service, "list_running_steps"):
                raise RuntimeError("step runtime inspection is unavailable")
            if schedule_service is None or not hasattr(schedule_service, "active_flow_advances"):
                raise RuntimeError("scheduler runtime inspection is unavailable")
            running_agents = list(agent_service.list_running_agents())
            running_steps = list(step_service.list_running_steps())
            active_advances = list(schedule_service.active_flow_advances)
        except Exception as exc:  # noqa: BLE001 - admission must fail closed.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "bounded_resume_inspection_failed",
                    f"Failed to inspect bounded resume admission: {exc}",
                )
            )
        if running_agents or running_steps or active_advances:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "bounded_resume_runtime_busy",
                    "Bounded scheduler resume requires no running Agent, Step, or Flow advance.",
                    details={
                        "running_agents": len(running_agents),
                        "running_steps": len(running_steps),
                        "active_flow_advances": len(active_advances),
                    },
                )
            )
        return self.runtime.foundation.ok(None)

    def _run_control_view(self) -> SchedulerRunControlView | None:
        schedule_service = self.runtime.ark.schedule_service
        if schedule_service is None or not hasattr(schedule_service, "get_run_control_view"):
            return None
        return schedule_service.get_run_control_view()

    def list_flow_tree(
        self,
        *,
        scope_id: str | None = None,
        include_terminal: bool = True,
    ) -> ServiceResult[FlowTreeMonitorView]:
        try:
            flows = list(self.runtime.ark.flow_service.list_flows(scope_id=scope_id))
            if not include_terminal:
                flows = [
                    flow
                    for flow in flows
                    if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}
                ]
            flow_by_id = {str(flow.flow_id): flow for flow in flows}
            children_by_parent: dict[str, list[Any]] = {}
            roots = []
            for flow in flows:
                parent_id = getattr(flow, "parent_flow_id", None)
                if parent_id and str(parent_id) in flow_by_id:
                    children_by_parent.setdefault(str(parent_id), []).append(flow)
                else:
                    roots.append(flow)
            nodes = [
                self._flow_tree_node_payload(flow, children_by_parent)
                for flow in sorted(roots, key=lambda item: (str(item.created_at), str(item.flow_id)))
            ]
            total_steps = sum(len(getattr(flow, "step_ids", []) or []) for flow in flows)
            return self.runtime.foundation.ok(
                FlowTreeMonitorView(
                    scope_id=scope_id,
                    include_terminal=include_terminal,
                    total_flows=len(flows),
                    total_steps=total_steps,
                    root_count=len(nodes),
                    roots=nodes,
                    summary=f"Loaded {len(flows)} flows and {total_steps} steps.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("flow_tree_monitor_failed", f"Failed to load flow tree: {exc}")
            )

    def get_flow_monitor(self, flow_id: str) -> ServiceResult[FlowMonitorView]:
        try:
            flow = self.runtime.ark.flow_service.get_flow(flow_id)
            return self.runtime.foundation.ok(self._flow_monitor_view(flow, include_steps=True))
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("flow_monitor_failed", f"Failed to load flow monitor view: {exc}")
            )

    def get_step_monitor(self, step_id: str) -> ServiceResult[StepMonitorView]:
        try:
            step = self.runtime.ark.step_service.store.get_step(step_id)
            return self.runtime.foundation.ok(self._step_monitor_view(step))
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("step_monitor_failed", f"Failed to load step monitor view: {exc}")
            )

    def get_content_task_progress(self, flow_id: str) -> ServiceResult[ContentTaskProgressView]:
        try:
            flow = self.runtime.ark.flow_service.get_flow(flow_id)
            if flow.flow_type != "content_node_task":
                raise TypeError(f"flow is not a ContentNodeTask: {flow_id}")
            input_model = flow.input
            state = flow.state
            steps = [
                self.runtime.ark.step_service.store.get_step(step_id)
                for step_id in list(getattr(flow, "step_ids", []) or [])
            ]
            plan_agent_id = None
            for step in reversed(steps):
                if step.step_type == "content_plan_agent_step":
                    plan_agent_id = self._bound_agent_id(step)
                    if plan_agent_id is not None:
                        break

            child_flows = self.runtime.ark.flow_service.store.list_child_flows(parent_flow_id=flow_id)
            active_child = next(
                (child for child in reversed(child_flows) if child.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}),
                None,
            )
            if active_child is None and getattr(state, "completed_child_flow_id", None):
                active_child = next(
                    (child for child in child_flows if child.flow_id == state.completed_child_flow_id),
                    None,
                )
            active_round_id = None
            round_status = None
            active_decl_names: list[str] = []
            current_stage = None
            if active_child is not None and active_child.flow_type == "decl_graph_round":
                active_round_id = getattr(active_child.input, "round_id", None)
                round_status = str(active_child.status)
                active_decl_names = list(getattr(active_child.state, "current_target_decl_names", []) or [])
                current_stage = getattr(active_child.state, "current_stage", None)

            current_step = (
                self.runtime.ark.step_service.store.get_step(flow.current_step_id)
                if flow.current_step_id
                else None
            )
            if active_child is not None and active_child.current_step_id:
                current_step = self.runtime.ark.step_service.store.get_step(active_child.current_step_id)
            latest_checkpoint_id = None
            for step in reversed(steps):
                if getattr(getattr(step, "result", None), "result_type", None) == "content_progress_checkpoint":
                    latest_checkpoint_id = getattr(step.result, "snapshot_id", None)
                    if latest_checkpoint_id:
                        break
            queues = self._candidate_queue_view()
            callback_outcome = getattr(state, "completed_child_outcome", None)
            blocker_summary = getattr(state, "latest_callback_summary", None)
            return self.runtime.foundation.ok(
                ContentTaskProgressView(
                    flow_id=flow.flow_id,
                    node_path=getattr(input_model, "node_path", ""),
                    contract_version=getattr(input_model, "contract_version", None),
                    task_status=str(flow.status),
                    phase=getattr(getattr(state, "position", None), "phase", None),
                    plan_agent_id=plan_agent_id,
                    active_child_flow_id=getattr(active_child, "flow_id", None),
                    active_child_type=getattr(active_child, "flow_type", None),
                    active_round_id=active_round_id,
                    round_index=(
                        getattr(active_child.input, "round_index", None)
                        if active_child is not None and active_child.flow_type == "decl_graph_round"
                        else getattr(state, "decl_round_count", None)
                    ),
                    round_status=round_status,
                    active_decl_names=active_decl_names,
                    current_stage=current_stage,
                    current_step_id=getattr(current_step, "step_id", None),
                    current_agent_id=self._bound_agent_id(current_step) if current_step is not None else None,
                    latest_callback_outcome=callback_outcome,
                    blocker_summary=blocker_summary,
                    latest_content_progress_checkpoint_id=latest_checkpoint_id,
                    candidate_summary={
                        "flow_candidate": flow.flow_id in queues.flow_candidate_queue,
                        "step_candidate": (
                            getattr(current_step, "step_id", None) in queues.step_candidate_queue
                            if current_step is not None
                            else False
                        ),
                        "running_step_ids": queues.running_step_ids,
                    },
                    truth_version=str(flow.updated_at),
                    observed_at=utc_now_iso(),
                    summary=f"Content task {flow.flow_id} is {flow.status} at {getattr(state.position, 'phase', None)}.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - Admin observation boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "content_task_progress_failed",
                    f"Failed to load ContentNodeTask progress: {exc}",
                )
            )

    def list_waiting_requirements(
        self,
        *,
        workspace_root: Path | None = None,
        repo_root: Path | None = None,
        provider_repo: str | None = None,
    ) -> ServiceResult[WaitingRequirementsMonitorView]:
        try:
            roots = self._requirement_scan_roots(workspace_root=workspace_root, repo_root=repo_root)
            provider_key = (
                self.runtime.foundation.layout.ensure_safe_key(provider_repo)
                if provider_repo
                else None
            )
            items: list[WaitingRequirementMonitorView] = []
            for root in roots:
                listed = self.runtime.repo_workspace.requirement.list_requirements(root)
                if not listed.ok or listed.value is None:
                    return self.runtime.foundation.fail(listed.issues)
                for view in listed.value:
                    requirement = view.requirement
                    if not self.runtime.repo_workspace.requirement.is_requirement_waiting(requirement):
                        continue
                    resolved_provider = self.runtime.repo_workspace.requirement.effective_provider_repo(requirement)
                    if provider_key is not None and resolved_provider != provider_key:
                        continue
                    items.append(
                        WaitingRequirementMonitorView(
                            consumer_repo=root.name,
                            consumer_repo_root=str(root),
                            requirement_name=requirement.name,
                            target_repo=requirement.target_repo,
                            provider_repo=resolved_provider,
                            status=requirement.status,
                            waiting=True,
                            result_observed=False,
                            submitted_at=requirement.provider_request_submitted_at,
                            result_observed_at=requirement.provider_result_observed_at,
                            reason=requirement.reason,
                            summary=f"{root.name}/{requirement.name} is waiting for provider {resolved_provider}.",
                        )
                    )
            return self.runtime.foundation.ok(
                WaitingRequirementsMonitorView(
                    workspace_root=str(workspace_root or self.workspace_root) if workspace_root or self.workspace_root else None,
                    repo_root=str(repo_root) if repo_root is not None else None,
                    provider_repo=provider_key,
                    requirements=sorted(items, key=lambda item: (item.consumer_repo, item.requirement_name)),
                    summary=f"Loaded {len(items)} waiting requirements.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("waiting_requirements_monitor_failed", f"Failed to list waiting requirements: {exc}")
            )

    def list_requirement_resume_candidates(
        self,
        *,
        provider_repo: str,
        workspace_root: Path | None = None,
    ) -> ServiceResult[RequirementResumeCandidatesMonitorView]:
        resolved_workspace = Path(workspace_root or self.workspace_root).expanduser() if workspace_root or self.workspace_root else None
        if resolved_workspace is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "workspace_root_required",
                    "Listing requirement resume candidates requires workspace_root.",
                )
            )
        candidates = self.runtime.repo_workspace.list_resume_candidates_for_requirement(
            resolved_workspace,
            provider_repo=provider_repo,
        )
        if not candidates.ok or candidates.value is None:
            return self.runtime.foundation.fail(candidates.issues)
        return self.runtime.foundation.ok(
            RequirementResumeCandidatesMonitorView(
                workspace_root=str(resolved_workspace),
                provider_repo=provider_repo,
                candidates=candidates.value,
                summary=f"Loaded {len(candidates.value)} resume candidates for provider {provider_repo}.",
            )
        )

    def list_agent_monitor(
        self,
        *,
        scope_id: str | None = None,
        agent_type: str | None = None,
        status: str | None = None,
    ) -> ServiceResult[AgentListMonitorView]:
        try:
            agents = list(self.runtime.ark.agent_service.list_agents(scope_id=scope_id, status=status))
            if agent_type is not None:
                agents = [agent for agent in agents if agent.agent_type == agent_type]
            views = [self._agent_monitor_view(agent) for agent in agents]
            return self.runtime.foundation.ok(
                AgentListMonitorView(
                    scope_id=scope_id,
                    agent_type=agent_type,
                    status=status,
                    agents=views,
                    summary=f"Loaded {len(views)} agents.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_monitor_failed", f"Failed to list agents: {exc}")
            )

    def audit_running_agents(self, *, repo_key: str) -> ServiceResult[RunningAgentAuditView]:
        try:
            prefix = f"repo:{repo_key}"
            records = [
                item
                for item in self.runtime.ark.agent_service.audit_running_agents()
                if item.scope_id == prefix or item.scope_id.startswith(f"{prefix}:")
            ]
            return self.runtime.foundation.ok(
                RunningAgentAuditView(
                    repo_key=repo_key,
                    agents=[
                        RunningAgentAuditItemView(
                            agent_id=item.agent_id,
                            scope_id=item.scope_id,
                            classification=item.classification,
                            session_id=item.session_id,
                            artifact_ref=item.artifact_ref,
                            evidence=list(item.evidence),
                        )
                        for item in records
                    ],
                    summary=f"Audited {len(records)} running agents for repo {repo_key}.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "running_agent_audit_failed",
                    f"Failed to audit running agents: {exc}",
                )
            )

    def repair_running_agent(
        self,
        agent_id: str,
        input_model: RunningAgentRepairInput,
        *,
        repo_key: str,
    ) -> ServiceResult[RunningAgentRepairView]:
        prefix = f"repo:{repo_key}"
        if not (
            input_model.expected_scope_id == prefix
            or input_model.expected_scope_id.startswith(f"{prefix}:")
        ):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "running_agent_repair_scope_mismatch",
                    "Agent repair expected_scope_id is outside the requested repo.",
                    current=input_model.expected_scope_id,
                    expected=prefix,
                )
            )
        try:
            result = self.runtime.ark.agent_service.repair_running_agent(
                agent_id,
                expected_scope_id=input_model.expected_scope_id,
                expected_session_id=input_model.expected_session_id,
                expected_artifact_ref=input_model.expected_artifact_ref,
                action=input_model.action,
                dry_run=input_model.dry_run,
            )
            return self.runtime.foundation.ok(
                RunningAgentRepairView(
                    agent_id=result.agent_id,
                    classification=result.classification,
                    action=result.action,
                    dry_run=result.dry_run,
                    repaired=result.repaired,
                    summary=(
                        f"Agent repair {'previewed' if result.dry_run else 'applied'} for "
                        f"{result.agent_id}: {result.action}."
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "running_agent_repair_rejected",
                    f"Running agent repair was rejected: {exc}",
                    object_ref=agent_id,
                )
            )

    def get_agent_report_index(self, agent_id: str) -> ServiceResult[AgentReportIndexView]:
        try:
            agent_service = self.runtime.ark.agent_service
            if hasattr(agent_service, "get_default_trace_report_paths"):
                paths = agent_service.get_default_trace_report_paths(agent_id)
                reports_root = getattr(paths, "reports_root", None)
                latest_json = getattr(paths, "latest_json_path", None)
                latest_markdown = getattr(paths, "latest_markdown_path", None)
            else:
                reports_root = Path(agent_service.runtime_root) / "reports" / "agents" / agent_id
                latest_json = reports_root / "latest.json"
                latest_markdown = reports_root / "latest.md"
            existing = []
            if reports_root is not None and Path(reports_root).exists():
                existing = [
                    str(path)
                    for path in sorted(Path(reports_root).rglob("*"))
                    if path.is_file()
                ]
            return self.runtime.foundation.ok(
                AgentReportIndexView(
                    agent_id=agent_id,
                    reports_root=str(reports_root) if reports_root is not None else None,
                    latest_json_path=str(latest_json) if latest_json is not None else None,
                    latest_markdown_path=str(latest_markdown) if latest_markdown is not None else None,
                    existing_report_paths=existing,
                    summary=f"Loaded {len(existing)} trace report artifacts.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_report_index_failed", f"Failed to load Agent report index: {exc}")
            )

    def get_agent_live(
        self,
        agent_id: str,
        *,
        repo_key: str,
        after_cursor: str | None = None,
        wait_s: float = 0.0,
        wake_on: Literal["activity", "status", "response"] = "activity",
    ) -> ServiceResult[AgentLiveMonitorView]:
        if wait_s < 0 or wait_s > 30:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "agent_live_wait_invalid",
                    "Agent live wait_s must be between 0 and 30 seconds.",
                )
            )
        if wake_on not in {"activity", "status", "response"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "agent_live_wake_invalid",
                    "Agent live wake_on must be activity, status, or response.",
                )
            )
        try:
            baseline = self._decode_agent_live_cursor(after_cursor) if after_cursor else None
        except ValueError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_live_cursor_invalid", str(exc))
            )
        deadline = time.monotonic() + wait_s
        try:
            if baseline is not None and wait_s > 0 and wake_on == "status":
                agent_service = self.runtime.ark.agent_service
                if not hasattr(agent_service, "wait_agent_status_change"):
                    raise RuntimeError("ARK Agent status observation is unavailable")
                status_wait = agent_service.wait_agent_status_change(
                    agent_id,
                    after_status=str(baseline["status"]),
                    timeout_s=wait_s,
                )
                snapshot = self._agent_live_snapshot(agent_id)
                changed = status_wait.changed
            else:
                wake_keys = {
                    "activity": ("turns", "events", "tool_calls", "responses", "status"),
                    "status": ("status",),
                    "response": ("responses", "status"),
                }[wake_on]
                while True:
                    snapshot = self._agent_live_snapshot(agent_id)
                    changed = baseline is None or any(
                        snapshot[key] != baseline.get(key)
                        for key in wake_keys
                    )
                    if changed or wait_s == 0 or time.monotonic() >= deadline:
                        break
                    time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))

            timed_out = baseline is not None and not changed
            turn_start = int(baseline.get("turns", 0)) if baseline else 0
            event_start = int(baseline.get("events", 0)) if baseline else 0
            tool_start = int(baseline.get("tool_calls", 0)) if baseline else 0
            turns = snapshot["turn_items"]
            tool_calls = snapshot["tool_call_items"]
            event_delta_count = max(0, int(snapshot["events"]) - event_start)
            delta_events = snapshot["event_items"][event_start:][-100:] if event_delta_count else []
            latest_response = snapshot["latest_response"]
            agent = snapshot["agent"]
            owning_steps = [
                self._step_monitor_view(step)
                for step in self.runtime.ark.step_service.store.list_steps(scope_id=agent.scope_id)
                if agent_id in set(self._agent_binding_values(step))
            ]
            next_cursor = self._encode_agent_live_cursor(
                {
                    key: snapshot[key]
                    for key in ("turns", "events", "tool_calls", "responses", "status")
                }
            )
            return self.runtime.foundation.ok(
                AgentLiveMonitorView(
                    agent=self._agent_monitor_view(agent),
                    wake_on=wake_on,
                    owning_steps=owning_steps,
                    delta_turns=[to_jsonable(item) for item in turns[turn_start:]][-50:],
                    delta_events=[to_jsonable(item) for item in delta_events],
                    delta_tool_calls=[to_jsonable(item) for item in tool_calls[tool_start:]][-50:],
                    latest_response_available=latest_response is not None,
                    latest_response_summary=(latest_response[-500:] if latest_response is not None else None),
                    report_index_url=f"/admin/repos/{repo_key}/agents/{agent_id}/report-index",
                    trace_report_url=f"/admin/repos/{repo_key}/agents/{agent_id}/trace-report",
                    next_cursor=next_cursor,
                    timed_out=timed_out,
                    observed_at=utc_now_iso(),
                    summary=(
                        f"Agent {agent_id} {wake_on} observation has "
                        f"{max(0, int(snapshot['events']) - event_start)} new provider events."
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - Admin observation boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_live_failed", f"Failed to load Agent live view: {exc}")
            )

    def get_external_health(
        self,
        *,
        required_toolkit_groups: list[str] | None = None,
        required_toolkit_tools: list[str] | None = None,
    ) -> ServiceResult[ExternalHealthMonitorView]:
        try:
            health = self.runtime.external.check_external_client_health(
                required_toolkit_groups=required_toolkit_groups,
                required_toolkit_tools=required_toolkit_tools,
            )
            toolkit_process = None
            if self.toolkit_state is not None and hasattr(self.toolkit_state, "model_dump"):
                toolkit_process = self.toolkit_state.model_dump(mode="json")
            return self.runtime.foundation.ok(
                ExternalHealthMonitorView(
                    health=health.model_dump(mode="json"),
                    toolkit_process=toolkit_process,
                    summary=health.summary,
                )
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("external_health_monitor_failed", f"Failed to check external health: {exc}")
            )

    def get_main_repo_status(self, repo_root: Path) -> ServiceResult[MainRepoStatusView]:
        try:
            root = Path(repo_root).expanduser()
            ctx = FoundationContext(repo_root=root)
            constellation_root = self.runtime.foundation.layout.constellation_root(ctx)
            preparation_path = self.runtime.foundation.layout.preparation_input_path(ctx)
            repo_state_result = self.runtime.repo_workspace.metadata.get_repo_state_view(root)
            repo_state = (
                repo_state_result.value.model_dump(mode="json")
                if repo_state_result.ok and repo_state_result.value is not None
                else None
            )
            source_exists = None
            source_file_count = None
            if preparation_path.exists():
                loaded = self.runtime.repo_workspace.preparation.get_preparation_input(root)
                if loaded.ok and loaded.value is not None and loaded.value.input.source_corpus_mode != SourceCorpusMode.NONE:
                    source_root = self.runtime.foundation.layout.source_corpus_root(
                        ctx,
                        loaded.value.input.source_corpus_relpath or ".lean_constellation/source",
                    )
                    source_exists = source_root.exists() and source_root.is_dir()
                    source_file_count = (
                        sum(1 for item in source_root.rglob("*") if item.is_file())
                        if source_exists
                        else 0
                    )
            flows = self.runtime.ark.flow_service.list_flows(scope_id=f"repo:{root.name}")
            agents = self.runtime.ark.agent_service.list_agents(scope_id=f"repo:{root.name}")
            nonterminal = [
                flow
                for flow in flows
                if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}
            ]
            return self.runtime.foundation.ok(
                MainRepoStatusView(
                    repo_root=str(root),
                    repo_exists=root.exists(),
                    constellation_exists=constellation_root.exists(),
                    preparation_input_exists=preparation_path.exists(),
                    source_corpus_exists=source_exists,
                    source_corpus_file_count=source_file_count,
                    repo_state=repo_state,
                    flow_count=len(flows),
                    nonterminal_flow_count=len(nonterminal),
                    agent_count=len(agents),
                    summary=f"Loaded main repo status for {root.name}.",
                )
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("main_repo_status_failed", f"Failed to load main repo status: {exc}")
            )

    def get_test_control_runtime_view(self) -> ServiceResult[TestControlRuntimeView]:
        paused = False
        controller = self.runtime.ark.pause_controller
        if controller is not None and hasattr(controller, "is_paused"):
            paused = bool(controller.is_paused())
        return self.runtime.foundation.ok(
            TestControlRuntimeView(
                test_control_enabled=self.runtime.test_control_enabled,
                paused=paused,
                candidate_queues=self._candidate_queue_view(),
                summary="Loaded test-control runtime view.",
            )
        )

    def rebuild_candidate_queues(self, *, scope_id: str | None = None) -> ServiceResult[TestControlCandidateQueueView]:
        guarded = self._require_test_control()
        if guarded is not None:
            return guarded
        schedule_service = self.runtime.ark.schedule_service
        if schedule_service is None or not hasattr(schedule_service, "rebuild_candidate_queues"):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("schedule_service_missing", "ARK schedule service is not configured.")
            )
        try:
            schedule_service.rebuild_candidate_queues(scope_id=scope_id)
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("candidate_queue_rebuild_failed", f"Failed to rebuild candidate queues: {exc}")
            )
        return self.runtime.foundation.ok(self._candidate_queue_view())

    def advance_flow_once(self, input_model: AdminFlowAdvanceInput) -> ServiceResult[AdminFlowAdvanceView]:
        guarded = self._require_test_control()
        if guarded is not None:
            return guarded
        controller = self.runtime.ark.pause_controller
        flow_service = self.runtime.ark.flow_service
        if controller is None or not hasattr(controller, "bypass_current_thread"):
            return self.runtime.foundation.fail(self.runtime.foundation.issue("pause_controller_missing", "ARK pause controller is not configured."))
        if flow_service is None or not hasattr(flow_service, "advance_flow"):
            return self.runtime.foundation.fail(self.runtime.foundation.issue("flow_service_missing", "ARK flow service is not configured."))
        try:
            with controller.bypass_current_thread():
                created_step_id = flow_service.advance_flow(input_model.flow_id)
            flow = flow_service.get_flow(input_model.flow_id)
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("advance_flow_once_failed", f"Failed to advance flow once: {exc}")
            )
        return self.runtime.foundation.ok(
            AdminFlowAdvanceView(
                flow_id=input_model.flow_id,
                scope_id=str(flow.scope_id),
                flow_status=str(flow.status),
                created_step_id=created_step_id,
                summary="Advanced flow once.",
            )
        )

    def start_step_once(self, input_model: AdminStepStartInput) -> ServiceResult[AdminStepRunView]:
        guarded = self._require_test_control()
        if guarded is not None:
            return guarded
        step_service = self.runtime.ark.step_service
        if step_service is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("step_service_missing", "ARK step service is not configured."))
        try:
            if input_model.wait:
                step_service.run_step(input_model.step_id, bypass_pause=True)
                step = step_service.wait_step(input_model.step_id, timeout_s=input_model.timeout_s)
            else:
                step_service.start_step(input_model.step_id, bypass_pause=True)
                step = step_service.store.get_step(input_model.step_id)
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("start_step_once_failed", f"Failed to start step once: {exc}")
            )
        return self.runtime.foundation.ok(
            AdminStepRunView(
                step_id=step.step_id,
                flow_id=step.flow_id,
                scope_id=step.scope_id,
                step_type=step.step_type,
                status=str(step.status),
                waited=input_model.wait,
                summary="Started step once." if not input_model.wait else "Started and waited for step.",
            )
        )

    def wait_step(self, input_model: AdminStepStartInput) -> ServiceResult[AdminStepRunView]:
        guarded = self._require_test_control()
        if guarded is not None:
            return guarded
        step_service = self.runtime.ark.step_service
        if step_service is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("step_service_missing", "ARK step service is not configured."))
        try:
            step = step_service.wait_step(input_model.step_id, timeout_s=input_model.timeout_s)
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("wait_step_failed", f"Failed to wait for step: {exc}")
            )
        return self.runtime.foundation.ok(
            AdminStepRunView(
                step_id=step.step_id,
                flow_id=step.flow_id,
                scope_id=step.scope_id,
                step_type=step.step_type,
                status=str(step.status),
                waited=True,
                summary="Waited for step.",
            )
        )

    def run_until_step_created(self, input_model: AdminRunUntilStepCreatedInput) -> ServiceResult[AdminFlowAdvanceView]:
        guarded = self._require_test_control()
        if guarded is not None:
            return guarded
        latest_view: AdminFlowAdvanceView | None = None
        for _ in range(input_model.max_advances):
            advanced = self.advance_flow_once(AdminFlowAdvanceInput(flow_id=input_model.flow_id))
            if not advanced.ok or advanced.value is None:
                return advanced
            latest_view = advanced.value
            if advanced.value.created_step_id is None:
                flow = self.runtime.ark.flow_service.get_flow(input_model.flow_id)
                if flow.status in {FlowStatus.COMPLETED, FlowStatus.FAILED}:
                    return advanced
                continue
            step = self.runtime.ark.step_service.store.get_step(advanced.value.created_step_id)
            if input_model.step_type is None or step.step_type == input_model.step_type:
                return advanced
        if latest_view is not None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "run_until_step_created_exhausted",
                    "Reached max_advances before creating the requested step.",
                    details={"flow_id": input_model.flow_id, "max_advances": input_model.max_advances},
                )
            )
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue("run_until_step_created_failed", "No flow advance was attempted.")
        )

    def get_agent_step_control_view(self, step_id: str) -> ServiceResult[AgentStepControlView]:
        try:
            return self.runtime.foundation.ok(self._agent_step_control_view(step_id))
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_step_control_view_failed", f"Failed to load AgentStep control view: {exc}")
            )

    def set_agent_step_override(self, input_model: SetAgentStepOverrideInput) -> ServiceResult[AgentStepControlView]:
        guarded = self._require_test_control()
        if guarded is not None:
            return guarded
        step_service = self.runtime.ark.step_service
        if step_service is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("step_service_missing", "ARK step service is not configured."))
        try:
            step = step_service.store.get_step(input_model.step_id)
            self._validate_agent_step_override_target(step, input_model.override)

            def update(target_step) -> None:
                state = target_step.state
                if not isinstance(state, AgentStepState):
                    raise TypeError("step state is not AgentStepState")
                state.variables[CONTROLLED_AGENT_OVERRIDE_KEY] = input_model.override.model_dump()

            step_service.store.update_step_record(input_model.step_id, update)
            return self.runtime.foundation.ok(self._agent_step_control_view(input_model.step_id))
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("set_agent_step_override_failed", f"Failed to set AgentStep override: {exc}")
            )

    def clear_agent_step_override(self, input_model: ClearAgentStepOverrideInput) -> ServiceResult[AgentStepControlView]:
        guarded = self._require_test_control()
        if guarded is not None:
            return guarded
        step_service = self.runtime.ark.step_service
        if step_service is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("step_service_missing", "ARK step service is not configured."))
        try:
            step = step_service.store.get_step(input_model.step_id)
            if step.status is not StepStatus.CREATED:
                raise ValueError("override can only be cleared before Step starts")
            if not isinstance(step.state, AgentStepState):
                raise TypeError("step is not an AgentStep")

            def update(target_step) -> None:
                state = target_step.state
                if isinstance(state, AgentStepState):
                    state.variables.pop(CONTROLLED_AGENT_OVERRIDE_KEY, None)
                    state.variables.pop("controlled_agent_override", None)

            step_service.store.update_step_record(input_model.step_id, update)
            return self.runtime.foundation.ok(self._agent_step_control_view(input_model.step_id))
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("clear_agent_step_override_failed", f"Failed to clear AgentStep override: {exc}")
            )

    def restart_failed_agent_step(
        self,
        input_model: RestartFailedAgentStepInput,
    ) -> ServiceResult[RestartFailedAgentStepView]:
        flow_service = self.runtime.ark.flow_service
        step_service = self.runtime.ark.step_service
        if flow_service is None or step_service is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "flow_step_service_missing",
                    "ARK flow/step services are not configured.",
                )
            )
        try:
            failed_step = step_service.store.get_step(input_model.step_id)
            failed_flow = flow_service.get_flow(failed_step.flow_id)
            round_target: tuple[Path, str, str] | None = None
            if failed_flow.flow_type == "decl_graph_round":
                from lean_constellation.flows.content_node_task.decl_round.flow import (
                    DeclGraphRoundInput,
                )

                round_input = failed_flow.input
                if not isinstance(round_input, DeclGraphRoundInput) or round_input.repo_path is None:
                    raise TypeError("DeclGraphRoundFlow has no typed repo_path input")
                repo_root = Path(round_input.repo_path)
                validated = self.runtime.decl_graph.validate_failed_round_execution_restart(
                    repo_root,
                    node_path=round_input.node_path,
                    round_id=round_input.round_id,
                    failed_step_id=failed_step.step_id,
                )
                if not validated.ok:
                    return self.runtime.foundation.fail(validated.issues)
                round_target = (repo_root, round_input.node_path, round_input.round_id)

            restarted = flow_service.restart_failed_agent_step(input_model.step_id)
            reopened_round_id = None
            if round_target is not None:
                repo_root, node_path, round_id = round_target
                reopened = self.runtime.decl_graph.reopen_failed_round_execution(
                    repo_root,
                    node_path=node_path,
                    round_id=round_id,
                    failed_step_id=input_model.step_id,
                )
                if not reopened.ok:
                    return self.runtime.foundation.fail(reopened.issues)
                reopened_round_id = round_id
            return self.runtime.foundation.ok(
                RestartFailedAgentStepView(
                    failed_step_id=restarted.failed_step_id,
                    replacement_step_id=restarted.replacement_step_id,
                    flow_id=restarted.flow_id,
                    scope_id=failed_step.scope_id,
                    agent_id=restarted.agent_id,
                    agent_reused=restarted.agent_reused,
                    enqueued=restarted.enqueued,
                    reopened_round_id=reopened_round_id,
                    summary=(
                        f"Restarted failed AgentStep {restarted.failed_step_id} as "
                        f"{restarted.replacement_step_id}."
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - operator mutation boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "restart_failed_agent_step_failed",
                    f"Failed to restart AgentStep: {exc}",
                    object_ref=input_model.step_id,
                )
            )

    def create_manual_test_checkpoint(self, input_model: ManualCheckpointInput):
        guarded = self._require_test_control()
        if guarded is not None:
            return guarded
        if not input_model.scope_ids:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "manual_checkpoint_scope_ids_required",
                    "Manual test checkpoint requires explicit scope_ids.",
                    field="scope_ids",
                )
            )
        return self.runtime.app.snapshot_runtime.create_repo_stable_point_snapshot(
            input_model.repo_root,
            checkpoint_kind="manual_test_stable_point",
            label=input_model.label,
            node_paths=input_model.node_paths,
            node_ids=input_model.node_ids,
            scope_ids=input_model.scope_ids,
        )

    def list_snapshots(self, input_model: SnapshotListInput):
        return self.runtime.validation_snapshot.list_repo_checkpoint_snapshots(
            input_model.repo_root,
            checkpoint_kind=input_model.checkpoint_kind,
        )

    def create_snapshot(self, input_model: SnapshotCreateInput):
        return self.runtime.app.snapshot_runtime.create_repo_stable_point_snapshot(
            input_model.repo_root,
            checkpoint_kind=input_model.checkpoint_kind,
            label=input_model.label,
            node_paths=input_model.node_paths,
            node_ids=input_model.node_ids,
            scope_ids=input_model.scope_ids,
        )

    def restore_snapshot(self, input_model: SnapshotRestoreInput):
        return self.runtime.app.snapshot_runtime.restore_repo_checkpoint_snapshot(
            input_model.repo_root,
            snapshot_id=input_model.snapshot_id,
            dry_run=input_model.dry_run,
            leave_runtime_paused=input_model.leave_runtime_paused,
            prune_extra_files=input_model.prune_extra_files,
        )

    def resume_requirement(self, input_model: RequirementResumeInput) -> ServiceResult[RequirementResumeView]:
        loaded = self.runtime.repo_workspace.requirement.get_requirement(
            input_model.consumer_repo_root,
            name=input_model.requirement_name,
        )
        if not loaded.ok or loaded.value is None:
            return self.runtime.foundation.fail(loaded.issues)
        requirement = loaded.value.requirement
        expected_provider = self.runtime.foundation.layout.ensure_safe_key(input_model.provider_repo)
        waiting_provider = self.runtime.repo_workspace.requirement.effective_provider_repo(requirement)
        if waiting_provider != expected_provider:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "requirement_provider_mismatch",
                    "Requirement resume provider does not match the waiting requirement provider.",
                    current=expected_provider,
                    expected=waiting_provider,
                    object_ref=input_model.requirement_name,
                )
            )
        matching = self._find_requirement_resume_flow(
            consumer_repo_root=input_model.consumer_repo_root,
            requirement_name=input_model.requirement_name,
        )
        if not matching.ok or matching.value is None:
            return self.runtime.foundation.fail(matching.issues)
        flow = matching.value
        binding = self._validate_requirement_resume_binding(flow)
        if not binding.ok:
            return self.runtime.foundation.fail(binding.issues)
        schedule_service = self.runtime.ark.schedule_service
        if input_model.enqueue and schedule_service is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "schedule_service_missing",
                    "Requirement resume requested enqueue but the runtime schedule service is unavailable.",
                )
            )
        provider_truth = self.runtime.repo_workspace.requirement.validate_requirement_provider_truth(
            input_model.consumer_repo_root,
            requirement_name=input_model.requirement_name,
            provider_repo=expected_provider,
            require_stable=True,
        )
        if not provider_truth.ok:
            return self.runtime.foundation.fail(provider_truth.issues)
        observed = self.runtime.repo_workspace.mark_requirement_result_observed(
            input_model.consumer_repo_root,
            requirement_name=input_model.requirement_name,
            note=input_model.admin_note,
        )
        if not observed.ok or observed.value is None:
            return self.runtime.foundation.fail(observed.issues)
        if input_model.enqueue:
            assert schedule_service is not None
            schedule_service.enqueue_flow(flow.flow_id)
        resumed = AdminFlowStartView(
            flow_id=flow.flow_id,
            flow_type=flow.flow_type,
            scope_id=flow.scope_id,
            enqueued=input_model.enqueue,
            repo_root=str(input_model.consumer_repo_root),
            summary=f"Resumed existing waiting coordinator flow {flow.flow_id}.",
        )
        return self.runtime.foundation.ok(
            RequirementResumeView(
                requirement_name=input_model.requirement_name,
                consumer_repo_root=str(input_model.consumer_repo_root),
                provider_repo=input_model.provider_repo,
                observed=observed.value.result_observed,
                resume_flow=resumed,
                summary=f"Marked requirement {input_model.requirement_name} observed and resumed its existing coordinator flow.",
            )
        )

    def _find_requirement_resume_flow(
        self,
        *,
        consumer_repo_root: Path,
        requirement_name: str,
    ) -> ServiceResult[object]:
        flow_service = self.runtime.ark.flow_service
        if flow_service is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("flow_service_missing", "ARK flow service is not configured.")
            )
        scope_id = f"repo:{consumer_repo_root.name}"
        flows = flow_service.list_flows(scope_id=scope_id, flow_type="native_repo_coordinator")
        waiting = [
            flow
            for flow in flows
            if flow.status is FlowStatus.WAITING
            and getattr(getattr(flow.state, "position", None), "phase", None) == "waiting_requirement"
            and getattr(flow.state, "waiting_requirement_name", None) == requirement_name
        ]
        if len(waiting) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "waiting_coordinator_flow_ambiguous",
                    "Multiple waiting Coordinator Flows match this requirement; explicit admin repair is required.",
                    object_ref=requirement_name,
                    details={"flow_ids": ",".join(flow.flow_id for flow in waiting)},
                )
            )
        if waiting:
            return self.runtime.foundation.ok(waiting[0])

        resuming = [
            flow
            for flow in flows
            if flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}
            and getattr(getattr(flow.state, "position", None), "phase", None)
            in {"requirement_resume_gate", "coordinator_requirement_resume"}
            and (
                getattr(flow.state, "waiting_requirement_name", None) == requirement_name
                or getattr(flow.state, "resuming_requirement_name", None) == requirement_name
            )
        ]
        if len(resuming) > 1:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "waiting_coordinator_flow_ambiguous",
                    "Multiple active Coordinator Flows match this resumed requirement; explicit admin repair is required.",
                    object_ref=requirement_name,
                    details={"flow_ids": ",".join(flow.flow_id for flow in resuming)},
                )
            )
        if resuming:
            return self.runtime.foundation.ok(resuming[0])
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "waiting_coordinator_flow_not_found",
                "No original waiting Coordinator Flow matches this requirement; explicit admin repair is required.",
                object_ref=requirement_name,
                expected=f"{scope_id}:waiting_requirement",
            )
        )

    def _validate_requirement_resume_binding(self, flow) -> ServiceResult[object]:
        agent_id = flow.agent_bindings.get("coordinator")
        if not agent_id:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "waiting_coordinator_binding_invalid",
                    "The original waiting Flow has no Coordinator Agent binding.",
                    object_ref=flow.flow_id,
                )
            )
        agent_service = self.runtime.ark.agent_service
        if agent_service is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "waiting_coordinator_binding_invalid",
                    "The runtime cannot validate the original Coordinator Agent binding.",
                    object_ref=flow.flow_id,
                )
            )
        try:
            agent = agent_service.get_agent(agent_id)
        except Exception:  # noqa: BLE001 - normalize runtime store lookup at the admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "waiting_coordinator_binding_invalid",
                    "The original Flow-bound Coordinator Agent does not exist.",
                    object_ref=agent_id,
                )
            )
        if agent.scope_id != flow.scope_id or agent.agent_type != "CoordinatorAgent":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "waiting_coordinator_binding_invalid",
                    "The original Flow binding does not reference a CoordinatorAgent in the same repo scope.",
                    object_ref=agent_id,
                    current=f"{agent.scope_id}:{agent.agent_type}",
                    expected=f"{flow.scope_id}:CoordinatorAgent",
                )
            )
        return self.runtime.foundation.ok(agent)

    def list_agent_turns(self, agent_id: str):
        return self._agent_trace_call(
            lambda agent_service: agent_service.query_turns(agent_id, limit=10_000).items,
            failure_kind="agent_turns_failed",
            failure_message="Failed to list Agent turns",
        )

    def get_agent_turn(
        self,
        agent_id: str,
        *,
        turn_id: str | None = None,
        index: int | None = None,
        latest: bool = False,
    ):
        def read(agent_service):
            turns = list(agent_service.query_turns(agent_id, limit=10_000).items)
            if turn_id is not None:
                return next(item for item in turns if item.locator.turn_id == turn_id)
            if latest:
                return turns[-1]
            if index is None:
                raise ValueError("turn_id, index, or latest is required")
            return turns[index]

        return self._agent_trace_call(
            read,
            failure_kind="agent_turn_failed",
            failure_message="Failed to read Agent turn",
        )

    def get_agent_event(
        self,
        agent_id: str,
        *,
        index: int | None = None,
        last: bool = False,
    ):
        def read(agent_service):
            events = list(agent_service.query_events(agent_id, limit=100_000).items)
            if last:
                return events[-1]
            if index is None:
                raise ValueError("index or last is required")
            return events[index]

        return self._agent_trace_call(
            read,
            failure_kind="agent_event_failed",
            failure_message="Failed to read Agent event",
        )

    def tail_agent_events(
        self,
        agent_id: str,
        *,
        limit: int = 20,
        event_type: str | None = None,
        payload_type: str | None = None,
    ):
        def read(agent_service):
            events = list(agent_service.query_events(agent_id, limit=100_000).items)
            if event_type is not None:
                events = [item for item in events if item.kind == event_type]
            if payload_type is not None:
                events = [
                    item
                    for item in events
                    if getattr(item.provider_payload, "payload_type", None) == payload_type
                ]
            return events[-limit:]

        return self._agent_trace_call(
            read,
            failure_kind="agent_events_tail_failed",
            failure_message="Failed to tail Agent events",
        )

    def list_agent_response_texts(
        self,
        agent_id: str,
        *,
        turn_id: str | None = None,
        latest: bool = False,
    ):
        def read(agent_service):
            turns = list(agent_service.query_turns(agent_id, limit=10_000).items)
            if turn_id is not None:
                turns = [item for item in turns if item.locator.turn_id == turn_id]
            if latest:
                turns = turns[-1:]
            return [
                item.result.final_text
                for item in turns
                if item.result is not None and item.result.final_text is not None
            ]

        return self._agent_trace_call(
            read,
            failure_kind="agent_response_texts_failed",
            failure_message="Failed to list Agent response texts",
        )

    def get_latest_agent_response_text(self, agent_id: str):
        def read(agent_service):
            turn = agent_service.query_turn(agent_id, latest=True)
            return turn.result.final_text if turn is not None and turn.result is not None else None

        return self._agent_trace_call(
            read,
            failure_kind="agent_latest_response_text_failed",
            failure_message="Failed to read latest Agent response text",
        )

    def list_agent_tool_calls(
        self,
        agent_id: str,
        *,
        turn_id: str | None = None,
        latest: bool = False,
    ):
        def read(agent_service):
            turn = None
            if turn_id is not None or latest:
                turns = list(agent_service.query_turns(agent_id, limit=10_000).items)
                if turn_id is not None:
                    turn = next(item.locator for item in turns if item.locator.turn_id == turn_id)
                elif turns:
                    turn = turns[-1].locator
            return agent_service.query_tool_calls(
                agent_id,
                turn_id=turn.turn_id if turn is not None else None,
                limit=100_000,
            ).items

        return self._agent_trace_call(
            read,
            failure_kind="agent_tool_calls_failed",
            failure_message="Failed to list Agent tool calls",
        )

    def get_agent_tool_call(
        self,
        agent_id: str,
        *,
        call_id: str | None = None,
        index: int | None = None,
        last: bool = False,
    ):
        def read(agent_service):
            calls = list(agent_service.query_tool_calls(agent_id, call_id=call_id, limit=100_000).items)
            if call_id is not None:
                return calls[0]
            if last:
                return calls[-1]
            if index is None:
                raise ValueError("call_id, index, or last is required")
            return calls[index]

        return self._agent_trace_call(
            read,
            failure_kind="agent_tool_call_failed",
            failure_message="Failed to read Agent tool call",
        )

    def export_agent_trace_report(
        self,
        agent_id: str,
        *,
        artifact_path: str | Path | None = None,
        output_path: str | Path | None = None,
        format: Literal["json", "markdown"] = "json",
        rebuild: bool = False,
    ):
        def build(agent_service):
            def default_payload(report, paths, *, rebuilt: bool):
                report_path = getattr(paths, "latest_json_path", None) if format == "json" else getattr(paths, "latest_markdown_path", None)
                payload = dict(report) if isinstance(report, dict) else to_jsonable(report)
                if isinstance(payload, dict):
                    payload["agent_id"] = agent_id
                    payload["report_path"] = report_path
                    payload["rebuilt"] = rebuilt
                    return payload
                return {
                    "agent_id": agent_id,
                    "report_path": report_path,
                    "report": report,
                    "rebuilt": rebuilt,
                }

            if output_path is None:
                # A read/query request must not create a derived report as a
                # side effect. Existing materialized reports remain readable;
                # otherwise build the report in memory from canonical traces.
                if not rebuild and artifact_path is None and hasattr(agent_service, "read_default_trace_report"):
                    report = agent_service.read_default_trace_report(agent_id, format=format)
                    if report is not None:
                        paths = agent_service.get_default_trace_report_paths(agent_id)
                        return default_payload(report, paths, rebuilt=False)
                report = agent_service.build_trace_report(agent_id, artifact_path=artifact_path)
                return default_payload(report, None, rebuilt=bool(rebuild or artifact_path is not None))
            report = agent_service.export_trace_report(
                agent_id,
                output_path=output_path,
                format=format,
                artifact_path=artifact_path,
            )
            payload = to_jsonable(report)
            if isinstance(payload, dict):
                payload["report_path"] = str(output_path)
            return payload

        return self._agent_trace_call(
            build,
            failure_kind="agent_trace_report_failed",
            failure_message="Failed to build Agent trace report",
        )

    def _require_test_control(self):
        if self.runtime.test_control_enabled:
            return None
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "test_control_disabled",
                "This admin operation is only available on a test-control runtime.",
            )
        )

    def _flow_tree_node_payload(self, flow: Any, children_by_parent: dict[str, list[Any]]) -> dict[str, Any]:
        children = sorted(
            children_by_parent.get(str(flow.flow_id), []),
            key=lambda item: (str(item.created_at), str(item.flow_id)),
        )
        return {
            "flow": to_jsonable(self._flow_monitor_view(flow, include_steps=True)),
            "children": [self._flow_tree_node_payload(child, children_by_parent) for child in children],
        }

    def _flow_monitor_view(self, flow: Any, *, include_steps: bool) -> FlowMonitorView:
        position = getattr(getattr(flow, "state", None), "position", None)
        child_flows = self.runtime.ark.flow_service.store.list_child_flows(parent_flow_id=flow.flow_id)
        steps = [
            self._step_monitor_view(self.runtime.ark.step_service.store.get_step(step_id))
            for step_id in list(getattr(flow, "step_ids", []) or [])
        ] if include_steps else []
        return FlowMonitorView(
            flow_id=flow.flow_id,
            flow_type=flow.flow_type,
            scope_id=flow.scope_id,
            status=str(flow.status),
            phase=getattr(position, "phase", None),
            round_index=getattr(position, "round_index", None),
            current_step_id=flow.current_step_id,
            parent_flow_id=flow.parent_flow_id,
            parent_dispatch_step_id=flow.parent_dispatch_step_id,
            manual_pause_active=bool(getattr(getattr(flow, "manual_pause", None), "active", False)),
            step_count=len(getattr(flow, "step_ids", []) or []),
            child_flow_count=len(child_flows),
            result_type=getattr(flow.result, "result_type", None) if flow.result is not None else None,
            error_type=getattr(flow.error, "error_type", None) if flow.error is not None else None,
            created_at=flow.created_at,
            updated_at=flow.updated_at,
            started_at=flow.started_at,
            finished_at=flow.finished_at,
            steps=steps,
            summary=f"Flow {flow.flow_type} is {flow.status}.",
        )

    def _step_monitor_view(self, step: Any) -> StepMonitorView:
        agent_type = getattr(step.state, "agent_type", None)
        agent_role = getattr(step.state, "agent_role", None)
        bound_agent_id = step.agent_bindings.get(agent_role) if agent_role else None
        return StepMonitorView(
            step_id=step.step_id,
            flow_id=step.flow_id,
            scope_id=step.scope_id,
            step_type=step.step_type,
            status=str(step.status),
            state_type=getattr(step.state, "state_type", None),
            submission_type=getattr(step.submission, "submission_type", None) if step.submission is not None else None,
            submit_tool=getattr(step.submission, "tool_name", None) if step.submission is not None else None,
            result_type=getattr(step.result, "result_type", None) if step.result is not None else None,
            error_type=getattr(step.error, "error_type", None) if step.error is not None else None,
            agent_type=agent_type,
            bound_agent_id=bound_agent_id,
            created_at=step.created_at,
            updated_at=step.updated_at,
            started_at=step.started_at,
            finished_at=step.finished_at,
            summary=f"Step {step.step_type} is {step.status}.",
        )

    def _requirement_scan_roots(
        self,
        *,
        workspace_root: Path | None,
        repo_root: Path | None,
    ) -> list[Path]:
        if repo_root is not None:
            return [Path(repo_root).expanduser()]
        resolved_workspace = Path(workspace_root or self.workspace_root).expanduser() if workspace_root or self.workspace_root else None
        if resolved_workspace is None:
            raise ValueError("workspace_root or repo_root is required")
        if not resolved_workspace.exists():
            raise FileNotFoundError(f"workspace_root does not exist: {resolved_workspace}")
        roots = []
        for child in sorted(path for path in resolved_workspace.iterdir() if path.is_dir()):
            ctx = FoundationContext(repo_root=child)
            if self.runtime.foundation.layout.constellation_root(ctx).exists():
                roots.append(child)
        return roots

    def _agent_monitor_view(self, agent: Any) -> AgentMonitorView:
        artifact_exists = False
        latest_turn_duration = None
        tool_call_count = None
        try:
            latest = self.runtime.ark.agent_service.query_turn(agent.agent_id, latest=True)
            latest_turn_duration = (
                latest.result.duration_ms
                if latest is not None and latest.result is not None
                else None
            )
            tool_call_count = len(
                self.runtime.ark.agent_service.query_tool_calls(
                    agent.agent_id,
                    turn_id=(latest.locator.turn_id if latest is not None else None),
                ).items
            )
            artifact_exists = agent.artifact_locator is not None
        except Exception:
            pass
        completion = getattr(agent, "last_completion", None)
        return AgentMonitorView(
            agent_id=agent.agent_id,
            scope_id=agent.scope_id,
            agent_type=agent.agent_type,
            provider_type=agent.provider_type,
            home_id=agent.home_id,
            status=agent.status,
            session_id=(agent.session_locator.session_id if agent.session_locator else None),
            artifact_ref=(
                agent.artifact_locator.native_primary_ref
                if agent.artifact_locator is not None
                else None
            ),
            artifact_exists=artifact_exists,
            last_completion_status=getattr(completion, "status", None),
            last_completion_turn_id=getattr(completion, "turn_id", None),
            latest_turn_duration_ms=latest_turn_duration,
            tool_call_count=tool_call_count,
            summary=f"Agent {agent.agent_type} is {agent.status}.",
        )

    def _runtime_lease_monitor_view(
        self,
        lease: SchedulerRunLeaseView,
        *,
        timed_out: bool = False,
    ) -> RuntimeLeaseMonitorView:
        runtime_result = self.get_runtime_status()
        if not runtime_result.ok or runtime_result.value is None:
            raise RuntimeError("runtime status is unavailable while building scheduler lease view")
        advanced_flows = []
        for flow_id in lease.advanced_flow_ids:
            try:
                advanced_flows.append(
                    self._flow_monitor_view(self.runtime.ark.flow_service.get_flow(flow_id), include_steps=False)
                )
            except Exception:
                continue
        started_steps = []
        for step_id in lease.started_step_ids:
            try:
                started_steps.append(
                    self._step_monitor_view(self.runtime.ark.step_service.store.get_step(step_id))
                )
            except Exception:
                continue
        observation = get_semantic_lease_observation(self.runtime.ark.schedule_service, lease.lease_id)
        current_content = None
        if observation is not None and observation.content_task_flow_id is not None:
            try:
                current_content = self.runtime.ark.flow_service.get_flow(observation.content_task_flow_id)
            except Exception:
                current_content = None
        candidate_steps = list(reversed(started_steps))
        if observation is not None and observation.step_id is not None:
            try:
                target_step = self._step_monitor_view(
                    self.runtime.ark.step_service.store.get_step(observation.step_id)
                )
            except Exception:
                target_step = None
            if target_step is not None and all(step.step_id != target_step.step_id for step in candidate_steps):
                candidate_steps.insert(0, target_step)
        current_agent_id = None
        for step_view in candidate_steps:
            agent_id = step_view.bound_agent_id
            if agent_id is None:
                continue
            try:
                agent = self.runtime.ark.agent_service.get_agent(agent_id)
            except Exception:
                continue
            if str(agent.status) == "running":
                current_agent_id = agent_id
                break
        terminal_disposition, requires_review, suggested_next_action = _classify_runtime_lease_terminal(
            lease,
            advanced_flows,
        )
        return RuntimeLeaseMonitorView(
            lease=lease,
            runtime=runtime_result.value,
            advanced_flows=advanced_flows,
            started_steps=started_steps,
            current_content_task_flow_id=getattr(current_content, "flow_id", None),
            current_content_task_phase=getattr(
                getattr(getattr(current_content, "state", None), "position", None),
                "phase",
                None,
            ),
            current_agent_id=current_agent_id,
            truth_version=lease.version,
            observed_at=utc_now_iso(),
            timed_out=timed_out,
            terminal_disposition=terminal_disposition,
            requires_review=requires_review,
            suggested_next_action=suggested_next_action,
            summary=(
                f"Scheduler lease {lease.lease_id} is {lease.status} ({terminal_disposition})"
                + (" after a bounded wait timeout." if timed_out else ".")
            ),
        )

    def _lease_lost_result(self, lease_id: str) -> ServiceResult[RuntimeLeaseMonitorView]:
        runtime = self.get_runtime_status()
        details = {
            "lease_id": lease_id,
            "process_local": True,
            "runtime": runtime.value.model_dump(mode="json") if runtime.ok and runtime.value is not None else None,
        }
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "lease_lost",
                "The process-local scheduler lease is unavailable; the server may have restarted.",
                object_ref=lease_id,
                details=details,
            )
        )

    @staticmethod
    def _bound_agent_id(step: Any) -> str | None:
        agent_role = getattr(getattr(step, "state", None), "agent_role", None)
        if agent_role is not None:
            return step.agent_bindings.get(agent_role)
        bindings = LeanAdminApi._agent_binding_values(step)
        return bindings[0] if len(bindings) == 1 else None

    @staticmethod
    def _agent_binding_values(step: Any) -> list[str]:
        bindings = getattr(step, "agent_bindings", None)
        if bindings is None:
            return []
        by_role = getattr(bindings, "by_role", bindings)
        if not isinstance(by_role, dict):
            return []
        return list(by_role.values())

    def _agent_live_snapshot(self, agent_id: str) -> dict[str, Any]:
        service = self.runtime.ark.agent_service
        agent = service.get_agent(agent_id)
        if agent.session_locator is None:
            turns: list[Any] = []
            events: list[Any] = []
            tool_calls: list[Any] = []
        else:
            turns = list(service.query_turns(agent_id, limit=10_000).items)
            events = list(service.query_events(agent_id, limit=100_000).items)
            tool_calls = list(service.query_tool_calls(agent_id, limit=100_000).items)
        responses = [
            item.result.final_text
            for item in turns
            if item.result is not None and item.result.final_text is not None
        ]
        latest_response = responses[-1] if responses else None
        return {
            "agent": agent,
            "status": str(agent.status),
            "turns": len(turns),
            "events": len(events),
            "tool_calls": len(tool_calls),
            "responses": len(responses),
            "turn_items": turns,
            "event_items": events,
            "tool_call_items": tool_calls,
            "latest_response": latest_response,
        }

    @staticmethod
    def _encode_agent_live_cursor(payload: dict[str, Any]) -> str:
        raw = json.dumps({"v": 1, **payload}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_agent_live_cursor(cursor: str) -> dict[str, Any]:
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
            if payload.get("v") != 1:
                raise ValueError("unsupported cursor version")
            for key in ("turns", "events", "tool_calls", "responses"):
                if not isinstance(payload.get(key), int) or payload[key] < 0:
                    raise ValueError(f"invalid cursor field: {key}")
            if not isinstance(payload.get("status"), str):
                raise ValueError("invalid cursor field: status")
            return payload
        except Exception as exc:  # noqa: BLE001 - opaque cursor parsing boundary.
            raise ValueError("after_cursor is not a valid Agent live cursor") from exc

    def _candidate_queue_view(self) -> TestControlCandidateQueueView:
        schedule_service = self.runtime.ark.schedule_service
        step_service = self.runtime.ark.step_service
        if schedule_service is None:
            return TestControlCandidateQueueView()
        running_step_ids = []
        created_step_ids = []
        if step_service is not None:
            if hasattr(step_service, "list_running_steps"):
                running_step_ids = list(step_service.list_running_steps())
            if hasattr(step_service, "list_created_steps"):
                created_step_ids = list(step_service.list_created_steps())
        return TestControlCandidateQueueView(
            flow_candidate_queue=list(getattr(schedule_service, "flow_candidate_queue", [])),
            step_candidate_queue=list(getattr(schedule_service, "step_candidate_queue", [])),
            queued_flow_ids=sorted(getattr(schedule_service, "queued_flow_ids", set())),
            queued_step_ids=sorted(getattr(schedule_service, "queued_step_ids", set())),
            active_flow_advances=sorted(getattr(schedule_service, "active_flow_advances", set())),
            running_step_ids=running_step_ids,
            created_step_ids=created_step_ids,
        )

    def _agent_step_control_view(self, step_id: str) -> AgentStepControlView:
        step_service = self.runtime.ark.step_service
        flow_service = self.runtime.ark.flow_service
        if step_service is None or flow_service is None:
            raise RuntimeError("ARK flow/step services are not configured")
        step = step_service.store.get_step(step_id)
        if not isinstance(step.state, AgentStepState):
            raise TypeError(f"step is not an AgentStep: {step_id}")
        flow = flow_service.get_flow(step.flow_id)
        agent_type = step.state.agent_type
        tool_view_key = None
        if agent_type:
            tool_view = self.runtime.tool_facade.build_tool_view(agent_type)
            if tool_view.ok and tool_view.value is not None:
                tool_view_key = tool_view.value.key
        override = step.state.variables.get(CONTROLLED_AGENT_OVERRIDE_KEY)
        if override is None:
            override = step.state.variables.get("controlled_agent_override")
        if isinstance(override, ControlledAgentOverrideSpec):
            override = override.model_dump()
        record = step.state.variables.get(CONTROLLED_AGENT_RECORD_KEY)
        return AgentStepControlView(
            step_id=step.step_id,
            flow_id=step.flow_id,
            scope_id=step.scope_id,
            step_type=step.step_type,
            status=str(step.status),
            agent_role=step.state.agent_role,
            agent_type=agent_type,
            provider_type=step.state.provider_type or "codex",
            home_id=step.state.home_id,
            tool_view_key=tool_view_key,
            step_bound_agent_id=step.agent_bindings.get(step.state.agent_role),
            flow_bound_agent_id=flow.agent_bindings.get(step.state.agent_role),
            override=override if isinstance(override, dict) else None,
            controlled_record=record if isinstance(record, dict) else None,
            summary="Loaded AgentStep control view.",
        )

    def _validate_agent_step_override_target(self, step, override: ControlledAgentOverrideSpec) -> None:
        if step.status is not StepStatus.CREATED:
            raise ValueError("override can only be set before Step starts")
        if not isinstance(step.state, AgentStepState):
            raise TypeError("step is not an AgentStep")
        agent_service = self.runtime.ark.agent_service
        if agent_service is None:
            raise RuntimeError("ARK agent service is not configured")
        if override.agent_type_override:
            agent_service.agent_types.get(override.agent_type_override)
        if override.provider_type_override:
            if override.provider_type_override not in agent_service.provider_registry:
                raise ValueError(
                    f"unknown Agent provider_type: {override.provider_type_override}"
                )

    def _agent_trace_call(self, fn, *, failure_kind: str, failure_message: str):
        agent_service = self.runtime.ark.agent_service
        if agent_service is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_service_missing", "ARK agent service is not configured.")
            )
        try:
            return self.runtime.foundation.ok(to_jsonable(fn(agent_service)))
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(failure_kind, f"{failure_message}: {exc}")
            )

    def _agent_runtime_root(self) -> Path | None:
        agent_service = self.runtime.ark.agent_service
        if agent_service is None:
            return None
        return Path(agent_service.runtime_root)
