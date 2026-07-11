from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowPosition, FlowStatus

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.repo_lifecycle.submissions import (
    RootInterfacePrepareReadySubmission,
    SourceCorpusPreparedSubmission,
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)
from lean_constellation.services import LeanProviderOverrides
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
from lean_constellation.services.foundation import GateReport, ServiceResult
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
    return runtime.start_flow(
        "native_repo_preparation",
        {
            "repo_key": repo_root.name,
            "repo_root": str(repo_root),
            "start_reason": "bootstrap",
        },
        scope_id=f"repo:{repo_root.name}",
    )


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _complete_minimal_source_index(lean_runtime, repo_root: Path) -> None:
    material = lean_runtime.material
    overview = material.set_source_index_overview(repo_root, overview="A compactness theorem and proof outline.")
    assert overview.ok
    block = material.create_source_block(
        repo_root,
        parent_id="root",
        kind="statement",
        title="Compactness theorem",
        summary="Main theorem about finite subcovers.",
    )
    assert block.ok
    ref = material.add_source_block_ref(
        repo_root,
        block_id=block.value.block_id,
        path="README.md",
        start_line=1,
        end_line=3,
        role="main",
    )
    assert ref.ok
    assert material.mark_block_refs_done(repo_root, block_id=block.value.block_id).value.passed
    assert material.mark_block_links_done(repo_root, block_id=block.value.block_id).value.passed
    assert material.mark_block_completed(repo_root, block_id=block.value.block_id).value.passed
    assert material.set_file_survey_status(repo_root, path="README.md", status="surveyed", summary="Read.").ok
    assert material.set_file_indexing_status(repo_root, path="README.md", status="indexed").ok


def _run_to_builder(runtime: FakeLeanFlowRuntime, lean_runtime, flow_id: str, repo_root: Path) -> None:
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "source_corpus"
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "source_index_create"
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "source_index_builder"
    _complete_minimal_source_index(lean_runtime, repo_root)


def test_native_preparation_existing_source_handoff_dispatches_coordinator(tmp_path: Path) -> None:
    runtime, lean_runtime, ark_snapshot = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=False)
    flow_id = _start_native(runtime, repo_root)

    _run_to_builder(runtime, lean_runtime, flow_id, repo_root)
    assert ark_snapshot.created[0][1] == ["repo:Provider"]
    assert ark_snapshot.created[0][2] == "before native source processing for Provider"
    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_builder_round",
            tool_name="submit_source_index_builder_round",
            summary="Builder round ready.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "source_index_reviewer"

    runtime.agent_service.queue_submission(
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_reviewer_round",
            tool_name="submit_source_index_review_round",
            approved=True,
            summary="Approved.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "source_index_commit"

    _advance_and_run(runtime, flow_id)
    assert lean_runtime.material.get_source_index(repo_root).value.status == "committed"
    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    assert len(ark_snapshot.created) == 2
    assert ark_snapshot.created[1][1] == ["repo:Provider"]
    assert ark_snapshot.created[1][2] == "before native coordinator dispatch for Provider"
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "handoff_dispatched"
    coordinator_flows = runtime.flow_service.list_flows(flow_type="native_repo_coordinator")
    assert len(coordinator_flows) == 1
    assert coordinator_flows[0].input.start_mode == "native_preparation_handoff"


def test_native_preparation_interface_supplement_uses_root_interface_agent(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=True)
    flow_id = _start_native(runtime, repo_root)

    _run_to_builder(runtime, lean_runtime, flow_id, repo_root)
    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_builder_round",
            tool_name="submit_source_index_builder_round",
            summary="Builder round ready.",
        )
    )
    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_reviewer_round",
            tool_name="submit_source_index_review_round",
            approved=True,
            summary="Approved.",
        )
    )
    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "root_interface_prepare"
    root_step_id = runtime.flow_service.advance_flow(flow_id)
    assert root_step_id is not None
    root_step = runtime.flow_service.get_step(root_step_id)
    assert root_step.step_type == "root_interface_prepare_agent_step"

    runtime.agent_service.queue_submission(
        RootInterfacePrepareReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="root_interface_prepare_ready",
            tool_name="submit_root_interface_prepare_ready",
            summary="Root interfaces are ready.",
        )
    )
    runtime.run_step(root_step_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.root_interface_ready is True
    assert flow.state.position.phase == "handoff_gate"


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


def test_source_index_review_loop_prompts_include_builder_summary_and_feedback(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root)
    flow_id = _start_native(runtime, repo_root)

    _run_to_builder(runtime, lean_runtime, flow_id, repo_root)
    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_builder_round",
            tool_name="submit_source_index_builder_round",
            summary="Indexed compactness theorem and proof outline.",
        )
    )
    _advance_and_run(runtime, flow_id)

    reviewer_step_id = runtime.flow_service.advance_flow(flow_id)
    assert reviewer_step_id is not None
    reviewer_step = runtime.flow_service.get_step(reviewer_step_id)
    assert "Latest builder summary:" in reviewer_step.state.prompt_override
    assert "Indexed compactness theorem and proof outline." in reviewer_step.state.prompt_override

    runtime.agent_service.queue_submission(
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_reviewer_round",
            tool_name="submit_source_index_review_round",
            approved=False,
            feedback="Add a separate block for the finite subcover proof.",
            summary="Rejected.",
        )
    )
    runtime.run_step(reviewer_step_id)

    retry_step_id = runtime.flow_service.advance_flow(flow_id)
    assert retry_step_id is not None
    retry_step = runtime.flow_service.get_step(retry_step_id)
    assert "Previous reviewer feedback:" in retry_step.state.prompt_override
    assert "Add a separate block for the finite subcover proof." in retry_step.state.prompt_override


def test_commit_source_index_requires_reviewer_approval(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root)
    flow_id = _start_native(runtime, repo_root)

    _run_to_builder(runtime, lean_runtime, flow_id, repo_root)
    def force_commit_without_review(flow) -> None:  # noqa: ANN001
        flow.state.position = FlowPosition(phase="source_index_commit", round_index=flow.state.source_index_round)
        flow.state.last_source_index_review_approved = False

    runtime.flow_service.store.update_flow_record(flow_id, force_commit_without_review)

    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)

    step = runtime.flow_service.get_step(step_id)
    assert step.result.outcome == "blocked"
    assert step.result.error.code == "source_index_review_approval_required"
    assert lean_runtime.material.get_source_index(repo_root).value.status == "draft"


def test_native_preparation_rejected_review_reuses_builder_agent(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "Provider"
    _prepare_native_repo(lean_runtime, repo_root, allow_interface_supplement=False)
    flow_id = _start_native(runtime, repo_root)

    _run_to_builder(runtime, lean_runtime, flow_id, repo_root)
    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_builder_round",
            tool_name="submit_source_index_builder_round",
            summary="Round one.",
        )
    )
    _advance_and_run(runtime, flow_id)
    runtime.agent_service.queue_submission(
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_reviewer_round",
            tool_name="submit_source_index_review_round",
            approved=False,
            feedback="Add clearer proof coverage.",
            summary="Rejected.",
        )
    )
    _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "source_index_builder"
    assert flow.state.source_index_round == 2

    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="source_index_builder_round",
            tool_name="submit_source_index_builder_round",
            summary="Round two.",
        )
    )
    _advance_and_run(runtime, flow_id)

    builder_records = [
        record
        for record in runtime.agent_service.start_records
        if record.env["LEAN_CONSTELLATION_AGENT_TYPE"] == "SourceIndexBuilderAgent"
    ]
    assert len(builder_records) == 2
    assert builder_records[0].agent_id == builder_records[1].agent_id
    assert runtime.flow_service.get_flow(flow_id).state.position.round_index == 2
