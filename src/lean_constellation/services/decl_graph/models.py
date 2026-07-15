"""Decl graph truth and view models."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.domain.lean_check import LeanCheck, compact_lean_check
from lean_constellation.domain.refs import DeclRef, MathlibRef
from lean_constellation.domain.repo import ProofAvailability


class DeclState(StrEnum):
    """Coarse effective state for a declaration revision."""

    PLANNED = "planned"
    SPECIFIED = "specified"
    DECLARED = "declared"
    PROOF_PLANNED = "proof_planned"
    PROVED = "proved"
    OBSOLETE = "obsolete"


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
    COMMITTED = "committed"


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
    start_line: int | None = None
    end_line: int | None = None
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
    start_before_state: DeclState | None = None
    end_after_state: DeclState | None = None
    require_target_state_satisfied: bool = True
    objective: str | None = None
    summary: str | None = None

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
    change_summaries: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    result_kind: DeclRoundResultKind | None = None
    result_reason: str | None = None
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

    @field_validator("change_summaries")
    @classmethod
    def _non_empty_change_summaries(cls, value: dict[str, str]) -> dict[str, str]:
        return {_required_text(key): _required_text(summary) for key, summary in value.items()}

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
    statement: DeclStatement = Field(default_factory=DeclStatement)
    proof: DeclProof | None = None
    updated_at: str = Field(default_factory=utc_now_iso)

    @field_validator("lean_decl_name")
    @classmethod
    def _optional_lean_decl_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _required_text(value)

    @field_validator("revision")
    @classmethod
    def _revision_valid(cls, value: int) -> int:
        if value < 1:
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

    @property
    def statement_nl(self) -> str | None:
        return self.statement.nl.text if self.statement.nl is not None else None

    @statement_nl.setter
    def statement_nl(self, value: str | None) -> None:
        if value is None:
            self.statement.nl = None
            return
        origin = self.statement.nl.origin if self.statement.nl is not None else []
        self.statement.nl = DeclNaturalLanguageSection(text=value, origin=origin)

    @property
    def statement_origin(self) -> list[dict[str, object]]:
        return [item.model_dump(mode="json", exclude_none=True) for item in self.statement.nl.origin] if self.statement.nl is not None else []

    @statement_origin.setter
    def statement_origin(self, value: list[dict[str, object]]) -> None:
        text = self.statement.nl.text if self.statement.nl is not None else None
        self.statement.nl = DeclNaturalLanguageSection(text=text, origin=[DeclOriginRef.model_validate(item) for item in value])

    @property
    def statement_deps(self) -> list[str]:
        return [_decl_dep_display_name(dep) for dep in self.statement.deps]

    @statement_deps.setter
    def statement_deps(self, value: list[str]) -> None:
        self.statement.deps = _repo_decl_deps_from_names(value, "statement deps")

    @property
    def statement_lean_code(self) -> str | None:
        return self.statement.formal.code if self.statement.formal is not None else None

    @statement_lean_code.setter
    def statement_lean_code(self, value: str | None) -> None:
        if value is None:
            if self.statement.formal is not None:
                self.statement.formal = self.statement.formal.model_copy(update={"code": None})
            return
        check = self.statement.formal.check if self.statement.formal is not None else None
        self.statement.formal = DeclFormalSection(code=value, check=check)

    @property
    def statement_lean_check(self) -> dict[str, str] | None:
        return compact_lean_check(self.statement.formal.check) if self.statement.formal is not None else None

    @statement_lean_check.setter
    def statement_lean_check(self, value: LeanCheck | dict[str, object] | None) -> None:
        code = self.statement.formal.code if self.statement.formal is not None else None
        if value is None and code is None:
            self.statement.formal = None
            return
        check = None if value is None else value if isinstance(value, LeanCheck) else LeanCheck.model_validate(value)
        self.statement.formal = DeclFormalSection(code=code, check=check)

    def _ensure_proof(self) -> DeclProof:
        if self.proof is None:
            self.proof = DeclProof()
        return self.proof

    @property
    def proof_nl(self) -> str | None:
        return self.proof.nl.text if self.proof is not None and self.proof.nl is not None else None

    @proof_nl.setter
    def proof_nl(self, value: str | None) -> None:
        if value is None:
            if self.proof is not None:
                self.proof.nl = None
                if self.proof.formal is None and not self.proof.deps:
                    self.proof = None
            return
        proof = self._ensure_proof()
        origin = proof.nl.origin if proof.nl is not None else []
        proof.nl = DeclNaturalLanguageSection(text=value, origin=origin)

    @property
    def proof_origin(self) -> list[dict[str, object]]:
        return [item.model_dump(mode="json", exclude_none=True) for item in self.proof.nl.origin] if self.proof is not None and self.proof.nl is not None else []

    @proof_origin.setter
    def proof_origin(self, value: list[dict[str, object]]) -> None:
        proof = self._ensure_proof()
        text = proof.nl.text if proof.nl is not None else None
        proof.nl = DeclNaturalLanguageSection(text=text, origin=[DeclOriginRef.model_validate(item) for item in value])

    @property
    def proof_deps(self) -> list[str]:
        return [_decl_dep_display_name(dep) for dep in self.proof.deps] if self.proof is not None else []

    @proof_deps.setter
    def proof_deps(self, value: list[str]) -> None:
        proof = self._ensure_proof()
        proof.deps = _repo_decl_deps_from_names(value, "proof deps")
        if proof.nl is None and proof.formal is None and not proof.deps:
            self.proof = None

    @property
    def proof_lean_code(self) -> str | None:
        return self.proof.formal.code if self.proof is not None and self.proof.formal is not None else None

    @proof_lean_code.setter
    def proof_lean_code(self, value: str | None) -> None:
        if value is None:
            if self.proof is not None and self.proof.formal is not None:
                self.proof.formal = self.proof.formal.model_copy(update={"code": None})
            return
        proof = self._ensure_proof()
        check = proof.formal.check if proof.formal is not None else None
        proof.formal = DeclFormalSection(code=value, check=check)

    @property
    def proof_lean_check(self) -> dict[str, str] | None:
        return compact_lean_check(self.proof.formal.check) if self.proof is not None and self.proof.formal is not None else None

    @proof_lean_check.setter
    def proof_lean_check(self, value: LeanCheck | dict[str, object] | None) -> None:
        proof = self._ensure_proof()
        code = proof.formal.code if proof.formal is not None else None
        if value is None and code is None:
            proof.formal = None
            if proof.nl is None and not proof.deps:
                self.proof = None
            return
        check = None if value is None else value if isinstance(value, LeanCheck) else LeanCheck.model_validate(value)
        proof.formal = DeclFormalSection(code=code, check=check)

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
    deps: list[str] = Field(default_factory=list)
    dep_refs: list[DeclDep] = Field(default_factory=list)

    @field_validator("deps")
    @classmethod
    def _unique_deps(cls, value: list[str]) -> list[str]:
        return _sorted_unique_text(value, "decl file stage deps")


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


class DeclStageMutationView(StrictModel):
    """Truth mutation result with its managed Lean projection effects."""

    revision: DeclRevision
    projection_stage: Literal["statement", "proof"] | None = None
    managed_projection_changed: bool = False
    changed_files: list[str] = Field(default_factory=list)
    reread_required: bool = False
    summary: str


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
    change_ids: list[str] = Field(default_factory=list)
    change_summaries: dict[str, str] = Field(default_factory=dict)
    summary: str | None = None
    result_kind: DeclRoundResultKind | None = None
    result_reason: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    committed_at: str | None = None

    @field_validator("round_id", "node_path", "strategy_id", "objective")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)


class DeclRevisionToolView(StrictModel):
    """Agent-facing flat view derived from structured DeclRevision truth."""

    decl_name: str
    node_path: str
    revision: int
    kind: str
    lifecycle: DeclLifecycle
    public: bool = False
    visibility: str
    state: DeclState
    status: DeclRevisionStatus
    released_state: DeclState | None = None
    release_protected: bool = False
    module: str | None = None
    lean_decl_name: str | None = None
    change_id: str | None = None
    change_kind: DeclChangeKind | None = None
    change_objective: str | None = None
    change_summary: str | None = None
    start_before_state: DeclState | None = None
    end_after_state: DeclState | None = None
    require_target_state_satisfied: bool = True
    statement_nl: str | None = None
    statement_origin: list[DeclOriginRef] = Field(default_factory=list)
    statement_deps: list[str] = Field(default_factory=list)
    statement_dep_refs: list[DeclDep] = Field(default_factory=list)
    statement_lean_code: str | None = None
    statement_lean_check: dict[str, str] | None = None
    proof_nl: str | None = None
    proof_origin: list[DeclOriginRef] = Field(default_factory=list)
    proof_deps: list[str] = Field(default_factory=list)
    proof_dep_refs: list[DeclDep] = Field(default_factory=list)
    proof_lean_code: str | None = None
    proof_lean_check: dict[str, str] | None = None
    effective_deps: list[str] = Field(default_factory=list)
    effective_dep_refs: list[DeclDep] = Field(default_factory=list)
    summary: str | None = None
    updated_at: str | None = None

    @field_validator("decl_name", "node_path", "kind", "visibility")
    @classmethod
    def _required_text(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("statement_deps", "proof_deps", "effective_deps")
    @classmethod
    def _unique_deps(cls, value: list[str]) -> list[str]:
        return _sorted_deduped_text(value)


class DeclStageMutationToolView(StrictModel):
    """Agent-facing Decl mutation result including managed-file side effects."""

    decl: DeclRevisionToolView
    projection_stage: Literal["statement", "proof"] | None = None
    managed_projection_changed: bool = False
    changed_files: list[str] = Field(default_factory=list)
    reread_required: bool = False
    summary: str


class DeclChangeView(StrictModel):
    """Round-level declaration change view derived from DeclRevision.change."""

    change_id: str
    node_path: str
    round_id: str
    kind: DeclChangeKind
    decl_name: str
    start_before_state: DeclState | None = None
    end_after_state: DeclState | None = None
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


class DeclReadinessReport(StrictModel):
    """Dynamic readiness report for one declaration revision."""

    node_path: str
    decl_name: str
    revision: int | None = None
    ready: bool
    proof_policy_satisfied: bool | None = None
    target_proof_availability: ProofAvailability | None = None
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


def _sorted_unique_text(value: list[str], label: str) -> list[str]:
    stripped = [_required_text(item) for item in value]
    if len(set(stripped)) != len(stripped):
        raise ValueError(f"{label} must be unique")
    return sorted(stripped)


def _sorted_deduped_text(value: list[str]) -> list[str]:
    return sorted({_required_text(item) for item in value})


def _repo_decl_deps_from_names(value: list[str], label: str) -> list[DeclDep]:
    return [RepoDeclDep(ref=DeclRef(name=name)) for name in _sorted_unique_text(value, label)]


def _decl_dep_key(dep: DeclDep) -> str:
    if dep.kind == "repo_decl":
        repo = dep.ref.repo or ""
        node = dep.ref.node or ""
        return f"repo_decl:{repo}:{node}:{dep.ref.name}:{dep.ref.revision}"
    return f"mathlib_decl:{dep.ref.module or ''}:{dep.ref.name}"


def _decl_dep_display_name(dep: DeclDep) -> str:
    if dep.kind == "repo_decl":
        return dep.ref.name
    return dep.ref.name


def _unique_decl_deps(value: list[DeclDep], label: str) -> list[DeclDep]:
    keys = [_decl_dep_key(dep) for dep in value]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} must be unique")
    return sorted(value, key=_decl_dep_key)
