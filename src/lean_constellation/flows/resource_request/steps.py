"""Resource request deterministic steps and business AgentStep results."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import BaseStep, BaseStepResult, BaseStepState, StepTerminalReceipt
from pydantic import Field

from lean_constellation.domain.common import StrictModel
from lean_constellation.flows.common.rendering import LeanRenderableStepResult


class ResourceDuplicateResultView(StrictModel):
    existing_kind: Literal["resource", "source", "requirement"]
    ref_summary: str
    duplicate_reason: str
    existing_resource_key: str | None = None
    existing_source_path: str | None = None
    preview: str | None = None


class LocalResourceCreatedResultView(StrictModel):
    resource_key: str
    resource_ref_summary: str
    locator_summary: str
    canonical_entry: str | None = None
    classification_reason: str
    resource_role: str
    consumer_formalization_scope: str
    preview: str | None = None


class ExternalRepoRequiredResultView(StrictModel):
    reason: str
    source_description: str
    source_locator: str
    classification_reason: str
    relation_to_current_repo_or_node: str
    consumer_need: str
    provider_scope: str
    suggested_repo_name: str | None = None
    required_interfaces_hint: str | None = None
    existing_lean_repo_signal: str | None = None


class ResourceRejectedResultView(StrictModel):
    reason: str
    details: list[str] = Field(default_factory=list)


class ResourceCurationPreflightStepResult(LeanRenderableStepResult):
    result_type: Literal["resource_curation_preflight"] = "resource_curation_preflight"
    outcome: Literal["continue_to_curator", "rejected"]
    repo_key: str | None = None
    target_summary: str | None = None
    normalized_target_summary: str | None = None
    canonical_locator: str | None = None
    resource_duplicate_hint: ResourceDuplicateResultView | None = None
    source_duplicate_hint: ResourceDuplicateResultView | None = None
    rejected: ResourceRejectedResultView | None = None
    draft_id: str | None = None
    draft_root: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "target_summary": self.target_summary,
            "normalized_target_summary": self.normalized_target_summary,
            "canonical_locator": self.canonical_locator,
            "resource_duplicate_hint": self.resource_duplicate_hint.ref_summary if self.resource_duplicate_hint else None,
            "source_duplicate_hint": self.source_duplicate_hint.ref_summary if self.source_duplicate_hint else None,
            "draft_id": self.draft_id,
            "rejected_reason": self.rejected.reason if self.rejected else None,
        }


class ResourceCuratorStepResult(LeanRenderableStepResult):
    result_type: Literal["resource_curator"] = "resource_curator"
    outcome: Literal["duplicate", "local_resource_created", "external_repo_required", "rejected", "incomplete"]
    repo_key: str | None = None
    target_summary: str | None = None
    duplicate: ResourceDuplicateResultView | None = None
    local_resource: LocalResourceCreatedResultView | None = None
    external_repo: ExternalRepoRequiredResultView | None = None
    rejected: ResourceRejectedResultView | None = None
    incomplete_reason: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "repo_key": self.repo_key,
            "target_summary": self.target_summary,
            "duplicate": self.duplicate.ref_summary if self.duplicate else None,
            "local_resource": self.local_resource.resource_ref_summary if self.local_resource else None,
            "external_repo": self.external_repo.suggested_repo_name if self.external_repo else None,
            "rejected_reason": self.rejected.reason if self.rejected else None,
            "incomplete_reason": self.incomplete_reason,
        }


class ResourceCurationPreflightStep(BaseStep):
    step_type: ClassVar[str] = "resource_curation_preflight_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = ResourceCurationPreflightStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "resource_curation_preflight": ResourceCurationPreflightStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _load_resource_curation_flow(ctx)
        input_model = _require_resource_curation_input(flow.input)
        repo_root = _resource_repo_root(input_model)
        if repo_root is None:
            return ctx.complete_step(
                ResourceCurationPreflightStepResult(
                    outcome="rejected",
                    repo_key=input_model.repo_key,
                    target_summary=input_model.target.target,
                    rejected=ResourceRejectedResultView(
                        reason="Resource curation requires repo_root injected by the runtime context.",
                    ),
                    summary="Resource curation runtime context is incomplete.",
                )
            )

        material = _material(ctx)
        normalized_target = material.resource_curation.prepare_resource_target(
            target_kind=input_model.target.kind,
            target=input_model.target.target,
            arxiv_version=input_model.target.arxiv_version,
        )
        if not normalized_target.ok or normalized_target.value is None:
            reason = _issue_summary(normalized_target.issues) or "Resource target normalization failed."
            return ctx.complete_step(
                ResourceCurationPreflightStepResult(
                    outcome="rejected",
                    repo_key=input_model.repo_key,
                    target_summary=input_model.target.target,
                    rejected=ResourceRejectedResultView(reason=reason),
                    summary=reason,
                )
            )

        normalized = normalized_target.value
        duplicate = material.find_duplicate_resource(repo_root, target=normalized)
        if not duplicate.ok or duplicate.value is None:
            reason = _issue_summary(duplicate.issues) or "Resource duplicate preflight failed."
            return ctx.complete_step(
                ResourceCurationPreflightStepResult(
                    outcome="rejected",
                    repo_key=input_model.repo_key,
                    target_summary=input_model.target.target,
                    normalized_target_summary=normalized.summary,
                    canonical_locator=normalized.canonical_locator,
                    rejected=ResourceRejectedResultView(reason=reason),
                    summary=reason,
                )
            )
        resource_duplicate_hint = None
        if duplicate.value.duplicate:
            resource_duplicate_hint = ResourceDuplicateResultView(
                existing_kind="resource",
                ref_summary=f"Resource {duplicate.value.resource_key}",
                duplicate_reason=duplicate.value.summary,
                existing_resource_key=duplicate.value.resource_key,
            )

        source_duplicate_hint = None
        source_duplicate = material.source_corpus.check_target_in_source_corpus(
            repo_root,
            canonical_locator=normalized.canonical_locator,
        )
        if source_duplicate.ok and source_duplicate.value is not None and source_duplicate.value.duplicate:
            existing_path = source_duplicate.value.matching_paths[0] if source_duplicate.value.matching_paths else None
            source_duplicate_hint = ResourceDuplicateResultView(
                existing_kind="source",
                ref_summary=f"Source corpus material {existing_path or normalized.canonical_locator}",
                duplicate_reason=source_duplicate.value.summary,
                existing_source_path=existing_path,
            )

        draft = material.allocate_resource_draft(
            repo_root,
            target=normalized,
            resource_kind=normalized.kind,
            title_hint=normalized.target,
            requested_use=input_model.caller_context.requested_use,
            consumer_need=input_model.caller_context.consumer_need,
            caller_kind=input_model.caller_context.caller_kind,
            purpose_hint=input_model.caller_context.purpose_hint,
            allow_duplicate=True,
        )
        if not draft.ok or draft.value is None:
            reason = _issue_summary(draft.issues) or "Resource draft could not be prepared."
            return ctx.complete_step(
                ResourceCurationPreflightStepResult(
                    outcome="rejected",
                    repo_key=input_model.repo_key,
                    target_summary=input_model.target.target,
                    normalized_target_summary=normalized.summary,
                    canonical_locator=normalized.canonical_locator,
                    rejected=ResourceRejectedResultView(reason=reason),
                    summary=reason,
                )
            )
        return ctx.complete_step(
            ResourceCurationPreflightStepResult(
                outcome="continue_to_curator",
                repo_key=input_model.repo_key,
                target_summary=input_model.target.target,
                normalized_target_summary=normalized.summary,
                canonical_locator=normalized.canonical_locator,
                resource_duplicate_hint=resource_duplicate_hint,
                source_duplicate_hint=source_duplicate_hint,
                draft_id=draft.value.draft.draft_id,
                draft_root=draft.value.draft_root,
                summary=(
                    "Resource target passed deterministic preflight with duplicate hints."
                    if resource_duplicate_hint or source_duplicate_hint
                    else "Resource target passed deterministic preflight."
                ),
            )
        )


def new_resource_request_step_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _load_resource_curation_flow(ctx: StepRunContext):
    flow = ctx.ark.flow_service.get_flow(ctx.flow_id)
    if flow.flow_type != "resource_curation":
        raise ValueError(f"expected resource_curation flow, got {flow.flow_type}")
    return flow


def _require_resource_curation_input(input_model):
    from lean_constellation.flows.resource_request.flows import ResourceCurationInput

    if not isinstance(input_model, ResourceCurationInput):
        raise ValueError("resource_curation flow has invalid input")
    return input_model


def _resource_repo_root(input_model) -> Path | None:
    raw = getattr(input_model, "repo_root", None)
    if not raw:
        return None
    return Path(raw)


def _material(ctx: StepRunContext):
    material = getattr(ctx.app, "material", None)
    if material is None:
        raise ValueError("MaterialService is not registered")
    return material


def _issue_summary(issues) -> str | None:
    for issue in issues or []:
        message = getattr(issue, "message", None)
        if message:
            return str(message)
    return None


RESOURCE_REQUEST_STEP_TYPES: tuple[type[BaseStep], ...] = (ResourceCurationPreflightStep,)


__all__ = [
    "ExternalRepoRequiredResultView",
    "LocalResourceCreatedResultView",
    "ResourceCurationPreflightStep",
    "ResourceCurationPreflightStepResult",
    "ResourceCuratorStepResult",
    "ResourceDuplicateResultView",
    "ResourceRejectedResultView",
    "RESOURCE_REQUEST_STEP_TYPES",
    "new_resource_request_step_id",
]
