"""Admin-facing API for starting and controlling Lean Constellation runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from agent_runtime_kit.flow.models import FlowRequest
from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.preparation import SourceCorpusMode
from lean_constellation.services.foundation import ServiceResult
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

    @field_validator("repo_root", mode="before")
    @classmethod
    def _coerce_repo(cls, value: Any) -> Path:
        return Path(value).expanduser()


class SnapshotRestoreInput(StrictModel):
    repo_root: Path
    snapshot_id: str
    dry_run: bool = False
    leave_runtime_paused: bool = True

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

    def create_snapshot(self, input_model: SnapshotCreateInput):
        return self.runtime.validation_snapshot.create_repo_stable_point_snapshot(
            input_model.repo_root,
            checkpoint_kind=input_model.checkpoint_kind,
            label=input_model.label,
            node_paths=input_model.node_paths,
        )

    def restore_snapshot(self, input_model: SnapshotRestoreInput):
        return self.runtime.validation_snapshot.restore_repo_checkpoint_snapshot(
            input_model.repo_root,
            snapshot_id=input_model.snapshot_id,
            dry_run=input_model.dry_run,
            leave_runtime_paused=input_model.leave_runtime_paused,
        )

    def resume_requirement(self, input_model: RequirementResumeInput) -> ServiceResult[RequirementResumeView]:
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
