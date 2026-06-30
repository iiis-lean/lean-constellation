"""Shared render helpers for Lean Constellation Flow / Step models."""

from __future__ import annotations

from typing import Any

from agent_runtime_kit.flow.models import BaseFlowInput, BaseFlowResult, BaseStepResult


def render_agent_view(title: str, *, summary: str | None = None, fields: dict[str, Any] | None = None) -> str:
    """Render a compact Agent-facing view without runtime bookkeeping ids."""

    lines = [title]
    if summary:
        lines.append(f"Summary: {summary}")
    for key, value in (fields or {}).items():
        if value is None or value == [] or value == {}:
            continue
        label = key.replace("_", " ").title()
        if isinstance(value, list):
            rendered = ", ".join(str(item) for item in value)
        else:
            rendered = str(value)
        lines.append(f"{label}: {rendered}")
    return "\n".join(lines)


class LeanRenderableFlowInput(BaseFlowInput):
    """Base Flow input with a deterministic Agent-facing renderer."""

    def agent_title(self) -> str:
        return self.input_type.replace("_", " ").title()

    def agent_fields(self) -> dict[str, Any]:
        return {}

    def render_for_agent(self, ctx: object) -> str:
        return render_agent_view(self.agent_title(), summary=self.summary, fields=self.agent_fields())


class LeanRenderableFlowResult(BaseFlowResult):
    """Base Flow result with a deterministic Agent-facing renderer."""

    outcome: str

    def agent_title(self) -> str:
        return self.result_type.replace("_", " ").title()

    def agent_fields(self) -> dict[str, Any]:
        return {"outcome": self.outcome}

    def render_for_agent(self, ctx: object) -> str:
        return render_agent_view(self.agent_title(), summary=self.summary, fields=self.agent_fields())


class LeanRenderableStepResult(BaseStepResult):
    """Base Step result with a deterministic Agent-facing renderer."""

    outcome: str

    def agent_title(self) -> str:
        return self.result_type.replace("_", " ").title()

    def agent_fields(self) -> dict[str, Any]:
        return {"outcome": self.outcome}

    def render_for_agent(self, ctx: object) -> str:
        return render_agent_view(self.agent_title(), summary=self.summary, fields=self.agent_fields())
