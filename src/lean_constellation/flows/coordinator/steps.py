"""Native repo coordinator deterministic steps and business AgentStep results."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, FlowStepValidationError, StepTerminalReceipt
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.mathlib import MathlibIndex
from lean_constellation.flows.common.checkpoint_policy import repo_flow_boundary_checkpoints_enabled
from lean_constellation.domain.preparation import (
    ProviderRoute,
    RepoDependencyRequirementStatus,
)
from lean_constellation.domain.repo import ProofAvailability, RepoFormat, RepoPublicationStatus
from lean_constellation.domain.publication import ReleasePolicy
from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.flows.coordinator.submissions import (
    RepoExplorationKind,
    RepoExplorationSpec,
)
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


class CoordinatorRepoExplorationResultView(StrictModel):
    kinds: list[Literal["resource", "lean_provider", "mathlib"]] = Field(default_factory=list)


class InitialRepoExplorationContextView(StrictModel):
    run_objective: str
    completion_mode: str
    source_overview: str | None = None
    source_file_count: int = 0
    source_block_count: int = 0
    protected_root_interfaces: list[str] = Field(default_factory=list)
    resource_count: int = 0
    ready_provider_repos: list[str] = Field(default_factory=list)
    requirement_count: int = 0
    lake_dependencies: list[str] = Field(default_factory=list)
    mathlib_module_count: int = 0
    mathlib_decl_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    summary: str


class InitialRepoExplorationPlanStepResult(LeanRenderableStepResult):
    result_type: Literal["initial_repo_exploration_plan"] = "initial_repo_exploration_plan"
    outcome: Literal["planned", "not_required", "blocked"]
    plan_id: str | None = None
    explorations: list[RepoExplorationSpec] = Field(default_factory=list)
    context: InitialRepoExplorationContextView | None = None
    reason: str | None = None
    issue_code: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "plan_id": self.plan_id,
            "exploration_kinds": [spec.kind.value for spec in self.explorations],
            "context": self.context.model_dump(mode="json") if self.context is not None else None,
            "reason": self.reason,
            "issue_code": self.issue_code,
        }


class CoordinatorRepoRequirementResultView(StrictModel):
    requirement_name: str
    target_repo: str
    provider_route: ProviderRoute
    required_proof_availability: ProofAvailability = ProofAvailability.DECLARED
    reason: str | None = None
    source_description: str | None = None
    interface_count: int = 0


class CoordinatorRepoReadyResultView(StrictModel):
    repo_summary: str


class CoordinatorStepResult(LeanRenderableStepResult):
    result_type: Literal["coordinator"] = "coordinator"
    outcome: Literal["content_tasks", "resource_request", "repo_exploration", "repo_requirement", "repo_ready", "incomplete"]
    repo_key: str | None = None
    content_tasks: CoordinatorContentTasksResultView | None = None
    resource_request: CoordinatorResourceRequestResultView | None = None
    repo_exploration: CoordinatorRepoExplorationResultView | None = None
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
            "exploration_kinds": list(self.repo_exploration.kinds) if self.repo_exploration else None,
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
        "before_repo_exploration_dispatch",
        "after_repo_exploration_terminal",
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
            "blocking_issue_kinds": list(self.blocking_issue_kinds),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


class EnsureRepoExplorationAgentsStepResult(LeanRenderableStepResult):
    result_type: Literal["ensure_repo_exploration_agents"] = "ensure_repo_exploration_agents"
    outcome: Literal["ready"]
    created_roles: list[str] = Field(default_factory=list)
    reused_roles: list[str] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "created_roles": list(self.created_roles),
            "reused_roles": list(self.reused_roles),
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
        "before_repo_exploration_dispatch",
        "after_repo_exploration_terminal",
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


class EnsureRepoExplorationAgentsStep(BaseStep):
    step_type: ClassVar[str] = "ensure_repo_exploration_agents_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = EnsureRepoExplorationAgentsStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "ensure_repo_exploration_agents": EnsureRepoExplorationAgentsStepResult,
    }

    ROLE_BY_KIND: ClassVar[dict[str, tuple[str, str]]] = {
        "resource": ("repo_resource_discovery", "RepoResourceDiscoveryAgent"),
        "lean_provider": ("repo_lean_provider_discovery", "RepoLeanProviderDiscoveryAgent"),
        "mathlib": ("repo_mathlib_recon", "RepoMathlibReconAgent"),
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        from lean_constellation.flows.coordinator.submissions import (
            CoordinatorRepoExplorationSubmission,
        )

        flow = _load_native_coordinator_flow(ctx)
        source_step_id = getattr(flow.state, "pending_dispatch_source_step_id", None)
        if not source_step_id:
            raise FlowStepValidationError("repo exploration ensure requires a source Coordinator step")
        source_step = ctx.ark.flow_service.get_step(source_step_id)
        submission = source_step.submission
        if isinstance(source_step.result, InitialRepoExplorationPlanStepResult):
            if source_step.result.outcome != "planned":
                raise FlowStepValidationError("initial repo exploration source plan is not dispatchable")
            explorations = source_step.result.explorations
        elif isinstance(submission, CoordinatorRepoExplorationSubmission):
            explorations = submission.explorations
        else:
            raise FlowStepValidationError("repo exploration ensure source is neither a deterministic plan nor a Coordinator submission")
        agent_service = ctx.ark.agent_service
        if agent_service is None:
            raise FlowStepValidationError("ark.agent_service is not registered")

        created: list[str] = []
        reused: list[str] = []
        bindings: dict[str, str] = {}
        for spec in explorations:
            role, agent_type = self.ROLE_BY_KIND[spec.kind.value]
            agent_id = flow.agent_bindings.get(role)
            if agent_id is None:
                agent_id = _prior_repo_exploration_agent_id(
                    ctx,
                    flow=flow,
                    role=role,
                    agent_type=agent_type,
                )
            if agent_id is None:
                agent = agent_service.create_agent(ctx.scope_id, agent_type, home_id=agent_type)
                agent_id = str(agent.agent_id)
                created.append(role)
            else:
                agent = agent_service.get_agent(agent_id)
                if agent.agent_type != agent_type or agent.scope_id != ctx.scope_id:
                    raise FlowStepValidationError(
                        f"invalid reusable exploration Agent binding for {role}"
                    )
                if agent.status != "idle":
                    raise FlowStepValidationError(
                        f"exploration Agent {agent_id} for {role} is not idle"
                    )
                reused.append(role)
            bindings[role] = agent_id
        if bindings:
            ctx.ark.flow_service.store.update_flow_record(
                ctx.flow_id,
                lambda stored: stored.agent_bindings.by_role.update(bindings),
            )
        return ctx.complete_step(
            EnsureRepoExplorationAgentsStepResult(
                outcome="ready",
                created_roles=created,
                reused_roles=reused,
                summary="Requested repository exploration Agent roles are ready.",
            )
        )


class InitialRepoExplorationPlanStep(BaseStep):
    step_type: ClassVar[str] = "initial_repo_exploration_plan_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = InitialRepoExplorationPlanStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "initial_repo_exploration_plan": InitialRepoExplorationPlanStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_native_coordinator_flow(ctx)
        input_model = _require_native_coordinator_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(
                InitialRepoExplorationPlanStepResult(
                    outcome="blocked",
                    issue_code="repo_root_missing",
                    reason="Initial repository exploration requires repo_root.",
                    summary="Initial repository exploration planning is blocked without repo_root.",
                )
            )
        repo_workspace = _repo_workspace(ctx)
        repo_format = repo_workspace.metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            code, message = _first_issue(repo_format.issues, fallback_code="repo_format_invalid")
            return ctx.complete_step(
                InitialRepoExplorationPlanStepResult(
                    outcome="blocked",
                    issue_code=code,
                    reason=message,
                    summary="Initial repository exploration could not read repository format truth.",
                )
            )
        if repo_format.value.repo_format is not RepoFormat.NATIVE:
            return ctx.complete_step(
                InitialRepoExplorationPlanStepResult(
                    outcome="not_required",
                    reason="Initial exploration applies only after native repository preparation.",
                    summary="Initial repository exploration is not required for this repository format.",
                )
            )
        if _has_terminal_initial_repo_exploration(ctx, flow):
            return ctx.complete_step(
                InitialRepoExplorationPlanStepResult(
                    outcome="not_required",
                    reason="A terminal initial exploration batch already exists for this repository scope.",
                    summary="Initial repository exploration was already completed.",
                )
            )
        nodes = ctx.app.node.node_tree.node_store.list_nodes(repo_root)
        requirements = repo_workspace.requirement.list_requirements(repo_root)
        latest_release = repo_workspace.release.get_latest_release(repo_root)
        publication = repo_workspace.metadata.get_repo_publication(repo_root)
        for loaded, fallback in (
            (nodes, "node_truth_invalid"),
            (requirements, "requirement_truth_invalid"),
            (publication, "publication_truth_invalid"),
        ):
            if not loaded.ok or loaded.value is None:
                code, message = _first_issue(loaded.issues, fallback_code=fallback)
                return ctx.complete_step(
                    InitialRepoExplorationPlanStepResult(
                        outcome="blocked",
                        issue_code=code,
                        reason=message,
                        summary="Initial repository exploration could not verify the fresh repository boundary.",
                    )
                )
        if not latest_release.ok:
            code, message = _first_issue(latest_release.issues, fallback_code="release_truth_invalid")
            return ctx.complete_step(
                InitialRepoExplorationPlanStepResult(
                    outcome="blocked",
                    issue_code=code,
                    reason=message,
                    summary="Initial repository exploration could not verify Release truth.",
                )
            )
        business_nodes = [node.path for node in nodes.value if node.path != "Main"]
        root_closed = any(
            node.path == "Main" and getattr(node.lifecycle, "value", node.lifecycle) != "active"
            for node in nodes.value
        )
        repo_ready = (
            latest_release.value is not None
            or publication.value.publication.status is RepoPublicationStatus.STABLE
        )
        if business_nodes or root_closed or requirements.value or repo_ready:
            reasons = []
            if business_nodes:
                reasons.append(f"business nodes: {', '.join(business_nodes[:5])}")
            if root_closed:
                reasons.append("Main lifecycle is already terminal")
            if requirements.value:
                reasons.append(f"requirements: {len(requirements.value)}")
            if repo_ready:
                reasons.append("repository release/publication truth exists")
            return ctx.complete_step(
                InitialRepoExplorationPlanStepResult(
                    outcome="not_required",
                    reason="; ".join(reasons),
                    summary="Repository business truth is already beyond the initial preparation boundary.",
                )
            )

        context = _initial_repo_exploration_context(
            ctx,
            input_model=input_model,
            repo_root=repo_root,
            requirement_count=len(requirements.value),
        )
        objective = _bounded_text(context.run_objective, limit=240)
        context_summary = context.summary
        explorations = [
            RepoExplorationSpec(
                kind=RepoExplorationKind.RESOURCE,
                objective=(
                    "Find authoritative external material not already covered by current Source/Resources that can support "
                    f"the main definitions, results, or proof route for: {objective}"
                ),
                context_summary=context_summary,
            ),
            RepoExplorationSpec(
                kind=RepoExplorationKind.LEAN_PROVIDER,
                objective=(
                    "Find and verify Lean 4/Lake repositories that may directly provide relevant definitions, theorems, "
                    f"or an adapter/provider route for: {objective}"
                ),
                context_summary=context_summary,
            ),
            RepoExplorationSpec(
                kind=RepoExplorationKind.MATHLIB,
                objective=(
                    "Verify repository-level Mathlib modules and declarations needed by the project theme, including "
                    f"the main representation constraints for: {objective}"
                ),
                context_summary=context_summary,
            ),
        ]
        return ctx.complete_step(
            InitialRepoExplorationPlanStepResult(
                outcome="planned",
                plan_id=f"initial_repo_exploration_plan_{uuid.uuid4().hex}",
                explorations=explorations,
                context=context,
                summary="Planned the fixed resource, Lean-provider, and Mathlib initial exploration batch.",
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
        publication_policy = (
            validation_snapshot.runtime.repo_workspace.publication.resolve_policy(
                repo_root
            )
        )
        if not publication_policy.ok or publication_policy.value is None:
            code, message = _first_issue(
                publication_policy.issues,
                fallback_code="repo_publication_policy_invalid",
            )
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
        audited = validation_snapshot.preview_candidate_release(
            repo_root,
            base_release_id=base_release_id,
            summary=self.repo_summary,
        )
        if not audited.ok or audited.value is None:
            code, message = _first_issue(
                audited.issues,
                fallback_code="repo_release_audit_failed",
            )
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
        if not audited.value.gate.passed:
            code, message = _first_issue(
                audited.value.gate.issues,
                fallback_code="repo_release_candidate_blocked",
            )
            return ctx.complete_step(
                MarkCoordinatorRepoReadyStepResult(
                    outcome="candidate_blocked",
                    repo_key=input_model.repo_key,
                    repo_summary=self.repo_summary,
                    error_code=code,
                    error_message=message,
                    blocking_issue_kinds=list(audited.value.blocking_issue_kinds),
                    summary=audited.value.summary or message,
                )
            )
        if publication_policy.value.policy.release_policy == ReleasePolicy.MANUAL:
            return ctx.complete_step(
                MarkCoordinatorRepoReadyStepResult(
                    outcome="ready_marked",
                    repo_key=input_model.repo_key,
                    repo_summary=self.repo_summary,
                    summary=(
                        "Repository passed the authoritative repo-ready audit; publication policy defers "
                        "Semantic Release to an explicit operator action."
                    ),
                )
            )
        prepared = validation_snapshot.prepare_candidate_release(
            repo_root,
            base_release_id=base_release_id,
            summary=self.repo_summary,
            audited=audited.value,
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


def _prior_repo_exploration_agent_id(
    ctx: StepRunContext,
    *,
    flow,
    role: str,
    agent_type: str,
) -> str | None:
    candidates = [
        candidate
        for candidate in ctx.ark.flow_service.list_flows()
        if candidate.flow_id != flow.flow_id
        and candidate.flow_type == "native_repo_coordinator"
        and candidate.scope_id == flow.scope_id
        and candidate.agent_bindings.get(role)
    ]
    candidates.sort(key=lambda candidate: candidate.created_at)
    for candidate in reversed(candidates):
        agent_id = candidate.agent_bindings.get(role)
        if not agent_id:
            continue
        try:
            agent = ctx.ark.agent_service.get_agent(agent_id)
        except Exception:  # noqa: BLE001 - ignore stale historical bindings.
            continue
        if (
            agent.agent_type == agent_type
            and agent.scope_id == flow.scope_id
            and agent.status == "idle"
        ):
            return str(agent_id)
    return None


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


def _has_terminal_initial_repo_exploration(ctx: StepRunContext, current_flow) -> bool:  # noqa: ANN001
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        return False
    candidates = [
        flow
        for flow in flow_service.list_flows()
        if flow.flow_type == "native_repo_coordinator"
        and flow.scope_id == current_flow.scope_id
    ]
    for flow in candidates:
        planned = False
        for step_id in flow.step_ids:
            step = flow_service.get_step(step_id)
            if (
                isinstance(step.result, InitialRepoExplorationPlanStepResult)
                and step.result.outcome == "planned"
            ):
                planned = True
                continue
            if (
                planned
                and isinstance(step.result, CoordinatorContentBatchSnapshotStepResult)
                and step.result.checkpoint_kind == "after_repo_exploration_terminal"
                and step.result.outcome in {"snapshot_created", "skipped"}
            ):
                return True
    return False


def _initial_repo_exploration_context(
    ctx: StepRunContext,
    *,
    input_model,
    repo_root: Path,
    requirement_count: int,
) -> InitialRepoExplorationContextView:
    warnings: list[str] = []

    def record(label: str, result) -> None:  # noqa: ANN001
        if not result.ok:
            code, _ = _first_issue(result.issues, fallback_code=f"{label}_unavailable")
            warnings.append(code)

    config = ctx.app.repo_workspace.metadata.get_repo_config(repo_root)
    record("repo_config", config)
    completion_mode = (
        config.value.config.completion_mode.value
        if config.ok and config.value is not None
        else "unknown"
    )
    run_objective = (
        input_model.run_context.run_spec.run_objective
        if input_model.run_context is not None
        else input_model.start_reason
        or f"Complete native repository {input_model.repo_key or repo_root.name}."
    )

    manifest = ctx.app.material.source_corpus.get_source_corpus_manifest(repo_root)
    record("source_corpus", manifest)
    source_file_count = len(manifest.value.files) if manifest.ok and manifest.value is not None else 0
    source_overview = manifest.value.overview if manifest.ok and manifest.value is not None else None

    source_index = ctx.app.material.source_index.get_source_index_overview(
        repo_root,
        require_committed=True,
    )
    record("source_index", source_index)
    source_block_count = (
        source_index.value.block_count
        if source_index.ok and source_index.value is not None
        else 0
    )
    if source_overview is None and source_index.ok and source_index.value is not None:
        source_overview = source_index.value.overview

    interfaces = ctx.app.node.interface.list_interfaces(repo_root, node_path="Main")
    record("root_interfaces", interfaces)
    protected_root_interfaces = (
        list(interfaces.value.protected_names)
        if interfaces.ok and interfaces.value is not None
        else []
    )

    resources = ctx.app.material.resource_library.list_resources(repo_root)
    record("resources", resources)
    resource_count = len(resources.value) if resources.ok and resources.value is not None else 0

    providers = ctx.app.repo_workspace.workspace_catalog.list_ready_provider_repos(
        repo_root.parent,
        current_repo=repo_root.name,
    )
    record("providers", providers)
    ready_provider_repos = (
        [item.repo_key for item in providers.value]
        if providers.ok and providers.value is not None
        else []
    )

    lake = ctx.app.repo_workspace.lake_dependency.parse_lake_dependencies(repo_root)
    record("lake_dependencies", lake)
    lake_dependencies = (
        [item.name for item in lake.value.dependencies]
        if lake.ok and lake.value is not None
        else []
    )

    mathlib_path = ctx.app.mathlib.mathlib_index.index_path(repo_root)
    if mathlib_path.exists():
        mathlib = ctx.app.foundation.store.read_json(mathlib_path, MathlibIndex)
        record("mathlib_index", mathlib)
    else:
        mathlib = ctx.app.foundation.ok(MathlibIndex())
    mathlib_module_count = len(mathlib.value.modules) if mathlib.ok and mathlib.value is not None else 0
    mathlib_decl_count = len(mathlib.value.declarations) if mathlib.ok and mathlib.value is not None else 0

    summary = " | ".join(
        [
            f"objective={_bounded_text(run_objective, limit=180)}",
            f"completion={completion_mode}",
            f"source_files={source_file_count}",
            f"source_blocks={source_block_count}",
            f"root_interfaces={','.join(protected_root_interfaces[:8]) or 'none'}",
            f"resources={resource_count}",
            f"providers={','.join(ready_provider_repos[:8]) or 'none'}",
            f"requirements={requirement_count}",
            f"lake_deps={','.join(lake_dependencies[:8]) or 'none'}",
            f"mathlib={mathlib_module_count} modules/{mathlib_decl_count} decls",
        ]
    )
    return InitialRepoExplorationContextView(
        run_objective=run_objective,
        completion_mode=completion_mode,
        source_overview=_bounded_text(source_overview, limit=400) if source_overview else None,
        source_file_count=source_file_count,
        source_block_count=source_block_count,
        protected_root_interfaces=protected_root_interfaces,
        resource_count=resource_count,
        ready_provider_repos=ready_provider_repos,
        requirement_count=requirement_count,
        lake_dependencies=lake_dependencies,
        mathlib_module_count=mathlib_module_count,
        mathlib_decl_count=mathlib_decl_count,
        warnings=sorted(set(warnings)),
        summary=_bounded_text(summary, limit=1200),
    )


def _bounded_text(value: str, *, limit: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(0, limit - 3)].rstrip()}..."


COORDINATOR_STEP_TYPES: tuple[type[BaseStep], ...] = (
    InitialRepoExplorationPlanStep,
    CoordinatorContentBatchSnapshotStep,
    EnsureRepoExplorationAgentsStep,
    CoordinatorRequirementResumeGateStep,
    MarkCoordinatorRepoReadyStep,
)
