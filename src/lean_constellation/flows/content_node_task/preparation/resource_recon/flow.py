"""Resource recon Flow type definitions."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowReadContext, FlowStepContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition, FlowStatus
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState, DispatchStep, DispatchStepResult, DispatchStepState
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowResult
from lean_constellation.flows.content_node_task.context_brief import (
    build_prior_preparation_prompt_context,
)
from lean_constellation.flows.content_node_task.preparation.resource_recon.submissions import ResourceReconRequestResourceSubmission
from lean_constellation.flows.content_node_task.steps import ResourceReconStepResult, new_content_step_id
from lean_constellation.flows.content_node_task.preparation.common import (
    PreparationReconInput,
    PreparationReconParams,
    PreparationReconState,
    content_node_workdir,
)

_MAX_RESOURCE_REQUESTS_PER_FLOW = 8


class ResourceReconInput(PreparationReconInput):
    input_type: Literal["resource_recon"] = "resource_recon"

    def agent_title(self) -> str:
        return f"Review source resources for {self.node_path}"


class ResourceReconState(PreparationReconState):
    state_type: Literal["resource_recon"] = "resource_recon"
    resource_request_count: int = 0


class ResourceReconResult(LeanRenderableFlowResult):
    result_type: Literal["resource_recon"] = "resource_recon"
    outcome: Literal["completed", "blocked"]
    repo_key: str
    node_path: str
    material_change_summary: str | None = None
    checked_material_summary: str | None = None
    useful_findings: list[str] = Field(default_factory=list)
    unresolved_material_needs: list[str] = Field(default_factory=list)
    reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "material_change_summary": self.material_change_summary,
            "checked_material_summary": self.checked_material_summary,
            "useful_findings": list(self.useful_findings),
            "unresolved_material_needs": list(self.unresolved_material_needs),
            "reason": self.reason,
        }


class ResourceReconFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "resource_recon"
    Params: ClassVar[type[LeanFlowParams]] = PreparationReconParams
    Input: ClassVar[type[BaseFlowInput]] = ResourceReconInput
    State: ClassVar[type[BaseFlowState]] = ResourceReconState
    Result: ClassVar[type[BaseFlowResult]] = ResourceReconResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"resource_recon": ResourceReconResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "ResourceReconFlow":
        params = PreparationReconParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=ResourceReconInput(summary=params.context_summary, **params.model_dump()),
            state=ResourceReconState(),
        )

    def can_exit_waiting(self, ctx: FlowReadContext) -> bool:
        state = _require_state(self.state)
        if state.position.phase != "waiting_resource_request" or not state.waiting_dispatch_step_id:
            return False
        child_flows = _child_flows_for_dispatch(ctx, self.flow_id, state.waiting_dispatch_step_id)
        if not child_flows:
            return False
        return all(child.status in {FlowStatus.COMPLETED, FlowStatus.FAILED} for child in child_flows)

    def on_exit_waiting(self, ctx: FlowContext) -> None:
        state = _require_state(self.state)
        state.position = FlowPosition(phase="recon_callback")
        super().on_exit_waiting(ctx)

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_state(self.state)
        input_model = _require_input(self.input)
        if state.position.phase == "recon_agent":
            return ctx.create_step(
                _resource_recon_agent_step(ctx, self, input_model, callback=False)
            )
        if state.position.phase == "recon_callback":
            return ctx.create_step(
                _resource_recon_agent_step(ctx, self, input_model, callback=True)
            )
        if state.position.phase == "dispatch_resource_request":
            return ctx.create_step(_dispatch_resource_request_step(ctx, self, state))
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_state(self.state)
        input_model = _require_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="resource_recon_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return
        result = ctx.step.result
        if ctx.step.step_type == "resource_recon_agent_step":
            self._consume_agent_result(
                ctx,
                state,
                input_model,
                result,
                ctx.step.submission,
                ctx.step.step_id,
            )
        elif isinstance(result, DispatchStepResult):
            self._consume_dispatch_result(state, input_model, result, ctx.step.step_id)
        super().on_step_terminal(ctx)
        if self.result is None and self.error is None and state.position.phase == "waiting_resource_request":
            self.status = FlowStatus.WAITING

    def _consume_agent_result(
        self,
        ctx: FlowStepContext,
        state: ResourceReconState,
        input_model: ResourceReconInput,
        result: object | None,
        submission: object | None,
        step_id: str,
    ) -> None:
        if isinstance(result, AgentStepIncompleteResult) or result is None:
            self.error = BaseFlowError(error_type="resource_recon_incomplete", message="ResourceReconAgent did not submit a valid result.")
            return
        if not isinstance(result, ResourceReconStepResult):
            self.error = BaseFlowError(error_type="resource_recon_unsupported_result", message="ResourceReconAgent returned unsupported result.")
            return
        if result.outcome == "completed":
            state.position = FlowPosition(phase="completed")
            self.result = ResourceReconResult(
                outcome="completed",
                repo_key=input_model.repo_key,
                node_path=input_model.node_path,
                material_change_summary=result.material_change_summary,
                checked_material_summary=result.checked_material_summary,
                useful_findings=list(result.useful_findings),
                unresolved_material_needs=list(result.unresolved_material_needs),
                summary=result.summary,
            )
            return
        if result.outcome == "blocked":
            state.position = FlowPosition(phase="completed")
            self.result = ResourceReconResult(
                outcome="blocked",
                repo_key=input_model.repo_key,
                node_path=input_model.node_path,
                reason=result.reason or result.summary,
                summary=result.summary or result.reason or "Resource recon blocked.",
            )
            return
        if result.outcome == "resource_request" and isinstance(submission, ResourceReconRequestResourceSubmission):
            if state.resource_request_count >= _MAX_RESOURCE_REQUESTS_PER_FLOW:
                self.error = BaseFlowError(
                    error_type="resource_recon_request_safety_cap_exceeded",
                    message=(
                        "ResourceReconFlow reached its bounded resource request "
                        f"safety cap of {_MAX_RESOURCE_REQUESTS_PER_FLOW}."
                    ),
                )
                return
            requested_key = _resource_request_key(ctx, submission)
            prior_keys = {
                _resource_request_key(ctx, prior)
                for step in ctx.ark.flow_service.list_steps(flow_id=self.flow_id)
                if step.step_id != step_id
                and isinstance(
                    (prior := step.submission),
                    ResourceReconRequestResourceSubmission,
                )
            }
            if requested_key in prior_keys:
                self.error = BaseFlowError(
                    error_type="resource_recon_duplicate_request",
                    message=(
                        "ResourceReconFlow cannot request the same canonical "
                        f"resource twice: {requested_key}."
                    ),
                )
                return
            state.resource_request_count += 1
            state.waiting_dispatch_step_id = step_id
            state.position = FlowPosition(phase="dispatch_resource_request")
            return
        self.error = BaseFlowError(error_type="resource_recon_incomplete", message=result.incomplete_reason or "ResourceReconAgent incomplete.")

    def _consume_dispatch_result(
        self,
        state: ResourceReconState,
        input_model: ResourceReconInput,
        result: DispatchStepResult,
        step_id: str,
    ) -> None:
        del input_model
        if result.outcome == "dispatched" and result.continuation == "wait_for_callback":
            state.waiting_dispatch_step_id = step_id
            state.position = FlowPosition(phase="waiting_resource_request")
            return
        self.error = BaseFlowError(error_type="resource_recon_dispatch_failed", message=result.summary or "Resource request dispatch failed.")


def _resource_recon_agent_step(
    ctx: FlowContext,
    flow: ResourceReconFlow,
    input_model: ResourceReconInput,
    *,
    callback: bool,
):
    from lean_constellation.flows.common.agent_steps import ResourceReconAgentStep

    state = _require_state(flow.state)
    prior_context = build_prior_preparation_prompt_context(ctx, flow)
    return ResourceReconAgentStep(
        step_id=new_content_step_id("resource_recon_callback" if callback else "resource_recon"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=AgentStepState(
            agent_role="resource_recon",
            agent_type="ResourceReconAgent",
            home_id="ResourceReconAgent",
            create_agent_if_missing=True,
            bind_created_agent_to="flow",
            variables={
                "repo_key": input_model.repo_key,
                "node_path": input_model.node_path,
                "contract_version": input_model.contract_version,
                "objective": input_model.objective,
                "resource_request_count": state.resource_request_count,
            },
            prompt_mode="callback" if callback else "initial",
            prompt_override=(
                None
                if callback
                else f"{_recon_prompt('resource', input_model)}\n\n"
                f"Prior preparation context:\n{prior_context}"
            ),
            callback_dispatch_step_id=state.waiting_dispatch_step_id if callback else None,
            env_overrides={
                "LEAN_CONSTELLATION_AGENT_TYPE": "ResourceReconAgent",
                "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "resource_recon",
                "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "resource_recon_submit",
            },
            workdir_override=content_node_workdir(input_model.repo_path, input_model.node_path),
            max_auto_continue_turns=1,
        ),
    )


def _dispatch_resource_request_step(ctx: FlowContext, flow: ResourceReconFlow, state: ResourceReconState) -> DispatchStep:
    source_step_id = state.waiting_dispatch_step_id
    if source_step_id is None:
        raise TypeError("resource recon dispatch source step is missing")
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise TypeError("ark.flow_service is not registered")
    source_step = flow_service.get_step(source_step_id)
    submission = source_step.submission
    if not isinstance(submission, ResourceReconRequestResourceSubmission):
        raise TypeError(f"resource recon expected resource request submission, got {type(submission).__name__}")
    return DispatchStep(
        step_id=new_content_step_id("resource_recon_dispatch_resource_request"),
        flow_id=flow.flow_id,
        scope_id=flow.scope_id,
        state=DispatchStepState(
            source_step_id=source_step_id,
            source_submission_id=submission.submission_id,
            requests=list(submission.requests),
            continuation=submission.continuation,
        ),
    )


def _child_flows_for_dispatch(ctx: FlowReadContext | FlowContext, parent_flow_id: str, dispatch_step_id: str | None):
    if dispatch_step_id is None:
        return []
    flow_service = ctx.ark.flow_service
    store = getattr(flow_service, "store", None) if flow_service is not None else None
    if store is not None and hasattr(store, "list_child_flows"):
        return list(store.list_child_flows(parent_flow_id=parent_flow_id, parent_dispatch_step_id=dispatch_step_id))
    if flow_service is None:
        return []
    return [
        flow
        for flow in flow_service.list_flows()
        if flow.parent_flow_id == parent_flow_id and flow.parent_dispatch_step_id == dispatch_step_id
    ]


def _resource_request_key(
    ctx: FlowStepContext,
    submission: ResourceReconRequestResourceSubmission,
) -> str:
    material = getattr(ctx.app, "material", None)
    if material is not None:
        normalized = material.prepare_resource_target(
            target_kind=submission.target_kind,
            target=submission.target,
            arxiv_version=submission.arxiv_version,
        )
        if normalized.ok and normalized.value is not None:
            return normalized.value.canonical_locator
    version = submission.arxiv_version or ""
    return (
        f"{submission.target_kind}:"
        f"{submission.target.strip().casefold()}:{version.casefold()}"
    )


def _require_state(state: BaseFlowState) -> ResourceReconState:
    if not isinstance(state, ResourceReconState):
        raise TypeError("resource_recon flow has invalid state")
    return state


def _require_input(input_model: BaseFlowInput | None) -> ResourceReconInput:
    if not isinstance(input_model, ResourceReconInput):
        raise TypeError("resource_recon flow has invalid input")
    return input_model


def _recon_prompt(kind: str, input_model: PreparationReconInput) -> str:
    parts = [f"Run {kind} recon for content node {input_model.node_path}."]
    parts.append(
        "Required Skill re-entry: read and apply $content-contract-reading from the current Home. Read "
        "external-resource-discovery or resource-request-submission only when a concrete unresolved external "
        "need makes that branch necessary."
    )
    if input_model.objective:
        parts.append(f"Objective: {input_model.objective}.")
    if input_model.context_summary:
        parts.append(f"Context: {input_model.context_summary}.")
    parts.append(
        "Reuse matching verified prior preparation findings without broad rediscovery unless they are stale or "
        "unresolved. Use tools for current truth, then call `submit_resource_recon_completed`, "
        "`submit_resource_recon_blocked`, or `submit_resource_request`."
    )
    return "\n".join(parts)
