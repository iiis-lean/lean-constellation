"""Shared contracts for the application-owned Operator Data API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Callable
from typing import Any, Generic, Literal, TypeVar

from pydantic import Field, model_validator

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import (
    GateReport,
    IssueSeverity,
    ServiceIssue,
    ServiceResult,
)


TSource = TypeVar("TSource")
TOutput = TypeVar("TOutput")


class OperatorAccess(StrEnum):
    READ = "read"
    PREVIEW = "preview"
    MUTATION = "mutation"


class OperatorLockPolicy(StrEnum):
    NONE = "none"
    OPERATOR = "operator"
    SELF_MANAGED = "self_managed"


@dataclass(frozen=True, slots=True)
class OperatorOperationSpec:
    """Internal operation policy selected by a typed domain facade."""

    name: str
    access: OperatorAccess
    lock_policy: OperatorLockPolicy
    requires_stable_runtime: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Operator operation name must be non-empty.")
        if self.access is not OperatorAccess.MUTATION:
            if self.lock_policy is not OperatorLockPolicy.NONE:
                raise ValueError("Read and preview operations cannot acquire mutation locks.")
            if self.requires_stable_runtime:
                raise ValueError("Read and preview operations cannot require a managed runtime.")
        elif self.lock_policy is OperatorLockPolicy.NONE:
            raise ValueError("Repo-local mutations must use an operator or self-managed lock policy.")
        elif not self.requires_stable_runtime:
            raise ValueError("Repo-local mutations must require stable runtime admission.")


FORBIDDEN_OPERATOR_INPUT_FIELDS = frozenset(
    {
        "repo_key",
        "repo_root",
        "flow_id",
        "step_id",
        "agent_id",
        "scope_id",
        "ark_runtime_snapshot_id",
        "owner_flow_id",
        "owner_step_id",
        "owner_agent_id",
        "added_by",
        "lean_check",
        "diagnostics_passed",
        "method_name",
        "service_name",
        "lock_policy",
    }
)


class OperatorInputModel(StrictModel):
    """Strict business payload that cannot carry execution identity or policy."""

    @model_validator(mode="before")
    @classmethod
    def _reject_execution_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            forbidden = sorted(FORBIDDEN_OPERATOR_INPUT_FIELDS.intersection(value))
            if forbidden:
                raise ValueError(
                    "Operator request body cannot supply execution-controlled fields: "
                    + ", ".join(forbidden)
                )
        return value


class OperatorEmptyInput(OperatorInputModel):
    """Explicit empty body for operations without business parameters."""


class OperatorAdmissionView(StrictModel):
    repo_key: str
    management_state: Literal["data_only", "paused_runtime"]
    runtime_loaded: bool
    runtime_history: bool
    paused: bool
    stable: bool
    summary: str


class OperatorIssueView(StrictModel):
    """Stable issue vocabulary exposed by Operator APIs."""

    kind: str
    message: str
    severity: IssueSeverity


_PUBLIC_OPERATOR_ISSUE_MESSAGES = {
    "operator_request_validation_failed": "The operator request is invalid.",
    "operator_repo_key_invalid": "The repository key is invalid.",
    "repo_outside_workspace": "The requested repository is outside the managed workspace.",
    "repo_not_found": "The requested repository does not exist.",
    "repo_not_initialized": "The requested repository is not initialized for Lean Constellation.",
    "operator_repo_runtime_history_unloaded": "The repository must enter paused management mode before mutation.",
    "operator_repo_runtime_not_paused": "The repository runtime is not paused for operator mutation.",
    "operator_repo_runtime_not_stable": "The repository runtime is not stable for operator mutation.",
    "invalid_json": "Stored repository data is invalid.",
    "read_failed": "Stored repository data could not be read.",
    "schema_validation_failed": "Stored repository data does not match the required schema.",
    "write_failed": "Repository data could not be written.",
    "repo_lifecycle_lock_busy": "The repository is busy with another lifecycle operation.",
}

_PUBLIC_OPERATOR_ERROR_FALLBACK = (
    "The operator request could not be completed. Inspect server logs for internal details."
)
_PUBLIC_OPERATOR_WARNING_FALLBACK = (
    "The operator operation completed with an internal warning. Inspect server logs for details."
)
_PUBLIC_OPERATOR_INFO_FALLBACK = "The operator operation reported additional internal information."


class OperatorGateView(StrictModel):
    """Gate result whose nested issues cannot expose storage identity."""

    gate_name: str
    passed: bool
    summary: str | None = None
    issues: list[OperatorIssueView] = Field(default_factory=list)


class OperatorResult(StrictModel, Generic[TOutput]):
    """Public result envelope without Service-internal issue identity fields."""

    ok: bool
    value: TOutput | None = None
    issues: list[OperatorIssueView] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent_status(self) -> "OperatorResult[TOutput]":
        has_error = any(
            issue.severity in {IssueSeverity.ERROR, IssueSeverity.BLOCKER}
            for issue in self.issues
        )
        if self.ok and has_error:
            raise ValueError("successful OperatorResult cannot contain error issues")
        if not self.ok and not has_error:
            raise ValueError("failed OperatorResult must contain an error issue")
        return self


def operator_issue_view(issue: ServiceIssue | OperatorIssueView) -> OperatorIssueView:
    message = _PUBLIC_OPERATOR_ISSUE_MESSAGES.get(issue.kind)
    if message is None:
        if issue.severity in {IssueSeverity.ERROR, IssueSeverity.BLOCKER}:
            message = _PUBLIC_OPERATOR_ERROR_FALLBACK
        elif issue.severity is IssueSeverity.WARNING:
            message = _PUBLIC_OPERATOR_WARNING_FALLBACK
        else:
            message = _PUBLIC_OPERATOR_INFO_FALLBACK
    return OperatorIssueView(
        kind=issue.kind,
        message=message,
        severity=issue.severity,
    )


def operator_gate_view(gate: GateReport) -> OperatorGateView:
    return OperatorGateView(
        gate_name=gate.gate_name,
        passed=gate.passed,
        summary=gate.summary,
        issues=[operator_issue_view(issue) for issue in gate.issues],
    )


def project_operator_result(
    result: ServiceResult[TSource] | OperatorResult[TSource],
    projector: Callable[[TSource], TOutput] | None = None,
) -> OperatorResult[TOutput | TSource]:
    """Project one fixed operation result and remove private issue fields.

    Value projection is deliberately supplied by each typed operation.  This
    helper only handles the common result envelope; it is not a recursive
    serializer or a field-name scrubber.
    """

    value: TOutput | TSource | None = result.value
    if value is not None and projector is not None:
        value = projector(value)
    return OperatorResult[TOutput | TSource](
        ok=result.ok,
        value=value,
        issues=[operator_issue_view(issue) for issue in result.issues],
    )


class OperatorHttpEnvelope(StrictModel):
    """Stable JSON representation of one ServiceResult."""

    ok: bool
    value: Any | None = None
    issues: list[OperatorIssueView] = Field(default_factory=list)


__all__ = [
    "FORBIDDEN_OPERATOR_INPUT_FIELDS",
    "OperatorAccess",
    "OperatorAdmissionView",
    "OperatorEmptyInput",
    "OperatorHttpEnvelope",
    "OperatorInputModel",
    "OperatorGateView",
    "OperatorIssueView",
    "OperatorLockPolicy",
    "OperatorOperationSpec",
    "OperatorResult",
    "operator_gate_view",
    "operator_issue_view",
    "project_operator_result",
]
