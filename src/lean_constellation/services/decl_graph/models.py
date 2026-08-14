"""Decl graph truth and view models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.lean_check import LeanCheck
from lean_constellation.domain.refs import DeclRef, MathlibRef
from lean_constellation.domain.repo import ProofAvailability


class DeclState(StrEnum):
    """Coarse effective state for a declaration revision."""

    PLANNED = "planned"
    SPECIFIED = "specified"
    DECLARED = "declared"
    PROOF_PLANNED = "proof_planned"
    PROVED = "proved"


class DeclChangeKind(StrEnum):
    """Round-level change operation for a declaration."""

    CREATE = "create"
    UPDATE = "update"


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
    AWAITING_CLOSEOUT = "awaiting_closeout"
    COMMITTED = "committed"
    DISCARDED = "discarded"


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


class DeclRevisionStatus(StrEnum):
    """Edit lifecycle for a declaration revision."""

    OPEN = "open"
    COMMITTED = "committed"


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


class DeclOriginRef(StrictModel):
    """Structured source/material reference for natural language content."""

    kind: str
    ref: str | None = None
    source_path: str | None = None
    resource_key: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    start_locator: str | None = None
    end_locator: str | None = None
    note: str | None = None

    @field_validator("kind")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("ref", "source_path", "resource_key", "start_locator", "end_locator", "note")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @model_validator(mode="after")
    def _validate_source_range(self) -> DeclOriginRef:
        if self.kind == "source":
            if self.source_path is None:
                raise ValueError("source origin requires source_path")
            if self.start_line is None or self.end_line is None:
                raise ValueError("source origin requires explicit start_line and end_line")
            if self.start_line > self.end_line:
                raise ValueError("source origin start_line must be <= end_line")
        elif (self.start_line is None) != (self.end_line is None):
            raise ValueError("origin start_line and end_line must be present together")
        return self


class DeclNaturalLanguageSection(StrictModel):
    """Natural language content for a statement or proof section."""

    text: str | None = None
    origin: list[DeclOriginRef] = Field(default_factory=list)


class DeclFormalSection(StrictModel):
    """Lean content and check summary for a statement or proof section."""

    code: str | None = None
    check: LeanCheck | None = None


class RepoDeclDep(StrictModel):
    """Dependency on a declaration in this or another Constellation repo."""

    kind: Literal["repo_decl"] = "repo_decl"
    ref: DeclRef
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


class MathlibDeclDep(StrictModel):
    """Dependency on an external Mathlib declaration."""

    kind: Literal["mathlib_decl"] = "mathlib_decl"
    ref: MathlibRef
    reason: str | None = None

    @field_validator("reason")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


DeclDep = Annotated[RepoDeclDep | MathlibDeclDep, Field(discriminator="kind")]


class DeclStatement(StrictModel):
    """Structured statement portion of a DeclRevision."""

    nl: DeclNaturalLanguageSection | None = None
    formal: DeclFormalSection | None = None
    deps: list[DeclDep] = Field(default_factory=list)

    @field_validator("deps")
    @classmethod
    def _unique_deps(cls, value: list[DeclDep]) -> list[DeclDep]:
        return _unique_decl_deps(value, "statement deps")


class DeclProof(StrictModel):
    """Structured proof portion of a theorem-like DeclRevision."""

    nl: DeclNaturalLanguageSection | None = None
    formal: DeclFormalSection | None = None
    deps: list[DeclDep] = Field(default_factory=list)

    @field_validator("deps")
    @classmethod
    def _unique_deps(cls, value: list[DeclDep]) -> list[DeclDep]:
        return _unique_decl_deps(value, "proof deps")


class DeclRevisionChange(StrictModel):
    """Reason and transition metadata embedded in a DeclRevision."""

    kind: DeclChangeKind
    base_revision: int | None = None
    start_stage: DeclStage | None = None
    target_state: DeclState | None = None
    require_target_state_satisfied: bool = True
    objective: str | None = None
    summary: str | None = None

    @field_validator("base_revision")
    @classmethod
    def _base_revision_valid(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("base_revision must be >= 1")
        return value

    @field_validator("objective", "summary")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

class DeclRevisionRef(StrictModel):
    """Round-local reference to the revision that carries one change."""

    change_id: str
    decl_name: str
    revision: int

    @field_validator("change_id", "decl_name")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("revision")
    @classmethod
    def _revision_valid(cls, value: int) -> int:
        if value < 1:
            raise ValueError("revision must be >= 1")
        return value


class DeclGraphIndex(StrictModel):
    """Small cached index for one Content node decl graph."""

    schema_version: int = 1
    node_id: str
    node_path: str
    strategy_ids: list[str] = Field(default_factory=list)
    round_ids: list[str] = Field(default_factory=list)
    decl_names: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now_iso)
    summary: str

    @field_validator("node_id", "node_path", "summary")
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


class DeclGraphStrategy(StrictModel):
    """Persisted strategy truth for one Content node DeclGraph."""

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


class DeclGraphRound(StrictModel):
    """Persisted decl round truth for one Content node DeclGraph."""

    round_id: str
    node_path: str
    strategy_id: str
    round_index: int
    status: DeclRoundStatus = DeclRoundStatus.DRAFT
    objective: str
    revision_refs: list[DeclRevisionRef] = Field(default_factory=list)
    discarded_by: str | None = None
    discarded_at: str | None = None
    summary: str | None = None
    execution_result_kind: DeclRoundResultKind | None = None
    execution_reason: str | None = None
    execution_completed_at: str | None = None
    result_kind: DeclRoundResultKind | None = None
    result_reason: str | None = None
    plan_closeout_acknowledged_at: str | None = None
    plan_closeout_acknowledged_by: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    started_at: str | None = None
    committed_at: str | None = None

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

    @model_validator(mode="after")
    def _validate_committed_closeout(self) -> DeclGraphRound:
        if self.status == DeclRoundStatus.COMMITTED and (
            self.plan_closeout_acknowledged_at is None
            or self.plan_closeout_acknowledged_by is None
        ):
            raise ValueError(
                "committed declaration rounds require complete Plan closeout truth"
            )
        if self.status == DeclRoundStatus.DISCARDED:
            if self.revision_refs:
                raise ValueError("discarded declaration rounds cannot retain active revision refs")
            if not self.discarded_by or not self.discarded_at:
                raise ValueError("discarded declaration rounds require complete discard truth")
        elif (
            self.discarded_by is not None
            or self.discarded_at is not None
        ):
            raise ValueError("non-discarded declaration rounds cannot carry discard truth")
        return self

    @field_validator("revision_refs")
    @classmethod
    def _unique_revision_refs(cls, value: list[DeclRevisionRef]) -> list[DeclRevisionRef]:
        change_ids = [item.change_id for item in value]
        if len(set(change_ids)) != len(change_ids):
            raise ValueError("revision_refs change_id values must be unique")
        targets = [(item.decl_name, item.revision) for item in value]
        if len(set(targets)) != len(targets):
            raise ValueError("revision_refs target revisions must be unique")
        return value

    @property
    def change_ids(self) -> list[str]:
        return [item.change_id for item in self.revision_refs]


class Decl(StrictModel):
    """Persisted declaration catalog truth."""

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

    @field_validator("node_path", "kind")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("name")
    @classmethod
    def _flat_decl_name(cls, value: str) -> str:
        normalized = _required_text(value)
        if not normalized[0].isalpha() or any(not (char.isalnum() or char == "_") for char in normalized):
            raise ValueError("Decl.name must be one flat Lean module segment")
        return normalized

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


class DeclRevision(StrictModel):
    """Persisted declaration revision truth."""

    lean_decl_name: str | None = None
    revision: int = 1
    state: DeclState = DeclState.PLANNED
    status: DeclRevisionStatus = DeclRevisionStatus.OPEN
    change: DeclRevisionChange | None = None
    restored_from_revision: int | None = None
    statement: DeclStatement = Field(default_factory=DeclStatement)
    proof: DeclProof | None = None
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("lean_decl_name")
    @classmethod
    def _optional_lean_decl_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _required_text(value)

    @field_validator("revision", "restored_from_revision")
    @classmethod
    def _revision_valid(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("revision must be >= 1")
        return value

    @property
    def change_kind(self) -> DeclChangeKind | None:
        return self.change.kind if self.change is not None else None

    @change_kind.setter
    def change_kind(self, value: DeclChangeKind | str | None) -> None:
        if value is None:
            self.change = None
            return
        kind = DeclChangeKind(value)
        if self.change is None:
            self.change = DeclRevisionChange(kind=kind)
        else:
            self.change = self.change.model_copy(update={"kind": kind})

    def _ensure_proof(self) -> DeclProof:
        if self.proof is None:
            self.proof = DeclProof()
        return self.proof

class DeclFileNaturalLanguageView(StrictModel):
    """Provider-facing natural language section for Decl-owned Lean file projection."""

    text: str | None = None
    origin: list[DeclOriginRef] = Field(default_factory=list)


class DeclFileFormalView(StrictModel):
    """Provider-facing formal section for Decl-owned Lean file projection."""

    code: str | None = None
    check: LeanCheck | None = None


class DeclFileStageView(StrictModel):
    """Provider-facing statement/proof section for Decl-owned Lean file projection."""

    nl: DeclFileNaturalLanguageView = Field(default_factory=DeclFileNaturalLanguageView)
    formal: DeclFileFormalView | None = None
    deps: list[DeclDep] = Field(default_factory=list)


class DeclFileRevisionView(StrictModel):
    """Provider-facing revision view consumed by Decl-owned Lean file projection."""

    decl_name: str
    revision: int
    kind: str
    state: DeclState
    version_status: Literal["open", "committed"]
    module: str | None = None
    lean_decl_name: str | None = None
    statement: DeclFileStageView = Field(default_factory=DeclFileStageView)
    proof: DeclFileStageView | None = None

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


class _CompactMutationReceipt(StrictModel):
    """Serialize optional receipt details only when they carry information."""

    @model_serializer(mode="wrap")
    def _serialize_compact(self, handler):
        data = handler(self)
        return {
            key: value
            for key, value in data.items()
            if value is not None and value != []
        }


class DeclRoundDraftDiscardReceipt(_CompactMutationReceipt):
    """Compact receipt for atomically discarding one unsubmitted draft round."""

    round_id: str
    strategy_id: str
    changed: bool
    discarded_change_ids: list[str] = Field(default_factory=list)
    deleted_created_decl_names: list[str] = Field(default_factory=list)
    restored_decl_revisions: dict[str, int] | None = None
    discarded_by: str
    discarded_at: str
    summary: str


class DeclRestoreReceipt(_CompactMutationReceipt):
    """Receipt for restoring historical accepted content as a new committed revision."""

    decl_name: str
    source_revision: int
    restored_revision: int
    changed_files: list[str] = Field(default_factory=list)
    summary: str


class DeclDeleteReceipt(_CompactMutationReceipt):
    """Receipt for deleting one exact current downstream closure."""

    deleted_decl_names: list[str]
    changed_files: list[str] = Field(default_factory=list)
    summary: str


class DeclManagedProjectionEffect(StrictModel):
    """Managed Lean projection effects exposed by a compact mutation receipt."""

    stage: Literal["statement", "proof"] | None = None
    changed_files: list[str] = Field(default_factory=list)
    reread_required: bool = False


class DeclStageWriteReceipt(_CompactMutationReceipt):
    """Narrow result for internal/operator compound stage writes."""

    decl_name: str
    revision: int
    changed: bool
    managed_projection: DeclManagedProjectionEffect | None = None


class DeclTextMutationReceipt(_CompactMutationReceipt):
    """Compact receipt for one Statement NL or Proof NL text update."""

    target: str
    operation: Literal["set"] = "set"
    changed: bool
    managed_projection: DeclManagedProjectionEffect | None = None


class DeclOriginMutationReceipt(_CompactMutationReceipt):
    """Compact receipt for one origin-reference collection update."""

    target: str
    operation: Literal["add", "remove", "clear"]
    changed: bool
    added: list[DeclOriginRef] = Field(default_factory=list)
    removed: list[DeclOriginRef] = Field(default_factory=list)
    already_present: list[DeclOriginRef] = Field(default_factory=list)
    managed_projection: DeclManagedProjectionEffect | None = None


class DeclDependencyMutationReceipt(_CompactMutationReceipt):
    """Compact receipt for one typed dependency collection update."""

    target: str
    operation: Literal["add", "remove", "clear"]
    changed: bool
    added: list[DeclDep] = Field(default_factory=list)
    removed: list[DeclDep] = Field(default_factory=list)
    already_present: list[DeclDep] = Field(default_factory=list)
    managed_projection: DeclManagedProjectionEffect | None = None
    dependency_stage: Literal["statement", "proof"] | None = None
    mathlib_index: dict[str, object] | None = None
    formal_capture_refreshed: bool | None = None


class DeclView(StrictModel):
    """Agent/API-facing declaration catalog view."""

    name: str
    node_path: str
    kind: str
    lifecycle: DeclLifecycle
    public: bool = False
    visibility: str
    current_revision: int
    revision_ids: list[int] = Field(default_factory=list)
    module: str | None = None
    lean_decl_name: str | None = None
    state: DeclState | None = None
    status: DeclRevisionStatus | None = None
    released_state: DeclState | None = None
    release_protected: bool = False
    summary: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("name", "node_path", "kind", "visibility")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


class DeclGraphStrategyView(StrictModel):
    """Agent/API-facing declaration strategy view."""

    strategy_id: str
    node_path: str
    status: DeclStrategyStatus
    objective: str
    rationale: str | None = None
    created_round_ids: list[str] = Field(default_factory=list)
    summary: str | None = None
    closed_reason: str | None = None
    created_at: str | None = None
    closed_at: str | None = None

    @field_validator("strategy_id", "node_path", "objective")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


class DeclGraphRoundView(StrictModel):
    """Agent/API-facing declaration round view."""

    round_id: str
    node_path: str
    strategy_id: str
    round_index: int
    status: DeclRoundStatus
    objective: str
    revision_refs: list[DeclRevisionRef] = Field(default_factory=list)
    discarded_by: str | None = None
    discarded_at: str | None = None
    change_ids: list[str] = Field(default_factory=list)
    summary: str | None = None
    execution_result_kind: DeclRoundResultKind | None = None
    execution_reason: str | None = None
    result_kind: DeclRoundResultKind | None = None
    result_reason: str | None = None
    closeout_required: bool = False
    required_next_action: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    committed_at: str | None = None

    @field_validator("round_id", "node_path", "strategy_id", "objective")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


class DeclChangeView(StrictModel):
    """Round-level declaration change view derived from DeclRevision.change."""

    change_id: str
    node_path: str
    round_id: str
    kind: DeclChangeKind
    decl_name: str
    base_revision: int | None = None
    start_stage: DeclStage | None = None
    target_state: DeclState | None = None
    require_target_state_satisfied: bool = True
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
    issue_categories: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    recommended_next_action: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("round_id", "node_path", "decl_name", "summary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("recommended_next_action")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @field_validator("issue_categories", "required_changes")
    @classmethod
    def _strip_unique_text_list(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("review mark lists must be unique")
        return stripped


class DeclReviewMarkView(StrictModel):
    """Agent/API-facing review mark view derived from reviewer step state."""

    round_id: str
    node_path: str
    stage: DeclStage
    decl_name: str
    passed: bool
    summary: str
    issue_kind: str | None = None
    suggested_fix: str | None = None
    issue_categories: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    recommended_next_action: str | None = None
    created_at: str | None = None

    @field_validator("round_id", "node_path", "decl_name", "summary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("issue_categories", "required_changes")
    @classmethod
    def _strip_unique_text_list(cls, value: list[str]) -> list[str]:
        stripped = [_required_text(item) for item in value]
        if len(set(stripped)) != len(stripped):
            raise ValueError("review mark lists must be unique")
        return stripped

    @field_validator("recommended_next_action")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


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


class DeclReadinessBlocker(StrictModel):
    """The first typed blocker that prevents one declaration from being available."""

    reason: DeclReadinessReason
    blocking_decl: DeclRef | None = None
    required_availability: ProofAvailability | None = None
    current_state: DeclState | None = None
    required_state: DeclState | None = None
    check_stage: Literal["statement", "proof"] | None = None
    dependency_chain: list[DeclRef] = Field(default_factory=list)
    message: str

    @field_validator("message")
    @classmethod
    def _required_message(cls, value: str) -> str:
        return _required_text(value)


class DeclReadinessReport(StrictModel):
    """Dynamic readiness report for one exact declaration revision."""

    node_path: str
    decl_name: str
    revision: int | None = None
    required_availability: ProofAvailability
    ready: bool
    blocker: DeclReadinessBlocker | None = None
    summary: str

    @field_validator("node_path", "decl_name", "summary")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


def _required_text(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("field must be non-empty")
    return value.strip()


def _sorted_unique_text(value: list[str], label: str) -> list[str]:
    stripped = [_required_text(item) for item in value]
    if len(set(stripped)) != len(stripped):
        raise ValueError(f"{label} must be unique")
    return sorted(stripped)


def _sorted_deduped_text(value: list[str]) -> list[str]:
    return sorted({_required_text(item) for item in value})


def _decl_dep_key(dep: DeclDep) -> str:
    if dep.kind == "repo_decl":
        repo = dep.ref.repo or ""
        node = dep.ref.node or ""
        return f"repo_decl:{repo}:{node}:{dep.ref.name}:{dep.ref.revision}"
    return f"mathlib_decl:{dep.ref.module or ''}:{dep.ref.name}"


def _unique_decl_deps(value: list[DeclDep], label: str) -> list[DeclDep]:
    keys = [_decl_dep_key(dep) for dep in value]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} must be unique")
    return sorted(value, key=_decl_dep_key)
