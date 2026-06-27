"""Common result, issue, and gate-report helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import Field, field_validator, model_validator

from lean_constellation.domain.common import StrictModel

T = TypeVar("T")


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKER = "blocker"


class ServiceIssue(StrictModel):
    kind: str
    message: str
    severity: IssueSeverity = IssueSeverity.ERROR
    object_ref: str | None = None
    field: str | None = None
    current: str | None = None
    expected: str | None = None
    suggested_action: str | None = None
    details: dict[str, str] = Field(default_factory=dict)

    @field_validator("kind", "message")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("value must be non-empty")
        return value.strip()


class ServiceResult(StrictModel, Generic[T]):
    ok: bool
    value: T | None = None
    issues: list[ServiceIssue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_result(self) -> "ServiceResult[T]":
        has_error = any(ResultErrorComponent.is_error_issue(issue) for issue in self.issues)
        if self.ok and has_error:
            raise ValueError("successful ServiceResult cannot contain error issues")
        if not self.ok and not has_error:
            raise ValueError("failed ServiceResult must contain at least one error issue")
        return self


class GateReport(StrictModel):
    gate_name: str
    passed: bool
    summary: str | None = None
    issues: list[ServiceIssue] = Field(default_factory=list)

    @field_validator("gate_name")
    @classmethod
    def _non_empty_gate_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("gate_name must be non-empty")
        return value.strip()

    @model_validator(mode="after")
    def _validate_report(self) -> "GateReport":
        has_error = any(ResultErrorComponent.is_error_issue(issue) for issue in self.issues)
        if self.passed and has_error:
            raise ValueError("passed GateReport cannot contain error issues")
        if not self.passed and not has_error:
            raise ValueError("failed GateReport must contain at least one error issue")
        return self


class MutationSummaryView(StrictModel):
    object_ref: str
    changed: bool
    summary: str
    changed_items: list[str] = Field(default_factory=list)
    auto_maintenance: list[str] = Field(default_factory=list)
    warnings: list[ServiceIssue] = Field(default_factory=list)


class ToolResultView(StrictModel):
    ok: bool
    summary: str
    issues: list[ServiceIssue] = Field(default_factory=list)
    value: dict[str, Any] | None = None


class ResultErrorComponent:
    """Factory for the shared service result vocabulary."""

    @staticmethod
    def is_error_issue(issue: ServiceIssue) -> bool:
        return issue.severity in {IssueSeverity.ERROR, IssueSeverity.BLOCKER}

    def issue(
        self,
        kind: str,
        message: str,
        *,
        severity: IssueSeverity = IssueSeverity.ERROR,
        object_ref: str | None = None,
        field: str | None = None,
        current: str | None = None,
        expected: str | None = None,
        suggested_action: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> ServiceIssue:
        normalized_details = {str(key): str(value) for key, value in (details or {}).items()}
        return ServiceIssue(
            kind=kind,
            message=message,
            severity=IssueSeverity(severity),
            object_ref=object_ref,
            field=field,
            current=current,
            expected=expected,
            suggested_action=suggested_action,
            details=normalized_details,
        )

    def ok(self, value: T | None = None, warnings: Sequence[ServiceIssue] | None = None) -> ServiceResult[T]:
        warning_list = list(warnings or [])
        if any(self.is_error_issue(issue) for issue in warning_list):
            raise ValueError("ok warnings cannot contain error severity issues")
        return ServiceResult[T](ok=True, value=value, issues=warning_list)

    def fail(self, issues: ServiceIssue | Sequence[ServiceIssue]) -> ServiceResult[Any]:
        issue_list = self._coerce_issues(issues)
        if not issue_list:
            raise ValueError("fail requires at least one issue")
        if not any(self.is_error_issue(issue) for issue in issue_list):
            raise ValueError("fail requires at least one error severity issue")
        return ServiceResult[Any](ok=False, issues=issue_list)

    def gate_passed(
        self,
        gate_name: str,
        summary: str | None = None,
        warnings: Sequence[ServiceIssue] | None = None,
    ) -> GateReport:
        warning_list = list(warnings or [])
        if any(self.is_error_issue(issue) for issue in warning_list):
            raise ValueError("passed gate warnings cannot contain error severity issues")
        return GateReport(gate_name=gate_name, passed=True, summary=summary, issues=warning_list)

    def gate_failed(
        self,
        gate_name: str,
        issues: ServiceIssue | Sequence[ServiceIssue],
        summary: str | None = None,
    ) -> GateReport:
        issue_list = self._coerce_issues(issues)
        if not issue_list:
            raise ValueError("gate_failed requires at least one issue")
        if not any(self.is_error_issue(issue) for issue in issue_list):
            raise ValueError("gate_failed requires at least one error severity issue")
        return GateReport(gate_name=gate_name, passed=False, summary=summary, issues=issue_list)

    def merge_gate_reports(self, gate_name: str, reports: Sequence[GateReport]) -> GateReport:
        reports_list = list(reports)
        issues: list[ServiceIssue] = []
        failed_count = 0
        for report in reports_list:
            issues.extend(report.issues)
            if not report.passed:
                failed_count += 1
        warning_count = sum(1 for issue in issues if issue.severity == IssueSeverity.WARNING)
        if failed_count:
            summary = f"{failed_count} checks failed, {warning_count} warnings"
            return self.gate_failed(gate_name, issues, summary=summary)
        summary = f"{len(reports_list)} checks passed, {warning_count} warnings"
        return self.gate_passed(gate_name, summary=summary, warnings=issues)

    def mutation_view(
        self,
        *,
        object_ref: str,
        changed: bool,
        summary: str,
        changed_items: Sequence[str] | None = None,
        auto_maintenance: Sequence[str] | None = None,
        warnings: Sequence[ServiceIssue] | None = None,
    ) -> MutationSummaryView:
        warning_list = list(warnings or [])
        if any(self.is_error_issue(issue) for issue in warning_list):
            raise ValueError("mutation warnings cannot contain error severity issues")
        return MutationSummaryView(
            object_ref=object_ref,
            changed=changed,
            summary=summary,
            changed_items=list(changed_items or []),
            auto_maintenance=list(auto_maintenance or []),
            warnings=warning_list,
        )

    def gate_report_view(self, report: GateReport) -> ToolResultView:
        return ToolResultView(
            ok=report.passed,
            summary=report.summary or report.gate_name,
            issues=report.issues,
            value={"gate_name": report.gate_name, "passed": report.passed},
        )

    @staticmethod
    def _coerce_issues(issues: ServiceIssue | Sequence[ServiceIssue]) -> list[ServiceIssue]:
        if isinstance(issues, ServiceIssue):
            return [issues]
        return list(issues)
