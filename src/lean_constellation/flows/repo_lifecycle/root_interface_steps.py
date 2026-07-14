"""Deterministic steps for incremental root-interface preparation."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal
import uuid

from agent_runtime_kit.flow.contexts import StepRunContext
from agent_runtime_kit.flow.models import (
    BaseStep,
    BaseStepResult,
    BaseStepState,
    FlowStepValidationError,
    StepTerminalReceipt,
)
from pydantic import Field

from lean_constellation.domain.interface import DeclInterface
from lean_constellation.domain.preparation import RepoPreparationInput
from lean_constellation.domain.repo import RepoFormat
from lean_constellation.flows.common.rendering import LeanRenderableStepResult
from lean_constellation.services.material import SourceIndex
from lean_constellation.services.validation_snapshot import RepoCheckpointKind


class RootInterfaceFlowStepResult(LeanRenderableStepResult):
    result_type: Literal["root_interface_flow_step"] = "root_interface_flow_step"
    outcome: Literal["valid", "appended", "synced", "decided", "verified", "ready", "blocked", "invalid_input"]
    added_names: list[str] = Field(default_factory=list)
    existing_names: list[str] = Field(default_factory=list)
    previous_interfaces: dict[str, dict[str, object]] = Field(default_factory=dict)
    previous_exports: list[dict[str, object]] = Field(default_factory=list)
    agent_required: bool | None = None
    supplement_names_added: list[str] = Field(default_factory=list)
    error_code: str | None = None

    def agent_fields(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "added_names": self.added_names,
            "existing_names": self.existing_names,
            "agent_required": self.agent_required,
            "supplement_names_added": self.supplement_names_added,
            "error_code": self.error_code,
        }


class ValidateRootInterfaceRunStep(BaseStep):
    step_type: ClassVar[str] = "validate_root_interface_run_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RootInterfaceFlowStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "root_interface_flow_step": RootInterfaceFlowStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        input_model = _input(ctx)
        repo_root = Path(input_model.repo_root)
        if input_model.repo_key != repo_root.name:
            return _complete(
                ctx,
                "invalid_input",
                "Root-interface run repo_key does not identify repo_root.",
                "root_interface_repo_key_mismatch",
            )
        if not input_model.pre_run_mutation_checkpoint_id.strip():
            return _complete(
                ctx,
                "invalid_input",
                "A pre-run mutation checkpoint id is required before root-interface work.",
                "root_interface_checkpoint_required",
            )
        checkpoint_adapter = _source_index_checkpoint(ctx)
        checkpoint = checkpoint_adapter.validate_root_interface_baseline_checkpoint(
            repo_root,
            checkpoint_id=input_model.pre_run_mutation_checkpoint_id,
            expected_kind=_expected_checkpoint_kind(input_model),
        )
        if not checkpoint.ok or checkpoint.value is None:
            return _complete_from_issues(
                ctx,
                "invalid_input",
                checkpoint.issues,
                "root_interface_checkpoint_unavailable",
            )
        if Path(checkpoint.value.repo_root) != repo_root:
            return _complete(
                ctx,
                "invalid_input",
                "The pre-run mutation checkpoint does not belong to this repository.",
                "root_interface_checkpoint_mismatch",
            )
        if input_model.start_reason == "initial" and input_model.run_context.start_kind != "initial":
            return _complete(
                ctx,
                "invalid_input",
                "Initial root-interface preparation requires an initial RepoRunContext.",
                "root_interface_start_kind_mismatch",
            )
        if input_model.start_reason == "continuation" and input_model.run_context.start_kind != "continuation":
            return _complete(
                ctx,
                "invalid_input",
                "Continuation root-interface preparation requires a continuation RepoRunContext.",
                "root_interface_start_kind_mismatch",
            )
        repo_format = _repo_workspace(ctx).metadata.get_repo_format(repo_root)
        if not repo_format.ok or repo_format.value is None:
            return _complete_from_issues(ctx, "invalid_input", repo_format.issues, "repo_format_missing")
        if repo_format.value.repo_format != RepoFormat.NATIVE:
            return _complete(
                ctx,
                "invalid_input",
                "RootInterfacePreparationFlow only supports native repositories.",
                "invalid_repo_format",
            )
        preparation = _repo_workspace(ctx).preparation.get_preparation_input(repo_root)
        if not preparation.ok or preparation.value is None:
            return _complete_from_issues(ctx, "invalid_input", preparation.issues, "preparation_input_missing")
        if not _preparation_matches_checkpoint_or_required_append(
            checkpoint.value.preparation_input,
            preparation.value.input,
            input_model.run_context.run_spec.additional_required_interfaces,
        ):
            return _complete(
                ctx,
                "invalid_input",
                "Current preparation input is not the archived baseline or its exact required-interface append.",
                "root_interface_preparation_baseline_mismatch",
            )
        policy = input_model.run_context.run_spec.root_interface_policy
        if policy == "prepare" and not preparation.value.input.allow_interface_supplement:
            return _complete(
                ctx,
                "invalid_input",
                "root_interface_policy=prepare requires allow_interface_supplement=true.",
                "root_interface_supplement_disabled",
            )
        source_index = _material(ctx).get_committed_source_index(repo_root)
        if not source_index.ok or source_index.value is None:
            return _complete_from_issues(
                ctx,
                "blocked",
                source_index.issues,
                "committed_source_index_required",
            )
        if input_model.source_index_delta.outcome not in {"committed", "no_op"}:
            return _complete(
                ctx,
                "invalid_input",
                "Root-interface preparation requires a successful SourceIndex child result.",
                "root_interface_source_index_result_not_ready",
            )
        if input_model.repo_key != input_model.source_index_delta.repo_key:
            return _complete(
                ctx,
                "invalid_input",
                "SourceIndex child result belongs to a different repository key.",
                "root_interface_source_delta_repo_mismatch",
            )
        if sorted(input_model.source_index_delta.resolved_file_paths) != sorted(
            input_model.run_context.resolved_source_files
        ):
            return _complete(
                ctx,
                "invalid_input",
                "SourceIndex child scope must exactly match the resolved RepoRunContext source scope.",
                "root_interface_source_delta_mismatch",
            )
        current_model = _material(ctx).source_index.get_source_index_model(repo_root)
        if not current_model.ok or current_model.value is None:
            return _complete_from_issues(
                ctx,
                "blocked",
                current_model.issues,
                "committed_source_index_required",
            )
        resolved_paths = input_model.source_index_delta.resolved_file_paths
        if len(resolved_paths) != len(set(resolved_paths)):
            return _complete(
                ctx,
                "invalid_input",
                "SourceIndex child result resolved_file_paths must not contain duplicates.",
                "root_interface_source_delta_duplicate_path",
            )
        missing_paths = sorted(set(resolved_paths) - set(current_model.value.files))
        if missing_paths:
            return _complete(
                ctx,
                "invalid_input",
                "SourceIndex child result contains paths absent from current committed SourceIndex files: "
                + ", ".join(missing_paths),
                "root_interface_source_delta_phantom_path",
            )
        delta_error = _validate_source_index_result_against_truth(
            checkpoint.value.source_index,
            current_model.value,
            input_model.source_index_delta,
        )
        if delta_error is not None:
            return _complete(
                ctx,
                "invalid_input",
                delta_error,
                "root_interface_source_delta_mismatch",
            )
        preview = _repo_workspace(ctx).preview_preparation_interface_append(
            repo_root,
            interfaces=input_model.run_context.run_spec.additional_required_interfaces,
        )
        if not preview.ok or preview.value is None:
            return _complete_from_issues(ctx, "invalid_input", preview.issues, "protected_interface_conflict")
        return ctx.complete_step(
            RootInterfaceFlowStepResult(
                outcome="valid",
                added_names=preview.value.added_names,
                existing_names=preview.value.existing_names,
                summary="Validated root-interface run input and protected interface delta.",
            )
        )


class AppendRequiredPreparationInterfacesStep(BaseStep):
    step_type: ClassVar[str] = "append_required_preparation_interfaces_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RootInterfaceFlowStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "root_interface_flow_step": RootInterfaceFlowStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        input_model = _input(ctx)
        appended = _repo_workspace(ctx).append_preparation_interfaces(
            Path(input_model.repo_root),
            interfaces=input_model.run_context.run_spec.additional_required_interfaces,
        )
        if not appended.ok or appended.value is None:
            return _complete_from_issues(ctx, "blocked", appended.issues, "preparation_interface_append_failed")
        return ctx.complete_step(
            RootInterfaceFlowStepResult(
                outcome="appended",
                added_names=appended.value.added_names,
                existing_names=appended.value.existing_names,
                summary=appended.value.summary,
            )
        )


class SyncProtectedRootInterfacesStep(BaseStep):
    step_type: ClassVar[str] = "sync_protected_root_interfaces_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RootInterfaceFlowStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "root_interface_flow_step": RootInterfaceFlowStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        input_model = _input(ctx)
        synced = _node(ctx).interface.sync_protected_root_interfaces_from_preparation_input(
            Path(input_model.repo_root),
            node_path="Main",
        )
        if not synced.ok or synced.value is None:
            return _complete_from_issues(ctx, "blocked", synced.issues, "protected_interface_sync_failed")
        return ctx.complete_step(
            RootInterfaceFlowStepResult(
                outcome="synced",
                summary="Synchronized protected preparation interfaces into root Main.",
            )
        )


class DecideRootInterfaceAgentStep(BaseStep):
    step_type: ClassVar[str] = "decide_root_interface_agent_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RootInterfaceFlowStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "root_interface_flow_step": RootInterfaceFlowStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        input_model = _input(ctx)
        repo_root = Path(input_model.repo_root)
        preparation = _repo_workspace(ctx).preparation.get_preparation_input(repo_root)
        contract = _node(ctx).contract.get_current_contract(repo_root, node_path="Main")
        if not preparation.ok or preparation.value is None:
            return _complete_from_issues(ctx, "blocked", preparation.issues, "preparation_input_missing")
        if not contract.ok or contract.value is None:
            return _complete_from_issues(ctx, "blocked", contract.issues, "root_contract_missing")
        policy = input_model.run_context.run_spec.root_interface_policy
        allow_supplement = preparation.value.input.allow_interface_supplement
        agent_required = policy == "prepare" or (
            policy == "auto" and allow_supplement and _source_delta_has_changes(input_model.source_index_delta)
        )
        previous_interfaces = {
            interface.name: interface.model_dump(mode="json")
            for interface in contract.value.contract.interfaces
        }
        previous_exports = [ref.model_dump(mode="json") for ref in contract.value.contract.exports]
        return ctx.complete_step(
            RootInterfaceFlowStepResult(
                outcome="decided",
                agent_required=agent_required,
                previous_interfaces=previous_interfaces,
                previous_exports=previous_exports,
                summary=(
                    "RootInterfacePrepareAgent is required for this run."
                    if agent_required
                    else "Protected sync and deterministic gate are sufficient for this run."
                ),
            )
        )


class VerifyRootInterfaceDeltaStep(BaseStep):
    step_type: ClassVar[str] = "verify_root_interface_delta_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RootInterfaceFlowStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "root_interface_flow_step": RootInterfaceFlowStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        flow = _flow(ctx)
        state = flow.state
        input_model = _input(ctx)
        repo_root = Path(input_model.repo_root)
        contract = _node(ctx).contract.get_current_contract(repo_root, node_path="Main")
        if not contract.ok or contract.value is None:
            return _complete_from_issues(ctx, "blocked", contract.issues, "root_contract_missing")
        current = {
            interface.name: interface.model_dump(mode="json")
            for interface in contract.value.contract.interfaces
        }
        for name, payload in state.previous_interfaces.items():
            if current.get(name) != payload:
                return _complete(
                    ctx,
                    "blocked",
                    f"Existing root interface was removed or changed during preparation: {name}",
                    "root_interface_existing_payload_changed",
                )
        current_exports = [ref.model_dump(mode="json") for ref in contract.value.contract.exports]
        if current_exports != state.previous_exports:
            return _complete(
                ctx,
                "blocked",
                "RootInterfacePreparationFlow must not change root Main exports.",
                "root_interface_exports_changed",
            )
        added_names = [name for name in current if name not in state.previous_interfaces]
        for name in added_names:
            if current[name]["bound_decl"] is not None:
                return _complete(
                    ctx,
                    "blocked",
                    f"New root interface was bound during preparation: {name}",
                    "root_interface_bound_during_prepare",
                )
        if added_names and input_model.run_context.run_spec.root_interface_policy == "auto" and not _source_delta_has_changes(input_model.source_index_delta):
            return _complete(
                ctx,
                "blocked",
                "Automatic supplement interfaces require a non-empty SourceIndex delta.",
                "root_interface_source_delta_required",
            )
        return ctx.complete_step(
            RootInterfaceFlowStepResult(
                outcome="verified",
                supplement_names_added=added_names,
                summary=f"Verified root-interface delta; {len(added_names)} supplement interfaces were added.",
            )
        )


class RootInterfaceReadyGateStep(BaseStep):
    step_type: ClassVar[str] = "root_interface_ready_gate_step"
    State: ClassVar[type[BaseStepState]] = BaseStepState
    Result: ClassVar[type[BaseStepResult]] = RootInterfaceFlowStepResult
    Results: ClassVar[dict[str, type[BaseStepResult]]] = {
        "root_interface_flow_step": RootInterfaceFlowStepResult,
    }

    def run(self, ctx: StepRunContext) -> StepTerminalReceipt:
        input_model = _input(ctx)
        ready = _node(ctx).interface.submit_root_interface_prepare_ready(
            Path(input_model.repo_root),
            summary="Root-interface preparation deterministic ready gate passed.",
        )
        if not ready.ok or ready.value is None:
            return _complete_from_issues(ctx, "blocked", ready.issues, "root_interface_ready_gate_failed")
        return ctx.complete_step(
            RootInterfaceFlowStepResult(
                outcome="ready",
                summary=ready.value.gate.summary or "Root interfaces are ready.",
            )
        )


ROOT_INTERFACE_STEP_TYPES: tuple[type[BaseStep], ...] = (
    ValidateRootInterfaceRunStep,
    AppendRequiredPreparationInterfacesStep,
    SyncProtectedRootInterfacesStep,
    DecideRootInterfaceAgentStep,
    VerifyRootInterfaceDeltaStep,
    RootInterfaceReadyGateStep,
)


def new_root_interface_step_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _flow(ctx: StepRunContext):
    flow_service = ctx.ark.flow_service
    if flow_service is None:
        raise FlowStepValidationError("ark.flow_service is not registered")
    flow = flow_service.get_flow(ctx.flow_id)
    if flow.flow_type != "root_interface_preparation":
        raise FlowStepValidationError(
            f"expected root_interface_preparation flow, got {flow.flow_type}"
        )
    return flow


def _input(ctx: StepRunContext):
    from lean_constellation.flows.repo_lifecycle.root_interface import RootInterfacePreparationInput

    value = _flow(ctx).input
    if not isinstance(value, RootInterfacePreparationInput):
        raise FlowStepValidationError("RootInterfacePreparationFlow has invalid input")
    return value


def _repo_workspace(ctx: StepRunContext):
    service = getattr(ctx.app, "repo_workspace", None)
    if service is None:
        raise FlowStepValidationError("Lean repo_workspace service is not registered")
    return service


def _node(ctx: StepRunContext):
    service = getattr(ctx.app, "node", None)
    if service is None:
        raise FlowStepValidationError("Lean node service is not registered")
    return service


def _material(ctx: StepRunContext):
    service = getattr(ctx.app, "material", None)
    if service is None:
        raise FlowStepValidationError("Lean material service is not registered")
    return service


def _source_index_checkpoint(ctx: StepRunContext):
    service = getattr(ctx.app, "source_index_checkpoint", None)
    if service is None:
        raise FlowStepValidationError("Lean source_index_checkpoint adapter is not registered")
    return service


def _preparation_matches_checkpoint_or_required_append(
    baseline: RepoPreparationInput,
    current: RepoPreparationInput,
    required: list[DeclInterface],
) -> bool:
    if baseline.model_dump(mode="json") == current.model_dump(mode="json"):
        return True
    expected = baseline.model_copy(deep=True)
    by_name = {interface.name: interface for interface in expected.interface_inputs}
    for interface in required:
        existing = by_name.get(interface.name)
        if existing is not None:
            if existing.model_dump(mode="json") != interface.model_dump(mode="json"):
                return False
            continue
        copied = interface.model_copy(deep=True)
        expected.interface_inputs.append(copied)
        by_name[copied.name] = copied
    return expected.model_dump(mode="json") == current.model_dump(mode="json")


def _validate_source_index_result_against_truth(
    baseline: SourceIndex | None,
    current: SourceIndex,
    result: object,
) -> str | None:
    if current.status != "committed" or current.active_file_scope:
        return "Root-interface preparation requires current committed SourceIndex truth."

    baseline_blocks = baseline.blocks if baseline is not None else {}
    baseline_links = baseline.links if baseline is not None else {}
    baseline_files = baseline.files if baseline is not None else {}
    baseline_refs = {
        ref.ref_id
        for block in baseline_blocks.values()
        for ref in block.refs
    }
    current_refs = {
        ref.ref_id
        for block in current.blocks.values()
        for ref in block.refs
    }

    if set(baseline_blocks) - set(current.blocks):
        return "Current SourceIndex removed blocks present in the archived baseline."
    if set(baseline_links) - set(current.links):
        return "Current SourceIndex removed links present in the archived baseline."
    if baseline_refs - current_refs:
        return "Current SourceIndex removed refs present in the archived baseline."
    for block_id, old in baseline_blocks.items():
        new = current.blocks[block_id]
        old_scalar = old.model_dump(exclude={"refs", "child_ids", "link_ids", "updated_at"})
        new_scalar = new.model_dump(exclude={"refs", "child_ids", "link_ids", "updated_at"})
        if old_scalar != new_scalar or new.refs != old.refs:
            return f"Current SourceIndex changed archived block semantics: {block_id}."
        if new.child_ids[: len(old.child_ids)] != old.child_ids:
            return f"Current SourceIndex rewrote archived block child adjacency: {block_id}."
        if new.link_ids[: len(old.link_ids)] != old.link_ids:
            return f"Current SourceIndex rewrote archived block link adjacency: {block_id}."
    for link_id, old in baseline_links.items():
        if current.links[link_id] != old:
            return f"Current SourceIndex changed an archived link: {link_id}."
    for path, old in baseline_files.items():
        if not old.committed:
            continue
        new = current.files.get(path)
        if new is None or new != old or not new.committed:
            return f"Current SourceIndex changed an archived committed file: {path}."

    expected_files = sorted(
        path
        for path, file in current.files.items()
        if file.committed and (path not in baseline_files or not baseline_files[path].committed)
    )
    expected_blocks = sorted(set(current.blocks) - set(baseline_blocks) - {current.root_block_id})
    expected_links = sorted(set(current.links) - set(baseline_links))
    expected_refs = sorted(current_refs - baseline_refs)
    supplied = {
        "newly committed files": list(getattr(result, "newly_committed_file_paths", [])),
        "appended blocks": list(getattr(result, "appended_block_ids", [])),
        "appended links": list(getattr(result, "appended_link_ids", [])),
        "appended refs": list(getattr(result, "appended_ref_ids", [])),
    }
    expected = {
        "newly committed files": expected_files,
        "appended blocks": expected_blocks,
        "appended links": expected_links,
        "appended refs": expected_refs,
    }
    for label, expected_values in expected.items():
        if supplied[label] != expected_values:
            return f"SourceIndex child result {label} do not match committed truth."

    outcome = getattr(result, "outcome", None)
    has_delta = any(expected.values())
    if outcome == "no_op":
        if has_delta or baseline is None or current.model_dump(mode="json") != baseline.model_dump(mode="json"):
            return "SourceIndex no_op requires an empty delta and current truth equal to the archived baseline."
    elif outcome != "committed":
        return "Root-interface preparation requires a committed or no_op SourceIndex result."
    return None


def _expected_checkpoint_kind(input_model: object) -> RepoCheckpointKind:
    if (
        getattr(input_model, "invocation_kind", None) == "child"
        and getattr(input_model, "start_reason", None) == "initial"
    ):
        return RepoCheckpointKind.BEFORE_NATIVE_SOURCE_PROCESSING
    return RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION


def _source_delta_has_changes(delta: object) -> bool:
    return bool(
        getattr(delta, "newly_committed_file_paths", [])
        or getattr(delta, "appended_block_ids", [])
        or getattr(delta, "appended_link_ids", [])
        or getattr(delta, "appended_ref_ids", [])
    )


def _complete(
    ctx: StepRunContext,
    outcome: Literal["blocked", "invalid_input"],
    summary: str,
    error_code: str,
) -> StepTerminalReceipt:
    return ctx.complete_step(
        RootInterfaceFlowStepResult(
            outcome=outcome,
            error_code=error_code,
            summary=summary,
        )
    )


def _complete_from_issues(
    ctx: StepRunContext,
    outcome: Literal["blocked", "invalid_input"],
    issues: list[object],
    fallback_code: str,
) -> StepTerminalReceipt:
    first = issues[0] if issues else None
    code = str(getattr(first, "kind", None) or getattr(first, "code", None) or fallback_code)
    summary = "; ".join(str(getattr(issue, "message", issue)) for issue in issues)
    return _complete(ctx, outcome, summary or fallback_code.replace("_", " "), code)


__all__ = [
    "AppendRequiredPreparationInterfacesStep",
    "DecideRootInterfaceAgentStep",
    "ROOT_INTERFACE_STEP_TYPES",
    "RootInterfaceFlowStepResult",
    "RootInterfaceReadyGateStep",
    "SyncProtectedRootInterfacesStep",
    "ValidateRootInterfaceRunStep",
    "VerifyRootInterfaceDeltaStep",
    "new_root_interface_step_id",
]
