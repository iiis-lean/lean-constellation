"""Agent steps and compact results for repository exploration."""

from __future__ import annotations

from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.models import BaseSubmission
from agent_runtime_kit.flow.standard_steps.agent_step import AgentStep
from pydantic import Field

from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.flows.repo_exploration.submissions import (
    RepoLeanProviderDiscoverySubmission,
    RepoLeanProviderCandidate,
    RepoMathlibReconSubmission,
    RepoResourceDiscoverySubmission,
    RepoResourceCandidate,
)


class RepoResourceDiscoveryStepResult(LeanRenderableStepResult):
    result_type: Literal["repo_resource_discovery"] = "repo_resource_discovery"
    outcome: Literal["completed", "no_useful_findings", "incomplete"]
    candidates: list[RepoResourceCandidate] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {"outcome": self.outcome, "candidates": list(self.candidates)}


class RepoLeanProviderDiscoveryStepResult(LeanRenderableStepResult):
    result_type: Literal["repo_lean_provider_discovery"] = "repo_lean_provider_discovery"
    outcome: Literal["completed", "no_useful_findings", "incomplete"]
    candidates: list[RepoLeanProviderCandidate] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {"outcome": self.outcome, "candidates": list(self.candidates)}


class RepoMathlibReconStepResult(LeanRenderableStepResult):
    result_type: Literal["repo_mathlib_recon"] = "repo_mathlib_recon"
    outcome: Literal["completed", "no_useful_findings", "incomplete"]
    created_modules: list[str] = Field(default_factory=list)
    reused_modules: list[str] = Field(default_factory=list)
    created_declarations: list[str] = Field(default_factory=list)
    reused_declarations: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    usage_notes: list[str] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return self.model_dump(exclude={"result_type", "summary"})


def _submission_map(*classes: type[BaseSubmission]) -> dict[str, type[BaseSubmission]]:
    return {str(cls.model_fields["submission_type"].default): cls for cls in classes}


class RepoResourceDiscoveryAgentStep(AgentStep):
    step_type: ClassVar[str] = "repo_resource_discovery_agent_step"
    Results = {**AgentStep.Results, "repo_resource_discovery": RepoResourceDiscoveryStepResult}
    Submissions = _submission_map(RepoResourceDiscoverySubmission)
    SubmitTools = {"submit_repo_resource_discovery_result"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RepoResourceDiscoverySubmission):
            return RepoResourceDiscoveryStepResult(
                outcome=submission.outcome,
                candidates=list(submission.candidates),
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id, reason, turn_result, attempt_count):
        del ctx, agent_id, turn_result, attempt_count
        return RepoResourceDiscoveryStepResult(outcome="incomplete", summary=reason)


class RepoLeanProviderDiscoveryAgentStep(AgentStep):
    step_type: ClassVar[str] = "repo_lean_provider_discovery_agent_step"
    Results = {**AgentStep.Results, "repo_lean_provider_discovery": RepoLeanProviderDiscoveryStepResult}
    Submissions = _submission_map(RepoLeanProviderDiscoverySubmission)
    SubmitTools = {"submit_repo_lean_provider_discovery_result"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RepoLeanProviderDiscoverySubmission):
            return RepoLeanProviderDiscoveryStepResult(
                outcome=submission.outcome,
                candidates=list(submission.candidates),
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id, reason, turn_result, attempt_count):
        del ctx, agent_id, turn_result, attempt_count
        return RepoLeanProviderDiscoveryStepResult(outcome="incomplete", summary=reason)


class RepoMathlibReconAgentStep(AgentStep):
    step_type: ClassVar[str] = "repo_mathlib_recon_agent_step"
    Results = {**AgentStep.Results, "repo_mathlib_recon": RepoMathlibReconStepResult}
    Submissions = _submission_map(RepoMathlibReconSubmission)
    SubmitTools = {"submit_repo_mathlib_recon_result"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RepoMathlibReconSubmission):
            return RepoMathlibReconStepResult(
                outcome=submission.outcome,
                created_modules=list(submission.created_modules),
                reused_modules=list(submission.reused_modules),
                created_declarations=list(submission.created_declarations),
                reused_declarations=list(submission.reused_declarations),
                unresolved=list(submission.unresolved),
                usage_notes=list(submission.usage_notes),
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id, reason, turn_result, attempt_count):
        del ctx, agent_id, turn_result, attempt_count
        return RepoMathlibReconStepResult(outcome="incomplete", summary=reason)


REPO_EXPLORATION_AGENT_STEP_TYPES: tuple[type[AgentStep], ...] = (
    RepoResourceDiscoveryAgentStep,
    RepoLeanProviderDiscoveryAgentStep,
    RepoMathlibReconAgentStep,
)


def new_repo_exploration_step_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
