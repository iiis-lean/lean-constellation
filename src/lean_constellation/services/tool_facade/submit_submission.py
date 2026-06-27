"""Submit-tool bridge to the runtime AgentStep submission gateway."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from lean_constellation.domain.common import StrictModel, utc_now_iso
from lean_constellation.services.foundation import FoundationService, ServiceIssue, ServiceResult, ToolResultView
from lean_constellation.services.tool_facade.context_resolver import ToolExecutionContext
from lean_constellation.services.tool_facade.tool_view import SubmitBehavior, ToolViewComponent


class SubmissionKind(StrEnum):
    TERMINAL = "terminal"
    DISPATCH_CHILD_FLOWS = "dispatch_child_flows"


class DispatchSubmissionPayload(StrictModel):
    flow_requests: list[dict[str, Any]]


class SubmissionView(StrictModel):
    tool_name: str
    submission_kind: SubmissionKind
    payload: dict[str, Any] = Field(default_factory=dict)
    result_view: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now_iso)


class SubmissionAckView(StrictModel):
    accepted: bool
    submission: SubmissionView
    message: str


class SubmitRejectedView(StrictModel):
    accepted: bool = False
    issues: list[ServiceIssue] = Field(default_factory=list)
    summary: str


@runtime_checkable
class RuntimeSubmissionGateway(Protocol):
    def accept_step_submission(self, ctx: ToolExecutionContext, submission: SubmissionView) -> Any:
        ...


class SubmitSubmissionComponent:
    """Build and record successful submit_* submissions."""

    STOP_MESSAGE = "Submission accepted. Stop making further state-changing tool calls and wait for the workflow to continue."

    def __init__(
        self,
        foundation: FoundationService | None = None,
        *,
        tool_view: ToolViewComponent | None = None,
        submission_gateway: RuntimeSubmissionGateway | None = None,
    ) -> None:
        self.foundation = foundation or FoundationService()
        self.tool_view = tool_view
        self.submission_gateway = submission_gateway

    def build_submission(
        self,
        tool_name: str,
        *,
        payload: dict[str, Any],
        result_view: dict[str, Any],
    ) -> ServiceResult[SubmissionView]:
        if not tool_name.startswith("submit_"):
            return self.foundation.fail(
                self.foundation.issue("invalid_submit_tool_name", "Successful submissions must come from submit_* tools.", object_ref=tool_name)
            )
        serializable = self._assert_json_serializable({"payload": payload, "result_view": result_view})
        if not serializable.ok:
            return self.foundation.fail(serializable.issues)
        kind = self._infer_submission_kind(tool_name, payload)
        submission = SubmissionView(
            tool_name=tool_name,
            submission_kind=kind,
            payload=payload,
            result_view=result_view,
        )
        return self.foundation.ok(submission)

    def record_successful_submission(
        self,
        ctx: ToolExecutionContext,
        *,
        submission: SubmissionView,
    ) -> ServiceResult[SubmissionAckView]:
        conflict = self.assert_no_conflicting_submission(ctx, submission_kind=submission.submission_kind.value)
        if not conflict.ok:
            return self.foundation.fail(conflict.issues)
        if self.submission_gateway is None:
            return self.foundation.fail(
                self.foundation.issue(
                    "submission_gateway_missing",
                    "Cannot record a successful submission because no ARK submission gateway is configured.",
                    suggested_action="Inject RuntimeMcpToolGateway.accept_step_submission before exposing submit tools.",
                )
            )
        try:
            accepted = self.submission_gateway.accept_step_submission(ctx, submission)
        except Exception as exc:  # noqa: BLE001 - runtime boundary.
            return self.foundation.fail(
                self.foundation.issue("submission_gateway_failed", f"Runtime submission gateway failed: {exc}")
            )
        if isinstance(accepted, ServiceResult):
            if not accepted.ok:
                return self.foundation.fail(accepted.issues)
        return self.foundation.ok(
            SubmissionAckView(
                accepted=True,
                submission=submission,
                message=self.STOP_MESSAGE,
            )
        )

    def reject_submit(self, ctx: ToolExecutionContext, *, issues: list[ServiceIssue]) -> ServiceResult[ToolResultView]:
        del ctx
        if not issues:
            issues = [self.foundation.issue("submit_rejected", "Submit was rejected.")]
        return self.foundation.fail(issues)

    def assert_no_conflicting_submission(
        self,
        ctx: ToolExecutionContext,
        *,
        submission_kind: str,
    ) -> ServiceResult[None]:
        if ctx.runtime.successful_submission_count <= 0:
            return self.foundation.ok(None)
        current = ctx.runtime.successful_submission_kind
        if current == submission_kind == SubmissionKind.DISPATCH_CHILD_FLOWS.value:
            return self.foundation.fail(
                self.foundation.issue(
                    "dispatch_submission_already_recorded",
                    "A dispatch submission is already recorded for this AgentStep.",
                    current=current,
                    expected="no existing submission",
                )
            )
        return self.foundation.fail(
            self.foundation.issue(
                "conflicting_submission",
                "A successful submission is already recorded for this AgentStep.",
                current=current,
                expected="no existing submission",
                suggested_action="Stop making tool calls and wait for the workflow to continue.",
            )
        )

    def _infer_submission_kind(self, tool_name: str, payload: dict[str, Any]) -> SubmissionKind:
        if self.tool_view is not None:
            spec = self.tool_view.get_tool(tool_name)
            if spec.ok and spec.value is not None:
                if spec.value.submit_behavior == SubmitBehavior.DISPATCH_CHILD_FLOWS:
                    return SubmissionKind.DISPATCH_CHILD_FLOWS
                return SubmissionKind.TERMINAL
        if "flow_requests" in payload or "flow_request" in payload:
            return SubmissionKind.DISPATCH_CHILD_FLOWS
        return SubmissionKind.TERMINAL

    def _assert_json_serializable(self, value: Any) -> ServiceResult[None]:
        try:
            json.dumps(value, sort_keys=True)
        except TypeError as exc:
            return self.foundation.fail(
                self.foundation.issue("submission_payload_not_json", f"Submission payload must be JSON serializable: {exc}")
            )
        return self.foundation.ok(None)
