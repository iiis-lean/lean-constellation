"""Admin-facing API for starting and controlling Lean Constellation runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from agent_runtime_kit.agent.models import to_jsonable
from agent_runtime_kit.flow.models import FlowRequest, FlowStatus, StepStatus
from agent_runtime_kit.flow.standard_steps import AgentStepState
from pydantic import Field, field_validator

from lean_constellation.app.external_takeover import (
    ExternalTakeoverCompleteInput,
    ExternalTakeoverHandoffView,
    ExternalTakeoverToolCallInput,
    ExternalTakeoverToolListInput,
    call_external_takeover_tool,
    complete_external_takeover_handoff,
    list_external_takeover_handoffs,
    list_external_takeover_tools,
)
from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import RepoPreparationInput, RepoPreparationInputView, RepoShellView, SourceCorpusMode
from lean_constellation.flows.testing import (
    CONTROLLED_AGENT_OVERRIDE_KEY,
    CONTROLLED_AGENT_RECORD_KEY,
    ControlledAgentOverrideSpec,
)
from lean_constellation.services.foundation import FoundationContext, ServiceIssue, ServiceResult
from lean_constellation.services.repo_workspace import RepoSkeletonView
from lean_constellation.services.runtime import LeanRuntimeServices


class AdminFlowStartView(StrictModel):
    flow_id: str
    flow_type: str
    scope_id: str
    enqueued: bool
    repo_root: str | None = None
    summary: str


class RuntimePauseView(StrictModel):
    paused: bool
    scope_id: str | None = None
    summary: str


class RuntimeStatusView(StrictModel):
    paused: bool
    test_control_enabled: bool
    flow_candidate_queue: list[str] = Field(default_factory=list)
    step_candidate_queue: list[str] = Field(default_factory=list)
    queued_flow_ids: list[str] = Field(default_factory=list)
    queued_step_ids: list[str] = Field(default_factory=list)
    active_flow_advances: list[str] = Field(default_factory=list)
    running_step_ids: list[str] = Field(default_factory=list)
    created_step_ids: list[str] = Field(default_factory=list)
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
    pending_external_handoffs: list[ExternalTakeoverHandoffView] = Field(default_factory=list)
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
    cli_type: str
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


class ManualCheckpointInput(StrictModel):
    repo_root: Path
    scope_ids: list[str]
    label: str | None = None
    node_paths: list[str] = Field(default_factory=list)

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


class LeanAdminApi:
    """Small admin service that composes existing runtime services."""

    def __init__(self, runtime: LeanRuntimeServices) -> None:
        self.runtime = runtime

    def start_requirement_group_bootstrap(
        self,
        input_model: StartRequirementGroupBootstrapInput,
    ) -> ServiceResult[AdminFlowStartView]:
        draft = self.runtime.repo_workspace.preparation.build_preparation_input_from_group(
            input_model.workspace_root,
            target_repo=input_model.target_repo,
            source_corpus_mode=input_model.source_corpus_mode,
        )
        if not draft.ok or draft.value is None:
            return self.runtime.foundation.fail(draft.issues)
        prepared = self.runtime.repo_workspace.prepare_provider_repo_runtime_shell(
            input_model.workspace_root,
            target_repo=input_model.target_repo,
            preparation_input=draft.value.input,
            project_name=input_model.project_name,
        )
        if not prepared.ok or prepared.value is None:
            return self.runtime.foundation.fail(prepared.issues)
        refs = [
            f"{ref.consumer_repo}:{ref.requirement_name}"
            for ref in prepared.value.preparation_input.input.requirement_refs
        ]
        return self.start_arbitrary_flow(
            StartFlowInput(
                flow_type="requirement_group_repo_bootstrap",
                scope_id=f"repo:{input_model.target_repo}",
                enqueue=input_model.enqueue,
                params={
                    "target_repo": input_model.target_repo,
                    "repo_root": prepared.value.shell.repo_root,
                    "workspace_root": str(input_model.workspace_root),
                    "requirement_refs": refs,
                    "admin_notes": input_model.admin_notes,
                },
            ),
            repo_root=prepared.value.shell.repo_root,
        )

    def start_native_preparation(self, input_model: StartPreparationInput) -> ServiceResult[AdminFlowStartView]:
        repo_key = input_model.repo_key or input_model.repo_root.name
        return self.start_arbitrary_flow(
            StartFlowInput(
                flow_type="native_repo_preparation",
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
        passed = not issues
        view = MainSourceCorpusValidationView(
            repo_root=str(input_model.repo_root),
            source_corpus_mode=preparation.source_corpus_mode,
            source_corpus_relpath=preparation.source_corpus_relpath,
            source_corpus_path=str(source_path) if source_path is not None else None,
            exists=exists,
            file_count=file_count,
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
        return self.runtime.foundation.ok(
            RuntimePauseView(paused=True, scope_id=scope_id, summary="Paused runtime scheduling.")
        )

    def resume_runtime(self, *, scope_id: str | None = None) -> ServiceResult[RuntimePauseView]:
        controller = self.runtime.ark.pause_controller
        if controller is None:
            return self.runtime.foundation.fail(self.runtime.foundation.issue("pause_controller_missing", "ARK pause controller is not configured."))
        controller.resume(scope_id)
        schedule_service = self.runtime.ark.schedule_service
        if schedule_service is not None and hasattr(schedule_service, "rebuild_candidate_queues"):
            schedule_service.rebuild_candidate_queues(scope_id=scope_id)
        return self.runtime.foundation.ok(
            RuntimePauseView(paused=False, scope_id=scope_id, summary="Resumed runtime scheduling.")
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

    def get_test_control_runtime_view(
        self,
        *,
        handoff_dirname: str = "external_turns",
    ) -> ServiceResult[TestControlRuntimeView]:
        paused = False
        controller = self.runtime.ark.pause_controller
        if controller is not None and hasattr(controller, "is_paused"):
            paused = bool(controller.is_paused())
        pending = self.list_external_takeovers(handoff_dirname=handoff_dirname, status="pending")
        handoffs = pending.value if pending.ok and pending.value is not None else []
        return self.runtime.foundation.ok(
            TestControlRuntimeView(
                test_control_enabled=self.runtime.test_control_enabled,
                paused=paused,
                candidate_queues=self._candidate_queue_view(),
                pending_external_handoffs=handoffs,
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
        return self.runtime.validation_snapshot.create_repo_stable_point_snapshot(
            input_model.repo_root,
            checkpoint_kind="manual_test_stable_point",
            label=input_model.label,
            node_paths=input_model.node_paths,
            scope_ids=input_model.scope_ids,
        )

    def list_snapshots(self, input_model: SnapshotListInput):
        return self.runtime.validation_snapshot.list_repo_checkpoint_snapshots(
            input_model.repo_root,
            checkpoint_kind=input_model.checkpoint_kind,
        )

    def create_snapshot(self, input_model: SnapshotCreateInput):
        return self.runtime.validation_snapshot.create_repo_stable_point_snapshot(
            input_model.repo_root,
            checkpoint_kind=input_model.checkpoint_kind,
            label=input_model.label,
            node_paths=input_model.node_paths,
            scope_ids=input_model.scope_ids,
        )

    def restore_snapshot(self, input_model: SnapshotRestoreInput):
        return self.runtime.validation_snapshot.restore_repo_checkpoint_snapshot(
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
        waiting_provider = requirement.waiting_state.provider_repo or requirement.provider_repo or requirement.target_repo
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
        observed = self.runtime.repo_workspace.mark_requirement_result_observed(
            input_model.consumer_repo_root,
            requirement_name=input_model.requirement_name,
            note=input_model.admin_note,
        )
        if not observed.ok or observed.value is None:
            return self.runtime.foundation.fail(observed.issues)
        started = self.start_arbitrary_flow(
            StartFlowInput(
                flow_type="native_repo_coordinator",
                scope_id=f"repo:{input_model.consumer_repo_root.name}",
                enqueue=input_model.enqueue,
                params={
                    "repo_key": input_model.consumer_repo_root.name,
                    "repo_root": str(input_model.consumer_repo_root),
                    "start_mode": "requirement_resume",
                    "start_reason": f"Provider requirement {input_model.requirement_name} is satisfied.",
                    "resumed_requirement_name": input_model.requirement_name,
                    "admin_note": input_model.admin_note,
                },
            ),
            repo_root=str(input_model.consumer_repo_root),
        )
        if not started.ok or started.value is None:
            return self.runtime.foundation.fail(started.issues)
        return self.runtime.foundation.ok(
            RequirementResumeView(
                requirement_name=input_model.requirement_name,
                consumer_repo_root=str(input_model.consumer_repo_root),
                provider_repo=input_model.provider_repo,
                observed=observed.value.result_observed,
                resume_flow=started.value,
                summary=f"Marked requirement {input_model.requirement_name} observed and started coordinator resume flow.",
            )
        )

    def complete_external_takeover(
        self,
        input_model: ExternalTakeoverCompleteInput,
    ) -> ServiceResult[ExternalTakeoverHandoffView]:
        runtime_root = self._agent_runtime_root()
        if runtime_root is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_service_missing", "ARK agent service is not configured.")
            )
        try:
            view = complete_external_takeover_handoff(runtime_root, input_model)
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("external_takeover_complete_failed", f"Failed to complete external handoff: {exc}")
            )
        return self.runtime.foundation.ok(view)

    def list_external_takeovers(
        self,
        *,
        handoff_dirname: str = "external_turns",
        status: str | None = None,
    ) -> ServiceResult[list[ExternalTakeoverHandoffView]]:
        runtime_root = self._agent_runtime_root()
        if runtime_root is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_service_missing", "ARK agent service is not configured.")
            )
        try:
            return self.runtime.foundation.ok(
                list_external_takeover_handoffs(runtime_root, handoff_dirname=handoff_dirname, status=status)
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("external_takeover_list_failed", f"Failed to list external handoffs: {exc}")
            )

    def list_external_takeover_tools(
        self,
        input_model: ExternalTakeoverToolListInput,
    ):
        runtime_root = self._agent_runtime_root()
        if runtime_root is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_service_missing", "ARK agent service is not configured.")
            )
        try:
            return self.runtime.foundation.ok(
                list_external_takeover_tools(self.runtime, runtime_root, input_model)
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("external_takeover_tools_failed", f"Failed to list external handoff tools: {exc}")
            )

    def call_external_takeover_tool(
        self,
        input_model: ExternalTakeoverToolCallInput,
    ):
        runtime_root = self._agent_runtime_root()
        if runtime_root is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("agent_service_missing", "ARK agent service is not configured.")
            )
        try:
            return self.runtime.foundation.ok(
                call_external_takeover_tool(self.runtime, runtime_root, input_model)
            )
        except Exception as exc:  # noqa: BLE001 - admin boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("external_takeover_call_failed", f"Failed to call external handoff tool: {exc}")
            )

    def get_agent_rollout_info(self, agent_id: str):
        return self._agent_trace_call(
            lambda agent_service: agent_service.get_rollout_info(agent_id),
            failure_kind="agent_rollout_info_failed",
            failure_message="Failed to read Agent rollout info",
        )

    def list_agent_turns(self, agent_id: str):
        return self._agent_trace_call(
            lambda agent_service: agent_service.list_trace_turns(agent_id),
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
        return self._agent_trace_call(
            lambda agent_service: agent_service.get_trace_turn(
                agent_id,
                turn_id=turn_id,
                index=index,
                latest=latest,
            ),
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
        return self._agent_trace_call(
            lambda agent_service: agent_service.get_trace_event(agent_id, index=index, last=last),
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
        return self._agent_trace_call(
            lambda agent_service: agent_service.tail_trace_events(
                agent_id,
                limit=limit,
                event_type=event_type,
                payload_type=payload_type,
            ),
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
        return self._agent_trace_call(
            lambda agent_service: agent_service.list_response_texts(
                agent_id,
                turn_id=turn_id,
                latest=latest,
            ),
            failure_kind="agent_response_texts_failed",
            failure_message="Failed to list Agent response texts",
        )

    def get_latest_agent_response_text(self, agent_id: str):
        return self._agent_trace_call(
            lambda agent_service: agent_service.get_latest_response_text(agent_id),
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
        return self._agent_trace_call(
            lambda agent_service: agent_service.list_tool_calls(
                agent_id,
                turn_id=turn_id,
                latest=latest,
            ),
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
        return self._agent_trace_call(
            lambda agent_service: agent_service.get_tool_call(
                agent_id,
                call_id=call_id,
                index=index,
                last=last,
            ),
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
    ):
        def build(agent_service):
            if output_path is None:
                return agent_service.build_trace_report(agent_id, artifact_path=artifact_path)
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
            cli_type=step.state.cli_type,
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
        if override.cli_type_override:
            providers = getattr(agent_service, "providers", {})
            if override.cli_type_override not in providers:
                raise ValueError(f"unknown Agent provider cli_type: {override.cli_type_override}")

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
