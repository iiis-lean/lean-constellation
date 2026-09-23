"""Dedicated Repo/Content root Flows for Restructure."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, BaseStepState, FlowPosition
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.restructure.steps import (
    RestructureBuildStep,
    RestructureBuildStepResult,
    RestructureRepoRepairAgentStep,
    RestructureRepoRepairStepResult,
    RestructureContentAgentStep,
    RestructureContentStepResult,
    RestructureRepoPlanAgentStep,
    RestructureRepoPlanStepResult,
    RestructureReviewAgentStep,
    RestructureReviewStepResult,
    new_restructure_step_id,
)


class RestructureContentParams(LeanFlowParams):
    repo_key: str
    directory: str
    node_path: str
    stage: Literal["declared", "proved"]
    agent_id: str | None = None
    agent_type: str = "RestructureContentPlanAgent"
    request_id: str | None = None
    attempt_epoch: int = 0
    reservation_id: str | None = None
    workspace_root: str | None = None
    application_tool_view: str = "restructure_content"
    submit_tool_view: str = "restructure_content_submit"


class RestructureBuildParams(LeanFlowParams):
    repo_key: str
    directory: str
    stage: Literal["declared", "proved", "final"] = "proved"
    request_id: str | None = None
    attempt_epoch: int = 0
    reservation_id: str | None = None
    workspace_root: str | None = None
    operation_id: str | None = None
    targets: list[str] = Field(default_factory=list)
    provider_refs: dict[str, str] = Field(default_factory=dict)
    agent_id: str | None = None
    agent_type: str = "RestructureCoordinatorAgent"
    application_tool_view: str = "restructure_repo_repair"
    submit_tool_view: str = "restructure_repo_repair_submit"
    max_repair_attempts: int = 3


class RestructureBuildInput(LeanRenderableFlowInput):
    input_type: Literal["restructure_build"] = "restructure_build"
    repo_key: str
    directory: str
    stage: Literal["declared", "proved", "final"]
    request_id: str | None = None
    attempt_epoch: int = 0
    reservation_id: str | None = None
    workspace_root: str | None = None
    operation_id: str | None = None
    targets: list[str] = Field(default_factory=list)
    provider_refs: dict[str, str] = Field(default_factory=dict)
    agent_id: str | None = None
    agent_type: str = "RestructureCoordinatorAgent"
    application_tool_view: str = "restructure_repo_repair"
    submit_tool_view: str = "restructure_repo_repair_submit"
    max_repair_attempts: int = 3

    def agent_title(self) -> str:
        return f"Build Restructure repo {self.repo_key} ({self.stage})"

    def agent_fields(self) -> dict[str, object]:
        return {"repo_key": self.repo_key, "stage": self.stage, "targets": self.targets}


class RestructureBuildState(BaseFlowState):
    state_type: Literal["restructure_build"] = "restructure_build"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="build"))
    repair_attempts: int = 0
    diagnostics: list[str] = Field(default_factory=list)
    report_id: str | None = None


class RestructureBuildResult(LeanRenderableFlowResult):
    result_type: Literal["restructure_build"] = "restructure_build"
    outcome: Literal["succeeded", "failed", "blocked", "incomplete"]
    operation_id: str
    diagnostics: list[str] = Field(default_factory=list)


class RestructureBuildFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "restructure_build"
    Params = RestructureBuildParams
    Input = RestructureBuildInput
    State = RestructureBuildState
    Result = RestructureBuildResult
    Results = {"restructure_build": RestructureBuildResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext):
        params = cls.Params.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=RestructureBuildInput(**params.model_dump(), summary="Build Restructure repository."),
            state=cls.State(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        if self.state.position.phase == "repair":
            inp = self.input
            assert isinstance(inp, RestructureBuildInput)
            workdir = Path(inp.directory)
            if not workdir.is_absolute() and inp.workspace_root:
                workdir = Path(inp.workspace_root) / workdir
            prompt = (
                "Repair every compiler error in the supplied repository build report. "
                "You may edit declaration files in any Content node. "
                "Batch all repairs, use check_restructure_files or check_restructure_content, then submit the repair. Checks and submissions register sources automatically; do not call capture or calculate hashes.\n\n"
                + "\n\n".join(self.state.diagnostics)
                + f"\nFull log: read_restructure_build_report(report_id={self.state.report_id!r})."
            )
            return ctx.create_step(
                RestructureRepoRepairAgentStep(
                    step_id=new_restructure_step_id("restructure_repo_repair"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="restructure_coordinator",
                        agent_type=inp.agent_type,
                        create_agent_if_missing=True,
                        variables={"repo_key": inp.repo_key, "stage": inp.stage},
                        prompt_override=prompt,
                        env_overrides={
                            "LEAN_CONSTELLATION_AGENT_TYPE": inp.agent_type,
                            "LEAN_CONSTELLATION_REPO_ROOT": str(workdir.resolve()),
                            "LEAN_CONSTELLATION_WORKSPACE_ROOT": str(Path(inp.workspace_root or workdir.parent).resolve()),
                            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": inp.application_tool_view,
                            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": inp.submit_tool_view,
                        },
                        workdir_override=str(workdir.resolve()),
                        max_auto_continue_turns=2,
                    ),
                )
            )
        if self.state.position.phase != "build":
            return None
        return ctx.create_step(
            RestructureBuildStep(
                step_id=new_restructure_step_id("restructure_build"),
                flow_id=self.flow_id,
                scope_id=self.scope_id,
                state=BaseStepState(),
            )
        )

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        result = ctx.step.result
        inp = self.input
        operation_id = inp.operation_id or "" if isinstance(inp, RestructureBuildInput) else ""
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="restructure_build_step_failed",
                message=ctx.step.error.message,
                details=ctx.step.error.details,
            )
        elif isinstance(result, RestructureBuildStepResult):
            if result.outcome == "failed" and self.state.repair_attempts < self.input.max_repair_attempts:
                self.state.repair_attempts += 1
                self.state.diagnostics = list(result.diagnostics)
                self.state.report_id = result.operation_id
                self.state.position = FlowPosition(phase="repair")
            else:
                self.result = RestructureBuildResult(
                    outcome=result.outcome,
                    operation_id=result.operation_id or operation_id,
                    diagnostics=list(result.diagnostics),
                    summary=result.summary,
                )
                self.state.position = FlowPosition(phase="completed")
        elif isinstance(result, RestructureRepoRepairStepResult):
            if result.outcome == "repaired":
                self.state.position = FlowPosition(phase="build")
            else:
                self.result = RestructureBuildResult(
                    outcome="blocked",
                    operation_id=operation_id,
                    diagnostics=list(result.issues),
                    summary=result.summary or "Repository repair was blocked.",
                )
                self.state.position = FlowPosition(phase="completed")
        else:
            self.result = RestructureBuildResult(
                outcome="incomplete",
                operation_id=operation_id,
                summary="Build step ended without a result.",
            )
            self.state.position = FlowPosition(phase="completed")
        super().on_step_terminal(ctx)


class RestructureContentInput(LeanRenderableFlowInput):
    input_type: Literal["restructure_content"] = "restructure_content"
    repo_key: str
    directory: str
    node_path: str
    stage: Literal["declared", "proved"]
    agent_id: str | None = None
    agent_type: str
    application_tool_view: str
    submit_tool_view: str
    request_id: str | None = None
    attempt_epoch: int = 0
    reservation_id: str | None = None
    workspace_root: str | None = None

    def agent_title(self) -> str:
        return f"Restructure {self.repo_key}/{self.node_path} ({self.stage})"

    def agent_fields(self) -> dict[str, object]:
        return {
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "stage": self.stage,
        }


class RestructureContentState(BaseFlowState):
    state_type: Literal["restructure_content"] = "restructure_content"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="content_agent"))


class RestructureContentResult(LeanRenderableFlowResult):
    result_type: Literal["restructure_content"] = "restructure_content"
    outcome: Literal["declared", "proved", "blocked", "incomplete"]
    stage: str
    issues: list[str] = Field(default_factory=list)


class RestructureContentFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "restructure_content"
    Params = RestructureContentParams
    Input = RestructureContentInput
    State = RestructureContentState
    Result = RestructureContentResult
    Results = {"restructure_content": RestructureContentResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext):
        params = cls.Params.model_validate(ctx.params)
        values = params.model_dump()
        if values["stage"] == "proved" and values["agent_type"] == "RestructureContentPlanAgent":
            values["agent_type"] = "RestructureContentImplementationAgent"
        flow = cls._build(ctx, input_model=RestructureContentInput(**values, summary="Run Content restructure stage."), state=cls.State())
        return flow

    def create_next_step(self, ctx: FlowContext) -> str | None:
        if self.state.position.phase != "content_agent":
            return None
        inp = self.input
        assert isinstance(inp, RestructureContentInput)
        role = "plan" if inp.stage == "declared" else "worker"
        workdir = Path(inp.directory)
        if not workdir.is_absolute() and inp.workspace_root:
            workdir = Path(inp.workspace_root) / workdir
        return ctx.create_step(
            RestructureContentAgentStep(
                step_id=new_restructure_step_id("restructure_content"),
                flow_id=self.flow_id,
                scope_id=self.scope_id,
                state=AgentStepState(
                    agent_role=role,
                    agent_type=inp.agent_type,
                    create_agent_if_missing=True,
                    variables={"repo_key": inp.repo_key, "node_path": inp.node_path, "stage": inp.stage},
                    prompt_override=(
                        "Use the Restructure Content ToolView. Explicitly register every declaration, "
                        "set statement NL/origin/deps, edit only bound files, capture after editing, "
                        f"then submit the {inp.stage} candidate. Do not use Native LC rounds."
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_AGENT_TYPE": inp.agent_type,
                        "LEAN_CONSTELLATION_REPO_ROOT": str(workdir.resolve()),
                        "LEAN_CONSTELLATION_WORKSPACE_ROOT": str(Path(inp.workspace_root or workdir.parent).resolve()),
                        "LEAN_CONSTELLATION_NODE_PATH": getattr(inp, "node_path", ""),
                        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": inp.application_tool_view,
                        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": inp.submit_tool_view,
                    },
                    workdir_override=str(workdir.resolve()),
                    max_auto_continue_turns=1,
                ),
            )
        )

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        result = ctx.step.result
        if ctx.step.error is not None:
            self.error = BaseFlowError(error_type="restructure_content_step_failed", message=ctx.step.error.message, details=ctx.step.error.details)
        elif isinstance(result, RestructureContentStepResult):
            self.result = RestructureContentResult(outcome=result.outcome, stage=result.stage, issues=list(result.issues), summary=result.summary)
            self.state.position = FlowPosition(phase="completed")
        elif isinstance(result, AgentStepIncompleteResult) or result is None:
            self.result = RestructureContentResult(outcome="incomplete", stage=self.input.stage if isinstance(self.input, RestructureContentInput) else "unknown", summary="Agent ended without a valid submission.")
            self.state.position = FlowPosition(phase="completed")
        else:
            self.error = BaseFlowError(error_type="restructure_content_unsupported_result", message="Unsupported Content result.")
        super().on_step_terminal(ctx)


class RestructureRepoPlanParams(LeanFlowParams):
    repo_key: str
    directory: str
    agent_id: str | None = None
    agent_type: str = "RestructureCoordinatorAgent"
    application_tool_view: str = "restructure_coordinator"
    submit_tool_view: str = "restructure_coordinator_submit"
    request_id: str | None = None
    attempt_epoch: int = 0
    reservation_id: str | None = None
    workspace_root: str | None = None


class RestructureRepoPlanInput(LeanRenderableFlowInput):
    input_type: Literal["restructure_repo_plan"] = "restructure_repo_plan"
    repo_key: str
    directory: str
    agent_id: str | None = None
    agent_type: str
    application_tool_view: str
    submit_tool_view: str
    request_id: str | None = None
    attempt_epoch: int = 0
    reservation_id: str | None = None
    workspace_root: str | None = None

    def agent_title(self) -> str:
        return f"Plan Restructure repo {self.repo_key}"

    def agent_fields(self) -> dict[str, object]:
        return {"repo_key": self.repo_key}


class RestructureRepoPlanState(BaseFlowState):
    state_type: Literal["restructure_repo_plan"] = "restructure_repo_plan"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="repo_plan_agent"))


class RestructureRepoPlanResult(LeanRenderableFlowResult):
    result_type: Literal["restructure_repo_plan"] = "restructure_repo_plan"
    outcome: Literal["planned", "blocked", "incomplete"]
    plan_version: int | None = None
    issues: list[str] = Field(default_factory=list)


class RestructureRepoPlanFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "restructure_repo_plan"
    Params = RestructureRepoPlanParams
    Input = RestructureRepoPlanInput
    State = RestructureRepoPlanState
    Result = RestructureRepoPlanResult
    Results = {"restructure_repo_plan": RestructureRepoPlanResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext):
        params = cls.Params.model_validate(ctx.params)
        return cls._build(ctx, input_model=RestructureRepoPlanInput(**params.model_dump(), summary="Plan restructure node tree."), state=cls.State())

    def create_next_step(self, ctx: FlowContext) -> str | None:
        if self.state.position.phase != "repo_plan_agent":
            return None
        inp = self.input
        assert isinstance(inp, RestructureRepoPlanInput)
        workdir = Path(inp.directory)
        if not workdir.is_absolute() and inp.workspace_root:
            workdir = Path(inp.workspace_root) / workdir
        return ctx.create_step(
            RestructureRepoPlanAgentStep(
                step_id=new_restructure_step_id("restructure_repo_plan"),
                flow_id=self.flow_id,
                scope_id=self.scope_id,
                state=AgentStepState(
                    agent_role="restructure_coordinator",
                    agent_type=inp.agent_type,
                    create_agent_if_missing=True,
                    variables={"repo_key": inp.repo_key},
                    prompt_override="Design Main → Scope → Content and submit the validated repo plan through the Coordinator ToolView.",
                    env_overrides={
                        "LEAN_CONSTELLATION_AGENT_TYPE": inp.agent_type,
                        "LEAN_CONSTELLATION_REPO_ROOT": str(workdir.resolve()),
                        "LEAN_CONSTELLATION_WORKSPACE_ROOT": str(Path(inp.workspace_root or workdir.parent).resolve()),
                        "LEAN_CONSTELLATION_NODE_PATH": getattr(inp, "node_path", ""),
                        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": inp.application_tool_view,
                        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": inp.submit_tool_view,
                    },
                    workdir_override=str(workdir.resolve()),
                    max_auto_continue_turns=1,
                ),
            )
        )

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        result = ctx.step.result
        if ctx.step.error is not None:
            self.error = BaseFlowError(error_type="restructure_repo_plan_step_failed", message=ctx.step.error.message, details=ctx.step.error.details)
        elif isinstance(result, RestructureRepoPlanStepResult):
            self.result = RestructureRepoPlanResult(outcome=result.outcome, plan_version=result.plan_version, issues=list(result.issues), summary=result.summary)
            self.state.position = FlowPosition(phase="completed")
        elif isinstance(result, AgentStepIncompleteResult) or result is None:
            self.result = RestructureRepoPlanResult(outcome="incomplete", summary="Coordinator ended without a valid submission.")
            self.state.position = FlowPosition(phase="completed")
        else:
            self.error = BaseFlowError(error_type="restructure_repo_plan_unsupported_result", message="Unsupported repo plan result.")
        super().on_step_terminal(ctx)


class RestructureReviewParams(LeanFlowParams):
    repo_key: str
    directory: str
    node_path: str
    artifact_id: str
    agent_id: str | None = None
    agent_type: str = "RestructureContentReviewAgent"
    application_tool_view: str = "restructure_review"
    submit_tool_view: str = "restructure_review_submit"
    request_id: str | None = None
    attempt_epoch: int = 0
    reservation_id: str | None = None
    workspace_root: str | None = None


class RestructureReviewInput(LeanRenderableFlowInput):
    input_type: Literal["restructure_review"] = "restructure_review"
    repo_key: str
    directory: str
    node_path: str
    artifact_id: str
    agent_id: str | None = None
    agent_type: str
    application_tool_view: str
    submit_tool_view: str
    request_id: str | None = None
    attempt_epoch: int = 0
    reservation_id: str | None = None
    workspace_root: str | None = None

    def agent_title(self) -> str:
        return f"Review Restructure {self.repo_key}/{self.node_path}"

    def agent_fields(self) -> dict[str, object]:
        return {"repo_key": self.repo_key, "node_path": self.node_path, "artifact_id": self.artifact_id}


class RestructureReviewState(BaseFlowState):
    state_type: Literal["restructure_review"] = "restructure_review"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="review_agent"))


class RestructureReviewResult(LeanRenderableFlowResult):
    result_type: Literal["restructure_review"] = "restructure_review"
    outcome: Literal["passed", "needs_content_fix", "needs_replan", "blocked", "incomplete"]
    artifact_id: str
    findings: list[str] = Field(default_factory=list)


class RestructureReviewFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "restructure_review"
    Params = RestructureReviewParams
    Input = RestructureReviewInput
    State = RestructureReviewState
    Result = RestructureReviewResult
    Results = {"restructure_review": RestructureReviewResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext):
        params = cls.Params.model_validate(ctx.params)
        return cls._build(ctx, input_model=RestructureReviewInput(**params.model_dump(), summary="Review Restructure Content artifact."), state=cls.State())

    def create_next_step(self, ctx: FlowContext) -> str | None:
        if self.state.position.phase != "review_agent":
            return None
        inp = self.input
        assert isinstance(inp, RestructureReviewInput)
        workdir = Path(inp.directory)
        if not workdir.is_absolute() and inp.workspace_root:
            workdir = Path(inp.workspace_root) / workdir
        return ctx.create_step(
            RestructureReviewAgentStep(
                step_id=new_restructure_step_id("restructure_review"),
                flow_id=self.flow_id,
                scope_id=self.scope_id,
                state=AgentStepState(
                    agent_role="reviewer",
                    agent_type=inp.agent_type,
                    create_agent_if_missing=True,
                    variables={"repo_key": inp.repo_key, "node_path": inp.node_path, "artifact_id": inp.artifact_id},
                    prompt_override="Review the current Content artifact read-only and submit the structured review decision.",
                    env_overrides={
                        "LEAN_CONSTELLATION_AGENT_TYPE": inp.agent_type,
                        "LEAN_CONSTELLATION_REPO_ROOT": str(workdir.resolve()),
                        "LEAN_CONSTELLATION_WORKSPACE_ROOT": str(Path(inp.workspace_root or workdir.parent).resolve()),
                        "LEAN_CONSTELLATION_NODE_PATH": getattr(inp, "node_path", ""),
                        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": inp.application_tool_view,
                        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": inp.submit_tool_view,
                    },
                    workdir_override=str(workdir.resolve()),
                    max_auto_continue_turns=1,
                ),
            )
        )

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        result = ctx.step.result
        if ctx.step.error is not None:
            self.error = BaseFlowError(error_type="restructure_review_step_failed", message=ctx.step.error.message, details=ctx.step.error.details)
        elif isinstance(result, RestructureReviewStepResult):
            self.result = RestructureReviewResult(outcome=result.outcome, artifact_id=result.artifact_id, findings=list(result.findings), summary=result.summary)
            self.state.position = FlowPosition(phase="completed")
        elif isinstance(result, AgentStepIncompleteResult) or result is None:
            self.result = RestructureReviewResult(outcome="incomplete", artifact_id=inp.artifact_id if (inp := self.input) and isinstance(inp, RestructureReviewInput) else "", summary="Reviewer ended without a valid submission.")
            self.state.position = FlowPosition(phase="completed")
        else:
            self.error = BaseFlowError(error_type="restructure_review_unsupported_result", message="Unsupported review result.")
        super().on_step_terminal(ctx)


RESTRUCTURE_FLOW_TYPES = (RestructureRepoPlanFlow, RestructureContentFlow, RestructureReviewFlow, RestructureBuildFlow)
