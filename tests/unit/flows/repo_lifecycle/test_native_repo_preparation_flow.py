from __future__ import annotations

from pathlib import Path

import pytest
from agent_runtime_kit.flow.models import BaseFlowError, FlowStatus

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.repo_lifecycle.submissions import (
    SourceCorpusPreparedSubmission,
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)
from lean_constellation.flows.repo_lifecycle.root_interface import RootInterfacePreparationResult
from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildResult
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
from lean_constellation.services.foundation import GateReport, ServiceResult
from lean_constellation.services.validation_snapshot.source_index_checkpoint import SourceIndexCheckpointAdapter
from tests.unit_services_helpers import make_runtime


class FakeLakeClient:
    def __init__(self) -> None:
        self.built: list[tuple[Path, str | None]] = []

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        self.built.append((Path(repo_root), target))
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build"] + ([target] if target else []),
            cwd=str(repo_root),
            exit_code=0,
            summary="lake build ok",
        )

    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        return ExternalCommandResult(
            ok=True,
            command=["lake", "update"],
            cwd=str(repo_root),
            exit_code=0,
            summary="lake update ok",
        )

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        return LeanCheckSummaryView(
            ok=True,
            module=module,
            command=["lean"],
            summary=f"import {module} ok",
        )

    def summarize_command_result(self, result: ExternalCommandResult):
        from lean_constellation.services.external_clients import LakeCommandSummaryView

        return LakeCommandSummaryView(
            ok=result.ok,
            command=result.command,
            summary=result.summary or "",
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stderr_excerpt=result.stderr_excerpt,
        )


class FakeRuntimeStabilityProvider:
    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: object,
        node_paths: list[str],
    ) -> ServiceResult[GateReport]:
        del repo_root, node_paths
        kind = getattr(checkpoint_kind, "value", str(checkpoint_kind))
        return ServiceResult(ok=True, value=GateReport(gate_name=f"{kind}_stable_point", passed=True, summary="runtime stable"))


class FakeArkSnapshotProvider:
    def __init__(self) -> None:
        self.created: list[tuple[Path, list[str], str | None]] = []

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None) -> ServiceResult[str]:
        self.created.append((Path(repo_root), list(scope_ids), label))
        return ServiceResult(ok=True, value=f"ark_snapshot_{len(self.created)}")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str) -> ServiceResult[str]:
        del repo_root
        return ServiceResult(ok=True, value=snapshot_id)


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, object, FakeArkSnapshotProvider]:
    lake = FakeLakeClient()
    ark_snapshot = FakeArkSnapshotProvider()
    lean_runtime = make_runtime(
        external_overrides={"lake": lake},
        providers=LeanProviderOverrides(
            runtime_stability_provider=FakeRuntimeStabilityProvider(),
            ark_snapshot_provider=ark_snapshot,
        ),
    )
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    lean_runtime.app.source_index_checkpoint = SourceIndexCheckpointAdapter(lean_runtime)
    return flow_runtime, lean_runtime, ark_snapshot


def _prepare_native_repo(
    lean_runtime,
    repo_root: Path,
    *,
    allow_interface_supplement: bool = False,
) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    written = lean_runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Formalize the topology facts in the source corpus.",
            source_corpus_mode=SourceCorpusMode.EXISTING,
            source_corpus_relpath=".lean_constellation/source",
            interface_inputs=[
                DeclInterface(
                    name="TopologyFact",
                    kind=DeclKind.THEOREM,
                    summary="Expose the main topology fact.",
                )
            ],
            allow_interface_supplement=allow_interface_supplement,
        ),
    )
    assert written.ok
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text(
        "Topology facts\n"
        "Source provenance: local source corpus fixture.\n"
        "Reading order: start with this entry and then use it as the main material.\n"
        "Main material: compactness facts using open covers and finite subcovers.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    initialized = lean_runtime.repo_workspace.initialize_repo_as_native(repo_root, project_name=repo_root.name)
    assert initialized.ok


def _prepare_native_repo_for_source_prepare(lean_runtime, repo_root: Path, *, source_corpus_relpath: str) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    written = lean_runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Prepare source corpus in a custom root.",
            source_corpus_mode=SourceCorpusMode.PREPARE,
            source_corpus_relpath=source_corpus_relpath,
            interface_inputs=[],
        ),
    )
    assert written.ok
    initialized = lean_runtime.repo_workspace.initialize_repo_as_native(repo_root, project_name=repo_root.name)
    assert initialized.ok


def _start_native(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    flow_id = runtime.start_flow(
        "native_repo_preparation",
        {
            "repo_key": repo_root.name,
            "repo_root": str(repo_root),
            "start_reason": "bootstrap",
            "run_spec": _initial_run_spec(repo_root),
        },
        scope_id=f"repo:{repo_root.name}",
    )
    # Preserve explicit coverage for historical serialized inline flows. New
    # production builds keep the default reusable-child mode.
    runtime.flow_service.store.update_flow_record(
        flow_id,
        lambda flow: setattr(flow.state, "use_reusable_preparation_children", False),
    )
    return flow_id


def _start_native_with_children(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    return runtime.start_flow(
        "native_repo_preparation",
        {
            "repo_key": repo_root.name,
            "repo_root": str(repo_root),
            "start_reason": "bootstrap",
            "run_spec": _initial_run_spec(repo_root),
        },
        scope_id=f"repo:{repo_root.name}",
    )


def _initial_run_spec(repo_root: Path) -> dict[str, object]:
    return {
        "run_objective": f"Prepare {repo_root.name}.",
        "target_proof_availability": "proved",
        "work_mode": "proved_full_graph",
        "source_scope": {"mode": "all", "selectors": []},
        "index_policy": "auto",
        "root_interface_policy": "auto",
        "additional_required_interfaces": [],
    }


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _complete_child_flow(runtime: FakeLeanFlowRuntime, child_flow_id: str, result) -> None:  # noqa: ANN001
    runtime.flow_service.store.update_flow_record(
        child_flow_id,
        lambda flow: (
            setattr(flow, "result", result),
            setattr(flow, "status", FlowStatus.COMPLETED),
            setattr(flow, "current_step_id", None),
        ),
    )


def _run_to_source_child_waiting(
    runtime: FakeLeanFlowRuntime,
    repo_root: Path,
) -> tuple[str, str]:
    flow_id = _start_native_with_children(runtime, repo_root)
    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    children = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=dispatch_step_id,
    )
    assert len(children) == 1
    assert runtime.flow_service.get_flow(flow_id).status is FlowStatus.WAITING
    return flow_id, children[0].flow_id


def test_historical_inline_source_index_state_is_read_only_without_owned_flow(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=False)
    flow_id = _start_native(runtime, repo_root)
    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "source_index_builder"
    mutation = lean_runtime.material.set_source_index_overview(
        repo_root, overview="Historical inline mutation must not resume."
    )
    commit = lean_runtime.material.commit_source_index(repo_root)
    assert not mutation.ok and mutation.issues[0].kind == "source_index_update_context_required"
    assert not commit.ok and commit.issues[0].kind == "source_index_update_context_required"


def test_fresh_native_preparation_dispatches_reusable_children_and_resumes_after_restart(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=False)
    flow_id = _start_native_with_children(runtime, repo_root)

    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.pre_run_mutation_checkpoint_id is not None
    assert flow.state.position.phase == "source_corpus"
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "prepare_source_index_child"

    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "dispatch_preparation_child"
    source_dispatch_step_id = _advance_and_run(runtime, flow_id)
    parent = runtime.flow_service.get_flow(flow_id)
    assert parent.status is FlowStatus.WAITING
    assert parent.state.position.phase == "waiting_source_index_child"
    source_children = runtime.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=source_dispatch_step_id,
    )
    assert len(source_children) == 1
    assert source_children[0].flow_type == "source_index_build"
    assert source_children[0].input.pre_update_checkpoint_id == parent.state.pre_run_mutation_checkpoint_id

    _complete_child_flow(
        runtime,
        source_children[0].flow_id,
        SourceIndexBuildResult(
            outcome="committed",
            repo_key="Provider",
            resolved_file_paths=["README.md"],
            newly_committed_file_paths=["README.md"],
            coverage_summary="Indexed the selected source scope.",
            summary="SourceIndex child committed.",
        ),
    )
    restarted = create_fake_lean_flow_runtime(
        runtime.root,
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    root_prepare_step_id = restarted.flow_service.advance_flow(flow_id)
    assert root_prepare_step_id is not None
    restarted.run_step(root_prepare_step_id)
    assert restarted.flow_service.get_flow(flow_id).state.position.phase == "dispatch_preparation_child"

    root_dispatch_step_id = _advance_and_run(restarted, flow_id)
    parent = restarted.flow_service.get_flow(flow_id)
    assert parent.status is FlowStatus.WAITING
    assert parent.state.position.phase == "waiting_root_interface_child"
    root_children = restarted.flow_service.store.list_child_flows(
        parent_flow_id=flow_id,
        parent_dispatch_step_id=root_dispatch_step_id,
    )
    assert len(root_children) == 1
    assert root_children[0].flow_type == "root_interface_preparation"
    assert root_children[0].input.source_index_delta.summary == "SourceIndex child committed."

    _complete_child_flow(
        restarted,
        root_children[0].flow_id,
        RootInterfacePreparationResult(
            outcome="ready",
            repo_key="Provider",
            invocation_kind="child",
            summary="Root interfaces are ready.",
        ),
    )
    restarted_again = create_fake_lean_flow_runtime(
        runtime.root,
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    handoff_gate_step_id = restarted_again.flow_service.advance_flow(flow_id)
    assert handoff_gate_step_id is not None
    parent = restarted_again.flow_service.get_flow(flow_id)
    assert parent.status is FlowStatus.RUNNING
    assert parent.state.position.phase == "handoff_gate"
    assert parent.state.source_index_child_result.outcome == "committed"
    assert parent.state.root_interface_child_result.outcome == "ready"


def test_fresh_native_parent_runs_real_source_child_into_real_root_validation(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=False)
    parent_id, source_child_id = _run_to_source_child_waiting(runtime, repo_root)
    parent = runtime.flow_service.get_flow(parent_id)
    checkpoints = lean_runtime.validation_snapshot.list_repo_checkpoint_snapshots(repo_root).value
    checkpoint = next(item for item in checkpoints if item.snapshot_id == parent.state.pre_run_mutation_checkpoint_id)
    assert checkpoint.checkpoint_kind.value == "before_native_source_processing"

    for _ in range(4):
        _advance_and_run(runtime, source_child_id)
    source_flow = runtime.flow_service.get_flow(source_child_id)
    assert source_flow.state.position.phase == "builder"
    update_id = source_flow.state.active_update_id
    assert lean_runtime.material.set_source_index_overview(
        repo_root,
        overview="Topology source index.",
        expected_update_id=update_id,
    ).ok
    block = lean_runtime.material.create_source_block(
        repo_root,
        parent_id="root",
        kind="statement",
        title="Topology fact",
        summary="The selected topology fact.",
        expected_update_id=update_id,
    )
    assert block.ok and block.value is not None
    assert lean_runtime.material.add_source_block_ref(
        repo_root,
        block_id=block.value.block_id,
        path="README.md",
        start_line=1,
        end_line=5,
        role="primary",
        expected_update_id=update_id,
    ).ok
    assert lean_runtime.material.mark_block_refs_done(
        repo_root,
        block_id=block.value.block_id,
        expected_update_id=update_id,
    ).value.passed
    assert lean_runtime.material.mark_block_links_done(
        repo_root,
        block_id=block.value.block_id,
        expected_update_id=update_id,
    ).value.passed
    assert lean_runtime.material.mark_block_completed(
        repo_root,
        block_id=block.value.block_id,
        expected_update_id=update_id,
    ).value.passed
    assert lean_runtime.material.set_file_survey_status(
        repo_root,
        path="README.md",
        status="surveyed",
        summary="Surveyed.",
        expected_update_id=update_id,
    ).ok
    assert lean_runtime.material.set_file_indexing_status(
        repo_root,
        path="README.md",
        status="indexed",
        expected_update_id=update_id,
    ).ok
    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("builder"),
            tool_name="submit_source_index_builder_round",
            summary="Builder completed the selected scope.",
            validation_summary="Scoped draft completed.",
        )
    )
    _advance_and_run(runtime, source_child_id)
    runtime.agent_service.queue_submission(
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("reviewer"),
            tool_name="submit_source_index_review_round",
            summary="Reviewer approved the selected scope.",
            approved=True,
        )
    )
    _advance_and_run(runtime, source_child_id)
    _advance_and_run(runtime, source_child_id)
    source_flow = runtime.flow_service.get_flow(source_child_id)
    assert source_flow.status is FlowStatus.COMPLETED
    assert source_flow.result.outcome == "committed"

    _advance_and_run(runtime, parent_id)
    root_dispatch_step_id = _advance_and_run(runtime, parent_id)
    root_children = runtime.flow_service.store.list_child_flows(
        parent_flow_id=parent_id,
        parent_dispatch_step_id=root_dispatch_step_id,
    )
    assert len(root_children) == 1
    root_child = root_children[0]
    assert root_child.input.start_reason == "initial"
    assert root_child.input.invocation_kind == "child"

    root_validate_step_id = _advance_and_run(runtime, root_child.flow_id)
    root_validate = runtime.flow_service.get_step(root_validate_step_id)
    assert root_validate.result.outcome == "valid"


@pytest.mark.parametrize("child_outcome", ["blocked", "invalid_input"])
def test_native_source_child_business_terminal_completes_parent_without_next_step(
    tmp_path: Path,
    child_outcome: str,
) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=False)
    flow_id, child_flow_id = _run_to_source_child_waiting(runtime, repo_root)
    _complete_child_flow(
        runtime,
        child_flow_id,
        SourceIndexBuildResult(
            outcome=child_outcome,
            repo_key="Provider",
            reason=f"child {child_outcome}",
            summary=f"child {child_outcome}",
        ),
    )
    restarted = create_fake_lean_flow_runtime(
        runtime.root,
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    assert restarted.flow_service.prepare_flow_for_advance(flow_id)
    parent = restarted.flow_service.get_flow(flow_id)
    assert parent.status is FlowStatus.COMPLETED
    assert parent.result.outcome == child_outcome
    assert parent.result.blocked_reason == f"child {child_outcome}"
    assert not restarted.flow_service.can_advance_flow(flow_id)


def test_native_source_child_runtime_failure_fails_parent_without_next_step(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=False)
    flow_id, child_flow_id = _run_to_source_child_waiting(runtime, repo_root)

    def fail_child(flow) -> None:  # noqa: ANN001
        flow.error = BaseFlowError(error_type="child_runtime_failed", message="child runtime exploded")
        flow.status = FlowStatus.FAILED
        flow.current_step_id = None

    runtime.flow_service.store.update_flow_record(child_flow_id, fail_child)
    restarted = create_fake_lean_flow_runtime(
        runtime.root,
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    assert restarted.flow_service.prepare_flow_for_advance(flow_id)
    parent = restarted.flow_service.get_flow(flow_id)
    assert parent.status is FlowStatus.FAILED
    assert parent.result is None
    assert parent.error.error_type == "native_preparation_child_failed"
    assert parent.error.message == "child runtime exploded"
    assert parent.error.details["child_error_type"] == "child_runtime_failed"
    assert not restarted.flow_service.can_advance_flow(flow_id)


def test_native_preparation_source_prepare_workdir_uses_preparation_relpath(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo_for_source_prepare(lean_runtime, repo_root, source_corpus_relpath="custom_sources")
    flow_id = _start_native(runtime, repo_root)

    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "source_corpus"
    _advance_and_run(runtime, flow_id)

    assert runtime.agent_service.start_records[-1].workdir == str(repo_root / "custom_sources")


def test_native_preparation_source_prepare_accepted_submission_finalizes_manifest(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo_for_source_prepare(lean_runtime, repo_root, source_corpus_relpath="custom_sources")
    flow_id = _start_native(runtime, repo_root)

    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "source_corpus"
    source_root = repo_root / "custom_sources"
    source_root.mkdir(parents=True)
    (source_root / "README.md").write_text(
        "Prepared source corpus\n"
        "Source provenance: local prepared source fixture.\n"
        "Reading order: this README is the entry and main material.\n"
        "Main material: compactness facts for downstream indexing.\n"
        "Known gaps and extraction limits: no missing source sections are known.\n",
        encoding="utf-8",
    )
    runtime.agent_service.queue_submission(
        SourceCorpusPreparedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_corpus_prepared",
            tool_name="submit_source_corpus_prepared",
            repo_key=repo_root.name,
            relpath="custom_sources",
            entry_path="README.md",
            overview="Prepared custom source corpus.",
            preparation_summary="Prepared source corpus in custom root.",
            summary="Source corpus ready.",
        )
    )

    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "source_index_create"
    assert flow.state.source_corpus_ready is True
    manifest = lean_runtime.material.source_corpus.get_source_corpus_manifest(repo_root)
    assert manifest.ok and manifest.value is not None
    assert manifest.value.relpath == "custom_sources"
    assert manifest.value.entry_path == "README.md"
