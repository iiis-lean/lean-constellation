"""Controlled AgentStep variants for runtime scheduling tests."""

from __future__ import annotations

from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, FlowStepValidationError
from agent_runtime_kit.flow.standard_steps import AgentStepState
from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.flows.common.agent_steps import BUSINESS_AGENT_STEP_TYPES


ControlledAgentStrategy = Literal[
    "reuse_bound_agent",
    "fresh_same_agent_type",
    "fresh_same_agent_type_bind_flow",
    "fresh_test_agent_type",
    "fork_bound_agent",
]

CONTROLLED_AGENT_OVERRIDE_KEY = "test_override_spec"
CONTROLLED_AGENT_OVERRIDE_ALIASES = (CONTROLLED_AGENT_OVERRIDE_KEY, "controlled_agent_override")
CONTROLLED_AGENT_RECORD_KEY = "controlled_agent_record"


class ControlledAgentOverrideSpec(StrictModel):
    """Local Step override used by controlled runtime tests."""

    strategy: ControlledAgentStrategy = "reuse_bound_agent"
    agent_type_override: str | None = None
    provider_type_override: str | None = None
    home_id_override: str | None = None
    bind_to_flow: bool | None = None
    prompt_override: str | None = None
    prompt_overlay: str | None = None
    continue_prompt_override: str | None = None
    developer_instructions_override: str | None = None
    developer_instructions_overlay: str | None = None
    env_overrides: dict[str, str] = Field(default_factory=dict)
    workdir_override: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "ControlledAgentOverrideSpec":
        if self.strategy == "fresh_test_agent_type" and not self.agent_type_override:
            raise ValueError("fresh_test_agent_type requires agent_type_override")
        return self


class ControlledAgentStepMixin:
    """Mixin that lets a test Step locally replace Agent binding and start inputs."""

    ControlledSpec: ClassVar[type[ControlledAgentOverrideSpec]] = ControlledAgentOverrideSpec

    def prepare_agent(self, ctx: StepRunContext) -> str:
        latest = self._latest_agent_step(ctx)
        state = self._agent_step_state(latest)
        spec = latest.controlled_agent_override_spec_or_none(state)
        if spec is None:
            return super().prepare_agent(ctx)
        role = state.agent_role
        original_agent_id = latest.resolve_bound_agent_id(ctx, role)

        if spec.strategy == "reuse_bound_agent":
            agent_id = super().prepare_agent(ctx)
            latest.record_controlled_agent_decision(ctx, spec, original_agent_id, agent_id)
            return agent_id

        if spec.strategy == "fork_bound_agent":
            if original_agent_id is None:
                raise FlowStepValidationError(f"agent role {role!r} is not bound for step {ctx.step_id}")
            agent = latest._agent_service(ctx).fork_agent(original_agent_id, target_scope_id=ctx.scope_id)
            agent_id = str(agent.agent_id)
        else:
            agent_type = latest.resolve_controlled_agent_type(ctx, state, spec, original_agent_id)
            home_id = latest.resolve_controlled_home_id(state, spec, agent_type)
            agent = latest._agent_service(ctx).create_agent(
                ctx.scope_id,
                agent_type,
                provider_type=spec.provider_type_override or state.provider_type,
                home_id=home_id,
            )
            agent_id = str(agent.agent_id)

        latest.bind_agent_to_step(ctx, role, agent_id)
        if latest.should_bind_controlled_agent_to_flow(spec):
            latest.bind_agent_to_flow(ctx, role, agent_id)
        latest.record_controlled_agent_decision(ctx, spec, original_agent_id, agent_id)
        return agent_id

    def controlled_agent_override_spec_or_none(self, state: AgentStepState) -> ControlledAgentOverrideSpec | None:
        for key in CONTROLLED_AGENT_OVERRIDE_ALIASES:
            raw = state.variables.get(key)
            if raw is None:
                continue
            if isinstance(raw, ControlledAgentOverrideSpec):
                return raw
            if isinstance(raw, dict):
                return self.ControlledSpec.model_validate(raw)
            raise FlowStepValidationError(f"{key} must be a mapping")
        return None

    def controlled_agent_override_spec(self, state: AgentStepState) -> ControlledAgentOverrideSpec:
        return self.controlled_agent_override_spec_or_none(state) or self.ControlledSpec()

    def resolve_controlled_agent_type(
        self,
        ctx: StepRunContext,
        state: AgentStepState,
        spec: ControlledAgentOverrideSpec,
        original_agent_id: str | None,
    ) -> str:
        if spec.agent_type_override:
            return spec.agent_type_override
        if state.agent_type:
            return state.agent_type
        if original_agent_id is not None:
            original_agent = self._agent_service(ctx).get_agent(original_agent_id)
            return str(original_agent.agent_type)
        raise FlowStepValidationError(f"agent_type is required for controlled step {self.step_id}")

    def should_bind_controlled_agent_to_flow(self, spec: ControlledAgentOverrideSpec) -> bool:
        if spec.bind_to_flow is not None:
            return spec.bind_to_flow
        return spec.strategy == "fresh_same_agent_type_bind_flow"

    def resolve_controlled_home_id(
        self,
        state: AgentStepState,
        spec: ControlledAgentOverrideSpec,
        agent_type: str,
    ) -> str | None:
        if spec.home_id_override is not None:
            return spec.home_id_override
        if spec.strategy == "fresh_test_agent_type":
            return agent_type
        return state.home_id

    def record_controlled_agent_decision(
        self,
        ctx: StepRunContext,
        spec: ControlledAgentOverrideSpec,
        original_agent_id: str | None,
        agent_id: str,
    ) -> None:
        record = {
            "strategy": spec.strategy,
            "original_agent_id": original_agent_id or "",
            "agent_id": agent_id,
            "agent_type_override": spec.agent_type_override or "",
            "bind_to_flow": str(self.should_bind_controlled_agent_to_flow(spec)).lower(),
            "metadata": dict(spec.metadata),
        }

        def update(step: BaseStep) -> None:
            state = getattr(step, "state", None)
            if isinstance(state, AgentStepState):
                state.variables[CONTROLLED_AGENT_RECORD_KEY] = record

        ctx.update_step(update)

    def build_start_prompt(self, ctx: StepRunContext, agent_id: str) -> str | None:
        latest = self._latest_agent_step(ctx)
        state = self._agent_step_state(latest)
        spec = latest.controlled_agent_override_spec_or_none(state)
        if spec is None:
            return super().build_start_prompt(ctx, agent_id)
        prompt = spec.prompt_override if spec.prompt_override is not None else super().build_start_prompt(ctx, agent_id)
        if spec.prompt_overlay:
            return latest.append_overlay(prompt, spec.prompt_overlay)
        return prompt

    def build_continue_prompt(self, ctx, agent_id: str, turn_result: object, decision) -> str:
        latest = self._latest_agent_step(ctx)
        state = self._agent_step_state(latest)
        spec = latest.controlled_agent_override_spec_or_none(state)
        if spec is None:
            return super().build_continue_prompt(ctx, agent_id, turn_result, decision)
        if spec.continue_prompt_override is not None:
            return spec.continue_prompt_override
        return super().build_continue_prompt(ctx, agent_id, turn_result, decision)

    def build_developer_instructions_override(self, ctx: StepRunContext, agent_id: str) -> str | None:
        latest = self._latest_agent_step(ctx)
        state = self._agent_step_state(latest)
        spec = latest.controlled_agent_override_spec_or_none(state)
        if spec is None:
            return super().build_developer_instructions_override(ctx, agent_id)
        if spec.developer_instructions_override is not None:
            return spec.developer_instructions_override
        if spec.developer_instructions_overlay:
            base = super().build_developer_instructions_override(ctx, agent_id)
            if base is None:
                base = latest.resolve_agent_developer_instructions_template(ctx, agent_id)
            return latest.append_overlay(base, spec.developer_instructions_overlay)
        return super().build_developer_instructions_override(ctx, agent_id)

    def build_agent_env(self, ctx: StepRunContext, agent_id: str) -> dict[str, str]:
        latest = self._latest_agent_step(ctx)
        state = self._agent_step_state(latest)
        spec = latest.controlled_agent_override_spec_or_none(state)
        if spec is None:
            return super().build_agent_env(ctx, agent_id)
        env = super().build_agent_env(ctx, agent_id)
        env.update(spec.env_overrides)
        agent = latest._agent_service(ctx).get_agent(agent_id)
        env.update(
            {
                "ARK_STEP_ID": ctx.step_id,
                "ARK_FLOW_ID": ctx.flow_id,
                "ARK_AGENT_ID": agent_id,
                "LEAN_CONSTELLATION_AGENT_TYPE": str(agent.agent_type),
            }
        )
        return env

    def resolve_workdir(self, ctx: StepRunContext, agent_id: str) -> str | None:
        latest = self._latest_agent_step(ctx)
        state = self._agent_step_state(latest)
        spec = latest.controlled_agent_override_spec_or_none(state)
        if spec is None:
            return super().resolve_workdir(ctx, agent_id)
        if spec.workdir_override is not None:
            return spec.workdir_override
        return super().resolve_workdir(ctx, agent_id)

    def resolve_agent_developer_instructions_template(self, ctx: StepRunContext, agent_id: str) -> str | None:
        agent_service = self._agent_service(ctx)
        if not hasattr(agent_service, "get_agent"):
            return None
        agent = agent_service.get_agent(agent_id)
        agent_types = getattr(agent_service, "agent_types", None)
        if agent_types is None or not hasattr(agent_types, "get"):
            return None
        agent_type = agent_types.get(str(agent.agent_type))
        return getattr(agent_type, "developer_instructions_template", None)

    @staticmethod
    def append_overlay(base: str | None, overlay: str) -> str:
        if base is None or not base.strip():
            return overlay
        return f"{base.rstrip()}\n\n{overlay}"


def build_controlled_agent_step_type(base_cls: type[BaseStep]) -> type[BaseStep]:
    """Build a controlled subclass that preserves the original step_type."""

    return type(
        f"Controlled{base_cls.__name__}",
        (ControlledAgentStepMixin, base_cls),
        {
            "__module__": __name__,
            "step_type": base_cls.step_type,
        },
    )


CONTROLLED_BUSINESS_AGENT_STEP_TYPES: tuple[type[BaseStep], ...] = tuple(
    build_controlled_agent_step_type(step_cls)
    for step_cls in BUSINESS_AGENT_STEP_TYPES
)
CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES: dict[str, type[BaseStep]] = {
    step_cls.step_type: step_cls
    for step_cls in CONTROLLED_BUSINESS_AGENT_STEP_TYPES
}


__all__ = [
    "CONTROLLED_AGENT_OVERRIDE_ALIASES",
    "CONTROLLED_AGENT_OVERRIDE_KEY",
    "CONTROLLED_AGENT_RECORD_KEY",
    "CONTROLLED_BUSINESS_AGENT_STEP_OVERRIDES",
    "CONTROLLED_BUSINESS_AGENT_STEP_TYPES",
    "ControlledAgentOverrideSpec",
    "ControlledAgentStepMixin",
    "ControlledAgentStrategy",
    "build_controlled_agent_step_type",
]
