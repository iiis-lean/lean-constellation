"""Repository-level resource, Lean-provider, and Mathlib exploration flows."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext
from agent_runtime_kit.flow.models import (
    BaseFlowError,
    BaseFlowState,
    FlowPosition,
)
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.repo_exploration.steps import (
    RepoLeanProviderDiscoveryAgentStep,
    RepoLeanProviderDiscoveryStepResult,
    RepoMathlibReconAgentStep,
    RepoMathlibReconStepResult,
    RepoResourceDiscoveryAgentStep,
    RepoResourceDiscoveryStepResult,
    new_repo_exploration_step_id,
)
from lean_constellation.flows.repo_exploration.submissions import (
    RepoLeanProviderCandidate,
    RepoResourceCandidate,
)


class RepoExplorationParams(LeanFlowParams):
    repo_key: str
    repo_root: str
    objective: str
    context_summary: str | None = None
    agent_id: str


class RepoExplorationInput(LeanRenderableFlowInput):
    repo_key: str
    repo_root: str
    objective: str
    context_summary: str | None = None
    agent_id: str

    def agent_fields(self) -> dict[str, object]:
        return {
            "repo_key": self.repo_key,
            "objective": self.objective,
            "context_summary": self.context_summary,
        }


class RepoExplorationState(BaseFlowState):
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="exploration_agent"))


class RepoResourceDiscoveryInput(RepoExplorationInput):
    input_type: Literal["repo_resource_discovery"] = "repo_resource_discovery"

    def agent_title(self) -> str:
        return f"Explore supporting resources for {self.repo_key}"


class RepoLeanProviderDiscoveryInput(RepoExplorationInput):
    input_type: Literal["repo_lean_provider_discovery"] = "repo_lean_provider_discovery"

    def agent_title(self) -> str:
        return f"Explore Lean providers for {self.repo_key}"


class RepoMathlibReconInput(RepoExplorationInput):
    input_type: Literal["repo_mathlib_recon"] = "repo_mathlib_recon"

    def agent_title(self) -> str:
        return f"Explore Mathlib support for {self.repo_key}"


class RepoResourceDiscoveryState(RepoExplorationState):
    state_type: Literal["repo_resource_discovery"] = "repo_resource_discovery"


class RepoLeanProviderDiscoveryState(RepoExplorationState):
    state_type: Literal["repo_lean_provider_discovery"] = "repo_lean_provider_discovery"


class RepoMathlibReconState(RepoExplorationState):
    state_type: Literal["repo_mathlib_recon"] = "repo_mathlib_recon"


class RepoExplorationResultBase(LeanRenderableFlowResult):
    outcome: Literal["completed", "no_useful_findings", "incomplete"]
    objective: str


class RepoResourceDiscoveryResult(RepoExplorationResultBase):
    result_type: Literal["repo_resource_discovery"] = "repo_resource_discovery"
    candidates: list[RepoResourceCandidate] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "objective": self.objective,
            "recommended_candidates": list(self.candidates),
        }


class RepoLeanProviderDiscoveryResult(RepoExplorationResultBase):
    result_type: Literal["repo_lean_provider_discovery"] = "repo_lean_provider_discovery"
    candidates: list[RepoLeanProviderCandidate] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "objective": self.objective,
            "provider_candidates": list(self.candidates),
        }


class RepoMathlibReconResult(RepoExplorationResultBase):
    result_type: Literal["repo_mathlib_recon"] = "repo_mathlib_recon"
    created_modules: list[str] = Field(default_factory=list)
    reused_modules: list[str] = Field(default_factory=list)
    created_declarations: list[str] = Field(default_factory=list)
    reused_declarations: list[str] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    usage_notes: list[str] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "objective": self.objective,
            "created_modules": list(self.created_modules),
            "reused_modules": list(self.reused_modules),
            "created_declarations": list(self.created_declarations),
            "reused_declarations": list(self.reused_declarations),
            "unresolved": list(self.unresolved),
            "usage_notes": list(self.usage_notes),
        }


class _RepoExplorationFlow(LeanBusinessFlow):
    Params: ClassVar[type[LeanFlowParams]] = RepoExplorationParams
    input_model: ClassVar[type[RepoExplorationInput]]
    state_model: ClassVar[type[RepoExplorationState]]
    step_model: ClassVar[type]
    step_result_model: ClassVar[type]
    result_model: ClassVar[type[RepoExplorationResultBase]]
    agent_role: ClassVar[str]
    agent_type: ClassVar[str]
    app_view: ClassVar[str]
    submit_view: ClassVar[str]
    skill_name: ClassVar[str]

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext):
        params = RepoExplorationParams.model_validate(ctx.params)
        flow = cls._build(
            ctx,
            input_model=cls.input_model(
                **params.model_dump(),
                summary=f"Explore {cls.flow_type} support for {params.repo_key}.",
            ),
            state=cls.state_model(),
        )
        flow.agent_bindings.by_role[cls.agent_role] = params.agent_id
        return flow

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = self._state()
        input_model = self._input()
        if state.position.phase != "exploration_agent":
            return None
        return ctx.create_step(
            self.step_model(
                step_id=new_repo_exploration_step_id(self.flow_type),
                flow_id=self.flow_id,
                scope_id=self.scope_id,
                state=AgentStepState(
                    agent_role=self.agent_role,
                    agent_type=self.agent_type,
                    home_id=self.agent_type,
                    create_agent_if_missing=False,
                    variables={
                        "repo_key": input_model.repo_key,
                        "objective": input_model.objective,
                    },
                    prompt_override=_exploration_prompt(
                        input_model,
                        skill_name=self.skill_name,
                    ),
                    env_overrides={
                        "LEAN_CONSTELLATION_AGENT_TYPE": self.agent_type,
                        "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": self.app_view,
                        "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": self.submit_view,
                    },
                    workdir_override=input_model.repo_root,
                    max_auto_continue_turns=1,
                ),
            )
        )

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = self._state()
        result = ctx.step.result
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type=f"{self.flow_type}_step_failed",
                message=ctx.step.error.message,
                details=ctx.step.error.details,
            )
        elif isinstance(result, AgentStepIncompleteResult) or result is None:
            self.result = self.result_model(
                outcome="incomplete",
                objective=self._input().objective,
                summary="Exploration Agent ended without a valid terminal submission.",
            )
            state.position = FlowPosition(phase="completed")
        elif isinstance(result, self.step_result_model):
            self.result = self._convert_result(result)
            state.position = FlowPosition(phase="completed")
        else:
            self.error = BaseFlowError(
                error_type=f"{self.flow_type}_unsupported_result",
                message=f"Unsupported exploration result: {getattr(result, 'result_type', None)}.",
            )
        super().on_step_terminal(ctx)

    def _convert_result(self, result):
        data = result.model_dump(exclude={"result_type", "summary"})
        return self.result_model(
            **data,
            objective=self._input().objective,
            summary=result.summary,
        )

    def _state(self) -> RepoExplorationState:
        if not isinstance(self.state, self.state_model):
            raise TypeError(f"{self.flow_type} flow has invalid state")
        return self.state

    def _input(self) -> RepoExplorationInput:
        if not isinstance(self.input, self.input_model):
            raise TypeError(f"{self.flow_type} flow has invalid input")
        return self.input


class RepoResourceDiscoveryFlow(_RepoExplorationFlow):
    flow_type: ClassVar[str] = "repo_resource_discovery"
    Input = RepoResourceDiscoveryInput
    State = RepoResourceDiscoveryState
    Result = RepoResourceDiscoveryResult
    Results = {"repo_resource_discovery": RepoResourceDiscoveryResult}
    input_model = RepoResourceDiscoveryInput
    state_model = RepoResourceDiscoveryState
    step_model = RepoResourceDiscoveryAgentStep
    step_result_model = RepoResourceDiscoveryStepResult
    result_model = RepoResourceDiscoveryResult
    agent_role = "repo_resource_discovery"
    agent_type = "RepoResourceDiscoveryAgent"
    app_view = "repo_resource_discovery"
    submit_view = "repo_resource_discovery_submit"
    skill_name = "repo-resource-discovery"


class RepoLeanProviderDiscoveryFlow(_RepoExplorationFlow):
    flow_type: ClassVar[str] = "repo_lean_provider_discovery"
    Input = RepoLeanProviderDiscoveryInput
    State = RepoLeanProviderDiscoveryState
    Result = RepoLeanProviderDiscoveryResult
    Results = {"repo_lean_provider_discovery": RepoLeanProviderDiscoveryResult}
    input_model = RepoLeanProviderDiscoveryInput
    state_model = RepoLeanProviderDiscoveryState
    step_model = RepoLeanProviderDiscoveryAgentStep
    step_result_model = RepoLeanProviderDiscoveryStepResult
    result_model = RepoLeanProviderDiscoveryResult
    agent_role = "repo_lean_provider_discovery"
    agent_type = "RepoLeanProviderDiscoveryAgent"
    app_view = "repo_lean_provider_discovery"
    submit_view = "repo_lean_provider_discovery_submit"
    skill_name = "repo-lean-provider-discovery"


class RepoMathlibReconFlow(_RepoExplorationFlow):
    flow_type: ClassVar[str] = "repo_mathlib_recon"
    Input = RepoMathlibReconInput
    State = RepoMathlibReconState
    Result = RepoMathlibReconResult
    Results = {"repo_mathlib_recon": RepoMathlibReconResult}
    input_model = RepoMathlibReconInput
    state_model = RepoMathlibReconState
    step_model = RepoMathlibReconAgentStep
    step_result_model = RepoMathlibReconStepResult
    result_model = RepoMathlibReconResult
    agent_role = "repo_mathlib_recon"
    agent_type = "RepoMathlibReconAgent"
    app_view = "repo_mathlib_recon"
    submit_view = "repo_mathlib_recon_submit"
    skill_name = "repo-mathlib-recon"


def _exploration_prompt(input_model: RepoExplorationInput, *, skill_name: str) -> str:
    parts = [
        "You are exploring support for the current Lean repository.",
        f"Exploration objective: {input_model.objective}",
    ]
    if input_model.context_summary:
        parts.append(f"Additional direction: {input_model.context_summary}")
    parts.extend(
        [
            f"Read and apply ${skill_name} before acting.",
            "Read current repository evidence through compact tools. Return only verified, source-attributed findings relevant to this objective.",
            "Do not create resources, requirements, provider repositories, nodes, contracts, or declarations.",
        ]
    )
    return "\n".join(parts)


REPO_EXPLORATION_FLOW_TYPES: tuple[type[LeanBusinessFlow], ...] = (
    RepoResourceDiscoveryFlow,
    RepoLeanProviderDiscoveryFlow,
    RepoMathlibReconFlow,
)
