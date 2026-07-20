"""Node directory dependency recon Flow type definitions."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowResult
from lean_constellation.flows.content_node_task.context_brief import (
    build_prior_preparation_prompt_context,
)
from lean_constellation.flows.content_node_task.steps import NodeDirDependencyReconStepResult, new_content_step_id
from lean_constellation.flows.content_node_task.preparation.common import (
    PreparationReconInput,
    PreparationReconParams,
    PreparationReconState,
    content_node_workdir,
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
    dependency_change_summary: str | None = None
    checked_boundary_summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_within_visible_boundaries: list[str] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "dependency_change_summary": self.dependency_change_summary,
            "checked_boundary_summary": self.checked_boundary_summary,
            "useful_findings": list(self.useful_findings),
            "unresolved_within_visible_boundaries": list(self.unresolved_within_visible_boundaries),
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

            prior_context = build_prior_preparation_prompt_context(ctx, self)
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
                            "prior_preparation_context": prior_context,
                        },
                        prompt_override=(
                            f"{_recon_prompt('node dependency', input_model)}\n\n"
                            f"Prior preparation context:\n{prior_context}"
                        ),
                        env_overrides={
                            "LEAN_CONSTELLATION_AGENT_TYPE": "NodeDirDependencyReconAgent",
                            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "node_dir_dependency_recon",
                            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "node_dir_dependency_recon_submit",
                        },
                        workdir_override=content_node_workdir(input_model.repo_path, input_model.node_path),
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
                dependency_change_summary=result.dependency_change_summary,
                checked_boundary_summary=result.checked_boundary_summary,
                useful_findings=list(result.useful_findings),
                unresolved_within_visible_boundaries=list(result.unresolved_within_visible_boundaries),
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
    parts.append(
        "Required Skill re-entry: read and apply $content-contract-reading and "
        "$visible-node-dependency-recon from the current Home before acting."
    )
    if input_model.objective:
        parts.append(f"Objective: {input_model.objective}.")
    if input_model.context_summary:
        parts.append(f"Context: {input_model.context_summary}.")
    parts.append(
        "Reuse matching verified prior preparation findings without broad re-query; independently verify only "
        "stale, unresolved, or role-specific claims. Then call `submit_node_dir_dependency_recon_completed`."
    )
    return "\n".join(parts)
