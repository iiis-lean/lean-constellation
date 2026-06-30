"""Node directory dependency recon Flow type definitions."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowResult
from lean_constellation.flows.content_node_task.steps import NodeDirDependencyReconStepResult, new_content_step_id
from lean_constellation.flows.content_node_task.preparation.common import (
    PreparationReconInput,
    PreparationReconParams,
    PreparationReconState,
)


class NodeDirDependencyReconInput(PreparationReconInput):
    input_type: Literal["node_dir_dependency_recon"] = "node_dir_dependency_recon"

    def agent_title(self) -> str:
        return f"Review node dependencies for {self.node_path}"


class NodeDirDependencyReconState(PreparationReconState):
    state_type: Literal["node_dir_dependency_recon"] = "node_dir_dependency_recon"


class NodeDirDependencyReconResult(LeanRenderableFlowResult):
    result_type: Literal["node_dir_dependency_recon"] = "node_dir_dependency_recon"
    outcome: Literal["completed"]
    repo_key: str
    node_path: str
    added_node_deps: list[str] = Field(default_factory=list)
    removed_node_deps: list[str] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "added_node_deps": self.added_node_deps,
            "removed_node_deps": self.removed_node_deps,
        }


class NodeDirDependencyReconFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "node_dir_dependency_recon"
    Params: ClassVar[type[LeanFlowParams]] = PreparationReconParams
    Input: ClassVar[type[BaseFlowInput]] = NodeDirDependencyReconInput
    State: ClassVar[type[BaseFlowState]] = NodeDirDependencyReconState
    Result: ClassVar[type[BaseFlowResult]] = NodeDirDependencyReconResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {
        "node_dir_dependency_recon": NodeDirDependencyReconResult,
    }

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "NodeDirDependencyReconFlow":
        params = PreparationReconParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=NodeDirDependencyReconInput(
                summary=params.context_summary,
                **params.model_dump(),
            ),
            state=NodeDirDependencyReconState(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_state(self.state)
        input_model = _require_input(self.input)
        if state.position.phase == "recon_agent":
            from lean_constellation.flows.common.agent_steps import NodeDirDependencyReconAgentStep

            return ctx.create_step(
                NodeDirDependencyReconAgentStep(
                    step_id=new_content_step_id("node_dir_dependency_recon"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="node_dir_dependency_recon",
                        agent_type="NodeDirDependencyReconAgent",
                        home_id="NodeDirDependencyReconAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="step",
                        variables={
                            "repo_key": input_model.repo_key,
                            "node_path": input_model.node_path,
                            "contract_version": input_model.contract_version,
                            "objective": input_model.objective,
                        },
                        prompt_override=_recon_prompt("node dependency", input_model),
                        env_overrides={
                            "LEAN_CONSTELLATION_AGENT_TYPE": "NodeDirDependencyReconAgent",
                            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "node_dir_dependency_recon",
                            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "node_dir_dependency_recon_submit",
                        },
                        workdir_override=input_model.repo_path,
                        max_auto_continue_turns=1,
                    ),
                )
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_state(self.state)
        input_model = _require_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="node_dir_dependency_recon_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return
        result = ctx.step.result
        if isinstance(result, AgentStepIncompleteResult) or result is None:
            self.error = BaseFlowError(
                error_type="node_dir_dependency_recon_incomplete",
                message="NodeDirDependencyReconAgent did not submit completed.",
            )
        elif isinstance(result, NodeDirDependencyReconStepResult) and result.outcome == "completed":
            state.position = FlowPosition(phase="completed")
            self.result = NodeDirDependencyReconResult(
                outcome="completed",
                repo_key=input_model.repo_key,
                node_path=input_model.node_path,
                added_node_deps=list(result.added_node_deps),
                removed_node_deps=list(result.removed_node_deps),
                summary=result.summary,
            )
        else:
            self.error = BaseFlowError(
                error_type="node_dir_dependency_recon_unsupported_result",
                message="NodeDirDependencyReconAgent returned unsupported result.",
            )
        super().on_step_terminal(ctx)


def _require_state(state: BaseFlowState) -> NodeDirDependencyReconState:
    if not isinstance(state, NodeDirDependencyReconState):
        raise TypeError("node_dir_dependency_recon flow has invalid state")
    return state


def _require_input(input_model: BaseFlowInput | None) -> NodeDirDependencyReconInput:
    if not isinstance(input_model, NodeDirDependencyReconInput):
        raise TypeError("node_dir_dependency_recon flow has invalid input")
    return input_model


def _recon_prompt(kind: str, input_model: PreparationReconInput) -> str:
    parts = [f"Run {kind} recon for content node {input_model.node_path}."]
    if input_model.objective:
        parts.append(f"Objective: {input_model.objective}.")
    if input_model.context_summary:
        parts.append(f"Context: {input_model.context_summary}.")
    parts.append("Use tools for current truth and submit the completed recon result.")
    return "\n".join(parts)
