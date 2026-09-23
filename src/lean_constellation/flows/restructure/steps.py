"""ARK AgentSteps for the Restructure workflow."""

from __future__ import annotations

from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import (
    BaseStep,
    BaseStepResult,
    BaseStepState,
    BaseSubmission,
    StepTerminalReceipt,
)
from agent_runtime_kit.flow.standard_steps.agent_step import AgentStep
from pydantic import Field

from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.flows.restructure.submissions import (
    RestructureContentSubmission,
    RestructureRepoPlanSubmission,
    RestructureRepoRepairSubmission,
    RestructureReviewSubmission,
)
from lean_constellation.services.restructure import RestructureService


def validate_execution(ctx):
    flow = ctx.ark.flow_service.get_flow(ctx.flow_id)
    inp = flow.input
    if not inp.workspace_root:
        raise ValueError("Restructure requires a prepared workspace and supervisor admission")
    service = RestructureService(inp.workspace_root)
    run, _ = service.store.load_run()
    binding = next((b for b in run.bindings.values() if b.request_id == inp.request_id and not b.stale), None) if run else None
    if binding is None or binding.flow_id != flow.flow_id or binding.terminal_consumed:
        raise ValueError("Restructure task binding is absent or stale")
    reservations = service.store.load_reservations()
    if not any(r.reservation_id == inp.reservation_id and r.owner_ref == inp.request_id and r.released_at is None for r in reservations):
        raise ValueError("Restructure execution requires an active budget reservation")
    return service


class RestructureAgentStep(AgentStep):
    def prepare_agent(self, ctx):
        validate_execution(ctx)
        return super().prepare_agent(ctx)


def _submission_map(*classes: type[BaseSubmission]) -> dict[str, type[BaseSubmission]]:
    return {str(cls.model_fields["submission_type"].default): cls for cls in classes}


class RestructureContentStepResult(LeanRenderableStepResult):
    result_type: Literal["restructure_content"] = "restructure_content"
    outcome: Literal["declared", "proved", "blocked", "incomplete"]
    stage: str
    changed_decl_names: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class RestructureRepoPlanStepResult(LeanRenderableStepResult):
    result_type: Literal["restructure_repo_plan"] = "restructure_repo_plan"
    outcome: Literal["planned", "blocked", "incomplete"]
    plan_version: int | None = None
    issues: list[str] = Field(default_factory=list)


class RestructureReviewStepResult(LeanRenderableStepResult):
    result_type: Literal["restructure_review"] = "restructure_review"
    outcome: Literal["passed", "needs_content_fix", "needs_replan", "blocked", "incomplete"]
    artifact_id: str
    findings: list[str] = Field(default_factory=list)


class RestructureBuildStepResult(LeanRenderableStepResult):
    result_type: Literal["restructure_build"] = "restructure_build"
    outcome: Literal["succeeded", "failed", "blocked"]
    operation_id: str
    diagnostics: list[str] = Field(default_factory=list)


class RestructureRepoRepairStepResult(LeanRenderableStepResult):
    result_type: Literal["restructure_repo_repair"] = "restructure_repo_repair"
    outcome: Literal["repaired", "blocked", "incomplete"]
    issues: list[str] = Field(default_factory=list)


class RestructureBuildStep(BaseStep):
    """Run one frozen repository build through the existing build service."""

    step_type: ClassVar[str] = "restructure_build_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RestructureBuildStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "restructure_build": RestructureBuildStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = ctx.ark.flow_service.get_flow(ctx.flow_id)
        input_model = flow.input
        service = validate_execution(ctx)
        if service is None:
            if not input_model.workspace_root:
                return ctx.complete_step(
                    RestructureBuildStepResult(
                        outcome="blocked",
                        operation_id=input_model.operation_id or "",
                        diagnostics=["restructure service is not registered and workspace_root is missing"],
                        summary="Restructure build is not configured.",
                    )
                )
            service = RestructureService(input_model.workspace_root)
        try:
            result = service.build_repo(
                input_model.repo_key,
                operation_id=(input_model.operation_id if not flow.state.repair_attempts else None),
                targets=input_model.targets,
                stage=input_model.stage,
                provider_refs=input_model.provider_refs,
                declared_repair=input_model.stage == "declared" and flow.state.repair_attempts > 0,
                request_id=input_model.request_id,
            )


        except Exception as exc:  # noqa: BLE001 - build diagnostics are terminal data.
            return ctx.complete_step(
                RestructureBuildStepResult(
                    outcome="failed",
                    operation_id=input_model.operation_id or "",
                    diagnostics=[str(exc)],
                    summary="Restructure repository build failed before completion.",
                )
            )
        return ctx.complete_step(
            RestructureBuildStepResult(
                outcome="succeeded" if result.receipt.success else "failed",
                operation_id=result.receipt.operation_id,
                diagnostics=list(result.receipt.diagnostics),
                summary="Restructure repository build completed." if result.receipt.success else "Restructure repository build failed.",
            )
        )


class RestructureRepoRepairAgentStep(RestructureAgentStep):
    step_type: ClassVar[str] = "restructure_repo_repair_agent_step"
    offline_submission_finalize_supported: ClassVar[bool] = True
    Results = {**AgentStep.Results, "restructure_repo_repair": RestructureRepoRepairStepResult}
    Submissions = _submission_map(RestructureRepoRepairSubmission)
    SubmitTools = {"submit_restructure_repo_repair"}

    def prepare_agent(self, ctx):
        service = validate_execution(ctx)
        inp = ctx.ark.flow_service.get_flow(ctx.flow_id).input
        if inp.stage == "declared":
            from lean_constellation.services.restructure.repair import prepare_declared_repair
            prepare_declared_repair(service, inp.repo_key, inp.request_id)
        return super().prepare_agent(ctx)

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RestructureRepoRepairSubmission):
            return RestructureRepoRepairStepResult(
                outcome=submission.outcome,
                issues=list(submission.issues),
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id, reason, turn_result, attempt_count):
        del ctx, agent_id, turn_result, attempt_count
        return RestructureRepoRepairStepResult(outcome="incomplete", summary=reason)


class RestructureContentAgentStep(RestructureAgentStep):
    step_type: ClassVar[str] = "restructure_content_agent_step"
    offline_submission_finalize_supported: ClassVar[bool] = True
    Results = {**AgentStep.Results, "restructure_content": RestructureContentStepResult}
    Submissions = _submission_map(RestructureContentSubmission)
    SubmitTools = {"submit_restructure_content"}

    def validate_submission(self, ctx, submission):
        super().validate_submission(ctx, submission)
        if isinstance(submission, RestructureContentSubmission):
            flow = ctx.ark.flow_service.get_flow(ctx.flow_id)
            inp = flow.input
            if submission.node_path != inp.node_path or submission.repo_key != inp.repo_key or submission.stage != inp.stage:
                raise ValueError("Content submission does not match its task")
            if submission.outcome not in {"blocked", inp.stage}:
                raise ValueError("Content outcome does not match its stage")

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RestructureContentSubmission):
            service = validate_execution(ctx)
            inp = ctx.ark.flow_service.get_flow(ctx.flow_id).input
            if submission.outcome != "blocked":
                from lean_constellation.domain.restructure import RestructureStage
                with service.store._lock:
                    work = service.content.submit(inp.directory, inp.node_path, stage=RestructureStage(inp.stage))
                    artifact = service.artifacts.seal_content(inp.directory, work, stage=RestructureStage(inp.stage))
                    current, version = service.content.load(inp.directory, inp.node_path)
                    current.accepted_artifact_ids.append(artifact.artifact_id)
                    service.store.save_content(inp.directory, inp.node_path, current, expected_version=version)
            return RestructureContentStepResult(
                outcome=submission.outcome,
                stage=submission.stage,
                changed_decl_names=list(submission.changed_decl_names),
                issues=list(submission.issues),
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id, reason, turn_result, attempt_count):
        del ctx, agent_id, turn_result, attempt_count
        return RestructureContentStepResult(outcome="incomplete", stage="unknown", summary=reason)


class RestructureRepoPlanAgentStep(RestructureAgentStep):
    step_type: ClassVar[str] = "restructure_repo_plan_agent_step"
    offline_submission_finalize_supported: ClassVar[bool] = True
    Results = {**AgentStep.Results, "restructure_repo_plan": RestructureRepoPlanStepResult}
    Submissions = _submission_map(RestructureRepoPlanSubmission)
    SubmitTools = {"submit_restructure_repo_plan"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RestructureRepoPlanSubmission):
            return RestructureRepoPlanStepResult(
                outcome=submission.outcome,
                plan_version=submission.plan_version,
                issues=list(submission.issues),
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id, reason, turn_result, attempt_count):
        del ctx, agent_id, turn_result, attempt_count
        return RestructureRepoPlanStepResult(outcome="incomplete", summary=reason)


class RestructureReviewAgentStep(RestructureAgentStep):
    step_type: ClassVar[str] = "restructure_review_agent_step"
    offline_submission_finalize_supported: ClassVar[bool] = True
    Results = {**AgentStep.Results, "restructure_review": RestructureReviewStepResult}
    Submissions = _submission_map(RestructureReviewSubmission)
    SubmitTools = {"submit_restructure_review"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RestructureReviewSubmission):
            return RestructureReviewStepResult(
                outcome=submission.outcome,
                artifact_id=submission.artifact_id,
                findings=list(submission.findings),
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id, reason, turn_result, attempt_count):
        del ctx, agent_id, turn_result, attempt_count
        return RestructureReviewStepResult(outcome="incomplete", artifact_id="", summary=reason)


RESTRUCTURE_AGENT_STEP_TYPES = (
    RestructureContentAgentStep,
    RestructureRepoPlanAgentStep,
    RestructureReviewAgentStep,
    RestructureRepoRepairAgentStep,
)

RESTRUCTURE_LOGIC_STEP_TYPES = (RestructureBuildStep,)


def new_restructure_step_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
