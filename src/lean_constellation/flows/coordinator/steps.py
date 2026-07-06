"""Native repo coordinator deterministic steps and business AgentStep results."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, FlowStepValidationError, StepTerminalReceipt
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.flows.common.rendering import LeanRenderableStepResult


class CoordinatorContentTasksResultView(StrictModel):
    node_paths: list[str] = Field(default_factory=list)
    task_mode: str = "run"
    request_count: int = 0


class CoordinatorResourceRequestResultView(StrictModel):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    request_count: int = 0
    context_summary: str | None = None


class CoordinatorRepoRequirementResultView(StrictModel):
    requirement_name: str
    target_repo: str
    required_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    reason: str | None = None
    source_description: str | None = None
    interface_count: int = 0


class CoordinatorRepoReadyResultView(StrictModel):
    repo_summary: str


class CoordinatorStepResult(LeanRenderableStepResult):
    result_type: Literal["coordinator"] = "coordinator"
    outcome: Literal["content_tasks", "resource_request", "repo_requirement", "repo_ready", "incomplete"]
    repo_key: str | None = None
    content_tasks: CoordinatorContentTasksResultView | None = None
    resource_request: CoordinatorResourceRequestResultView | None = None
    repo_requirement: CoordinatorRepoRequirementResultView | None = None
    repo_ready: CoordinatorRepoReadyResultView | None = None
    snapshot_id: str | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "content_node_paths": list(self.content_tasks.node_paths) if self.content_tasks else None,
            "resource_target": f"{self.resource_request.target_kind}:{self.resource_request.target}"
            if self.resource_request
            else None,
            "requirement_name": self.repo_requirement.requirement_name if self.repo_requirement else None,
            "repo_ready_summary": self.repo_ready.repo_summary if self.repo_ready else None,
            "snapshot_id": self.snapshot_id,
            "incomplete_reason": self.incomplete_reason,
        }


class CoordinatorContentBatchSnapshotStepResult(LeanRenderableStepResult):
    result_type: Literal["coordinator_content_batch_snapshot"] = "coordinator_content_batch_snapshot"
    outcome: Literal["snapshot_created", "blocked"]
    checkpoint_kind: Literal["before_content_task_dispatch", "after_content_task_batch_terminal"]
    snapshot_id: str | None = None
    node_paths: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "checkpoint_kind": self.checkpoint_kind,
            "snapshot_id": self.snapshot_id,
            "node_paths": list(self.node_paths),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class MarkCoordinatorRepoReadyStepResult(LeanRenderableStepResult):
    result_type: Literal["mark_coordinator_repo_ready"] = "mark_coordinator_repo_ready"
    outcome: Literal["ready_marked", "blocked"]
    repo_key: str | None = None
    provider_ready_marked: bool = False
    satisfied_requirement_count: int = 0
    repo_summary: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "provider_ready_marked": self.provider_ready_marked,
            "satisfied_requirement_count": self.satisfied_requirement_count,
            "repo_summary": self.repo_summary,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class CoordinatorContentBatchSnapshotStep(BaseStep):
    step_type: ClassVar[str] = "coordinator_content_batch_snapshot_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = CoordinatorContentBatchSnapshotStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "coordinator_content_batch_snapshot": CoordinatorContentBatchSnapshotStepResult,
    }

    checkpoint_kind: Literal["before_content_task_dispatch", "after_content_task_batch_terminal"]
    node_paths: list[str] = Field(default_factory=list)

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_coordinator_flow(ctx)
        input_model = _require_native_coordinator_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(
                CoordinatorContentBatchSnapshotStepResult(
                    outcome="blocked",
                    checkpoint_kind=self.checkpoint_kind,
                    node_paths=list(self.node_paths),
                    error_code="repo_root_missing",
                    error_message="Coordinator content task snapshot requires repo_root in Flow input.",
                    summary="Coordinator content task snapshot cannot run without repo_root.",
                )
            )
        if not self.node_paths:
            return ctx.complete_step(
                CoordinatorContentBatchSnapshotStepResult(
                    outcome="blocked",
                    checkpoint_kind=self.checkpoint_kind,
                    error_code="content_task_node_paths_missing",
                    error_message="Coordinator content task snapshot requires at least one node path.",
                    summary="Coordinator content task snapshot has no node paths.",
                )
            )

        return ctx.complete_step(
            CoordinatorContentBatchSnapshotStepResult(
                outcome="snapshot_created",
                checkpoint_kind=self.checkpoint_kind,
                node_paths=list(self.node_paths),
                summary="Checkpoint will be created after the step reaches a stable terminal state.",
            )
        )


class MarkCoordinatorRepoReadyStep(BaseStep):
    step_type: ClassVar[str] = "mark_coordinator_repo_ready_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = MarkCoordinatorRepoReadyStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "mark_coordinator_repo_ready": MarkCoordinatorRepoReadyStepResult,
    }

    repo_summary: str

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_coordinator_flow(ctx)
        input_model = _require_native_coordinator_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(
                MarkCoordinatorRepoReadyStepResult(
                    outcome="blocked",
                    repo_key=input_model.repo_key,
                    repo_summary=self.repo_summary,
                    error_code="repo_root_missing",
                    error_message="Coordinator repo ready marker requires repo_root in Flow input.",
                    summary="Coordinator repo ready marker cannot run without repo_root.",
                )
            )
        gate = _validation_snapshot(ctx).check_repo_ready(repo_root, summary=self.repo_summary)
        if not gate.ok or gate.value is None:
            code, message = _first_issue(gate.issues, fallback_code="repo_ready_gate_failed")
            return ctx.complete_step(
                MarkCoordinatorRepoReadyStepResult(
                    outcome="blocked",
                    repo_key=input_model.repo_key,
                    repo_summary=self.repo_summary,
                    error_code=code,
                    error_message=message,
                    summary=message,
                )
            )
        if not gate.value.passed:
            code, message = _first_issue(gate.value.issues, fallback_code="repo_ready_gate_blocked")
            return ctx.complete_step(
                MarkCoordinatorRepoReadyStepResult(
                    outcome="blocked",
                    repo_key=input_model.repo_key,
                    repo_summary=self.repo_summary,
                    error_code=code,
                    error_message=message,
                    summary=gate.value.summary or message,
                )
            )

        marked = _repo_workspace(ctx).mark_provider_repo_ready(repo_root, summary=self.repo_summary)
        if not marked.ok or marked.value is None:
            code, message = _first_issue(marked.issues, fallback_code="provider_ready_mark_failed")
            return ctx.complete_step(
                MarkCoordinatorRepoReadyStepResult(
                    outcome="blocked",
                    repo_key=input_model.repo_key,
                    repo_summary=self.repo_summary,
                    error_code=code,
                    error_message=message,
                    summary=message,
                )
            )
        return ctx.complete_step(
            MarkCoordinatorRepoReadyStepResult(
                outcome="ready_marked",
                repo_key=input_model.repo_key,
                provider_ready_marked=marked.value.provider_ready_marked,
                satisfied_requirement_count=marked.value.satisfied_requirement_count,
                repo_summary=marked.value.repo_summary or self.repo_summary,
                summary=marked.value.summary,
            )
        )


def new_coordinator_step_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _load_native_coordinator_flow(ctx: StepRunContext):
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    flow = flow_service.get_flow(ctx.flow_id)
    if flow.flow_type != "native_repo_coordinator":
        raise FlowStepValidationError(f"expected native_repo_coordinator flow, got {flow.flow_type}")
    return flow


def _require_native_coordinator_input(input_model):
    from lean_constellation.flows.coordinator.flows import NativeRepoCoordinatorInput

    if not isinstance(input_model, NativeRepoCoordinatorInput):
        raise FlowStepValidationError("native_repo_coordinator flow has invalid input")
    return input_model


def _repo_root(input_model) -> Path | None:
    raw = getattr(input_model, "repo_root", None)
    if not raw:
        return None
    return Path(raw)


def _validation_snapshot(ctx: StepRunContext):
    validation_snapshot = getattr(ctx.app, "validation_snapshot", None)
    if validation_snapshot is None:
        raise FlowStepValidationError("Lean validation_snapshot service is not registered in app services.")
    return validation_snapshot


def _repo_workspace(ctx: StepRunContext):
    repo_workspace = getattr(ctx.app, "repo_workspace", None)
    if repo_workspace is None:
        raise FlowStepValidationError("Lean repo_workspace service is not registered in app services.")
    return repo_workspace


def _first_issue(issues: list[object], *, fallback_code: str) -> tuple[str, str]:
    if issues:
        issue = issues[0]
        return (
            str(getattr(issue, "kind", fallback_code) or fallback_code),
            str(getattr(issue, "message", None) or getattr(issue, "summary", None) or fallback_code),
        )
    return fallback_code, fallback_code


COORDINATOR_STEP_TYPES: tuple[type[BaseStep], ...] = (
    CoordinatorContentBatchSnapshotStep,
    MarkCoordinatorRepoReadyStep,
)
