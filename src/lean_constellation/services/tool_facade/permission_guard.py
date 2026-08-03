"""Permission checks for ToolFacade calls."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING

from lean_constellation.services.foundation import ServiceResult
from lean_constellation.services.tool_facade.context_resolver import ToolExecutionContext
from lean_constellation.services.tool_facade.tool_view import ToolCapability, ToolSpec, ToolViewComponent

if TYPE_CHECKING:
    from lean_constellation.services.runtime import LeanRuntimeServices


class PermissionIssueCode(StrEnum):
    TOOL_NOT_IN_VIEW = "tool_not_in_view"
    ROLE_NOT_ALLOWED = "role_not_allowed"
    ENDPOINT_VIEW_MISMATCH = "endpoint_view_mismatch"
    CONTRACT_MUTATION_REJECTED = "contract_mutation_rejected"
    DECL_STAGE_MUTATION_REJECTED = "decl_stage_mutation_rejected"
    REVIEW_ONLY_REJECTED = "review_only_rejected"
    ADMIN_REQUIRED = "admin_required"
    AGENT_CAPABILITY_CONTEXT_MISSING = "agent_capability_context_missing"
    AGENT_CAPABILITY_RESOLUTION_FAILED = "agent_capability_resolution_failed"
    AGENT_CAPABILITY_REQUIRED = "agent_capability_required"
    SUBMISSION_ALREADY_ACCEPTED = "submission_already_accepted"


class ContractMutationFieldGroup(StrEnum):
    CORE = "core"
    INTERFACES = "interfaces"
    REFS = "refs"
    DEPS = "deps"
    MATHLIB = "mathlib"
    EXPORTS = "exports"
    SUMMARY_COMMIT = "summary_commit"


class DeclStageMutationScope(StrEnum):
    CURRENT_STAGE = "current_stage"
    REVIEW_RESULT = "review_result"


class PermissionDecision:
    """Namespace for permission decision constants."""

    ALLOWED = "allowed"
    REJECTED = "rejected"


class PermissionGuardComponent:
    """Check view, role, and object-level mutation permissions."""

    def __init__(
        self,
        runtime: LeanRuntimeServices,
        *,
        tool_view: ToolViewComponent,
        agent_type_capabilities: Callable[[str], set[str]] | None = None,
    ) -> None:
        self.runtime = runtime
        self.tool_view = tool_view
        self._agent_type_capabilities = agent_type_capabilities or (lambda agent_type: set())

    def assert_tool_allowed(self, ctx: ToolExecutionContext, *, tool_name: str) -> ServiceResult[None]:
        endpoint = self.assert_endpoint_view_allowed(ctx)
        if not endpoint.ok:
            return self.runtime.foundation.fail(endpoint.issues)
        names = self.tool_view.tool_names_for_view(ctx.expected_view_key)
        if not names.ok or names.value is None:
            return self.runtime.foundation.fail(names.issues)
        if tool_name not in names.value:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.TOOL_NOT_IN_VIEW.value,
                    "Tool is not part of the current ToolView.",
                    object_ref=tool_name,
                    expected=ctx.expected_view_key,
                )
            )
        spec_result = self.tool_view.get_tool(tool_name)
        if not spec_result.ok or spec_result.value is None:
            return self.runtime.foundation.fail(spec_result.issues)
        spec = spec_result.value
        if spec.allowed_roles and ctx.actor.role not in spec.allowed_roles:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.ROLE_NOT_ALLOWED.value,
                    "Current actor role is not allowed to call this tool.",
                    object_ref=tool_name,
                    current=ctx.actor.role,
                    expected=",".join(sorted(spec.allowed_roles)),
                )
            )
        if spec.required_agent_capabilities and ctx.actor.role != "admin":
            if not ctx.actor.agent_type:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        PermissionIssueCode.AGENT_CAPABILITY_CONTEXT_MISSING.value,
                        "Tool requires an AgentType capability but the current actor has no AgentType.",
                        object_ref=tool_name,
                        expected=",".join(sorted(spec.required_agent_capabilities)),
                    )
                )
            try:
                available = self._agent_type_capabilities(ctx.actor.agent_type)
            except Exception as exc:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        PermissionIssueCode.AGENT_CAPABILITY_RESOLUTION_FAILED.value,
                        "Current AgentType capabilities could not be resolved.",
                        object_ref=ctx.actor.agent_type,
                        details={"error": str(exc)},
                    )
                )
            missing = spec.required_agent_capabilities - set(available)
            if missing:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        PermissionIssueCode.AGENT_CAPABILITY_REQUIRED.value,
                        "Current AgentType lacks a capability required by this tool.",
                        object_ref=tool_name,
                        current=",".join(sorted(available)),
                        expected=",".join(sorted(spec.required_agent_capabilities)),
                    )
                )
        if ctx.has_successful_submission and spec.capability in {
            ToolCapability.WRITE,
            ToolCapability.SUBMIT,
            ToolCapability.ADMIN,
        }:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.SUBMISSION_ALREADY_ACCEPTED.value,
                    "A successful submission is already recorded for this AgentStep; further state-changing calls are rejected.",
                    object_ref=tool_name,
                    suggested_action="Stop making tool calls and wait for the workflow to continue.",
                )
            )
        return self.runtime.foundation.ok(None)

    def assert_endpoint_view_allowed(self, ctx: ToolExecutionContext) -> ServiceResult[None]:
        check = self.tool_view.validate_step_expected_view(ctx)
        if not check.ok:
            return self.runtime.foundation.fail(check.issues)
        return self.runtime.foundation.ok(None)

    def assert_contract_mutation_allowed(
        self,
        ctx: ToolExecutionContext,
        *,
        field_group: ContractMutationFieldGroup | str,
        item_added_by: str | None = None,
    ) -> ServiceResult[None]:
        field_group = ContractMutationFieldGroup(field_group)
        if ctx.actor.role == "reviewer":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.CONTRACT_MUTATION_REJECTED.value,
                    "Reviewer actors cannot mutate NodeContract fields.",
                    field=field_group.value,
                )
            )
        if ctx.actor.role in {"admin", "coordinator"}:
            return self.runtime.foundation.ok(None)
        if field_group in {
            ContractMutationFieldGroup.REFS,
            ContractMutationFieldGroup.DEPS,
            ContractMutationFieldGroup.MATHLIB,
        } and ctx.actor.role in {"worker", "plan"}:
            if item_added_by is not None and item_added_by != ctx.actor.added_by:
                return self.runtime.foundation.fail(
                    self.runtime.foundation.issue(
                        PermissionIssueCode.CONTRACT_MUTATION_REJECTED.value,
                        "Current actor cannot delete or modify contract items added by another actor.",
                        field=field_group.value,
                        current=item_added_by,
                        expected=ctx.actor.added_by,
                    )
                )
            return self.runtime.foundation.ok(None)
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(
                PermissionIssueCode.CONTRACT_MUTATION_REJECTED.value,
                "Current actor cannot mutate this contract field group.",
                field=field_group.value,
                current=ctx.actor.role,
                expected="coordinator/admin or worker-owned refs/deps/mathlib",
            )
        )

    def assert_decl_stage_mutation_allowed(
        self,
        ctx: ToolExecutionContext,
        *,
        stage: str,
        decl_name: str,
    ) -> ServiceResult[None]:
        if ctx.actor.role in {"reviewer", "admin"}:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.DECL_STAGE_MUTATION_REJECTED.value,
                    "Reviewer/admin actors cannot perform worker decl stage mutations.",
                    object_ref=decl_name,
                    current=ctx.actor.role,
                    expected="worker",
                )
            )
        if ctx.decl_stage is None:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue("decl_stage_context_missing", "Current context has no decl stage.")
            )
        if ctx.decl_stage.stage != stage:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.DECL_STAGE_MUTATION_REJECTED.value,
                    "Tool stage does not match current decl stage.",
                    object_ref=decl_name,
                    current=stage,
                    expected=ctx.decl_stage.stage,
                )
            )
        if ctx.decl_stage.batch_decls and decl_name not in ctx.decl_stage.batch_decls:
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.DECL_STAGE_MUTATION_REJECTED.value,
                    "Decl is not in the current stage batch.",
                    object_ref=decl_name,
                    expected=",".join(ctx.decl_stage.batch_decls),
                )
            )
        return self.runtime.foundation.ok(None)

    def assert_review_only(self, ctx: ToolExecutionContext) -> ServiceResult[None]:
        if ctx.actor.role != "reviewer":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.REVIEW_ONLY_REJECTED.value,
                    "This tool is only available to reviewer actors.",
                    current=ctx.actor.role,
                    expected="reviewer",
                )
            )
        return self.runtime.foundation.ok(None)

    def assert_admin(self, ctx: ToolExecutionContext) -> ServiceResult[None]:
        if ctx.actor.role != "admin":
            return self.runtime.foundation.fail(
                self.runtime.foundation.issue(
                    PermissionIssueCode.ADMIN_REQUIRED.value,
                    "This tool requires an admin actor.",
                    current=ctx.actor.role,
                    expected="admin",
                )
            )
        return self.runtime.foundation.ok(None)

    def assert_tool_capability_allowed(self, ctx: ToolExecutionContext, spec: ToolSpec) -> ServiceResult[None]:
        if spec.capability == ToolCapability.ADMIN:
            return self.assert_admin(ctx)
        return self.assert_tool_allowed(ctx, tool_name=spec.name)
