"""Decl graph truth and view models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso


class DeclState(StrEnum):
    """Coarse effective state for a declaration revision."""

    PLANNED = "planned"
    SPECIFIED = "specified"
    DECLARED = "declared"
    PROVED = "proved"


class DeclChangeKind(StrEnum):
    """Round-level change operation for a declaration."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class DeclLifecycle(StrEnum):
    """Lifecycle state for a declaration catalog entry."""

    ACTIVE = "active"
    DELETED = "deleted"


class DeclStrategyStatus(StrEnum):
    """Lifecycle state for a declaration formalization strategy."""

    OPEN = "open"
    CLOSED = "closed"
    FAILED = "failed"


class DeclRoundStatus(StrEnum):
    """Lifecycle state for a declaration round."""

    DRAFT = "draft"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class DeclRoundResultKind(StrEnum):
    """Terminal outcome for a declaration round."""

    SUCCESS = "success"
    BLOCKED = "blocked"
    FAILED = "failed"


class DeclChangeStatus(StrEnum):
    """Lifecycle state for a planned declaration change."""

    PLANNED = "planned"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DeclStage(StrEnum):
    """Fixed declaration execution/review stages."""

    STATEMENT_NL = "statement_nl"
    STATEMENT_FORMAL = "statement_formal"
    PROOF_NL = "proof_nl"
    PROOF_FORMAL = "proof_formal"


class DeclReadinessReason(StrEnum):
    """Reason code for a declaration that is not dynamically ready."""

    MISSING_DECL = "missing_decl"
    NO_ACTIVE_REVISION = "no_active_revision"
    STATE_TOO_LOW = "state_too_low"
    LEAN_CHECK_FAILED = "lean_check_failed"
    CONTAINS_SORRY = "contains_sorry"
    CONTAINS_AXIOM_OR_UNSAFE = "contains_axiom_or_unsafe"
    DEPENDENCY_NOT_READY = "dependency_not_ready"
    DEPENDENCY_MISSING = "dependency_missing"
    CYCLE_DETECTED = "cycle_detected"
    STALE_REVISION = "stale_revision"


class DeclGraphIndex(StrictModel):
    """Small cached index for one Content node decl graph."""

    schema_version: int = 1
    node_path: str
    strategy_ids: list[str] = Field(default_factory=list)
    round_ids: list[str] = Field(default_factory=list)
    decl_names: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)
    summary: str

    @field_validator("node_path", "summary")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("field must be non-empty")
        return value.strip()

    @field_validator("strategy_ids", "round_ids", "decl_names")
    @classmethod
    def _sorted_unique(cls, value: list[str]) -> list[str]:
        stripped = [item.strip() for item in value]
        if any(not item for item in stripped):
            raise ValueError("list values must be non-empty")
        if len(set(stripped)) != len(stripped):
            raise ValueError("list values must be unique")
        return sorted(stripped)


class DeclGraphStoreView(StrictModel):
    """Agent/internal view of the decl graph store location and index counts."""

    repo_root: str
    node_path: str
    graph_root: str
    index_path: str
    strategy_count: int = 0
    round_count: int = 0
    decl_count: int = 0
    summary: str


class DeclStrategyRecord(StrictModel):
    """Persisted Strategy record for one Content node DeclGraph."""

    strategy_id: str
    node_path: str
    status: DeclStrategyStatus = DeclStrategyStatus.OPEN
    objective: str
    rationale: str | None = None
    created_round_ids: list[str] = Field(default_factory=list)
    summary: str | None = None
    closed_reason: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    closed_at: str | None = None

    @field_validator("strategy_id", "node_path", "objective")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("created_round_ids")
    @classmethod
    def _unique_round_ids(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("created_round_ids must be unique")
        return stripped


class DeclRoundRecord(StrictModel):
    """Persisted decl round record shape."""

    round_id: str
    node_path: str
    strategy_id: str
    round_index: int
    status: DeclRoundStatus = DeclRoundStatus.DRAFT
    objective: str
    change_ids: list[str] = Field(default_factory=list)
    change_summaries: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    result_kind: DeclRoundResultKind | None = None
    result_reason: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    started_at: str | None = None
    completed_at: str | None = None

    @field_validator("round_id", "node_path", "strategy_id", "objective")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("round_index")
    @classmethod
    def _positive_round_index(cls, value: int) -> int:
        if value < 1:
            raise ValueError("round_index must be >= 1")
        return value

    @field_validator("change_ids")
    @classmethod
    def _unique_change_ids(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("change_ids must be unique")
        return stripped

    @field_validator("change_summaries")
    @classmethod
    def _non_empty_change_summaries(cls, value: dict[str, str]) -> dict[str, str]:
        return {_required_text(key): _required_text(summary) for key, summary in value.items()}


class DeclRecord(StrictModel):
    """Persisted declaration catalog record."""

    name: str
    node_path: str
    kind: str
    lifecycle: DeclLifecycle = DeclLifecycle.ACTIVE
    public: bool = False
    current_revision: int = 1
    revision_ids: list[int] = Field(default_factory=lambda: [1])
    module: str | None = None
    summary: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("name", "node_path", "kind")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("revision_ids")
    @classmethod
    def _revision_ids_valid(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("revision_ids must be non-empty")
        if any(item < 1 for item in value):
            raise ValueError("revision ids must be >= 1")
        if len(set(value)) != len(value):
            raise ValueError("revision_ids must be unique")
        return sorted(value)


class DeclRevisionRecord(StrictModel):
    """Persisted declaration revision record shape."""

    decl_name: str
    revision: int = 1
    state: DeclState = DeclState.PLANNED
    version_status: Literal["open", "committed"] = "open"
    change_kind: DeclChangeKind | None = None
    statement_nl: str | None = None
    statement_origin: list[dict[str, str]] = Field(default_factory=list)
    statement_deps: list[str] = Field(default_factory=list)
    statement_lean_code: str | None = None
    statement_lean_check: dict[str, str] | None = None
    proof_nl: str | None = None
    proof_origin: list[dict[str, str]] = Field(default_factory=list)
    proof_deps: list[str] = Field(default_factory=list)
    proof_lean_code: str | None = None
    proof_lean_check: dict[str, str] | None = None
    decl_deps: list[str] = Field(default_factory=list)
    module: str | None = None
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("decl_name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("revision")
    @classmethod
    def _revision_valid(cls, value: int) -> int:
        if value < 1:
            raise ValueError("revision must be >= 1")
        return value

    @field_validator("statement_deps", "proof_deps", "decl_deps")
    @classmethod
    def _unique_dep_names(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("dependency names must be unique")
        return sorted(stripped)


class DeclFileRevisionView(StrictModel):
    """Provider-facing revision view consumed by Decl-owned Lean file projection."""

    decl_name: str
    revision: int
    kind: str
    state: DeclState
    version_status: Literal["open", "committed"]
    module: str | None = None
    statement: dict[str, Any] = Field(default_factory=dict)
    proof: dict[str, Any] = Field(default_factory=dict)
    decl_deps: list[str] = Field(default_factory=list)

    @field_validator("decl_name", "kind")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("revision")
    @classmethod
    def _revision_valid(cls, value: int) -> int:
        if value < 1:
            raise ValueError("revision must be >= 1")
        return value


class DeclChangeRecord(StrictModel):
    """Persisted round-level declaration change record."""

    change_id: str
    node_path: str
    round_id: str
    kind: DeclChangeKind
    decl_name: str
    start_before_state: DeclState | None = None
    end_after_state: DeclState | None = None
    objective: str
    summary: str | None = None
    status: DeclChangeStatus = DeclChangeStatus.PLANNED
    target_revision: int | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("change_id", "node_path", "round_id", "decl_name", "objective")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


class DeclDeleteClosureView(StrictModel):
    """Downstream delete closure view for a set of declaration names."""

    requested_decl_names: list[str] = Field(default_factory=list)
    closure_decl_names: list[str] = Field(default_factory=list)
    missing_decl_names: list[str] = Field(default_factory=list)
    summary: str

    @field_validator("requested_decl_names", "closure_decl_names", "missing_decl_names")
    @classmethod
    def _sorted_unique_names(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("decl name lists must be unique")
        return sorted(stripped)


class DeclDependencyClosureView(StrictModel):
    """Dependency closure view for a set of declarations."""

    root_decl_names: list[str] = Field(default_factory=list)
    upstream_decl_names: list[str] = Field(default_factory=list)
    downstream_decl_names: list[str] = Field(default_factory=list)
    missing_decl_names: list[str] = Field(default_factory=list)
    summary: str

    @field_validator("root_decl_names", "upstream_decl_names", "downstream_decl_names", "missing_decl_names")
    @classmethod
    def _sorted_unique_names(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("decl name lists must be unique")
        return sorted(stripped)


class DeclReviewMarkRecord(StrictModel):
    """Per-declaration reviewer mark for one stage."""

    round_id: str
    node_path: str
    stage: DeclStage
    decl_name: str
    passed: bool
    summary: str
    issue_kind: str | None = None
    suggested_fix: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("round_id", "node_path", "decl_name", "summary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


class StageReviewResultView(StrictModel):
    """Stage review aggregate shown to Flow logic and retry prompts."""

    round_id: str
    node_path: str
    stage: DeclStage
    passed: bool
    reviewed_decl_names: list[str] = Field(default_factory=list)
    failed_decl_names: list[str] = Field(default_factory=list)
    missing_decl_names: list[str] = Field(default_factory=list)
    feedback: list[DeclReviewMarkRecord] = Field(default_factory=list)
    summary: str

    @field_validator("reviewed_decl_names", "failed_decl_names", "missing_decl_names")
    @classmethod
    def _sorted_unique_names(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("decl name lists must be unique")
        return sorted(stripped)


class DeclReadinessReport(StrictModel):
    """Dynamic readiness report for one declaration revision."""

    node_path: str
    decl_name: str
    revision: int | None = None
    ready: bool
    reason: DeclReadinessReason | None = None
    details: dict[str, str] = Field(default_factory=dict)
    dependencies_checked: list[str] = Field(default_factory=list)
    failed_dependencies: list[str] = Field(default_factory=list)
    summary: str

    @field_validator("node_path", "decl_name", "summary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("dependencies_checked", "failed_dependencies")
    @classmethod
    def _sorted_unique_names(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("dependency lists must be unique")
        return sorted(stripped)


def _required_text(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("field must be non-empty")
    return value.strip()
