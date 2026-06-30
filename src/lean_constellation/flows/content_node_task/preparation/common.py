"""Shared preparation recon Flow models."""

from __future__ import annotations

from typing import Literal

from agent_runtime_kit.flow.models import BaseFlowState, FlowPosition
from pydantic import Field

from lean_constellation.flows.common.business_flows import LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput


PreparationKind = Literal["node_dir_dependency", "mathlib", "resource"]


class PreparationReconParams(LeanFlowParams):
    repo_key: str
    node_path: str
    repo_path: str | None = None
    contract_version: int | None = None
    objective: str | None = None
    context_summary: str | None = None


class PreparationReconInput(LeanRenderableFlowInput):
    repo_key: str
    repo_path: str | None = None
    node_path: str
    contract_version: int | None = None
    objective: str | None = None
    context_summary: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "repo_key": self.repo_key,
            "node_path": self.node_path,
            "contract_version": self.contract_version,
            "objective": self.objective,
            "context_summary": self.context_summary,
        }


class PreparationReconState(BaseFlowState):
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="recon_agent"))
    waiting_dispatch_step_id: str | None = None
