"""Resource curation Flow type definitions."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from agent_runtime_kit.flow.contexts import FlowBuildContext, FlowContext, FlowStepContext
from agent_runtime_kit.flow.models import BaseFlowError, BaseFlowInput, BaseFlowResult, BaseFlowState, FlowPosition
from agent_runtime_kit.flow.standard_steps import AgentStepIncompleteResult, AgentStepState
from pydantic import BaseModel, ConfigDict, Field

from lean_constellation.flows.common.business_flows import LeanBusinessFlow, LeanFlowParams
from lean_constellation.flows.common.rendering import LeanRenderableFlowInput, LeanRenderableFlowResult
from lean_constellation.flows.resource_request.steps import (
    ExternalRepoRequiredResultView,
    LocalResourceCreatedResultView,
    ResourceCurationPreflightStep,
    ResourceCurationPreflightStepResult,
    ResourceCuratorStepResult,
    ResourceDuplicateResultView,
    ResourceRejectedResultView,
    new_resource_request_step_id,
)
from lean_constellation.flows.resource_request.submissions import LocalResourceCreatedSubmission


ResourceTargetKind = Literal["web", "arxiv", "local_file", "local_dir"]
ResourceCallerKind = Literal["coordinator", "content_plan", "resource_recon", "other"]


class ResourceTargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ResourceTargetKind
    target: str
    arxiv_version: str | None = None


class ResourceCallerContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caller_kind: ResourceCallerKind = "other"
    node_path: str | None = None
    purpose_hint: str | None = None


class ResourceCurationParams(LeanFlowParams):
    repo_key: str | None = None
    repo_root: str | None = None
    target_kind: ResourceTargetKind
    target: str
    arxiv_version: str | None = None
    requested_by: str | None = None
    context_summary: str | None = None
    node_path: str | None = None


class ResourceCurationInput(LeanRenderableFlowInput):
    input_type: Literal["resource_curation"] = "resource_curation"
    repo_key: str | None = None
    repo_root: str | None = None
    target: ResourceTargetInput
    caller_context: ResourceCallerContextInput = Field(default_factory=ResourceCallerContextInput)

    def agent_title(self) -> str:
        return f"Curate resource {self.target.target}"

    def agent_fields(self) -> dict[str, object]:
        return {
            "repo_key": self.repo_key,
            "target_kind": self.target.kind,
            "caller_kind": self.caller_context.caller_kind,
            "node_path": self.caller_context.node_path,
            "purpose_hint": self.caller_context.purpose_hint,
        }


class ResourceCurationState(BaseFlowState):
    state_type: Literal["resource_curation"] = "resource_curation"
    position: FlowPosition = Field(default_factory=lambda: FlowPosition(phase="preflight"))
    normalized_target_summary: str | None = None
    canonical_locator: str | None = None
    preflight_outcome: Literal["pending", "continue_to_curator", "rejected"] = "pending"
    duplicate_kind: Literal["resource", "source", "requirement"] | None = None
    resource_duplicate_hint: ResourceDuplicateResultView | None = None
    source_duplicate_hint: ResourceDuplicateResultView | None = None
    active_resource_draft_key: str | None = None
    draft_root: str | None = None


class ResourceCurationResult(LeanRenderableFlowResult):
    result_type: Literal["resource_curation"] = "resource_curation"
    outcome: Literal["duplicate", "local_resource_created", "external_repo_required", "rejected"]
    repo_key: str | None = None
    target_summary: str | None = None
    resource_key: str | None = None
    existing_resource_key: str | None = None
    existing_source_path: str | None = None
    reason: str | None = None
    duplicate: ResourceDuplicateResultView | None = None
    local_resource: LocalResourceCreatedResultView | None = None
    external_repo: ExternalRepoRequiredResultView | None = None
    rejected: ResourceRejectedResultView | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "target_summary": self.target_summary,
            "resource_key": self.resource_key,
            "existing_resource_key": self.existing_resource_key,
            "existing_source_path": self.existing_source_path,
            "reason": self.reason,
            "duplicate": self.duplicate.ref_summary if self.duplicate else None,
            "local_resource": self.local_resource.resource_ref_summary if self.local_resource else None,
            "external_repo": self.external_repo.suggested_repo_name if self.external_repo else None,
            "next_step_boundary": "This flow did not modify node contracts or create repository requirements.",
        }


class ResourceCurationFlow(LeanBusinessFlow):
    flow_type: ClassVar[str] = "resource_curation"
    Params: ClassVar[type[LeanFlowParams]] = ResourceCurationParams
    Input: ClassVar[type[BaseFlowInput]] = ResourceCurationInput
    State: ClassVar[type[BaseFlowState]] = ResourceCurationState
    Result: ClassVar[type[BaseFlowResult]] = ResourceCurationResult
    Results: ClassVar[dict[str, type[BaseFlowResult]]] = {"resource_curation": ResourceCurationResult}

    @classmethod
    def build_from_request(cls, ctx: FlowBuildContext) -> "ResourceCurationFlow":
        params = ResourceCurationParams.model_validate(ctx.params)
        caller_kind = _caller_kind(params.requested_by)
        return cls._build(
            ctx,
            input_model=ResourceCurationInput(
                summary=params.context_summary,
                repo_key=params.repo_key,
                repo_root=params.repo_root,
                target=ResourceTargetInput(
                    kind=params.target_kind,
                    target=params.target,
                    arxiv_version=params.arxiv_version,
                ),
                caller_context=ResourceCallerContextInput(
                    caller_kind=caller_kind,
                    node_path=params.node_path,
                    purpose_hint=params.context_summary,
                ),
            ),
            state=ResourceCurationState(),
        )

    def create_next_step(self, ctx: FlowContext) -> str | None:
        state = _require_resource_curation_state(self.state)
        input_model = _require_resource_curation_input(self.input)
        if state.position.phase == "preflight":
            return ctx.create_step(
                ResourceCurationPreflightStep(
                    step_id=new_resource_request_step_id("resource_curation_preflight"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                )
            )
        if state.position.phase == "curator_agent":
            from lean_constellation.flows.common.agent_steps import ResourceCuratorAgentStep

            return ctx.create_step(
                ResourceCuratorAgentStep(
                    step_id=new_resource_request_step_id("resource_curator"),
                    flow_id=self.flow_id,
                    scope_id=self.scope_id,
                    state=AgentStepState(
                        agent_role="resource_curator",
                        agent_type="ResourceCuratorAgent",
                        home_id="ResourceCuratorAgent",
                        create_agent_if_missing=True,
                        bind_created_agent_to="step",
                        variables={
                            "repo_key": input_model.repo_key,
                            "resource_draft_id": state.active_resource_draft_key,
                        },
                        prompt_override=_resource_curator_prompt(input_model, state),
                        env_overrides={
                            "LEAN_CONSTELLATION_AGENT_TYPE": "ResourceCuratorAgent",
                            "LEAN_CONSTELLATION_APPLICATION_TOOL_VIEW": "resource_curator",
                            "LEAN_CONSTELLATION_SUBMIT_TOOL_VIEW": "resource_curator_submit",
                            **({"LEAN_CONSTELLATION_RESOURCE_DRAFT_ID": state.active_resource_draft_key} if state.active_resource_draft_key else {}),
                        },
                        workdir_override=state.draft_root or _resource_draft_workdir(input_model),
                        max_auto_continue_turns=1,
                    ),
                )
            )
        return None

    def on_step_terminal(self, ctx: FlowStepContext) -> None:
        state = _require_resource_curation_state(self.state)
        input_model = _require_resource_curation_input(self.input)
        if ctx.step.error is not None:
            self.error = BaseFlowError(
                error_type="resource_curation_step_failed",
                message=ctx.step.error.message,
                details={"step_type": ctx.step.step_type, **ctx.step.error.details},
            )
            super().on_step_terminal(ctx)
            return

        result = ctx.step.result
        if isinstance(result, ResourceCurationPreflightStepResult):
            self._consume_preflight_result(state, input_model, result)
        elif ctx.step.step_type == "resource_curator_agent_step":
            self._consume_curator_result(ctx, state, input_model, result, ctx.step.submission)
        super().on_step_terminal(ctx)

    def _consume_preflight_result(
        self,
        state: ResourceCurationState,
        input_model: ResourceCurationInput,
        result: ResourceCurationPreflightStepResult,
    ) -> None:
        state.normalized_target_summary = result.normalized_target_summary
        state.canonical_locator = result.canonical_locator
        state.preflight_outcome = result.outcome
        state.active_resource_draft_key = result.draft_id
        state.draft_root = result.draft_root
        state.resource_duplicate_hint = result.resource_duplicate_hint
        state.source_duplicate_hint = result.source_duplicate_hint
        if result.resource_duplicate_hint is not None:
            state.duplicate_kind = result.resource_duplicate_hint.existing_kind
        elif result.source_duplicate_hint is not None:
            state.duplicate_kind = result.source_duplicate_hint.existing_kind
        if result.outcome == "continue_to_curator":
            state.position = FlowPosition(phase="curator_agent")
            return
        state.position = FlowPosition(phase="completed")
        rejected = result.rejected or ResourceRejectedResultView(reason=result.summary or "Resource target rejected.")
        self.result = _result_from_rejected(input_model, rejected, summary=result.summary)

    def _consume_curator_result(
        self,
        ctx: FlowStepContext,
        state: ResourceCurationState,
        input_model: ResourceCurationInput,
        result: object | None,
        submission: object | None,
    ) -> None:
        state.position = FlowPosition(phase="completed")
        if isinstance(result, AgentStepIncompleteResult) or result is None:
            rejected = ResourceRejectedResultView(
                reason="Resource curator did not reach a valid submission after retry limit.",
            )
            self.result = _result_from_rejected(input_model, rejected, summary=rejected.reason)
            return
        if not isinstance(result, ResourceCuratorStepResult):
            rejected = ResourceRejectedResultView(
                reason=f"Resource curator returned unsupported result: {getattr(result, 'result_type', None)}",
            )
            self.result = _result_from_rejected(input_model, rejected, summary=rejected.reason)
            return
        if result.outcome == "duplicate" and result.duplicate is not None:
            self.result = _result_from_duplicate(input_model, result.duplicate, summary=result.summary)
            _abandon_active_draft(ctx, input_model, state, reason=result.summary or result.duplicate.duplicate_reason)
            return
        if (
            result.outcome == "local_resource_created"
            and result.local_resource is not None
            and isinstance(submission, LocalResourceCreatedSubmission)
        ):
            material = getattr(ctx.app, "material", None)
            if material is None:
                rejected = ResourceRejectedResultView(reason="Material service is not registered; cannot finalize local resource.")
                self.result = _result_from_rejected(input_model, rejected, summary=rejected.reason)
                return
            normalized_target = material.resource_curation.prepare_resource_target(
                target_kind=submission.target_kind,
                target=submission.target,
                arxiv_version=submission.arxiv_version,
            )
            if not normalized_target.ok or normalized_target.value is None:
                rejected = ResourceRejectedResultView(
                    reason=normalized_target.issues[0].message if normalized_target.issues else "Resource target normalization failed."
                )
                self.result = _result_from_rejected(input_model, rejected, summary=rejected.reason)
                return
            finalized = material.resource_curation.submit_local_resource_created(
                _resource_repo_root(input_model),
                target=normalized_target.value,
                draft_id=submission.draft_id,
                summary=submission.summary or result.summary or "Curated local resource.",
            )
            if not finalized.ok or finalized.value is None or not finalized.value.resource_key:
                rejected = ResourceRejectedResultView(reason=finalized.issues[0].message if finalized.issues else "Local resource finalize failed.")
                self.result = _result_from_rejected(input_model, rejected, summary=rejected.reason)
                return
            local_resource = LocalResourceCreatedResultView(
                resource_key=finalized.value.resource_key,
                resource_ref_summary=f"Resource {finalized.value.resource_key}",
                locator_summary=f"{submission.target_kind}:{submission.target}",
            )
            self.result = ResourceCurationResult(
                outcome="local_resource_created",
                repo_key=input_model.repo_key,
                target_summary=_target_summary(input_model),
                resource_key=finalized.value.resource_key,
                reason=finalized.value.summary,
                local_resource=local_resource,
                summary=finalized.value.summary,
            )
            return
        if result.outcome == "external_repo_required" and result.external_repo is not None:
            self.result = ResourceCurationResult(
                outcome="external_repo_required",
                repo_key=input_model.repo_key,
                target_summary=_target_summary(input_model),
                reason=result.external_repo.reason,
                external_repo=result.external_repo,
                summary=result.summary or result.external_repo.reason,
            )
            _abandon_active_draft(ctx, input_model, state, reason=result.summary or result.external_repo.reason)
            return
        rejected = result.rejected or ResourceRejectedResultView(
            reason=result.incomplete_reason or result.summary or "Resource target rejected.",
        )
        self.result = _result_from_rejected(input_model, rejected, summary=result.summary or rejected.reason)
        _abandon_active_draft(ctx, input_model, state, reason=result.summary or rejected.reason)


def _caller_kind(requested_by: str | None) -> ResourceCallerKind:
    if requested_by in {"coordinator", "content_plan", "resource_recon"}:
        return requested_by
    return "other"


def _require_resource_curation_state(state: BaseFlowState) -> ResourceCurationState:
    if not isinstance(state, ResourceCurationState):
        raise TypeError("resource_curation flow has invalid state")
    return state


def _require_resource_curation_input(input_model: BaseFlowInput | None) -> ResourceCurationInput:
    if not isinstance(input_model, ResourceCurationInput):
        raise TypeError("resource_curation flow has invalid input")
    return input_model


def _target_summary(input_model: ResourceCurationInput) -> str:
    return f"{input_model.target.kind}:{input_model.target.target}"


def _resource_repo_root(input_model: ResourceCurationInput) -> Path:
    if not input_model.repo_root:
        raise TypeError("resource curation flow requires repo_root")
    return Path(input_model.repo_root)


def _resource_draft_workdir(input_model: ResourceCurationInput) -> str | None:
    if not input_model.repo_root:
        return None
    return str(Path(input_model.repo_root) / ".lean_constellation" / "resources" / ".drafts")


def _resource_curator_prompt(input_model: ResourceCurationInput, state: ResourceCurationState) -> str:
    parts = [
        f"Curate the explicit resource target {input_model.target.kind}: {input_model.target.target}.",
        f"Caller kind: {input_model.caller_context.caller_kind}.",
        "Current working directory: the active resource draft.",
        "Allowed write boundary: this directory and its descendants.",
        "Logical files: README.md, original/, normalized/.",
    ]
    if input_model.caller_context.node_path:
        parts.append(f"Caller node: {input_model.caller_context.node_path}.")
    if input_model.caller_context.purpose_hint:
        parts.append(f"Purpose: {input_model.caller_context.purpose_hint}.")
    if state.normalized_target_summary:
        parts.append(f"Preflight normalized target: {state.normalized_target_summary}.")
    if state.canonical_locator:
        parts.append(f"Canonical locator: {state.canonical_locator}.")
    if state.active_resource_draft_key:
        parts.append(f"Current resource draft id: {state.active_resource_draft_key}.")
        parts.append("Work in the current draft directory.")
    if state.resource_duplicate_hint:
        parts.append(f"Preflight resource duplicate hint: {state.resource_duplicate_hint.ref_summary}. Re-check before submitting.")
    if state.source_duplicate_hint:
        parts.append(f"Preflight source duplicate hint: {state.source_duplicate_hint.ref_summary}. Re-check before submitting.")
    parts.append(
        "Submit exactly one outcome: duplicate, local_resource_created, external_repo_required, or rejected. "
        "Do not modify node contracts or repository requirements."
    )
    return "\n".join(parts)


def _abandon_active_draft(ctx: FlowStepContext, input_model: ResourceCurationInput, state: ResourceCurationState, *, reason: str) -> None:
    if not state.active_resource_draft_key:
        return
    if not input_model.repo_root:
        return
    material = getattr(ctx.app, "material", None)
    if material is None:
        return
    abandoned = material.abandon_resource_draft(Path(input_model.repo_root), draft_id=state.active_resource_draft_key, reason=reason)
    if abandoned.ok:
        state.active_resource_draft_key = None


def _result_from_duplicate(
    input_model: ResourceCurationInput,
    duplicate: ResourceDuplicateResultView,
    *,
    summary: str | None,
) -> ResourceCurationResult:
    return ResourceCurationResult(
        outcome="duplicate",
        repo_key=input_model.repo_key,
        target_summary=_target_summary(input_model),
        existing_resource_key=duplicate.existing_resource_key,
        existing_source_path=duplicate.existing_source_path,
        reason=duplicate.duplicate_reason,
        duplicate=duplicate,
        summary=summary or duplicate.duplicate_reason,
    )


def _result_from_rejected(
    input_model: ResourceCurationInput,
    rejected: ResourceRejectedResultView,
    *,
    summary: str | None,
) -> ResourceCurationResult:
    return ResourceCurationResult(
        outcome="rejected",
        repo_key=input_model.repo_key,
        target_summary=_target_summary(input_model),
        reason=rejected.reason,
        rejected=rejected,
        summary=summary or rejected.reason,
    )


RESOURCE_REQUEST_FLOW_TYPES: tuple[type[LeanBusinessFlow], ...] = (ResourceCurationFlow,)
