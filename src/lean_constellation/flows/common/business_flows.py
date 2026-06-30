"""Shared base classes for Lean Constellation business Flow definitions."""

from __future__ import annotations

from typing import Any, ClassVar, Self

from agent_runtime_kit.flow.contexts import FlowBuildContext
from agent_runtime_kit.flow.models import BaseFlow, BaseFlowState, FlowPosition
from pydantic import BaseModel, ConfigDict, Field


class LeanFlowParams(BaseModel):
    """Strict FlowRequest params base for Lean Constellation business flows."""

    model_config = ConfigDict(extra="forbid")


class LeanFlowState(BaseFlowState):
    """Common dispatch bookkeeping used by several business flows."""

    state_type: str = "lean_flow_state"
    waiting_dispatch_step_id: str | None = None
    last_terminal_step_type: str | None = None
    last_outcome: str | None = None


class LeanBusinessFlow(BaseFlow):
    """Base helper for Flow classes built directly from validated params."""

    Params: ClassVar[type[BaseModel]] = LeanFlowParams
    State: ClassVar[type[BaseFlowState]] = LeanFlowState
    requires_callback_input: ClassVar[bool] = True

    @classmethod
    def _build(
        cls,
        ctx: FlowBuildContext,
        *,
        input_model: Any,
        state: BaseFlowState | None = None,
    ) -> Self:
        return cls(
            flow_id=ctx.flow_id,
            scope_id=ctx.scope_id,
            input=input_model,
            state=state or cls.State(),
            parent_flow_id=ctx.parent_flow_id,
            parent_dispatch_step_id=ctx.parent_dispatch_step_id,
        )


def initial_position(phase: str) -> FlowPosition:
    return FlowPosition(phase=phase, round_index=0)


def phase_state(*, state_type: str, phase: str, **fields: Any) -> LeanFlowState:
    return LeanFlowState(state_type=state_type, position=initial_position(phase), **fields)
