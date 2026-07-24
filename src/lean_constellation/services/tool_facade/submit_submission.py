"""Submit-tool bridge to the runtime AgentStep submission gateway."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from agent_runtime_kit.flow.models import BaseSubmission, ChildFlowDispatchSubmission
from agent_runtime_kit.runtime.mcp_tool_gateway import RuntimeToolContextError, RuntimeToolIdentity
from pydantic import Field, SerializeAsAny

from lean_constellation.domain.common import StrictModel
from lean_constellation.services.foundation import ServiceIssue, ServiceResult, ToolResultView
from lean_constellation.services.tool_facade.context_resolver import ToolExecutionContext
from lean_constellation.services.tool_facade.tool_view import ToolViewComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class PreparedSubmissionView(StrictModel):
    """Successful submit handler output consumed by MCPWrapperComponent."""

    submission: SerializeAsAny[BaseSubmission]
    summary: str
    agent_view: dict[str, Any] = Field(default_factory=dict)


class SubmissionAckView(StrictModel):
    accepted: bool
    submission_id: str
    submission_type: str
    message: str
    dispatch_request_count: int = 0


class SubmitRejectedView(StrictModel):
    accepted: bool = False
    issues: list[ServiceIssue] = Field(default_factory=list)
    summary: str


@runtime_checkable
class RuntimeSubmissionGateway(Protocol):
    def accept_step_submission(self, ctx: ToolExecutionContext, submission: BaseSubmission) -> Any:
        ...


class ArkRuntimeSubmissionGatewayAdapter:
    """Adapter from Lean ToolExecutionContext to ARK RuntimeMcpToolGateway."""

    def __init__(self, gateway: Any) -> None:
        self.gateway = gateway

    def accept_step_submission(self, ctx: ToolExecutionContext, submission: BaseSubmission) -> Any:
        runtime_context = ctx.runtime.extra.get("ark_runtime_context")
        if runtime_context is None:
            resolver = getattr(self.gateway, "resolver", None)
            resolve = getattr(resolver, "resolve_from_identity", None)
            if not callable(resolve):
                raise RuntimeError("ARK gateway cannot resolve RuntimeToolContext from Lean tool context.")
            if not ctx.runtime.step_id or not ctx.runtime.flow_id or not ctx.runtime.agent_id:
                raise RuntimeError("Lean tool context is missing ARK step_id, flow_id, or agent_id.")
            runtime_context = resolve(
                RuntimeToolIdentity(
                    step_id=ctx.runtime.step_id,
                    flow_id=ctx.runtime.flow_id,
                    agent_id=ctx.runtime.agent_id,
                ),
                require_running_step=True,
                allowed_submit_tool_name=submission.tool_name,
            )
        return self.gateway.accept_step_submission(runtime_context, submission)


class SubmitSubmissionComponent:
    """Build and record successful submit_* submissions."""

    STOP_MESSAGE = "Submission accepted. Stop making further state-changing tool calls and wait for the workflow to continue."

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        tool_view: ToolViewComponent | None = None,
        submission_gateway: RuntimeSubmissionGateway | None = None,
    ) -> None:
        self.runtime = runtime
        self.tool_view = tool_view
        self.submission_gateway = submission_gateway

    def prepare_submission(
        self,
        ctx: ToolExecutionContext,
        *,
        prepared: Any,
        tool_name: str,
    ) -> ServiceResult[PreparedSubmissionView]:
        del ctx
        if isinstance(prepared, PreparedSubmissionView):
            return self._validate_prepared_tool(prepared, tool_name=tool_name)
        if isinstance(prepared, BaseSubmission):
            return self._validate_prepared_tool(
                PreparedSubmissionView(
                    submission=prepared,
                    summary=prepared.summary or f"{tool_name} accepted.",
                ),
                tool_name=tool_name,
            )
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "prepared_submission_invalid",
                "Submit handlers must return PreparedSubmissionView or an ARK BaseSubmission subclass.",
                object_ref=tool_name,
                details={"returned_type": type(prepared).__name__},
            )
        )

    def record_successful_submission(
        self,
        ctx: ToolExecutionContext,
        *,
        submission: BaseSubmission,
    ) -> ServiceResult[SubmissionAckView]:
        conflict = self.assert_no_conflicting_submission(ctx, submission=submission)
        if not conflict.ok:
            return self.runtime.foundation.fail(conflict.issues)
        if submission.submitted_by_agent_id is None and ctx.runtime.agent_id:
            submission = submission.model_copy(update={"submitted_by_agent_id": ctx.runtime.agent_id})
        serializable = self.assert_json_serializable_submission(submission)
        if not serializable.ok:
            return self.runtime.foundation.fail(serializable.issues)
        if self.submission_gateway is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "submission_gateway_missing",
                    "Cannot record a successful submission because no ARK submission gateway is configured.",
                    suggested_action="Inject RuntimeMcpToolGateway.accept_step_submission before exposing submit tools.",
                )
            )
        try:
            accepted = self.submission_gateway.accept_step_submission(ctx, submission)
        except RuntimeToolContextError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(exc.code, exc.message, details=dict(exc.details))
            )
        except Exception as exc:  # noqa: BLE001 - runtime boundary.
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("submission_gateway_failed", f"Runtime submission gateway failed: {exc}")
            )
        if isinstance(accepted, ServiceResult) and not accepted.ok:
            return self.runtime.foundation.fail(accepted.issues)
        return self.runtime.foundation.ok(
            SubmissionAckView(
                accepted=True,
                submission_id=submission.submission_id,
                submission_type=submission.submission_type,
                message=self.STOP_MESSAGE,
                dispatch_request_count=len(submission.requests) if isinstance(submission, ChildFlowDispatchSubmission) else 0,
            )
        )

    def reject_submit(self, ctx: ToolExecutionContext, *, issues: list[ServiceIssue]) -> ServiceResult[ToolResultView]:
        del ctx
        if not issues:
            issues = [self.runtime.foundation.issue("submit_rejected", "Submit was rejected.")]
        return self.runtime.foundation.fail(issues)

    def assert_no_conflicting_submission(
        self,
        ctx: ToolExecutionContext,
        *,
        submission: BaseSubmission,
    ) -> ServiceResult[None]:
        if ctx.runtime.successful_submission_count <= 0:
            return self.runtime.foundation.ok(None)
        current = ctx.runtime.successful_submission_kind
        if current == submission.submission_type:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "submission_already_recorded",
                    "A successful submission of this type is already recorded for this AgentStep.",
                    current=current,
                    expected="no existing submission",
                )
            )
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                "conflicting_submission",
                "A successful submission is already recorded for this AgentStep.",
                current=current,
                expected="no existing submission",
                suggested_action="Stop making tool calls and wait for the workflow to continue.",
            )
        )

    def assert_json_serializable_submission(self, submission: BaseSubmission) -> ServiceResult[None]:
        return self._assert_json_serializable(submission.model_dump(mode="json"))

    def _validate_prepared_tool(
        self,
        prepared: PreparedSubmissionView,
        *,
        tool_name: str,
    ) -> ServiceResult[PreparedSubmissionView]:
        if not tool_name.startswith("submit_"):
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("invalid_submit_tool_name", "Successful submissions must come from submit_* tools.", object_ref=tool_name)
            )
        if prepared.submission.tool_name != tool_name:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    "submission_tool_mismatch",
                    "Prepared submission tool_name does not match the invoked submit tool.",
                    current=prepared.submission.tool_name,
                    expected=tool_name,
                )
            )
        return self.runtime.foundation.ok(prepared)

    def _assert_json_serializable(self, value: Any) -> ServiceResult[None]:
        try:
            json.dumps(value, sort_keys=True)
        except TypeError as exc:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("submission_payload_not_json", f"Submission payload must be JSON serializable: {exc}")
            )
        return self.runtime.foundation.ok(None)
