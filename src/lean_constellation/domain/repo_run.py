"""Per-run inputs and derived handoff context for native repository work."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.repo import ProofAvailability, RepoConfig, RepoWorkMode


class SourceScope(StrictModel):
    mode: Literal["none", "selected", "all"]
    selectors: list[str] = Field(default_factory=list)
    summary: str | None = None

    @field_validator("selectors")
    @classmethod
    def _normalize_selectors(cls, value: list[str]) -> list[str]:
        normalized = [selector.strip() for selector in value]
        if any(not selector for selector in normalized):
            raise ValueError("source scope selectors must be non-empty")
        return normalized

    @model_validator(mode="after")
    def _validate_mode_selectors(self) -> "SourceScope":
        if self.mode == "selected" and not self.selectors:
            raise ValueError("selected source scope requires at least one selector")
        if self.mode in {"none", "all"} and self.selectors:
            raise ValueError(f"{self.mode} source scope requires an empty selectors list")
        return self


class RepoRunSpec(StrictModel):
    run_objective: str
    target_proof_availability: ProofAvailability
    work_mode: RepoWorkMode
    source_scope: SourceScope
    index_policy: Literal["auto", "update", "reuse"]
    root_interface_policy: Literal["auto", "prepare", "reuse"]
    max_parallel_content_node_tasks: int = 1
    additional_required_interfaces: list[DeclInterface] = Field(default_factory=list)

    @field_validator("run_objective")
    @classmethod
    def _objective_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("run_objective must be non-empty")
        return normalized

    @field_validator("max_parallel_content_node_tasks")
    @classmethod
    def _positive_parallelism(cls, value: int) -> int:
        if value < 1:
            raise ValueError("max_parallel_content_node_tasks must be >= 1")
        return value

    @model_validator(mode="after")
    def _validate_config_and_interfaces(self) -> "RepoRunSpec":
        RepoConfig(
            target_proof_availability=self.target_proof_availability,
            work_mode=self.work_mode,
        )
        names = [interface.name for interface in self.additional_required_interfaces]
        if len(names) != len(set(names)):
            raise ValueError("additional_required_interfaces must have unique names")
        return self


class RepoRunContext(StrictModel):
    start_kind: Literal["initial", "continuation"]
    run_spec: RepoRunSpec
    resolved_source_files: list[str] = Field(default_factory=list)
    source_index_delta_summary: str | None = None
    root_interface_delta_summary: str | None = None
    config_change_summary: str | None = None
    base_release_id: str | None = None


__all__ = ["RepoRunContext", "RepoRunSpec", "SourceScope"]
