"""Native repo coordinator deterministic steps and business AgentStep results."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, FlowStepValidationError, StepTerminalReceipt
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.flows.common.checkpoint_policy import repo_flow_boundary_checkpoints_enabled
from lean_constellation.domain.preparation import RepoDependencyRequirementStatus
from lean_constellation.domain.repo import ProofAvailability
from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.services.validation_snapshot.release_finalizer import PreparedRepoReleaseView


class CoordinatorContentTasksResultView(StrictModel):
    node_paths: list[str] = Field(default_factory=list)
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
    outcome: Literal["snapshot_created", "skipped", "blocked"]
    checkpoint_kind: Literal[
        "before_content_task_dispatch",
        "after_content_task_batch_terminal",
        "before_resource_request_dispatch",
        "after_resource_request_terminal",
    ]
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
    outcome: Literal["ready_marked", "blocked", "candidate_prepared", "candidate_blocked"]
    repo_key: str | None = None
    provider_ready_marked: bool = False
    satisfied_requirement_count: int = 0
    repo_summary: str | None = None
    prepared_release: PreparedRepoReleaseView | None = None
    blocking_issue_kinds: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "provider_ready_marked": self.provider_ready_marked,
            "satisfied_requirement_count": self.satisfied_requirement_count,
            "repo_summary": self.repo_summary,
            "release_id": self.prepared_release.release.release_id if self.prepared_release else None,
            "checkpoint_id": self.prepared_release.release.repo_checkpoint_id if self.prepared_release else None,
            "blocking_issue_kinds": list(self.blocking_issue_kinds),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class CoordinatorRequirementResumeGateStepResult(LeanRenderableStepResult):
    result_type: Literal["coordinator_requirement_resume_gate"] = "coordinator_requirement_resume_gate"
    outcome: Literal["resumed", "still_waiting", "invalid_requirement"]
    requirement_name: str | None = None
    provider_repo: str | None = None
    requirement_status: str | None = None
    result_observed: bool = False
    lake_dependency_attached: bool = False
    requirement_handled: bool = False
    coordinator_agent_id: str | None = None
    issue_code: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "requirement_name": self.requirement_name,
            "provider_repo": self.provider_repo,
            "requirement_status": self.requirement_status,
            "result_observed": self.result_observed,
            "lake_dependency_attached": self.lake_dependency_attached,
            "requirement_handled": self.requirement_handled,
            "coordinator_agent_id": self.coordinator_agent_id,
            "issue_code": self.issue_code,
        }


class CoordinatorContentBatchSnapshotStep(BaseStep):
    step_type: ClassVar[str] = "coordinator_content_batch_snapshot_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = CoordinatorContentBatchSnapshotStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "coordinator_content_batch_snapshot": CoordinatorContentBatchSnapshotStepResult,
    }

    checkpoint_kind: Literal[
        "before_content_task_dispatch",
        "after_content_task_batch_terminal",
        "before_resource_request_dispatch",
        "after_resource_request_terminal",
    ]
    node_paths: list[str] = Field(default_factory=list)

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        if not repo_flow_boundary_checkpoints_enabled(ctx.app):
            return ctx.complete_step(
                CoordinatorContentBatchSnapshotStepResult(
                    outcome="skipped",
                    checkpoint_kind=self.checkpoint_kind,
                    node_paths=list(self.node_paths),
                    summary="Repo/Coordinator flow-boundary automatic checkpoints are disabled.",
                )
            )
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
        if self.checkpoint_kind in {"before_content_task_dispatch", "after_content_task_batch_terminal"} and not self.node_paths:
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


class CoordinatorRequirementResumeGateStep(BaseStep):
    step_type: ClassVar[str] = "coordinator_requirement_resume_gate_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = CoordinatorRequirementResumeGateStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "coordinator_requirement_resume_gate": CoordinatorRequirementResumeGateStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_coordinator_flow(ctx)
        input_model = _require_native_coordinator_input(flow.input)
        repo_root = _repo_root(input_model)
        requirement_name = getattr(flow.state, "waiting_requirement_name", None)
        coordinator_agent_id = flow.agent_bindings.get("coordinator")

        if repo_root is None:
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    requirement_name=requirement_name,
                    coordinator_agent_id=coordinator_agent_id,
                    issue_code="repo_root_missing",
                    summary="Requirement resume gate requires repo_root in Flow input.",
                )
            )
        if not requirement_name:
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    coordinator_agent_id=coordinator_agent_id,
                    issue_code="waiting_requirement_name_missing",
                    summary="Requirement resume gate has no waiting requirement name.",
                )
            )
        binding_issue = _validate_coordinator_binding(ctx, flow, coordinator_agent_id)
        if binding_issue is not None:
            code, message = binding_issue
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    requirement_name=requirement_name,
                    coordinator_agent_id=coordinator_agent_id,
                    issue_code=code,
                    summary=message,
                )
            )

        repo_workspace = _repo_workspace(ctx)
        loaded = repo_workspace.requirement.get_requirement(repo_root, name=requirement_name)
        if not loaded.ok or loaded.value is None:
            code, message = _first_issue(loaded.issues, fallback_code="requirement_not_found")
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    requirement_name=requirement_name,
                    coordinator_agent_id=coordinator_agent_id,
                    issue_code=code,
                    summary=message,
                )
            )
        requirement = loaded.value.requirement
        provider_repo = repo_workspace.requirement.effective_provider_repo(requirement)
        result_observed = repo_workspace.requirement.is_requirement_result_observed(requirement)
        if requirement.status not in {
            RepoDependencyRequirementStatus.SATISFIED,
            RepoDependencyRequirementStatus.HANDLED,
        } or not result_observed:
            return ctx.complete_step(
                CoordinatorRequirementResumeGateStepResult(
                    outcome="still_waiting",
                    requirement_name=requirement_name,
                    provider_repo=provider_repo,
                    requirement_status=requirement.status.value,
                    result_observed=result_observed,
                    coordinator_agent_id=coordinator_agent_id,
                    summary=f"Requirement {requirement_name} is not yet ready to resume.",
                )
            )

        valid = repo_workspace.requirement.validate_requirement_provider_truth(
            repo_root,
            requirement_name=requirement_name,
            provider_repo=provider_repo,
            require_stable=True,
        )
        if not valid.ok:
            code, message = _first_issue(valid.issues, fallback_code="requirement_provider_truth_invalid")
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    requirement_name=requirement_name,
                    provider_repo=provider_repo,
                    requirement_status=requirement.status.value,
                    result_observed=result_observed,
                    coordinator_agent_id=coordinator_agent_id,
                    issue_code=code,
                    summary=message,
                )
            )

        attached = repo_workspace.attach_provider_for_requirement(
            repo_root,
            requirement_name=requirement_name,
        )
        if not attached.ok or attached.value is None:
            code, message = _first_issue(attached.issues, fallback_code="requirement_provider_attach_failed")
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    requirement_name=requirement_name,
                    provider_repo=provider_repo,
                    requirement_status=requirement.status.value,
                    result_observed=result_observed,
                    coordinator_agent_id=coordinator_agent_id,
                    issue_code=code,
                    summary=message,
                )
            )

        current = repo_workspace.requirement.get_requirement(repo_root, name=requirement_name)
        dependencies = repo_workspace.lake_dependency.parse_lake_dependencies(repo_root)
        if not current.ok or current.value is None:
            code, message = _first_issue(current.issues, fallback_code="requirement_postcondition_missing")
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    requirement_name=requirement_name,
                    provider_repo=provider_repo,
                    result_observed=result_observed,
                    coordinator_agent_id=coordinator_agent_id,
                    issue_code=code,
                    summary=message,
                )
            )
        if not dependencies.ok or dependencies.value is None:
            code, message = _first_issue(dependencies.issues, fallback_code="lake_dependency_postcondition_failed")
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    requirement_name=requirement_name,
                    provider_repo=provider_repo,
                    requirement_status=current.value.requirement.status.value,
                    result_observed=result_observed,
                    coordinator_agent_id=coordinator_agent_id,
                    issue_code=code,
                    summary=message,
                )
            )
        requirement_handled = current.value.requirement.status == RepoDependencyRequirementStatus.HANDLED
        lake_dependency_attached = any(item.name == provider_repo for item in dependencies.value.dependencies)
        current_provider = repo_workspace.requirement.effective_provider_repo(current.value.requirement)
        current_observed = repo_workspace.requirement.is_requirement_result_observed(current.value.requirement)
        provider_matches = current_provider == provider_repo == attached.value.provider_repo
        if not requirement_handled or not lake_dependency_attached or not current_observed or not provider_matches:
            return ctx.complete_step(
                _requirement_resume_gate_invalid(
                    requirement_name=requirement_name,
                    provider_repo=provider_repo,
                    requirement_status=current.value.requirement.status.value,
                    result_observed=current_observed,
                    coordinator_agent_id=coordinator_agent_id,
                    lake_dependency_attached=lake_dependency_attached,
                    requirement_handled=requirement_handled,
                    issue_code="requirement_resume_postcondition_failed",
                    summary="Requirement resume attach did not satisfy provider, observed, handled, and Lake dependency postconditions.",
                )
            )
        return ctx.complete_step(
            CoordinatorRequirementResumeGateStepResult(
                outcome="resumed",
                requirement_name=requirement_name,
                provider_repo=provider_repo,
                requirement_status=current.value.requirement.status.value,
                result_observed=current_observed,
                lake_dependency_attached=True,
                requirement_handled=True,
                coordinator_agent_id=coordinator_agent_id,
                summary=f"Requirement {requirement_name} is handled and provider {provider_repo} is attached.",
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
        base_release_id = input_model.run_context.base_release_id if input_model.run_context is not None else None
        from lean_constellation.flows.coordinator.release_runtime import check_repo_release_runtime_closeout

        validation_snapshot = _validation_snapshot(ctx)
        runtime_closeout = check_repo_release_runtime_closeout(
            validation_snapshot.runtime,
            repo_root,
            owner_flow_id=flow.flow_id,
            phase="prepare",
        )
        if not runtime_closeout.ok or runtime_closeout.value is None or not runtime_closeout.value.passed:
            issues = runtime_closeout.issues if not runtime_closeout.ok else runtime_closeout.value.issues
            code, message = _first_issue(issues, fallback_code="repo_release_runtime_not_closed")
            return ctx.complete_step(MarkCoordinatorRepoReadyStepResult(
                outcome="candidate_blocked",
                repo_key=input_model.repo_key,
                repo_summary=self.repo_summary,
                error_code=code,
                error_message=message,
                summary=message,
            ))
        prepared = validation_snapshot.prepare_candidate_release(
            repo_root,
            base_release_id=base_release_id,
            summary=self.repo_summary,
        )
        if not prepared.ok or prepared.value is None:
            code, message = _first_issue(prepared.issues, fallback_code="repo_release_prepare_failed")
            return ctx.complete_step(
                MarkCoordinatorRepoReadyStepResult(
                    outcome="candidate_blocked",
                    repo_key=input_model.repo_key,
                    repo_summary=self.repo_summary,
                    error_code=code,
                    error_message=message,
                    summary=message,
                )
            )
        if prepared.value.outcome != "prepared" or prepared.value.prepared_release is None:
            code, message = _first_issue(prepared.value.gate.issues, fallback_code="repo_release_candidate_blocked")
            return ctx.complete_step(
                MarkCoordinatorRepoReadyStepResult(
                    outcome="candidate_blocked",
                    repo_key=input_model.repo_key,
                    repo_summary=self.repo_summary,
                    error_code=code,
                    error_message=message,
                    blocking_issue_kinds=list(prepared.value.blocking_issue_kinds),
                    summary=prepared.value.summary or message,
                )
            )
        return ctx.complete_step(
            MarkCoordinatorRepoReadyStepResult(
                outcome="candidate_prepared",
                repo_key=input_model.repo_key,
                repo_summary=self.repo_summary,
                prepared_release=prepared.value.prepared_release,
                summary=prepared.value.summary,
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


def _validate_coordinator_binding(ctx: StepRunContext, flow, coordinator_agent_id: str | None) -> tuple[str, str] | None:
    if not coordinator_agent_id:
        return "waiting_coordinator_binding_invalid", "Requirement resume requires a Flow-bound Coordinator Agent."
    agent_service = getattr(ctx.ark, "agent_service", None)
    if agent_service is None:
        return "waiting_coordinator_binding_invalid", "Requirement resume cannot validate the Coordinator Agent binding."
    try:
        agent = agent_service.get_agent(coordinator_agent_id)
    except Exception:  # noqa: BLE001 - normalize runtime store lookup as a gate result.
        return "waiting_coordinator_binding_invalid", "The Flow-bound Coordinator Agent does not exist."
    if getattr(agent, "scope_id", None) != flow.scope_id:
        return "waiting_coordinator_binding_invalid", "The Flow-bound Coordinator Agent belongs to a different scope."
    if getattr(agent, "agent_type", None) != "CoordinatorAgent":
        return "waiting_coordinator_binding_invalid", "The Flow binding does not reference a CoordinatorAgent."
    return None


def _requirement_resume_gate_invalid(
    *,
    issue_code: str,
    summary: str,
    requirement_name: str | None = None,
    provider_repo: str | None = None,
    requirement_status: str | None = None,
    result_observed: bool = False,
    lake_dependency_attached: bool = False,
    requirement_handled: bool = False,
    coordinator_agent_id: str | None = None,
) -> CoordinatorRequirementResumeGateStepResult:
    return CoordinatorRequirementResumeGateStepResult(
        outcome="invalid_requirement",
        requirement_name=requirement_name,
        provider_repo=provider_repo,
        requirement_status=requirement_status,
        result_observed=result_observed,
        lake_dependency_attached=lake_dependency_attached,
        requirement_handled=requirement_handled,
        coordinator_agent_id=coordinator_agent_id,
        issue_code=issue_code,
        summary=summary,
    )


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
    CoordinatorRequirementResumeGateStep,
    MarkCoordinatorRepoReadyStep,
)
