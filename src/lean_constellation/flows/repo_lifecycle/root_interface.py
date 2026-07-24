"""Reusable initial, continuation, and standalone root-interface preparation Flow."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext
from agent_runtime_kit.flow.models import (
    BaseFlowError,
    BaseFlowInput,
    BaseFlowResult,
    BaseFlowState,
    FlowPosition,
)
from agent_runtime_kit.flow.standard_steps import AgentStepState
from pydantic import Field

from lean_constellation.domain.repo_run import RepoRunContext
from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.repo_lifecycle.root_interface_steps import (
    AppendRequiredPreparationInterfacesStep,
    DecideRootInterfaceAgentStep,
    RootInterfaceFlowStepResult,
    RootInterfaceReadyGateStep,
    SyncProtectedRootInterfacesStep,
    ValidateRootInterfaceRunStep,
    VerifyRootInterfaceDeltaStep,
    new_root_interface_step_id,
)
from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildResult
from lean_constellation.flows.repo_lifecycle.steps import RootInterfacePrepareStepResult


class RootInterfacePreparationParams(LeanFlowParams):
    repo_key: str
    repo_root: str
    run_context: RepoRunContext
    source_index_delta: SourceIndexBuildResult
    start_reason: Literal["initial", "continuation", "admin_preprocess"]
    pre_run_mutation_checkpoint_id: str


class RootInterfacePreparationInput(LeanRenderableFlowInput):
    input_type: Literal["root_interface_preparation"] = "root_interface_preparation"
    repo_key: str
    repo_root: str
    run_context: RepoRunContext
    source_index_delta: SourceIndexBuildResult
    start_reason: Literal["initial", "continuation", "admin_preprocess"]
    invocation_kind: Literal["child", "standalone"]
    pre_run_mutation_checkpoint_id: str

    def agent_title(self) -> str:
        return f"Prepare root interfaces for {self.repo_key}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "start_reason": self.start_reason,
            "invocation_kind": self.invocation_kind,
            "run_objective": self.run_context.run_spec.run_objective,
            "root_interface_policy": self.run_context.run_spec.root_interface_policy,
            "resolved_source_files": self.run_context.resolved_source_files,
            "source_index_delta_summary": self.source_index_delta.coverage_summary,
            "additional_required_interface_names": [
                interface.name
                for interface in self.run_context.run_spec.additional_required_interfaces
            ],
        }


class RootInterfacePreparationState(BaseFlowState):
    state_type: Literal["root_interface_preparation"] = "root_interface_preparation"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="validate"))
    protected_sync_completed: bool = False
    agent_required: bool | None = None
    previous_interfaces: dict[str, dict[str, object]] = Field(default_factory=dict)
    previous_exports: list[dict[str, object]] = Field(default_factory=list)
    protected_interface_names_added: list[str] = Field(default_factory=list)
    supplement_interface_names_added: list[str] = Field(default_factory=list)
    gate_passed: bool = False


class RootInterfacePreparationResult(LeanRenderableFlowResult):
    result_type: Literal["root_interface_preparation"] = "root_interface_preparation"
    outcome: Literal["ready", "blocked", "invalid_input"]
    repo_key: str
    invocation_kind: Literal["child", "standalone"]
    protected_interface_names_added: list[str] = Field(default_factory=list)
    supplement_interface_names_added: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "invocation_kind": self.invocation_kind,
            "protected_interface_names_added": self.protected_interface_names_added,
            "supplement_interface_names_added": self.supplement_interface_names_added,
            "blocked_reason": self.blocked_reason,
        }


class RootInterfacePreparationFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "root_interface_preparation"
    Params: ClassVar[type[LeanFlowParams]] = RootInterfacePreparationParams
    Input: ClassVar[type[BaseFlowInput]] = RootInterfacePreparationInput
    State: ClassVar[type[BaseFlowState]] = RootInterfacePreparationState
    Result: ClassVar[type[BaseFlowResult]] = RootInterfacePreparationResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {
        "root_interface_preparation": RootInterfacePreparationResult,
    }

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "RootInterfacePreparationFlow":
        params = RootInterfacePreparationParams.model_validate(ctx.params)
        return cls._build(
            ctx,
            input_model=RootInterfacePreparationInput(
                summary=f"Prepare root interfaces for {params.repo_key}.",
                invocation_kind="child" if ctx.parent_flow_id is not None else "standalone",
                **params.model_dump(),
            ),
            state=RootInterfacePreparationState(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _state(self.state)
        input_model = _input(self.input)
        phase = state.position.phase
        step_classes = {
            "validate": ValidateRootInterfaceRunStep,
            "append_required": AppendRequiredPreparationInterfacesStep,
            "sync_protected": SyncProtectedRootInterfacesStep,
            "decide_agent": DecideRootInterfaceAgentStep,
            "verify_delta": VerifyRootInterfaceDeltaStep,
            "ready_gate": RootInterfaceReadyGateStep,
        }
        step_cls = step_classes.get(phase)
        if step_cls is not None:
            return ctx.create_step(
                step_cls(
                    step_id=new_root_interface_step_id(phase),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if phase == "agent":
            from lean_constellation.flows.common.agent_steps import RootInterfacePrepareAgentStep

            return ctx.create_step(
                RootInterfacePrepareAgentStep(
                    step_id=new_root_interface_step_id("root_interface_prepare"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="root_interface_preparer",
                        agent_type="RootInterfacePrepareAgent",
                        home_id="RootInterfacePrepareAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="step",
                        variables={
                            "repo_key": input_model.repo_key,
                            "start_reason": input_model.start_reason,
                        },
                        prompt_override=_agent_prompt(input_model, state),
                        env_overrides={
                            "LEAN_CONSTELLATION_AGENT_TYPE": "RootInterfacePrepareAgent",
                            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "root_interface_prepare",
                            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "root_interface_prepare_submit",
                        },
                        workdir_override=input_model.repo_root,
                        max_auto_continue_turns=1,
                    ),
                )
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _state(self.state)
        input_model = _input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="root_interface_preparation_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return
        if ctx.step.step_type == "root_interface_prepare_agent_step":
            result = ctx.step.result
            if isinstance(result, RootInterfacePrepareStepResult) and result.outcome == "ready":
                state.position = FlowPosition(phase="verify_delta")
            else:
                self._finish(
                    state,
                    input_model,
                    outcome="blocked",
                    summary="RootInterfacePrepareAgent did not submit a ready result.",
                )
            super().on_step_terminal(ctx)
            return
        result = ctx.step.result
        if not isinstance(result, RootInterfaceFlowStepResult):
            self._finish(
                state,
                input_model,
                outcome="blocked",
                summary="Root-interface step returned an unexpected result type.",
            )
            super().on_step_terminal(ctx)
            return
        if result.outcome in {"blocked", "invalid_input"}:
            self._finish(
                state,
                input_model,
                outcome=result.outcome,
                summary=result.summary,
            )
        elif result.outcome == "valid":
            state.position = FlowPosition(phase="append_required")
        elif result.outcome == "appended":
            state.protected_interface_names_added = list(result.added_names)
            state.position = FlowPosition(phase="sync_protected")
        elif result.outcome == "synced":
            state.protected_sync_completed = True
            state.position = FlowPosition(phase="decide_agent")
        elif result.outcome == "decided":
            state.agent_required = result.agent_required
            state.previous_interfaces = dict(result.previous_interfaces)
            state.previous_exports = list(result.previous_exports)
            state.position = FlowPosition(phase="agent" if result.agent_required else "verify_delta")
        elif result.outcome == "verified":
            state.supplement_interface_names_added = list(result.supplement_names_added)
            state.position = FlowPosition(phase="ready_gate")
        elif result.outcome == "ready":
            state.gate_passed = True
            self._finish(state, input_model, outcome="ready", summary=result.summary)
        super().on_step_terminal(ctx)

    def _finish(
        self,
        state: RootInterfacePreparationState,
        input_model: RootInterfacePreparationInput,
        *,
        outcome: Literal["ready", "blocked", "invalid_input"],
        summary: str,
    ) -> None:
        state.position = FlowPosition(phase="completed")
        self.result = RootInterfacePreparationResult(
            outcome=outcome,
            repo_key=input_model.repo_key,
            invocation_kind=input_model.invocation_kind,
            protected_interface_names_added=state.protected_interface_names_added,
            supplement_interface_names_added=state.supplement_interface_names_added,
            blocked_reason=None if outcome == "ready" else summary,
            summary=summary,
        )


ROOT_INTERFACE_FLOW_TYPES: tuple[type[RootInterfacePreparationFlow], ...] = (
    RootInterfacePreparationFlow,
)


def _state(value: BaseFlowState) -> RootInterfacePreparationState:
    if not isinstance(value, RootInterfacePreparationState):
        raise TypeError("RootInterfacePreparationFlow has invalid state")
    return value


def _input(value: BaseFlowInput | None) -> RootInterfacePreparationInput:
    if not isinstance(value, RootInterfacePreparationInput):
        raise TypeError("RootInterfacePreparationFlow has invalid input")
    return value


def _agent_prompt(
    input_model: RootInterfacePreparationInput,
    state: RootInterfacePreparationState,
) -> str:
    del state
    return "\n".join(
        [
            f"Prepare incremental root Main interfaces for native repo {input_model.repo_key}.",
            f"Start reason: {input_model.start_reason}.",
            "Use `get_root_interface_run_context` for this run's exact responsibility and `list_root_interfaces` for the protected baseline.",
            "Use compact SourceIndex overview/block tools for normal evidence reads; read the full index or preparation input only for an explicit holistic audit.",
            "All interfaces that existed when this step started must remain byte-for-byte equivalent in semantic payload; do not update or remove any of them.",
            "Only add supplement interfaces that are necessary for this run's repository-level public API. Do not bind declarations, select exports, or commit Main.",
            "Call `submit_root_interface_prepare_ready` after checking the protected interface gate. After an accepted submit, stop.",
        ]
    )


__all__ = [
    "ROOT_INTERFACE_FLOW_TYPES",
    "RootInterfacePreparationFlow",
    "RootInterfacePreparationInput",
    "RootInterfacePreparationParams",
    "RootInterfacePreparationResult",
    "RootInterfacePreparationState",
]
