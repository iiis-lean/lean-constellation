"""Mathlib recon Flow type definitions."""

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
from lean_constellation.flows.content_node_task.steps import MathlibReconStepResult, new_content_step_id
from lean_constellation.flows.content_node_task.preparation.common import (
    PreparationReconInput,
    PreparationReconParams,
    PreparationReconState,
    content_node_workdir,
)


class MathlibReconInput(PreparationReconInput):
    input_type: Literal["mathlib_recon"] = "mathlib_recon"

    def agent_title(self) -> str:
        return f"Review Mathlib dependencies for {self.node_path}"


class MathlibReconState(PreparationReconState):
    state_type: Literal["mathlib_recon"] = "mathlib_recon"


class MathlibReconResult(LeanRenderableFlowResult):
    result_type: Literal["mathlib_recon"] = "mathlib_recon"
    outcome: Literal["completed"]
    repo_key: str
    node_path: str
    index_update_summary: str | None = None
    node_mathlib_hint_summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_in_mathlib: list[str] = Field(default_factory=list)

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "index_update_summary": self.index_update_summary,
            "node_mathlib_hint_summary": self.node_mathlib_hint_summary,
            "useful_findings": list(self.useful_findings),
            "unresolved_in_mathlib": list(self.unresolved_in_mathlib),
        }


class MathlibReconFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "mathlib_recon"
    Params: ClassVar[type[LeanFlowParams]] = PreparationReconParams
    Input: ClassVar[type[BaseFlowInput]] = MathlibReconInput
    State: ClassVar[type[BaseFlowState]] = MathlibReconState
    Result: ClassVar[type[BaseFlowResult]] = MathlibReconResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"mathlib_recon": MathlibReconResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "MathlibReconFlow":
        params = PreparationReconParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=MathlibReconInput(summary=params.context_summary, **params.model_dump()),
            state=MathlibReconState(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_state(self.state)
        input_model = _require_input(self.input)
        if state.position.phase == "recon_agent":
            from lean_constellation.flows.common.agent_steps import MathlibReconAgentStep

            prior_context = build_prior_preparation_prompt_context(ctx, self)
            return ctx.create_step(
                MathlibReconAgentStep(
                    step_id=new_content_step_id("mathlib_recon"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="mathlib_recon",
                        agent_type="MathlibReconAgent",
                        home_id="MathlibReconAgent",
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
                            f"{_recon_prompt('Mathlib', input_model)}\n\n"
                            f"Prior preparation context:\n{prior_context}"
                        ),
                        env_overrides={
                            "LEAN_CONSTELLATION_AGENT_TYPE": "MathlibReconAgent",
                            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "mathlib_recon",
                            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "mathlib_recon_submit",
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
                error_type="mathlib_recon_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return
        result = ctx.step.result
        if isinstance(result, AgentStepIncompleteResult) or result is None:
            self.error = BaseFlowError(error_type="mathlib_recon_incomplete", message="MathlibReconAgent did not submit completed.")
        elif isinstance(result, MathlibReconStepResult) and result.outcome == "completed":
            state.position = FlowPosition(phase="completed")
            self.result = MathlibReconResult(
                outcome="completed",
                repo_key=input_model.repo_key,
                node_path=input_model.node_path,
                index_update_summary=result.index_update_summary,
                node_mathlib_hint_summary=result.node_mathlib_hint_summary,
                useful_findings=list(result.useful_findings),
                unresolved_in_mathlib=list(result.unresolved_in_mathlib),
                summary=result.summary,
            )
        else:
            self.error = BaseFlowError(error_type="mathlib_recon_unsupported_result", message="MathlibReconAgent returned unsupported result.")
        super().on_step_terminal(ctx)


def _require_state(state: BaseFlowState) -> MathlibReconState:
    if not isinstance(state, MathlibReconState):
        raise TypeError("mathlib_recon flow has invalid state")
    return state


def _require_input(input_model: BaseFlowInput | None) -> MathlibReconInput:
    if not isinstance(input_model, MathlibReconInput):
        raise TypeError("mathlib_recon flow has invalid input")
    return input_model


def _recon_prompt(kind: str, input_model: PreparationReconInput) -> str:
    parts = [f"Run {kind} recon for content node {input_model.node_path}."]
    parts.append(
        "Required Skill re-entry: read and apply $content-contract-reading and $mathlib-index-first-recon "
        "from the current Home before acting."
    )
    if input_model.objective:
        parts.append(f"Objective: {input_model.objective}.")
    if input_model.context_summary:
        parts.append(f"Context: {input_model.context_summary}.")
    parts.append(
        "Reuse matching verified prior preparation findings without broad re-query; independently verify only "
        "stale, unresolved, or role-specific claims. Then call `submit_mathlib_recon_completed`."
    )
    return "\n".join(parts)
