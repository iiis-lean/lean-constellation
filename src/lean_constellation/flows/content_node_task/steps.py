"""Content node task deterministic steps and business AgentStep results."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, FlowStepValidationError, StepTerminalReceipt
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.services.validation_snapshot.readiness_gate import ContentNodeCompletionGateView


PreparationKind = Literal["node_dir_dependency", "mathlib", "resource"]


class ContentPreparationDispatchResultView(StrictModel):
    recon_kind: PreparationKind
    objective: str | None = None
    context_summary: str | None = None
    request_count: int = 0


class ContentResourceRequestResultView(StrictModel):
    target_kind: Literal["web", "arxiv", "local_file", "local_dir"]
    target: str
    arxiv_version: str | None = None
    context_summary: str | None = None
    request_count: int = 0


class ContentDeclRoundDispatchResultView(StrictModel):
    strategy_id: str
    round_id: str
    round_index: int | None = None
    request_count: int = 0


class ContentCompletionResultView(StrictModel):
    outcome: Literal["ready", "blocked", "failed"]
    reason: str | None = None


class ContentPlanStepResult(LeanRenderableStepResult):
    result_type: Literal["content_plan"] = "content_plan"
    outcome: Literal[
        "preparation_dispatch",
        "resource_request",
        "decl_round_dispatch",
        "ready",
        "blocked",
        "failed",
        "incomplete",
    ]
    repo_key: str | None = None
    node_path: str | None = None
    preparation: ContentPreparationDispatchResultView | None = None
    resource_request: ContentResourceRequestResultView | None = None
    decl_round: ContentDeclRoundDispatchResultView | None = None
    completion: ContentCompletionResultView | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "preparation_kind": self.preparation.recon_kind if self.preparation else None,
            "resource_target": f"{self.resource_request.target_kind}:{self.resource_request.target}"
            if self.resource_request
            else None,
            "decl_round": self.decl_round.round_id if self.decl_round else None,
            "completion_reason": self.completion.reason if self.completion else None,
            "incomplete_reason": self.incomplete_reason,
        }


class NodeDirDependencyReconStepResult(LeanRenderableStepResult):
    result_type: Literal["node_dir_dependency_recon_agent"] = "node_dir_dependency_recon_agent"
    outcome: Literal["completed", "incomplete"]
    repo_key: str | None = None
    node_path: str | None = None
    dependency_change_summary: str | None = None
    checked_boundary_summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_within_visible_boundaries: list[str] = Field(default_factory=list)
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "dependency_change_summary": self.dependency_change_summary,
            "checked_boundary_summary": self.checked_boundary_summary,
            "useful_findings": list(self.useful_findings),
            "unresolved_within_visible_boundaries": list(self.unresolved_within_visible_boundaries),
            "incomplete_reason": self.incomplete_reason,
        }


class MathlibReconStepResult(LeanRenderableStepResult):
    result_type: Literal["mathlib_recon_agent"] = "mathlib_recon_agent"
    outcome: Literal["completed", "incomplete"]
    repo_key: str | None = None
    node_path: str | None = None
    index_update_summary: str | None = None
    node_mathlib_hint_summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_in_mathlib: list[str] = Field(default_factory=list)
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "index_update_summary": self.index_update_summary,
            "node_mathlib_hint_summary": self.node_mathlib_hint_summary,
            "useful_findings": list(self.useful_findings),
            "unresolved_in_mathlib": list(self.unresolved_in_mathlib),
            "incomplete_reason": self.incomplete_reason,
        }


class ResourceReconStepResult(LeanRenderableStepResult):
    result_type: Literal["resource_recon_agent"] = "resource_recon_agent"
    outcome: Literal["completed", "blocked", "resource_request", "incomplete"]
    repo_key: str | None = None
    node_path: str | None = None
    material_change_summary: str | None = None
    checked_material_summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_material_needs: list[str] = Field(default_factory=list)
    missing_targets: list[str] = Field(default_factory=list)
    reason: str | None = None
    resource_request: ContentResourceRequestResultView | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "material_change_summary": self.material_change_summary,
            "checked_material_summary": self.checked_material_summary,
            "useful_findings": list(self.useful_findings),
            "unresolved_material_needs": list(self.unresolved_material_needs),
            "missing_targets": list(self.missing_targets),
            "reason": self.reason,
            "resource_target": f"{self.resource_request.target_kind}:{self.resource_request.target}"
            if self.resource_request
            else None,
            "incomplete_reason": self.incomplete_reason,
        }


class ContentTaskAdmissionStepResult(LeanRenderableStepResult):
    result_type: Literal["content_task_admission"] = "content_task_admission"
    outcome: Literal["accepted", "rejected"]
    repo_key: str | None = None
    node_path: str | None = None
    contract_version: int | None = None
    reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "contract_version": self.contract_version,
            "reason": self.reason,
        }


class ContentCompletionAuditStepResult(LeanRenderableStepResult):
    result_type: Literal["content_completion_audit"] = "content_completion_audit"
    outcome: Literal["passed", "failed"]
    completion: ContentNodeCompletionGateView | None = None
    reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "completion": self.completion.model_dump(mode="json") if self.completion is not None else None,
            "reason": self.reason,
        }


class ContentProgressCheckpointStepResult(LeanRenderableStepResult):
    result_type: Literal["content_progress_checkpoint"] = "content_progress_checkpoint"
    outcome: Literal["checkpoint_ready", "blocked"]
    checkpoint_kind: Literal[
        "after_content_preparation_terminal",
        "after_content_decl_round_terminal",
    ]
    node_path: str
    child_kind: str
    child_flow_id: str
    child_outcome: Literal["completed", "failed"]
    decl_round_count: int | None = None
    callback_summary: str | None = None
    snapshot_id: str | None = None
    error_code: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class EnsureDeclStageAgentsStepResult(LeanRenderableStepResult):
    result_type: Literal["ensure_decl_stage_agents"] = "ensure_decl_stage_agents"
    outcome: Literal["ready", "failed"]
    initialized_roles: list[str] = Field(default_factory=list)
    reused_roles: list[str] = Field(default_factory=list)
    missing_roles: list[str] = Field(default_factory=list)
    reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "initialized_roles": list(self.initialized_roles),
            "reused_roles": list(self.reused_roles),
            "missing_roles": list(self.missing_roles),
            "reason": self.reason,
        }


class ContentTaskAdmissionStep(BaseStep):
    step_type: ClassVar[str] = "content_task_admission_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ContentTaskAdmissionStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "content_task_admission": ContentTaskAdmissionStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_content_node_task_flow(ctx)
        input_model = _require_content_node_task_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(
                ContentTaskAdmissionStepResult(
                    outcome="rejected",
                    repo_key=input_model.repo_key,
                    node_path=input_model.node_path,
                    contract_version=input_model.contract_version,
                    reason="Content node task admission requires repo_path in Flow input.",
                    summary="Content node task admission cannot run without repo_path.",
                )
            )
        gate = _node(ctx).prepare_content_task_admission(repo_root, node_path=input_model.node_path)
        if not gate.ok or gate.value is None:
            reason = _first_issue_message(gate.issues, "Content node task admission failed.")
            return ctx.complete_step(
                ContentTaskAdmissionStepResult(
                    outcome="rejected",
                    repo_key=input_model.repo_key,
                    node_path=input_model.node_path,
                    contract_version=input_model.contract_version,
                    reason=reason,
                    summary=reason,
                )
            )
        if not gate.value.passed:
            reason = gate.value.summary or _first_issue_message(gate.value.issues, "Content node task admission rejected.")
            return ctx.complete_step(
                ContentTaskAdmissionStepResult(
                    outcome="rejected",
                    repo_key=input_model.repo_key,
                    node_path=input_model.node_path,
                    contract_version=input_model.contract_version,
                    reason=reason,
                    summary=reason,
                )
            )
        return ctx.complete_step(
            ContentTaskAdmissionStepResult(
                outcome="accepted",
                repo_key=input_model.repo_key,
                node_path=input_model.node_path,
                contract_version=input_model.contract_version,
                summary=gate.value.summary or "Content node task admission accepted.",
            )
        )


class ContentProgressCheckpointStep(BaseStep):
    step_type: ClassVar[str] = "content_progress_checkpoint_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ContentProgressCheckpointStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "content_progress_checkpoint": ContentProgressCheckpointStepResult,
    }

    checkpoint_kind: Literal[
        "after_content_preparation_terminal",
        "after_content_decl_round_terminal",
    ]
    node_path: str
    child_kind: str
    child_flow_id: str
    child_outcome: Literal["completed", "failed"]
    decl_round_count: int | None = None
    callback_summary: str | None = None

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        return ctx.complete_step(
            ContentProgressCheckpointStepResult(
                outcome="checkpoint_ready",
                checkpoint_kind=self.checkpoint_kind,
                node_path=self.node_path,
                child_kind=self.child_kind,
                child_flow_id=self.child_flow_id,
                child_outcome=self.child_outcome,
                decl_round_count=self.decl_round_count,
                callback_summary=self.callback_summary,
                summary="Content task progress checkpoint is ready for stable-hook materialization.",
            )
        )


class ContentCompletionAuditStep(BaseStep):
    step_type: ClassVar[str] = "content_completion_audit_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ContentCompletionAuditStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "content_completion_audit": ContentCompletionAuditStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_content_node_task_flow(ctx)
        input_model = _require_content_node_task_input(flow.input)
        repo_root = _repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(
                ContentCompletionAuditStepResult(
                    outcome="failed",
                    reason="Content completion audit requires repo_path in Flow input.",
                    summary="Content completion audit cannot run without repo_path.",
                )
            )
        audited = _validation_snapshot(ctx).check_content_node_completion(
            repo_root,
            node_path=input_model.node_path,
        )
        if not audited.ok or audited.value is None:
            reason = _first_issue_message(audited.issues, "Content completion audit failed.")
            return ctx.complete_step(
                ContentCompletionAuditStepResult(
                    outcome="failed",
                    reason=reason,
                    summary=reason,
                )
            )
        passed = audited.value.gate.passed
        return ctx.complete_step(
            ContentCompletionAuditStepResult(
                outcome="passed" if passed else "failed",
                completion=audited.value,
                reason=None if passed else audited.value.summary,
                summary=audited.value.summary,
            )
        )


class EnsureDeclStageAgentsStep(BaseStep):
    step_type: ClassVar[str] = "ensure_decl_stage_agents_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = EnsureDeclStageAgentsStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "ensure_decl_stage_agents": EnsureDeclStageAgentsStepResult,
    }

    STAGE_ROLES: ClassVar[tuple[str, ...]] = (
        "statement_nl_worker",
        "statement_nl_reviewer",
        "statement_formal_worker",
        "statement_formal_reviewer",
        "proof_nl_worker",
        "proof_nl_reviewer",
        "proof_formal_worker",
        "proof_formal_reviewer",
    )
    STAGE_AGENT_TYPES: ClassVar[dict[str, str]] = {
        "statement_nl_worker": "StatementNLWorkerAgent",
        "statement_nl_reviewer": "StatementNLReviewerAgent",
        "statement_formal_worker": "StatementFormalWorkerAgent",
        "statement_formal_reviewer": "StatementFormalReviewerAgent",
        "proof_nl_worker": "ProofNLWorkerAgent",
        "proof_nl_reviewer": "ProofNLReviewerAgent",
        "proof_formal_worker": "ProofFormalWorkerAgent",
        "proof_formal_reviewer": "ProofFormalReviewerAgent",
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow_service = ctx.ark.flow_service
        if flow_service is None:
            raise FlowStepValidationError("ark.flow_service is not registered")
        agent_service = ctx.ark.agent_service
        if agent_service is None:
            raise FlowStepValidationError("ark.agent_service is not registered")
        flow = flow_service.get_flow(ctx.flow_id)
        existing = [role for role in self.STAGE_ROLES if flow.agent_bindings.get(role)]
        initialized: list[str] = []
        bindings: dict[str, str] = {}
        for role in self.STAGE_ROLES:
            if flow.agent_bindings.get(role):
                continue
            agent_type = self.STAGE_AGENT_TYPES[role]
            agent = agent_service.create_agent(ctx.scope_id, agent_type, home_id=agent_type)
            agent_id = str(agent.agent_id)
            bindings[role] = agent_id
            initialized.append(role)
        if bindings:
            flow_service.store.update_flow_record(
                ctx.flow_id,
                lambda stored: stored.agent_bindings.by_role.update(bindings),
            )
        return ctx.complete_step(
            EnsureDeclStageAgentsStepResult(
                outcome="ready",
                initialized_roles=initialized,
                reused_roles=existing,
                summary="Decl stage agent roles are ready for this content task.",
            )
        )


def new_content_step_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _load_content_node_task_flow(ctx: StepRunContext):
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    flow = flow_service.get_flow(ctx.flow_id)
    if flow.flow_type != "content_node_task":
        raise FlowStepValidationError(f"expected content_node_task flow, got {flow.flow_type}")
    return flow


def _require_content_node_task_input(input_model):
    from lean_constellation.flows.content_node_task.flows import ContentNodeTaskInput

    if not isinstance(input_model, ContentNodeTaskInput):
        raise FlowStepValidationError("content_node_task flow has invalid input")
    return input_model


def _repo_root(input_model) -> Path | None:
    raw = getattr(input_model, "repo_path", None)
    if not raw:
        return None
    return Path(raw)


def _node(ctx: StepRunContext):
    node = getattr(ctx.app, "node", None)
    if node is None:
        raise FlowStepValidationError("Lean node service is not registered in app services.")
    return node


def _validation_snapshot(ctx: StepRunContext):
    service = getattr(ctx.app, "validation_snapshot", None)
    if service is None:
        raise FlowStepValidationError("Lean validation_snapshot service is not registered in app services.")
    return service


def _first_issue_message(issues: list[object], fallback: str) -> str:
    if issues:
        issue = issues[0]
        return str(getattr(issue, "message", None) or getattr(issue, "summary", None) or fallback)
    return fallback


CONTENT_NODE_TASK_STEP_TYPES: tuple[type[BaseStep], ...] = (
    ContentTaskAdmissionStep,
    ContentCompletionAuditStep,
    ContentProgressCheckpointStep,
    EnsureDeclStageAgentsStep,
)
