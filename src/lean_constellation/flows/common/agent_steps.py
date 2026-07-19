"""Minimal business AgentStep shells for submission type registration."""

from __future__ import annotations

from typing import ClassVar

from agent_runtime_kit.flow.models import BaseSubmission, FlowStatus
from agent_runtime_kit.flow.standard_steps.agent_step import AgentStep

from lean_constellation.flows.content_node_task.decl_round.submissions import (
    DeclRoundDispatchSubmission,
    DeclStageReviewSubmittedSubmission,
    DeclStageWorkerBlockedSubmission,
    DeclStageWorkerCompletedSubmission,
)
from lean_constellation.flows.content_node_task.decl_round.steps import (
    DeclStageReviewerStepState,
    DeclStageReviewerStepResult,
    DeclStageWorkerStepResult,
)
from lean_constellation.flows.content_node_task.preparation.mathlib_recon.submissions import (
    MathlibReconCompletedSubmission,
)
from lean_constellation.flows.content_node_task.preparation.node_dir_recon.submissions import (
    NodeDirDependencyReconCompletedSubmission,
)
from lean_constellation.flows.content_node_task.preparation.resource_recon.submissions import (
    ResourceReconBlockedSubmission,
    ResourceReconCompletedSubmission,
    ResourceReconRequestResourceSubmission,
)
from lean_constellation.flows.content_node_task.submissions import (
    ContentNodeBlockedSubmission,
    ContentNodeFailedSubmission,
    ContentNodeReadySubmission,
    ContentPreparationDispatchSubmission,
    ContentResourceRequestSubmission,
)
from lean_constellation.flows.content_node_task.steps import (
    ContentCompletionResultView,
    ContentDeclRoundDispatchResultView,
    ContentPlanStepResult,
    ContentPreparationDispatchResultView,
    ContentResourceRequestResultView,
    MathlibReconStepResult,
    NodeDirDependencyReconStepResult,
    ResourceReconStepResult,
)
from lean_constellation.flows.coordinator.submissions import (
    CoordinatorContentTasksSubmission,
    CoordinatorRepoReadySubmission,
    CoordinatorRepoRequirementSubmission,
    CoordinatorResourceRequestSubmission,
)
from lean_constellation.flows.coordinator.steps import (
    CoordinatorContentTasksResultView,
    CoordinatorRepoReadyResultView,
    CoordinatorRepoRequirementResultView,
    CoordinatorResourceRequestResultView,
    CoordinatorStepResult,
)
from lean_constellation.flows.repo_lifecycle.submissions import (
    AdapterCatalogBlockedSubmission,
    AdapterCatalogReadySubmission,
    RepoFormatAdapterChoiceSubmission,
    RepoFormatNativeChoiceSubmission,
    RootInterfacePrepareReadySubmission,
    SourceCorpusBlockedSubmission,
    SourceCorpusPreparedSubmission,
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)
from lean_constellation.flows.repo_lifecycle.steps import (
    AdapterDeclCatalogStepResult,
    RepoFormatDiscoveryStepResult,
    RootInterfacePrepareStepResult,
    SourceCorpusPrepareStepResult,
    SourceIndexBuilderStepResult,
    SourceIndexReviewerStepResult,
)
from lean_constellation.flows.resource_request.steps import (
    ExternalRepoRequiredResultView,
    LocalResourceCreatedResultView,
    ResourceCuratorStepResult,
    ResourceDuplicateResultView,
    ResourceRejectedResultView,
)
from lean_constellation.flows.resource_request.submissions import (
    ExternalRepoRequiredSubmission,
    LocalResourceCreatedSubmission,
    ResourceDuplicateSubmission,
    ResourceRejectedSubmission,
)


def _submission_map(*classes: type[BaseSubmission]) -> dict[str, type[BaseSubmission]]:
    return {
        str(cls.model_fields["submission_type"].default): cls
        for cls in classes
    }


class RepoFormatDiscoveryAgentStep(AgentStep):
    step_type: ClassVar[str] = "repo_format_discovery_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "repo_format_discovery": RepoFormatDiscoveryStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        RepoFormatAdapterChoiceSubmission,
        RepoFormatNativeChoiceSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {
        "submit_adapter_repo_choice",
        "submit_native_repo_choice",
    }

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RepoFormatAdapterChoiceSubmission):
            return RepoFormatDiscoveryStepResult(
                outcome="adapter",
                selected_repo_format="adapter",
                summary=submission.summary,
            )
        if isinstance(submission, RepoFormatNativeChoiceSubmission):
            return RepoFormatDiscoveryStepResult(
                outcome="native",
                selected_repo_format="native",
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return RepoFormatDiscoveryStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class SourceCorpusPrepareAgentStep(AgentStep):
    step_type: ClassVar[str] = "source_corpus_prepare_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "source_corpus_prepare": SourceCorpusPrepareStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        SourceCorpusPreparedSubmission,
        SourceCorpusBlockedSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {
        "submit_source_corpus_prepared",
        "submit_source_corpus_blocked",
    }

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, SourceCorpusPreparedSubmission):
            return SourceCorpusPrepareStepResult(
                outcome="prepared",
                relpath=submission.relpath,
                entry_path=submission.entry_path,
                overview=submission.overview,
                summary=submission.summary or submission.preparation_summary,
            )
        if isinstance(submission, SourceCorpusBlockedSubmission):
            return SourceCorpusPrepareStepResult(
                outcome="blocked",
                blocked_reason=submission.reason,
                summary=submission.summary or submission.reason,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return SourceCorpusPrepareStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class SourceIndexBuilderAgentStep(AgentStep):
    step_type: ClassVar[str] = "source_index_builder_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "source_index_builder": SourceIndexBuilderStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        SourceIndexBuilderRoundSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {"submit_source_index_builder_round"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, SourceIndexBuilderRoundSubmission):
            return SourceIndexBuilderStepResult(
                outcome="submitted",
                validation_summary=submission.validation_summary,
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return SourceIndexBuilderStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class SourceIndexReviewerAgentStep(AgentStep):
    step_type: ClassVar[str] = "source_index_reviewer_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "source_index_reviewer": SourceIndexReviewerStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        SourceIndexReviewerRoundSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {"submit_source_index_review_round"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, SourceIndexReviewerRoundSubmission):
            return SourceIndexReviewerStepResult(
                outcome="approved" if submission.approved else "rejected",
                feedback=submission.feedback,
                summary=submission.summary,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return SourceIndexReviewerStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class RootInterfacePrepareAgentStep(AgentStep):
    step_type: ClassVar[str] = "root_interface_prepare_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "root_interface_prepare": RootInterfacePrepareStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        RootInterfacePrepareReadySubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {"submit_root_interface_prepare_ready"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, RootInterfacePrepareReadySubmission):
            return RootInterfacePrepareStepResult(outcome="ready", summary=submission.summary)
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return RootInterfacePrepareStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class AdapterDeclCatalogAgentStep(AgentStep):
    step_type: ClassVar[str] = "adapter_decl_catalog_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "adapter_decl_catalog": AdapterDeclCatalogStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        AdapterCatalogReadySubmission,
        AdapterCatalogBlockedSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {
        "submit_adapter_catalog_ready",
        "submit_adapter_catalog_blocked",
    }

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, AdapterCatalogReadySubmission):
            return AdapterDeclCatalogStepResult(outcome="ready", summary=submission.summary)
        if isinstance(submission, AdapterCatalogBlockedSubmission):
            return AdapterDeclCatalogStepResult(
                outcome="blocked",
                blocked_reason=submission.reason,
                missing_interfaces=submission.missing_interfaces,
                suggested_next_action=submission.suggested_next_action,
                summary=submission.summary or submission.reason,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return AdapterDeclCatalogStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class ResourceCuratorAgentStep(AgentStep):
    step_type: ClassVar[str] = "resource_curator_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "resource_curator": ResourceCuratorStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        ResourceDuplicateSubmission,
        LocalResourceCreatedSubmission,
        ExternalRepoRequiredSubmission,
        ResourceRejectedSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {
        "submit_resource_duplicate",
        "submit_local_resource_created",
        "submit_external_repo_required",
        "submit_resource_rejected",
    }

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, ResourceDuplicateSubmission):
            duplicate = ResourceDuplicateResultView(
                existing_kind=submission.existing_kind,
                ref_summary=(
                    f"Resource {submission.existing_resource_key}"
                    if submission.existing_kind == "resource" and submission.existing_resource_key
                    else f"Source corpus material {submission.existing_source_path}"
                    if submission.existing_kind == "source" and submission.existing_source_path
                    else "Existing resource requirement"
                ),
                duplicate_reason=submission.duplicate_reason,
                existing_resource_key=submission.existing_resource_key,
                existing_source_path=submission.existing_source_path,
                preview=submission.preview,
            )
            return ResourceCuratorStepResult(
                outcome="duplicate",
                repo_key=submission.repo_key,
                target_summary=f"{submission.target_kind}:{submission.target}",
                duplicate=duplicate,
                summary=submission.summary or submission.duplicate_reason,
            )
        if isinstance(submission, LocalResourceCreatedSubmission):
            resource_key = submission.resource_key or submission.draft_id
            local_resource = LocalResourceCreatedResultView(
                resource_key=resource_key,
                resource_ref_summary=f"Resource {resource_key}",
                locator_summary=f"{submission.target_kind}:{submission.target}",
            )
            return ResourceCuratorStepResult(
                outcome="local_resource_created",
                repo_key=submission.repo_key,
                target_summary=f"{submission.target_kind}:{submission.target}",
                local_resource=local_resource,
                summary=submission.summary or f"Created local resource {resource_key}.",
            )
        if isinstance(submission, ExternalRepoRequiredSubmission):
            external = ExternalRepoRequiredResultView(
                reason=submission.reason,
                source_description=submission.source_description,
                source_locator=f"{submission.target_kind}:{submission.target}",
                suggested_repo_name=submission.suggested_repo_name,
                required_interfaces_hint=submission.required_interfaces_hint,
            )
            return ResourceCuratorStepResult(
                outcome="external_repo_required",
                repo_key=submission.repo_key,
                target_summary=f"{submission.target_kind}:{submission.target}",
                external_repo=external,
                summary=submission.summary or submission.reason,
            )
        if isinstance(submission, ResourceRejectedSubmission):
            rejected = ResourceRejectedResultView(reason=submission.reason, details=submission.details)
            return ResourceCuratorStepResult(
                outcome="rejected",
                repo_key=submission.repo_key,
                target_summary=f"{submission.target_kind}:{submission.target}",
                rejected=rejected,
                summary=submission.summary or submission.reason,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return ResourceCuratorStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class CoordinatorAgentStep(AgentStep):
    step_type: ClassVar[str] = "coordinator_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "coordinator": CoordinatorStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        CoordinatorContentTasksSubmission,
        CoordinatorResourceRequestSubmission,
        CoordinatorRepoRequirementSubmission,
        CoordinatorRepoReadySubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {
        "submit_content_node_tasks",
        "submit_resource_request",
        "submit_repo_requirement",
        "submit_repo_ready",
    }

    def build_callback_prompt(self, ctx, agent_id: str) -> str:
        base = super().build_callback_prompt(ctx, agent_id)
        children = _callback_child_flows(self, ctx)
        if any(child.flow_type == "resource_curation" for child in children):
            guidance = (
                "Required Skill re-entry for this turn: read and apply resource-result-closeout first, "
                "then re-read the current Coordinator mode Skill. Close out duplicate/local/external/rejected "
                "resource truth before choosing exactly one next coordination move."
            )
        else:
            outcomes = [
                getattr(getattr(child, "result", None), "outcome", "runtime_failed")
                if child.status is not FlowStatus.FAILED
                else "runtime_failed"
                for child in children
            ]
            guidance = (
                "Required Skill re-entry for this turn: read and apply coordinator-content-result-closeout "
                "first, then re-read the current Coordinator mode Skill. Classify all content outcomes "
                f"({', '.join(str(item) for item in outcomes) or 'none'}); if a task reports scope overflow, "
                "choose between splitting the node and explicitly revising its contract scope."
            )
        return f"{base}\n\nCurrent callback routing:\n{guidance}"

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, CoordinatorContentTasksSubmission):
            return CoordinatorStepResult(
                outcome="content_tasks",
                repo_key=submission.repo_key,
                content_tasks=CoordinatorContentTasksResultView(
                    node_paths=list(submission.node_paths),
                    task_mode=submission.task_mode,
                    request_count=len(submission.requests),
                ),
                summary=submission.summary or f"Coordinator requested {len(submission.node_paths)} content node tasks.",
            )
        if isinstance(submission, CoordinatorResourceRequestSubmission):
            return CoordinatorStepResult(
                outcome="resource_request",
                repo_key=submission.repo_key,
                resource_request=CoordinatorResourceRequestResultView(
                    target_kind=submission.target_kind,
                    target=submission.target,
                    arxiv_version=submission.arxiv_version,
                    request_count=len(submission.requests),
                    context_summary=submission.context_summary,
                ),
                summary=submission.summary or f"Coordinator requested resource {submission.target_kind}:{submission.target}.",
            )
        if isinstance(submission, CoordinatorRepoRequirementSubmission):
            return CoordinatorStepResult(
                outcome="repo_requirement",
                repo_key=submission.repo_key,
                repo_requirement=CoordinatorRepoRequirementResultView(
                    requirement_name=submission.requirement_name,
                    target_repo=submission.target_repo,
                    required_proof_availability=submission.required_proof_availability,
                    reason=submission.reason,
                    source_description=submission.source_description,
                    interface_count=len(submission.interfaces),
                ),
                summary=submission.summary or submission.reason or f"Coordinator submitted requirement {submission.requirement_name}.",
            )
        if isinstance(submission, CoordinatorRepoReadySubmission):
            return CoordinatorStepResult(
                outcome="repo_ready",
                repo_key=submission.repo_key,
                repo_ready=CoordinatorRepoReadyResultView(repo_summary=submission.summary or "Repo ready."),
                summary=submission.summary or "Repo ready.",
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return CoordinatorStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class ContentPlanAgentStep(AgentStep):
    step_type: ClassVar[str] = "content_plan_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "content_plan": ContentPlanStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        ContentPreparationDispatchSubmission,
        ContentResourceRequestSubmission,
        DeclRoundDispatchSubmission,
        ContentNodeReadySubmission,
        ContentNodeBlockedSubmission,
        ContentNodeFailedSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {
        "submit_content_preparation_recon",
        "submit_resource_request",
        "submit_current_decl_round",
        "submit_content_node_ready",
        "submit_content_node_blocked",
        "submit_content_node_failed",
    }

    def build_callback_prompt(self, ctx, agent_id: str) -> str:
        base = super().build_callback_prompt(ctx, agent_id)
        children = _callback_child_flows(self, ctx)
        state = self._agent_step_state(self._latest_agent_step(ctx))
        guidance = _content_plan_callback_guidance(
            children,
            mode_skill=_current_content_plan_mode_skill(self, ctx),
        )
        brief = state.variables.get("context_brief")
        if not isinstance(brief, dict):
            return f"{base}\n\nCurrent callback routing:\n{guidance}"
        rendered = _render_context_brief_payload(brief)
        return (
            f"{base}\n\nCurrent callback routing:\n{guidance}"
            f"\n\nCurrent derived ContentPlan context brief:\n{rendered}"
        )

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, ContentPreparationDispatchSubmission):
            return ContentPlanStepResult(
                outcome="preparation_dispatch",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                preparation=ContentPreparationDispatchResultView(
                    recon_kind=submission.recon_kind,
                    objective=submission.objective,
                    context_summary=submission.context_summary,
                    request_count=len(submission.requests),
                ),
                summary=submission.summary or f"Dispatch {submission.recon_kind} preparation recon.",
            )
        if isinstance(submission, ContentResourceRequestSubmission):
            return ContentPlanStepResult(
                outcome="resource_request",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                resource_request=ContentResourceRequestResultView(
                    target_kind=submission.target_kind,
                    target=submission.target,
                    arxiv_version=submission.arxiv_version,
                    context_summary=submission.context_summary,
                    request_count=len(submission.requests),
                ),
                summary=submission.summary or f"Request resource {submission.target_kind}:{submission.target}.",
            )
        if isinstance(submission, DeclRoundDispatchSubmission):
            return ContentPlanStepResult(
                outcome="decl_round_dispatch",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                decl_round=ContentDeclRoundDispatchResultView(
                    strategy_id=submission.strategy_id,
                    round_id=submission.round_id,
                    round_index=submission.round_index,
                    request_count=len(submission.requests),
                ),
                summary=submission.summary or f"Dispatch decl round {submission.round_id}.",
            )
        if isinstance(submission, ContentNodeReadySubmission):
            return ContentPlanStepResult(
                outcome="ready",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                completion=ContentCompletionResultView(outcome="ready"),
                summary=submission.summary or "Content node task is ready.",
            )
        if isinstance(submission, ContentNodeBlockedSubmission):
            return ContentPlanStepResult(
                outcome="blocked",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                completion=ContentCompletionResultView(outcome="blocked", reason=submission.reason),
                summary=submission.summary or submission.reason,
            )
        if isinstance(submission, ContentNodeFailedSubmission):
            return ContentPlanStepResult(
                outcome="failed",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                completion=ContentCompletionResultView(outcome="failed", reason=submission.reason),
                summary=submission.summary or submission.reason,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return ContentPlanStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class NodeDirDependencyReconAgentStep(AgentStep):
    step_type: ClassVar[str] = "node_dir_dependency_recon_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "node_dir_dependency_recon_agent": NodeDirDependencyReconStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        NodeDirDependencyReconCompletedSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {"submit_node_dir_dependency_recon_completed"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, NodeDirDependencyReconCompletedSubmission):
            return NodeDirDependencyReconStepResult(
                outcome="completed",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                dependency_change_summary=submission.dependency_change_summary,
                checked_boundary_summary=submission.checked_boundary_summary,
                useful_findings=list(submission.useful_findings),
                unresolved_within_visible_boundaries=list(submission.unresolved_within_visible_boundaries),
                summary=submission.summary or "Node dependency recon completed.",
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return NodeDirDependencyReconStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class MathlibReconAgentStep(AgentStep):
    step_type: ClassVar[str] = "mathlib_recon_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "mathlib_recon_agent": MathlibReconStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        MathlibReconCompletedSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {"submit_mathlib_recon_completed"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, MathlibReconCompletedSubmission):
            return MathlibReconStepResult(
                outcome="completed",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                index_update_summary=submission.index_update_summary,
                node_mathlib_hint_summary=submission.node_mathlib_hint_summary,
                useful_findings=list(submission.useful_findings),
                unresolved_in_mathlib=list(submission.unresolved_in_mathlib),
                summary=submission.summary or "Mathlib recon completed.",
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return MathlibReconStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class ResourceReconAgentStep(AgentStep):
    step_type: ClassVar[str] = "resource_recon_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "resource_recon_agent": ResourceReconStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        ResourceReconCompletedSubmission,
        ResourceReconBlockedSubmission,
        ResourceReconRequestResourceSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {
        "submit_resource_recon_completed",
        "submit_resource_recon_blocked",
        "submit_resource_request",
    }

    def build_callback_prompt(self, ctx, agent_id: str) -> str:
        base = super().build_callback_prompt(ctx, agent_id)
        guidance = (
            "Required Skill re-entry for this turn: read and apply resource-result-closeout first. "
            "Consume the returned resource truth and either attach/finish it or report the precise remaining "
            "blocker; do not restart broad discovery."
        )
        state = self._agent_step_state(self._latest_agent_step(ctx))
        prior = state.variables.get("prior_preparation_context")
        if not isinstance(prior, str) or not prior.strip():
            return f"{base}\n\nCurrent callback routing:\n{guidance}"
        return (
            f"{base}\n\nCurrent callback routing:\n{guidance}"
            f"\n\nPrior preparation context (do not broadly rediscover):\n{prior}"
        )

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, ResourceReconCompletedSubmission):
            return ResourceReconStepResult(
                outcome="completed",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                material_change_summary=submission.material_change_summary,
                checked_material_summary=submission.checked_material_summary,
                useful_findings=list(submission.useful_findings),
                unresolved_material_needs=list(submission.unresolved_material_needs),
                summary=submission.summary or "Resource recon completed.",
            )
        if isinstance(submission, ResourceReconBlockedSubmission):
            return ResourceReconStepResult(
                outcome="blocked",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                missing_targets=list(submission.missing_targets),
                reason=submission.reason,
                summary=submission.summary or submission.reason,
            )
        if isinstance(submission, ResourceReconRequestResourceSubmission):
            return ResourceReconStepResult(
                outcome="resource_request",
                repo_key=submission.repo_key,
                node_path=submission.node_path,
                resource_request=ContentResourceRequestResultView(
                    target_kind=submission.target_kind,
                    target=submission.target,
                    arxiv_version=submission.arxiv_version,
                    context_summary=submission.context_summary,
                    request_count=len(submission.requests),
                ),
                summary=submission.summary or f"Request resource {submission.target_kind}:{submission.target}.",
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return ResourceReconStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class DeclStageWorkerAgentStep(AgentStep):
    step_type: ClassVar[str] = "decl_stage_worker_agent_step"
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "decl_stage_worker": DeclStageWorkerStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        DeclStageWorkerCompletedSubmission,
        DeclStageWorkerBlockedSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {
        "submit_stage_worker_completed",
        "submit_stage_worker_blocked",
    }

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, DeclStageWorkerCompletedSubmission):
            return DeclStageWorkerStepResult(
                outcome="completed",
                stage=submission.stage,
                round_id=submission.round_id,
                completed_decl_names=list(submission.completed_decl_names),
                summary=submission.summary or f"{submission.stage} worker completed.",
            )
        if isinstance(submission, DeclStageWorkerBlockedSubmission):
            return DeclStageWorkerStepResult(
                outcome="blocked",
                stage=submission.stage,
                round_id=submission.round_id,
                affected_decl_names=list(submission.affected_decl_names),
                reason=submission.reason,
                summary=submission.summary or submission.reason,
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return DeclStageWorkerStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


class DeclStageReviewerAgentStep(AgentStep):
    step_type: ClassVar[str] = "decl_stage_reviewer_agent_step"
    State: ClassVar[type] = DeclStageReviewerStepState
    Results: ClassVar[dict[str, type]] = {
        **AgentStep.Results,
        "decl_stage_reviewer": DeclStageReviewerStepResult,
    }
    Submissions: ClassVar[dict[str, type[BaseSubmission]]] = _submission_map(
        DeclStageReviewSubmittedSubmission,
    )
    SubmitTools: ClassVar[set[str] | None] = {"submit_stage_review"}

    def build_result_from_submission(self, ctx, agent_id: str, turn_result: object | None):
        submission = ctx.load_step().submission
        if isinstance(submission, DeclStageReviewSubmittedSubmission):
            return DeclStageReviewerStepResult(
                outcome="passed" if submission.accepted else "rejected",
                stage=submission.stage,
                round_id=submission.round_id,
                node_path=submission.node_path,
                accepted=submission.accepted,
                retry_required=submission.retry_required,
                reviewed_decl_names=list(submission.reviewed_decl_names),
                failed_decl_names=list(submission.failed_decl_names),
                missing_decl_names=list(submission.missing_decl_names),
                feedback=list(submission.feedback),
                summary=submission.summary or f"{submission.stage} review submitted.",
            )
        return super().build_result_from_submission(ctx, agent_id, turn_result)

    def build_incomplete_result(self, ctx, agent_id: str | None, reason: str, turn_result: object | None, attempt_count: int):
        del ctx, agent_id, turn_result, attempt_count
        return DeclStageReviewerStepResult(outcome="incomplete", incomplete_reason=reason, summary=reason)


def _render_context_brief_payload(payload: dict[str, object]) -> str:
    from lean_constellation.flows.content_node_task.context_brief import ContentPlanContextBrief

    return ContentPlanContextBrief.model_validate(payload).render()


def _callback_child_flows(step: AgentStep, ctx) -> list[object]:  # noqa: ANN001
    latest = step._latest_agent_step(ctx)
    state = step._agent_step_state(latest)
    dispatch_step_id = state.callback_dispatch_step_id
    if dispatch_step_id is None:
        return []
    dispatch = step._flow_service(ctx).get_step(dispatch_step_id)
    children = getattr(dispatch.state, "created_children", None) or []
    return [
        step._flow_service(ctx).get_flow(child.child_flow_id)
        for child in children
        if getattr(child, "child_flow_id", None)
    ]


def _content_plan_callback_guidance(children: list[object], *, mode_skill: str) -> str:
    child = children[0] if children else None
    flow_type = getattr(child, "flow_type", None)
    result = getattr(child, "result", None)
    outcome = (
        "runtime_failed"
        if child is not None and getattr(child, "status", None) is FlowStatus.FAILED
        else getattr(result, "outcome", "unknown")
    )
    if flow_type == "decl_graph_round":
        terminal_stage = getattr(result, "terminal_stage", None)
        affected = getattr(getattr(result, "terminal_reason", None), "affected_decl_names", []) or []
        return (
            "Required Skill order for this turn: read and apply decl-round-closeout first, then "
            f"{mode_skill}, then decl-strategy-planning. The round outcome is {outcome}; "
            f"terminal stage is {terminal_stage or 'none'}; affected declarations are "
            f"{', '.join(affected) or 'none'}. Close out the terminal round before any new mutation, "
            "reassess whether the strategy still explains the next round, classify any blocker, verify "
            "known dependency closure before retrying a parent proof, and check graph hygiene and node scope."
        )
    if flow_type == "resource_curation":
        return (
            "Required Skill order for this turn: read and apply resource-result-closeout first, then "
            f"{mode_skill}, then decl-strategy-planning. The resource outcome is {outcome}; consume its "
            "duplicate/local/external/rejected truth before choosing a round, completion, or task blocker."
        )
    if flow_type in {"node_dir_dependency_recon", "mathlib_recon", "resource_recon"}:
        return (
            "Required Skill order for this turn: read and apply content-preparation-orchestration, then "
            f"{mode_skill}, then decl-strategy-planning. The preparation outcome is {outcome}; reuse its "
            "verified findings without broad rediscovery unless evidence is stale, unresolved, or this role "
            "must independently verify it."
        )
    return (
        f"Re-read and apply {mode_skill} and decl-strategy-planning now. Classify the child outcome "
        f"({outcome}) before choosing exactly one next ContentPlan action."
    )


def _current_content_plan_mode_skill(step: AgentStep, ctx) -> str:  # noqa: ANN001
    from pathlib import Path

    work_mode = "proved_full_graph"
    flow = step._flow_service(ctx).get_flow(ctx.flow_id)
    repo_path = getattr(flow.input, "repo_path", None)
    if repo_path:
        loaded = ctx.app.repo_workspace.metadata.get_repo_config(Path(repo_path))
        if loaded.ok and loaded.value is not None:
            work_mode = loaded.value.config.work_mode.value
    return f"content-plan-{work_mode.replace('_', '-').lower()}-mode"


BUSINESS_AGENT_STEP_TYPES: tuple[type[AgentStep], ...] = (
    RepoFormatDiscoveryAgentStep,
    SourceCorpusPrepareAgentStep,
    SourceIndexBuilderAgentStep,
    SourceIndexReviewerAgentStep,
    RootInterfacePrepareAgentStep,
    AdapterDeclCatalogAgentStep,
    ResourceCuratorAgentStep,
    CoordinatorAgentStep,
    ContentPlanAgentStep,
    NodeDirDependencyReconAgentStep,
    MathlibReconAgentStep,
    ResourceReconAgentStep,
    DeclStageWorkerAgentStep,
    DeclStageReviewerAgentStep,
)
