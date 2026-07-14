from __future__ import annotations

from pathlib import Path

import pytest
from agent_runtime_kit.flow.contexts import FlowBuildContext
from agent_runtime_kit.flow.models import FlowRequest
from agent_runtime_kit.flow.registry import FlowTypeRegistry
from lean_constellation.app.runtime import ApplicationSnapshotRuntime

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.domain.repo import ProofAvailability, RepoFormat, RepoWorkMode
from lean_constellation.domain.repo_run import RepoRunContext, RepoRunSpec, SourceScope
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import create_fake_lean_flow_runtime
from lean_constellation.flows.repo_lifecycle.root_interface import (
    ROOT_INTERFACE_FLOW_TYPES,
    RootInterfacePreparationFlow,
    RootInterfacePreparationParams,
    RootInterfacePreparationResult,
)
from lean_constellation.flows.repo_lifecycle.root_interface_steps import ROOT_INTERFACE_STEP_TYPES
from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildResult
from lean_constellation.flows.repo_lifecycle.submissions import RootInterfacePrepareReadySubmission
from lean_constellation.services.validation_snapshot import RepoCheckpointKind
from lean_constellation.services.validation_snapshot.source_index_checkpoint import SourceIndexCheckpointAdapter
from lean_constellation.services.material import SourceFileIndex
from tests.unit_services_helpers import make_runtime


class _StableRuntimeProvider:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def check_repo_stable_point(self, repo_root: Path, *, checkpoint_kind, node_paths=None):
        del repo_root, checkpoint_kind, node_paths
        return self.runtime.foundation.ok(
            self.runtime.foundation.gate_passed("test_runtime_stable", summary="Test runtime is stable.")
        )


class _ArkSnapshotProvider:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root, scope_ids, label
        return self.runtime.foundation.ok("ark_root_interface_test")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root, leave_runtime_paused
        return self.runtime.foundation.ok(
            self.runtime.foundation.mutation_view(
                object_ref=snapshot_id,
                changed=True,
                summary="Restored fake ARK snapshot.",
            )
        )


def _register(runtime) -> None:
    for flow_type in ROOT_INTERFACE_FLOW_TYPES:
        if flow_type.flow_type not in runtime.flow_registry.types:
            runtime.flow_registry.register(flow_type)
    for step_type in ROOT_INTERFACE_STEP_TYPES:
        if step_type.step_type not in runtime.step_registry.types:
            runtime.step_registry.register(step_type)


def _flow_runtime(ark_root: Path, lean_runtime):
    runtime = create_fake_lean_flow_runtime(
        ark_root,
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    _register(runtime)
    return runtime


def _prepare_repo(
    repo_root: Path,
    *,
    allow_interface_supplement: bool = True,
    initial_interfaces: list[DeclInterface] | None = None,
    checkpoint_before_index: bool = False,
):
    runtime = make_runtime()
    repo_root.mkdir(parents=True, exist_ok=True)
    assert runtime.repo_workspace.metadata.ensure_repo_model(repo_root).ok
    assert runtime.repo_workspace.metadata.set_repo_format(
        repo_root,
        repo_format=RepoFormat.NATIVE,
        reason="Root-interface flow test fixture.",
    ).ok
    preparation = RepoPreparationInput(
        goal="Formalize the selected source material.",
        source_corpus_mode=SourceCorpusMode.EXISTING,
        source_corpus_relpath=".lean_constellation/source",
        interface_inputs=list(initial_interfaces or []),
        allow_interface_supplement=allow_interface_supplement,
    )
    assert runtime.repo_workspace.write_preparation_input(repo_root, input=preparation).ok
    assert runtime.node.ensure_native_root_main_contract(repo_root).ok
    runtime.app.snapshot_runtime = ApplicationSnapshotRuntime(
        runtime,
        _ArkSnapshotProvider(runtime),
        runtime_stability=_StableRuntimeProvider(runtime),
    )
    runtime.app.source_index_checkpoint = SourceIndexCheckpointAdapter(runtime)
    source_root = repo_root / ".lean_constellation" / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / "source.md").write_text(
        "Source provenance: local test fixture.\n"
        "Reading order: read this file from top to bottom.\n"
        "Main material: Definition A and Theorem B.\n"
        "Known gaps and extraction limits: no known gaps.\n"
        "Definition A.\n"
        "Theorem B.\n",
        encoding="utf-8",
    )
    assert runtime.material.submit_source_corpus_prepared(
        repo_root,
        entry_path="source.md",
        overview="Root-interface test source.",
        preparation_summary="Prepared test source.",
    ).ok
    if checkpoint_before_index:
        _create_checkpoint(runtime, repo_root)
    scope = runtime.material.resolve_source_scope(
        repo_root,
        source_scope=SourceScope(mode="selected", selectors=["source.md"]),
    )
    assert scope.ok and scope.value is not None
    opened = runtime.material.open_source_index_update(
        repo_root,
        resolved_scope=scope.value,
        index_policy="auto",
    )
    assert opened.ok and opened.value is not None
    block = runtime.material.create_source_block(
        repo_root,
        parent_id="root",
        kind="statement",
        title="Theorem B",
        summary="The main source theorem.",
    )
    assert block.ok and block.value is not None
    assert runtime.material.add_source_block_ref(
        repo_root,
        block_id=block.value.block_id,
        path="source.md",
        start_line=5,
        end_line=6,
        role="primary",
    ).ok
    assert runtime.material.mark_block_refs_done(
        repo_root, block_id=block.value.block_id
    ).value.passed
    assert runtime.material.mark_block_links_done(
        repo_root, block_id=block.value.block_id
    ).value.passed
    assert runtime.material.mark_block_completed(
        repo_root, block_id=block.value.block_id
    ).value.passed
    assert runtime.material.set_file_survey_status(
        repo_root,
        path="source.md",
        status="surveyed",
        summary="Surveyed.",
    ).ok
    assert runtime.material.set_file_indexing_status(
        repo_root,
        path="source.md",
        status="indexed",
    ).ok
    gate = runtime.material.validate_source_index_update(
        repo_root,
        baseline_index=None,
        expected_baseline_digest=opened.value.baseline_digest,
        resolved_scope=["source.md"],
        require_completed=True,
    )
    assert gate.ok and gate.value is not None and gate.value.gate.passed
    assert runtime.material.commit_source_index_update(
        repo_root,
        validated=gate.value,
    ).ok
    if not checkpoint_before_index:
        _create_checkpoint(runtime, repo_root)
    return runtime, block.value.block_id


def _create_checkpoint(
    runtime,
    repo_root: Path,
    *,
    checkpoint_id: str = "checkpoint-prepared-by-parent",
    kind: RepoCheckpointKind = RepoCheckpointKind.BEFORE_NATIVE_RUN_MUTATION,
) -> None:
    created = runtime.app.snapshot_runtime.create_repo_stable_point_snapshot_with_id(
        repo_root,
        snapshot_id=checkpoint_id,
        checkpoint_kind=kind,
        label="Root-interface test checkpoint.",
        scope_ids=[f"repo:{repo_root.name}"],
    )
    assert created.ok, created.issues


def _params(
    repo_root: Path,
    *,
    policy: str,
    additional: list[DeclInterface] | None = None,
    with_delta: bool = False,
    block_id: str = "block",
    ref_id: str | None = None,
    checkpoint_id: str = "checkpoint-prepared-by-parent",
) -> dict[str, object]:
    run_spec = RepoRunSpec(
        run_objective="Prepare the public API required in this run.",
        target_proof_availability=ProofAvailability.DECLARED,
        work_mode=RepoWorkMode.DECLARED_INTERFACE,
        source_scope=SourceScope(mode="selected", selectors=["source.md"]),
        index_policy="reuse",
        root_interface_policy=policy,
        additional_required_interfaces=list(additional or []),
    )
    run_context = RepoRunContext(
        start_kind="continuation",
        run_spec=run_spec,
        resolved_source_files=["source.md"],
        source_index_delta_summary="New source delta." if with_delta else "No new source delta.",
    )
    delta = SourceIndexBuildResult(
        outcome="committed" if with_delta else "no_op",
        repo_key=repo_root.name,
        resolved_file_paths=["source.md"],
        newly_committed_file_paths=["source.md"] if with_delta else [],
        appended_block_ids=[block_id] if with_delta else [],
        appended_ref_ids=[ref_id] if with_delta and ref_id is not None else [],
        coverage_summary=run_context.source_index_delta_summary,
        summary=run_context.source_index_delta_summary or "SourceIndex delta.",
    )
    return {
        "repo_key": repo_root.name,
        "repo_root": str(repo_root),
        "run_context": run_context.model_dump(mode="json"),
        "source_index_delta": delta.model_dump(mode="json"),
        "start_reason": "admin_preprocess",
        "pre_run_mutation_checkpoint_id": checkpoint_id,
    }


def _advance_and_run(runtime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _run_to_terminal(runtime, flow_id: str, *, limit: int = 12):
    for _ in range(limit):
        flow = runtime.flow_service.get_flow(flow_id)
        if flow.result is not None or flow.error is not None:
            return flow
        _advance_and_run(runtime, flow_id)
    raise AssertionError("root-interface flow did not terminate")


def test_reuse_standalone_appends_required_interface_without_agent_or_publication_change(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    required = DeclInterface(
        name="Required.result",
        kind=DeclKind.THEOREM,
        summary="The exact required result.",
        expected_statement_lean_code="theorem Required.result : True",
    )
    lean_runtime, block_id = _prepare_repo(repo_root, allow_interface_supplement=True)
    before_publication = lean_runtime.repo_workspace.metadata.get_repo_publication(repo_root).value.publication
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    flow_id = runtime.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="reuse", additional=[required], block_id=block_id),
        scope_id=f"repo:{repo_root.name}",
    )

    flow = _run_to_terminal(runtime, flow_id)

    assert isinstance(flow.result, RootInterfacePreparationResult)
    assert flow.result.outcome == "ready"
    assert flow.result.invocation_kind == "standalone"
    assert flow.result.protected_interface_names_added == ["Required.result"]
    assert runtime.agent_service.start_records == []
    preparation = lean_runtime.repo_workspace.preparation.get_preparation_input(repo_root).value.input
    assert preparation.interface_inputs == [required]
    listed = lean_runtime.node.interface.list_interfaces(repo_root, node_path="Main").value
    assert listed.protected_names == ["Required.result"]
    after_publication = lean_runtime.repo_workspace.metadata.get_repo_publication(repo_root).value.publication
    assert after_publication == before_publication


def test_auto_without_source_delta_is_direct_ready_no_op(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    protected = DeclInterface(name="Existing", kind=DeclKind.DEFINITION, summary="Existing API.")
    lean_runtime, block_id = _prepare_repo(repo_root, initial_interfaces=[protected])
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    flow_id = runtime.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="auto", block_id=block_id),
        scope_id=f"repo:{repo_root.name}",
    )

    flow = _run_to_terminal(runtime, flow_id)

    assert flow.result.outcome == "ready"
    assert flow.result.supplement_interface_names_added == []
    assert runtime.agent_service.start_records == []


def test_prepare_agent_can_only_append_and_flow_reports_delta(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    protected = DeclInterface(name="Required", kind=DeclKind.THEOREM, summary="Required theorem.")
    lean_runtime, block_id = _prepare_repo(
        repo_root,
        initial_interfaces=[protected],
        checkpoint_before_index=True,
    )
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    source_index = lean_runtime.material.source_index.get_source_index_model(repo_root).value
    assert source_index is not None
    ref_id = source_index.blocks[block_id].refs[0].ref_id
    flow_id = runtime.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="prepare", with_delta=True, block_id=block_id, ref_id=ref_id),
        scope_id=f"repo:{repo_root.name}",
    )
    for _ in range(4):
        _advance_and_run(runtime, flow_id)
    agent_step_id = runtime.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    assert runtime.flow_service.get_step(agent_step_id).step_type == "root_interface_prepare_agent_step"
    added = lean_runtime.node.interface.add_interface(
        repo_root,
        node_path="Main",
        name="Supplement",
        kind=DeclKind.DEFINITION,
        summary="A reusable public definition.",
        actor="worker",
    )
    assert added.ok
    runtime.agent_service.queue_submission(
        RootInterfacePrepareReadySubmission(
            submission_id=new_submission_id("root_ready"),
            submission_type="root_interface_prepare_ready",
            tool_name="submit_root_interface_prepare_ready",
            summary="Added one justified supplement.",
        )
    )
    runtime.run_step(agent_step_id)

    flow = _run_to_terminal(runtime, flow_id)

    assert flow.result.outcome == "ready"
    assert flow.result.supplement_interface_names_added == ["Supplement"]
    prompt = runtime.agent_service.start_records[-1].prompt
    assert "must remain byte-for-byte equivalent" in prompt
    assert "source.md" in prompt


def test_agent_change_to_existing_supplement_is_blocked_by_delta_gate(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    lean_runtime, block_id = _prepare_repo(repo_root)
    assert lean_runtime.node.interface.add_interface(
        repo_root,
        node_path="Main",
        name="ExistingSupplement",
        kind=DeclKind.LEMMA,
        summary="Stable supplement meaning.",
        actor="worker",
    ).ok
    checkpoint_id = "checkpoint-with-existing-supplement"
    _create_checkpoint(lean_runtime, repo_root, checkpoint_id=checkpoint_id)
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    flow_id = runtime.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="prepare", block_id=block_id, checkpoint_id=checkpoint_id),
        scope_id=f"repo:{repo_root.name}",
    )
    for _ in range(4):
        _advance_and_run(runtime, flow_id)
    agent_step_id = runtime.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    assert lean_runtime.node.interface.update_interface(
        repo_root,
        node_path="Main",
        name="ExistingSupplement",
        summary="Changed meaning.",
        actor="worker",
    ).ok
    runtime.agent_service.queue_submission(
        RootInterfacePrepareReadySubmission(
            submission_id=new_submission_id("root_ready"),
            submission_type="root_interface_prepare_ready",
            tool_name="submit_root_interface_prepare_ready",
            summary="Ready.",
        )
    )
    runtime.run_step(agent_step_id)

    flow = _run_to_terminal(runtime, flow_id)

    assert flow.result.outcome == "blocked"
    assert "ExistingSupplement" in flow.result.blocked_reason
    restored = lean_runtime.validation_snapshot.restore_repo_checkpoint_snapshot(
        repo_root,
        snapshot_id=checkpoint_id,
    )
    assert restored.ok, restored.issues
    interfaces = lean_runtime.node.interface.list_interfaces(repo_root, node_path="Main").value
    assert interfaces is not None
    restored_supplement = next(item for item in interfaces.interfaces if item.name == "ExistingSupplement")
    assert restored_supplement.summary == "Stable supplement meaning."


def test_prepare_is_invalid_when_supplement_is_disabled_and_does_not_append(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    lean_runtime, block_id = _prepare_repo(repo_root, allow_interface_supplement=False)
    required = DeclInterface(name="NeverAdded", kind=DeclKind.THEOREM, summary="Must not be written.")
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    flow_id = runtime.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="prepare", additional=[required], block_id=block_id),
        scope_id=f"repo:{repo_root.name}",
    )

    flow = _run_to_terminal(runtime, flow_id)

    assert flow.result.outcome == "invalid_input"
    preparation = lean_runtime.repo_workspace.preparation.get_preparation_input(repo_root).value.input
    assert preparation.interface_inputs == []


def test_flow_restarts_from_persisted_state_without_reappending(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    required = DeclInterface(name="Required", kind=DeclKind.THEOREM, summary="Required theorem.")
    lean_runtime, block_id = _prepare_repo(repo_root)
    ark_root = tmp_path / "ark"
    first = _flow_runtime(ark_root, lean_runtime)
    flow_id = first.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="reuse", additional=[required], block_id=block_id),
        scope_id=f"repo:{repo_root.name}",
    )
    _advance_and_run(first, flow_id)
    _advance_and_run(first, flow_id)

    restarted = _flow_runtime(ark_root, lean_runtime)
    flow = _run_to_terminal(restarted, flow_id)

    assert flow.result.outcome == "ready"
    preparation = lean_runtime.repo_workspace.preparation.get_preparation_input(repo_root).value.input
    assert [interface.name for interface in preparation.interface_inputs] == ["Required"]


def test_checkpoint_must_exist_and_have_exact_kind(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    lean_runtime, block_id = _prepare_repo(repo_root)
    _create_checkpoint(
        lean_runtime,
        repo_root,
        checkpoint_id="wrong-kind",
        kind=RepoCheckpointKind.MANUAL_TEST_STABLE_POINT,
    )
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)

    missing_id = runtime.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="reuse", block_id=block_id, checkpoint_id="missing"),
        scope_id=f"repo:{repo_root.name}",
    )
    wrong_kind_id = runtime.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="reuse", block_id=block_id, checkpoint_id="wrong-kind"),
        scope_id=f"repo:{repo_root.name}",
    )

    assert _run_to_terminal(runtime, missing_id).result.outcome == "invalid_input"
    wrong_kind = _run_to_terminal(runtime, wrong_kind_id)
    assert wrong_kind.result.outcome == "invalid_input"
    assert "invocation mode" in wrong_kind.result.blocked_reason


def test_checkpoint_archive_corruption_is_rejected(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    lean_runtime, block_id = _prepare_repo(repo_root)
    listed = lean_runtime.validation_snapshot.list_repo_checkpoint_snapshots(repo_root).value
    snapshot = next(item for item in listed if item.snapshot_id == "checkpoint-prepared-by-parent")
    archived_index = Path(snapshot.root) / "files" / "lean_constellation" / "source_index" / "index.json"
    archived_index.write_text("{}", encoding="utf-8")
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    flow_id = runtime.start_flow(
        "root_interface_preparation",
        _params(repo_root, policy="reuse", block_id=block_id),
        scope_id=f"repo:{repo_root.name}",
    )

    flow = _run_to_terminal(runtime, flow_id)

    assert flow.result.outcome == "invalid_input"
    assert "checksum" in flow.result.blocked_reason or "size" in flow.result.blocked_reason


def test_source_index_result_rejects_wrong_repo_and_phantom_delta(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    lean_runtime, block_id = _prepare_repo(repo_root)
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    wrong_repo = _params(repo_root, policy="reuse", block_id=block_id)
    wrong_repo["source_index_delta"]["repo_key"] = "OtherRepo"
    phantom = _params(repo_root, policy="reuse", block_id=block_id)
    phantom["source_index_delta"].update(
        outcome="committed",
        appended_block_ids=["phantom"],
    )

    wrong_repo_id = runtime.start_flow(
        "root_interface_preparation", wrong_repo, scope_id=f"repo:{repo_root.name}"
    )
    phantom_id = runtime.start_flow(
        "root_interface_preparation", phantom, scope_id=f"repo:{repo_root.name}"
    )

    assert _run_to_terminal(runtime, wrong_repo_id).result.outcome == "invalid_input"
    phantom_flow = _run_to_terminal(runtime, phantom_id)
    assert phantom_flow.result.outcome == "invalid_input"
    assert "blocks" in phantom_flow.result.blocked_reason


@pytest.mark.parametrize(
    ("paths", "expected_reason"),
    [
        (["source.md", "source.md"], "must not contain duplicates"),
        (["source.md", "phantom.md"], "absent from current committed SourceIndex files"),
    ],
)
def test_standalone_rejects_duplicate_or_phantom_resolved_paths(
    tmp_path: Path,
    paths: list[str],
    expected_reason: str,
) -> None:
    repo_root = tmp_path / "Repo"
    lean_runtime, block_id = _prepare_repo(repo_root)
    params = _params(repo_root, policy="reuse", block_id=block_id)
    params["source_index_delta"]["resolved_file_paths"] = paths
    params["run_context"]["resolved_source_files"] = paths
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    flow_id = runtime.start_flow(
        "root_interface_preparation",
        params,
        scope_id=f"repo:{repo_root.name}",
    )

    flow = _run_to_terminal(runtime, flow_id)

    assert flow.result.outcome == "invalid_input"
    assert expected_reason in flow.result.blocked_reason


def test_source_index_result_rejects_uncommitted_file_claim(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    lean_runtime, block_id = _prepare_repo(repo_root)
    current = lean_runtime.material.source_index.get_source_index_model(repo_root).value
    assert current is not None
    current.files["notes.bin"] = SourceFileIndex(
        path="notes.bin",
        source_sha256="0" * 64,
        readable_text=False,
        survey_status="skipped",
        indexing_status="skipped",
        committed=False,
    )
    index_path = repo_root / ".lean_constellation" / "source_index" / "index.json"
    assert lean_runtime.foundation.store.write_json_atomic(index_path, current).ok
    params = _params(repo_root, policy="reuse", block_id=block_id)
    params["source_index_delta"].update(
        outcome="committed",
        newly_committed_file_paths=["notes.bin"],
    )
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    flow_id = runtime.start_flow(
        "root_interface_preparation", params, scope_id=f"repo:{repo_root.name}"
    )

    flow = _run_to_terminal(runtime, flow_id)

    assert flow.result.outcome == "invalid_input"
    assert "newly committed files" in flow.result.blocked_reason


def test_auto_accepts_truthful_empty_committed_update_without_agent(tmp_path: Path) -> None:
    repo_root = tmp_path / "Repo"
    lean_runtime, block_id = _prepare_repo(repo_root)
    scope = lean_runtime.material.resolve_source_scope(
        repo_root,
        source_scope=SourceScope(mode="selected", selectors=["source.md"]),
    )
    assert scope.ok and scope.value is not None
    opened = lean_runtime.material.open_source_index_update(
        repo_root,
        resolved_scope=scope.value,
        index_policy="update",
    )
    assert opened.ok and opened.value is not None and opened.value.outcome == "opened"
    baseline = lean_runtime.app.source_index_checkpoint.load_source_index_baseline(
        repo_root,
        checkpoint_id="checkpoint-prepared-by-parent",
    )
    assert baseline.ok and baseline.value is not None
    validated = lean_runtime.material.validate_source_index_update(
        repo_root,
        baseline_index=baseline.value,
        expected_baseline_digest=opened.value.baseline_digest,
        resolved_scope=["source.md"],
        require_completed=True,
    )
    assert validated.ok and validated.value is not None and validated.value.gate.passed
    committed = lean_runtime.material.commit_source_index_update(
        repo_root,
        validated=validated.value,
    )
    assert committed.ok and committed.value is not None
    assert committed.value.newly_committed_file_paths == []
    assert committed.value.appended_block_ids == []
    params = _params(repo_root, policy="auto", block_id=block_id)
    params["source_index_delta"]["outcome"] = "committed"
    runtime = _flow_runtime(tmp_path / "ark", lean_runtime)
    flow_id = runtime.start_flow(
        "root_interface_preparation",
        params,
        scope_id=f"repo:{repo_root.name}",
    )

    flow = _run_to_terminal(runtime, flow_id)

    assert flow.result.outcome == "ready"
    assert runtime.agent_service.start_records == []


def test_build_from_parent_context_marks_child_invocation() -> None:
    lean_runtime = make_runtime()
    request = FlowRequest(
        flow_type="root_interface_preparation",
        scope_id="repo:Repo",
        params=_params(Path("/tmp/Repo"), policy="reuse"),
    )
    params = RootInterfacePreparationParams.model_validate(request.params)
    context = FlowBuildContext(
        ark=lean_runtime.ark,
        app=lean_runtime.app,
        request=request,
        params=params,
        flow_id="child-flow",
        scope_id="repo:Repo",
        parent_flow_id="parent-flow",
        parent_dispatch_step_id="dispatch-step",
    )

    flow = RootInterfacePreparationFlow.build_from_request(context)

    assert flow.input.invocation_kind == "child"
    assert flow.parent_flow_id == "parent-flow"
    registry = FlowTypeRegistry()
    registry.register(RootInterfacePreparationFlow)
    dumped = flow.model_dump(mode="json")
    restored_input = registry.parse_input(flow.flow_type, dumped["input"])
    restored_state = registry.parse_state(flow.flow_type, dumped["state"])
    assert restored_input.invocation_kind == "child"
    assert restored_input.run_context == flow.input.run_context
    assert restored_state == flow.state
