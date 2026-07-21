"""Read-only audit service for failed native SourceIndex recovery."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from agent_runtime_kit.flow.models import FlowStatus, StepStatus

from lean_constellation.domain.repo_recovery import (
    NativeSourceIndexRecoveryContract,
    native_source_index_recovery_token,
)
from lean_constellation.services.foundation import ServiceResult


class NativeSourceIndexRecoveryComponent:
    """Build a deterministic recovery CAS without mutating failed records."""

    def __init__(self, runtime) -> None:  # noqa: ANN001
        self.runtime = runtime

    def preview(
        self,
        repo_root: Path,
        *,
        repo_key: str,
        failed_parent_flow_id: str,
    ) -> ServiceResult[NativeSourceIndexRecoveryContract]:
        """Build the external paused-runtime preview contract."""

        return self._preview(
            repo_root,
            repo_key=repo_key,
            failed_parent_flow_id=failed_parent_flow_id,
        )

    def revalidate_successor(
        self,
        repo_root: Path,
        *,
        repo_key: str,
        failed_parent_flow_id: str,
        successor_parent_flow_id: str,
        successor_child_flow_id: str,
        running_validation_step_id: str,
    ) -> ServiceResult[NativeSourceIndexRecoveryContract]:
        """Rebuild the contract from the one allowed deterministic recovery Step."""

        return self._preview(
            repo_root,
            repo_key=repo_key,
            failed_parent_flow_id=failed_parent_flow_id,
            ignored_active_flow_ids=[successor_parent_flow_id, successor_child_flow_id],
            require_paused=False,
            ignored_running_step_ids=[running_validation_step_id],
        )

    def _preview(
        self,
        repo_root: Path,
        *,
        repo_key: str,
        failed_parent_flow_id: str,
        ignored_active_flow_ids: Iterable[str] = (),
        require_paused: bool = True,
        ignored_running_step_ids: Iterable[str] = (),
    ) -> ServiceResult[NativeSourceIndexRecoveryContract]:
        from lean_constellation.flows.repo_lifecycle.flows import (
            NativeRepoPreparationFlow,
            NativeRepoPreparationInput,
            NativeRepoPreparationState,
        )
        from lean_constellation.flows.repo_lifecycle.source_index import (
            SourceIndexBuildFlow,
            SourceIndexBuildInput,
            SourceIndexBuildState,
        )

        root = Path(repo_root).resolve(strict=False)
        scope_id = f"repo:{repo_key}"
        ignored = set(ignored_active_flow_ids)
        ignored_steps = set(ignored_running_step_ids)
        schedule_service = self.runtime.ark.schedule_service
        if schedule_service is not None:
            pause_controller = self.runtime.ark.pause_controller
            if require_paused and (pause_controller is None or not pause_controller.is_paused()):
                return self._fail(
                    "native_source_index_recovery_runtime_not_paused",
                    "Native SourceIndex recovery preview requires the repo runtime to be globally paused.",
                    object_ref=repo_key,
                )
            if getattr(schedule_service, "active_flow_advances", set()):
                return self._fail(
                    "native_source_index_recovery_runtime_busy",
                    "Native SourceIndex recovery preview requires no active Flow advance.",
                    object_ref=repo_key,
                )
            step_service = self.runtime.ark.step_service
            running_steps = (
                set(step_service.list_running_steps()) - ignored_steps
                if step_service is not None
                else set()
            )
            if running_steps:
                return self._fail(
                    "native_source_index_recovery_runtime_busy",
                    "Native SourceIndex recovery preview requires no running Step.",
                    object_ref=repo_key,
                )
        active = [
            flow
            for flow in self.runtime.ark.flow_service.list_flows(scope_id=scope_id)
            if flow.flow_id not in ignored
            and flow.status not in {FlowStatus.COMPLETED, FlowStatus.FAILED}
        ]
        if active:
            return self._fail(
                "repo_lifecycle_flow_conflict",
                "A repo lifecycle Flow is already active; recovery preview is not stable.",
                object_ref=active[0].flow_id,
            )
        try:
            parent = self.runtime.ark.flow_service.get_flow(failed_parent_flow_id)
        except Exception:  # noqa: BLE001 - public recovery boundary hides store details.
            return self._fail(
                "native_source_index_recovery_parent_missing",
                "The failed native preparation parent Flow does not exist.",
                object_ref=failed_parent_flow_id,
            )
        if (
            not isinstance(parent, NativeRepoPreparationFlow)
            or not isinstance(parent.input, NativeRepoPreparationInput)
            or not isinstance(parent.state, NativeRepoPreparationState)
            or parent.scope_id != scope_id
            or parent.status is not FlowStatus.FAILED
            or parent.error is None
            or parent.error.error_type != "native_preparation_child_failed"
        ):
            return self._fail(
                "native_source_index_recovery_parent_ineligible",
                "Recovery requires the reconciled failed native preparation parent.",
                object_ref=failed_parent_flow_id,
            )
        parent_root = Path(parent.input.repo_root or "").resolve(strict=False)
        if parent.input.repo_key != repo_key or parent_root != root:
            return self._fail(
                "native_source_index_recovery_repo_mismatch",
                "The requested repo does not match the failed parent lineage.",
                object_ref=failed_parent_flow_id,
            )
        child_id = parent.error.details.get("child_flow_id")
        if (
            not isinstance(child_id, str)
            or parent.error.details.get("child_flow_type") != "source_index_build"
            or parent.error.details.get("child_error_type") != "source_index_build_step_failed"
        ):
            return self._fail(
                "native_source_index_recovery_child_lineage_invalid",
                "The failed parent does not identify an eligible SourceIndex child.",
                object_ref=failed_parent_flow_id,
            )
        try:
            child = self.runtime.ark.flow_service.get_flow(child_id)
        except Exception:  # noqa: BLE001
            return self._fail(
                "native_source_index_recovery_child_missing",
                "The failed SourceIndex child Flow does not exist.",
                object_ref=child_id,
            )
        if (
            not isinstance(child, SourceIndexBuildFlow)
            or not isinstance(child.input, SourceIndexBuildInput)
            or not isinstance(child.state, SourceIndexBuildState)
            or child.parent_flow_id != parent.flow_id
            or child.scope_id != scope_id
            or child.status is not FlowStatus.FAILED
            or child.error is None
            or child.error.error_type != "source_index_build_step_failed"
            or child.error.details.get("step_type") != "source_index_builder_agent_step"
        ):
            return self._fail(
                "native_source_index_recovery_child_ineligible",
                "Recovery is limited to a failed SourceIndex builder AgentStep child.",
                object_ref=child_id,
            )
        state = child.state
        if (
            state.position.phase != "builder"
            or state.review_round < 2
            or state.review_round > child.input.max_review_rounds
            or state.review_approved
            or not (state.latest_reviewer_feedback or "").strip()
        ):
            return self._fail(
                "native_source_index_recovery_review_state_invalid",
                "Recovery requires a rejected draft waiting for a later builder round.",
                object_ref=child_id,
            )
        if not child.step_ids:
            return self._fail(
                "native_source_index_recovery_failed_step_missing",
                "The failed SourceIndex child has no failed Step record.",
                object_ref=child_id,
            )
        failed_step_id = child.step_ids[-1]
        try:
            failed_step = self.runtime.ark.flow_service.get_step(failed_step_id)
        except Exception:  # noqa: BLE001
            return self._fail(
                "native_source_index_recovery_failed_step_missing",
                "The terminal failed builder Step cannot be loaded.",
                object_ref=failed_step_id,
            )
        if (
            failed_step.flow_id != child.flow_id
            or failed_step.step_type != "source_index_builder_agent_step"
            or failed_step.status is not StepStatus.FAILED
            or failed_step.error is None
        ):
            return self._fail(
                "native_source_index_recovery_failed_step_invalid",
                "The last SourceIndex Step is not the preserved failed builder Step.",
                object_ref=failed_step_id,
            )
        failed_message = failed_step.error.message.strip()
        if (
            failed_step.error.error_type != "step_run_exception"
            or not failed_message.startswith("home materialized file hash mismatch:")
            or child.error.message != failed_step.error.message
            or parent.error.message != failed_step.error.message
        ):
            return self._fail(
                "native_source_index_recovery_failure_cause_ineligible",
                "Recovery is limited to the preserved provider Home hash-mismatch failure.",
                object_ref=failed_step_id,
            )
        checkpoint_id = parent.state.pre_run_mutation_checkpoint_id
        if (
            not checkpoint_id
            or child.input.pre_update_checkpoint_id != checkpoint_id
            or state.pre_update_checkpoint_id != checkpoint_id
        ):
            return self._fail(
                "native_source_index_recovery_checkpoint_lineage_invalid",
                "Parent and child do not share the original pre-mutation checkpoint.",
                object_ref=child_id,
            )
        checkpoint_adapter = self.runtime.app.source_index_checkpoint
        if checkpoint_adapter is None:
            return self._fail(
                "native_source_index_recovery_checkpoint_adapter_missing",
                "SourceIndex checkpoint validation is unavailable.",
                object_ref=checkpoint_id,
            )
        checkpoint = checkpoint_adapter.validate_source_index_baseline_checkpoint(
            root, checkpoint_id=checkpoint_id
        )
        if not checkpoint.ok or checkpoint.value is None:
            return self.runtime.foundation.fail(checkpoint.issues)
        if not state.baseline_digest or checkpoint.value.baseline_digest != state.baseline_digest:
            return self._fail(
                "native_source_index_recovery_baseline_drift",
                "The archived pre-mutation baseline no longer matches the failed child.",
                object_ref=checkpoint_id,
            )
        resolved = self.runtime.material.resolve_source_scope(
            root, source_scope=child.input.source_scope
        )
        if not resolved.ok or resolved.value is None:
            return self.runtime.foundation.fail(resolved.issues)
        if (
            resolved.value.resolved_file_paths != state.resolved_file_paths
            or resolved.value.readable_file_paths != state.readable_file_paths
            or resolved.value.artifact_file_paths != state.artifact_file_paths
            or resolved.value.manifest_digest != state.manifest_digest
        ):
            return self._fail(
                "native_source_index_recovery_source_scope_drift",
                "The current source scope or manifest differs from the failed child.",
                object_ref=child_id,
            )
        baseline = checkpoint_adapter.load_source_index_baseline(
            root, checkpoint_id=checkpoint_id
        )
        if not baseline.ok:
            return self.runtime.foundation.fail(baseline.issues)
        baseline_paths = set(baseline.value.files) if baseline.value is not None else set()
        expected_new_paths = sorted(
            path for path in state.resolved_file_paths if path not in baseline_paths
        )
        current = self.runtime.material.source_index.get_source_index_model(root)
        if not current.ok or current.value is None:
            return self.runtime.foundation.fail(current.issues)
        current_committed_paths = sorted(
            path
            for path in state.resolved_file_paths
            if path in current.value.files and current.value.files[path].committed
        )
        current_uncommitted_paths = sorted(
            path
            for path in state.resolved_file_paths
            if path in current.value.files and not current.value.files[path].committed
        )
        if (
            sorted(state.new_file_paths) != expected_new_paths
            or sorted(state.already_committed_file_paths) != current_committed_paths
            or sorted(state.uncommitted_file_paths) != current_uncommitted_paths
            or sorted(state.already_committed_file_paths + state.uncommitted_file_paths)
            != sorted(state.resolved_file_paths)
        ):
            return self._fail(
                "native_source_index_recovery_update_context_drift",
                "The failed child update context no longer matches its baseline and rejected draft.",
                object_ref=child_id,
            )
        validated = self.runtime.material.validate_source_index_update(
            root,
            baseline_index=baseline.value,
            expected_baseline_digest=state.baseline_digest,
            resolved_scope=list(state.resolved_file_paths),
            require_completed=False,
        )
        if not validated.ok or validated.value is None:
            return self.runtime.foundation.fail(validated.issues)
        if not validated.value.gate.passed:
            return self._fail(
                "native_source_index_recovery_draft_invalid",
                validated.value.gate.summary or "The rejected draft no longer validates against its original baseline.",
                object_ref=child_id,
            )
        draft_digest = self.runtime.material.source_index.canonical_source_index_digest(current.value)
        payload: dict[str, object] = {
            "recovery_kind": "native_source_index_successor",
            "repo_key": repo_key,
            "repo_root": str(root),
            "failed_parent_flow_id": parent.flow_id,
            "failed_source_index_flow_id": child.flow_id,
            "failed_step_id": failed_step.step_id,
            "failed_step_error_type": failed_step.error.error_type,
            "failed_step_error_message": failed_step.error.message,
            "pre_run_mutation_checkpoint_id": checkpoint_id,
            "baseline_digest": state.baseline_digest,
            "draft_digest": draft_digest,
            "review_round": state.review_round,
            "max_review_rounds": child.input.max_review_rounds,
            "reviewer_feedback": state.latest_reviewer_feedback,
            "latest_builder_summary": state.latest_builder_summary,
            "resolved_file_paths": list(state.resolved_file_paths),
            "readable_file_paths": list(state.readable_file_paths),
            "artifact_file_paths": list(state.artifact_file_paths),
            "new_file_paths": list(state.new_file_paths),
            "already_committed_file_paths": list(state.already_committed_file_paths),
            "uncommitted_file_paths": list(state.uncommitted_file_paths),
            "manifest_digest": state.manifest_digest,
            "source_corpus_mode": parent.state.source_corpus_mode,
            "allow_interface_supplement": parent.state.allow_interface_supplement,
        }
        if payload["source_corpus_mode"] not in {"existing", "prepare"} or not isinstance(
            payload["allow_interface_supplement"], bool
        ):
            return self._fail(
                "native_source_index_recovery_parent_state_invalid",
                "The failed parent does not retain its initialized preparation state.",
                object_ref=parent.flow_id,
            )
        payload["recovery_token"] = native_source_index_recovery_token(payload)
        return self.runtime.foundation.ok(NativeSourceIndexRecoveryContract.model_validate(payload))

    def _fail(
        self,
        kind: str,
        message: str,
        *,
        object_ref: str | None = None,
    ) -> ServiceResult[NativeSourceIndexRecoveryContract]:
        return self.runtime.foundation.fail(
            self.runtime.foundation.issue(kind, message, object_ref=object_ref)
        )


__all__ = ["NativeSourceIndexRecoveryComponent"]
