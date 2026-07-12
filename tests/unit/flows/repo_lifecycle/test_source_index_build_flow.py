from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from agent_runtime_kit.flow.contexts import StableStepTerminalContext
from agent_runtime_kit.flow.models import FlowPosition, FlowRequest, FlowStatus

from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.repo_lifecycle.source_index import SourceIndexBuildFlow, SourceIndexBuildResult
from lean_constellation.flows.repo_lifecycle.source_index_steps import (
    SOURCE_INDEX_BUILD_STEP_TYPES,
    SourceIndexBaselineCheckpointView,
)
from lean_constellation.flows.repo_lifecycle.submissions import (
    SourceIndexBuilderRoundSubmission,
    SourceIndexReviewerRoundSubmission,
)
from lean_constellation.services.foundation import ServiceResult
from tests.unit_services_helpers import make_runtime


class FakeSourceIndexCheckpointAdapter:
    def __init__(self, material) -> None:  # noqa: ANN001
        self.material = material
        self.baselines: dict[str, object | None] = {}
        self.records: list[tuple[str, list[str]]] = []
        self.materialize_failure: str | None = None

    def materialize_source_index_baseline_checkpoint(
        self,
        repo_root: Path,
        *,
        checkpoint_id: str,
        scope_ids: list[str],
        label: str,
    ) -> ServiceResult[SourceIndexBaselineCheckpointView]:
        del label
        if self.materialize_failure == "raise":
            raise RuntimeError("checkpoint backend exploded")
        if self.materialize_failure == "result":
            return ServiceResult(
                ok=False,
                issues=[
                    self.material.runtime.foundation.issue(
                        "checkpoint_backend_failed",
                        "checkpoint backend returned failure",
                    )
                ],
            )
        if checkpoint_id in self.baselines:
            return self.validate_source_index_baseline_checkpoint(repo_root, checkpoint_id=checkpoint_id)
        loaded = self.material.source_index.get_source_index_model(repo_root)
        baseline = deepcopy(loaded.value) if loaded.ok else None
        self.baselines[checkpoint_id] = baseline
        self.records.append((checkpoint_id, list(scope_ids)))
        digest = (
            self.material.source_index.missing_source_index_digest()
            if baseline is None
            else self.material.source_index.canonical_source_index_digest(baseline)
        )
        return ServiceResult(
            ok=True,
            value=SourceIndexBaselineCheckpointView(
                checkpoint_id=checkpoint_id,
                repo_root=str(repo_root),
                baseline_digest=digest,
                summary="Fake SourceIndex baseline checkpoint materialized.",
            ),
        )

    def validate_source_index_baseline_checkpoint(
        self, repo_root: Path, *, checkpoint_id: str
    ) -> ServiceResult[SourceIndexBaselineCheckpointView]:
        if checkpoint_id not in self.baselines:
            return ServiceResult(ok=False, issues=[])
        baseline = self.baselines[checkpoint_id]
        digest = (
            self.material.source_index.missing_source_index_digest()
            if baseline is None
            else self.material.source_index.canonical_source_index_digest(baseline)
        )
        return ServiceResult(
            ok=True,
            value=SourceIndexBaselineCheckpointView(
                checkpoint_id=checkpoint_id,
                repo_root=str(repo_root),
                baseline_digest=digest,
                summary="Fake SourceIndex baseline checkpoint validated.",
            ),
        )

    def load_source_index_baseline(self, repo_root: Path, *, checkpoint_id: str):  # noqa: ANN201
        del repo_root
        if checkpoint_id not in self.baselines:
            return ServiceResult(ok=False, issues=[])
        return ServiceResult(ok=True, value=deepcopy(self.baselines[checkpoint_id]))


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, object, FakeSourceIndexCheckpointAdapter]:
    lean_runtime = make_runtime()
    checkpoint = FakeSourceIndexCheckpointAdapter(lean_runtime.material)
    lean_runtime.app.source_index_checkpoint = checkpoint
    runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    if SourceIndexBuildFlow.flow_type not in runtime.flow_registry.types:
        runtime.flow_registry.register(SourceIndexBuildFlow)
    for step_type in SOURCE_INDEX_BUILD_STEP_TYPES:
        if step_type.step_type not in runtime.step_registry.types:
            runtime.step_registry.register(step_type)
    return runtime, lean_runtime, checkpoint


def _prepare_source(lean_runtime, repo_root: Path) -> None:  # noqa: ANN001
    source = repo_root / ".lean_constellation" / "source"
    source.mkdir(parents=True)
    (source / "README.md").write_text(
        "# Fixture\n\nSource provenance: local.\nReading order: read chapter.\n"
        "Main material: chapter.\nKnown gaps and extraction limits: none.\n",
        encoding="utf-8",
    )
    (source / "chapter.md").write_text("Definition A.\nTheorem B.\n", encoding="utf-8")
    prepared = lean_runtime.material.submit_source_corpus_prepared(
        repo_root,
        entry_path="README.md",
        overview="Scoped fixture.",
        preparation_summary="Prepared.",
    )
    assert prepared.ok, prepared.issues


def _params(repo_root: Path, **updates: object) -> dict[str, object]:
    params: dict[str, object] = {
        "repo_key": repo_root.name,
        "repo_root": str(repo_root),
        "run_objective": "Index the selected source chapter.",
        "target_proof_availability": "declared",
        "work_mode": "declared_interface",
        "source_scope": {"mode": "selected", "selectors": ["chapter.md"]},
        "index_policy": "auto",
        "start_reason": "admin_preprocess",
        "max_review_rounds": 2,
    }
    params.update(updates)
    return params


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _advance_to_builder(runtime: FakeLeanFlowRuntime, flow_id: str) -> None:
    for _ in range(4):
        _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "builder"


def _complete_active_draft(lean_runtime, repo_root: Path, update_id: str) -> None:  # noqa: ANN001
    material = lean_runtime.material
    assert material.set_source_index_overview(
        repo_root, overview="Scoped SourceIndex.", expected_update_id=update_id
    ).ok
    created = material.create_source_block(
        repo_root,
        parent_id="root",
        kind="statement",
        title="Theorem B",
        summary="The selected theorem.",
        expected_update_id=update_id,
    )
    assert created.ok and created.value is not None
    block_id = created.value.block_id
    assert material.add_source_block_ref(
        repo_root,
        block_id=block_id,
        path="chapter.md",
        start_line=1,
        end_line=2,
        role="primary",
        expected_update_id=update_id,
    ).ok
    assert material.mark_block_refs_done(repo_root, block_id=block_id, expected_update_id=update_id).value.passed
    assert material.mark_block_links_done(repo_root, block_id=block_id, expected_update_id=update_id).value.passed
    assert material.mark_block_completed(repo_root, block_id=block_id, expected_update_id=update_id).value.passed
    assert material.set_file_survey_status(
        repo_root,
        path="chapter.md",
        status="surveyed",
        summary="Surveyed.",
        expected_update_id=update_id,
    ).ok
    assert material.set_file_indexing_status(
        repo_root,
        path="chapter.md",
        status="indexed",
        expected_update_id=update_id,
    ).ok


def _submit_builder_and_run(runtime: FakeLeanFlowRuntime, flow_id: str, summary: str = "Builder done.") -> None:
    runtime.agent_service.queue_submission(
        SourceIndexBuilderRoundSubmission(
            submission_id=new_submission_id("builder"),
            tool_name="submit_source_index_builder_round",
            summary=summary,
            validation_summary="Scoped draft completed.",
        )
    )
    _advance_and_run(runtime, flow_id)


def _submit_review_and_run(runtime: FakeLeanFlowRuntime, flow_id: str, *, approved: bool, feedback: str | None = None) -> None:
    runtime.agent_service.queue_submission(
        SourceIndexReviewerRoundSubmission(
            submission_id=new_submission_id("review"),
            tool_name="submit_source_index_review_round",
            summary="Approved." if approved else "Rejected.",
            approved=approved,
            feedback=feedback,
        )
    )
    _advance_and_run(runtime, flow_id)


def test_standalone_flow_commits_scoped_delta_and_exposes_agent_boundaries(tmp_path: Path) -> None:
    runtime, lean_runtime, checkpoint = _runtime(tmp_path)
    repo_root = tmp_path / "WeightedSieve"
    _prepare_source(lean_runtime, repo_root)
    flow_id = runtime.start_flow("source_index_build", _params(repo_root), scope_id="repo:WeightedSieve")

    _advance_to_builder(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.parent_flow_id is None
    assert flow.state.active_update_id.startswith("source_index_update_")
    assert checkpoint.records == [(flow.state.pre_update_checkpoint_id, ["repo:WeightedSieve"])]
    _complete_active_draft(lean_runtime, repo_root, flow.state.active_update_id)
    _submit_builder_and_run(runtime, flow_id)
    _submit_review_and_run(runtime, flow_id, approved=True)
    _advance_and_run(runtime, flow_id)

    terminal = runtime.flow_service.get_flow(flow_id)
    assert terminal.status is FlowStatus.COMPLETED
    assert isinstance(terminal.result, SourceIndexBuildResult)
    assert terminal.result.outcome == "committed"
    assert terminal.result.newly_committed_file_paths == ["chapter.md"]
    assert terminal.result.appended_block_ids
    builder_record = runtime.agent_service.start_records[0]
    assert builder_record.variables["active_file_scope"] == ["chapter.md"]
    assert builder_record.variables["baseline_digest"]
    assert "forbidden_boundaries" in builder_record.variables
    assert "system injects update ownership" in (builder_record.prompt or "")


def test_review_rejection_loops_then_commits(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    flow_id = runtime.start_flow("source_index_build", _params(repo_root))
    _advance_to_builder(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    _complete_active_draft(lean_runtime, repo_root, flow.state.active_update_id)
    _submit_builder_and_run(runtime, flow_id)
    _submit_review_and_run(runtime, flow_id, approved=False, feedback="Clarify the summary.")
    looped = runtime.flow_service.get_flow(flow_id)
    assert looped.state.position == FlowPosition(phase="builder", round_index=2)
    _submit_builder_and_run(runtime, flow_id, "Summary clarified.")
    assert "Clarify the summary." in (runtime.agent_service.start_records[-1].prompt or "")
    _submit_review_and_run(runtime, flow_id, approved=True)
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).result.outcome == "committed"


def test_review_round_exhaustion_returns_blocked(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    flow_id = runtime.start_flow("source_index_build", _params(repo_root))
    _advance_to_builder(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    _complete_active_draft(lean_runtime, repo_root, flow.state.active_update_id)
    for round_index in (1, 2):
        _submit_builder_and_run(runtime, flow_id, f"Builder round {round_index}.")
        _submit_review_and_run(runtime, flow_id, approved=False, feedback=f"Reject round {round_index}.")
    terminal = runtime.flow_service.get_flow(flow_id)
    assert terminal.status is FlowStatus.COMPLETED
    assert terminal.result.outcome == "blocked"
    assert terminal.result.reason == "Reject round 2."


def test_already_open_restart_reuses_checkpoint_baseline(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    flow_id = runtime.start_flow("source_index_build", _params(repo_root))
    _advance_to_builder(runtime, flow_id)
    original = runtime.flow_service.get_flow(flow_id)
    baseline_digest = original.state.baseline_digest

    def retry_open(flow) -> None:  # noqa: ANN001
        flow.state.position = FlowPosition(phase="open_update")

    runtime.flow_service.store.update_flow_record(flow_id, retry_open)
    _advance_and_run(runtime, flow_id)
    retried = runtime.flow_service.get_flow(flow_id)
    assert retried.state.position.phase == "builder"
    assert retried.state.baseline_digest == baseline_digest
    runtime.flow_service.assert_restorable_flows()


def test_parent_supplied_checkpoint_is_validated_and_reused(tmp_path: Path) -> None:
    runtime, lean_runtime, checkpoint = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    prepared = checkpoint.materialize_source_index_baseline_checkpoint(
        repo_root,
        checkpoint_id="parent_checkpoint",
        scope_ids=["repo:Repo"],
        label="parent checkpoint",
    )
    assert prepared.ok
    flow_id = runtime.start_flow(
        "source_index_build",
        _params(repo_root, pre_update_checkpoint_id="parent_checkpoint"),
        scope_id="repo:Repo",
    )
    _advance_to_builder(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.pre_update_checkpoint_id == "parent_checkpoint"
    assert flow.state.baseline_digest == prepared.value.baseline_digest
    assert checkpoint.records == [("parent_checkpoint", ["repo:Repo"])]


@pytest.mark.parametrize("failure_mode", ["raise", "result"])
def test_stable_checkpoint_failure_marks_flow_failed(tmp_path: Path, failure_mode: str) -> None:
    runtime, lean_runtime, checkpoint = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    checkpoint.materialize_failure = failure_mode
    flow_id = runtime.start_flow("source_index_build", _params(repo_root))
    for _ in range(3):
        _advance_and_run(runtime, flow_id)
    failed = runtime.flow_service.get_flow(flow_id)
    assert failed.status is FlowStatus.FAILED
    assert failed.error.error_type == "source_index_baseline_checkpoint_failed"
    assert not runtime.flow_service.can_advance_flow(flow_id)
    assert runtime.flow_service.stable_hook_errors == []


def test_stable_checkpoint_hook_retry_is_idempotent(tmp_path: Path) -> None:
    runtime, lean_runtime, checkpoint = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    flow_id = runtime.start_flow("source_index_build", _params(repo_root), scope_id="repo:Repo")
    for _ in range(3):
        baseline_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    step = runtime.flow_service.get_step(baseline_step_id)
    flow.after_step_terminal_stable(
        StableStepTerminalContext(ark=runtime.ark, app=runtime.app, flow=flow, step=step)
    )
    retried = runtime.flow_service.get_flow(flow_id)
    assert retried.status is FlowStatus.RUNNING
    assert retried.state.baseline_digest == flow.state.baseline_digest
    assert checkpoint.records == [(flow.state.pre_update_checkpoint_id, ["repo:Repo"])]


def test_reusable_scope_is_no_op_and_child_result_is_authoritative(tmp_path: Path) -> None:
    runtime, lean_runtime, checkpoint = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    first = runtime.start_flow("source_index_build", _params(repo_root))
    _advance_to_builder(runtime, first)
    active = runtime.flow_service.get_flow(first).state.active_update_id
    _complete_active_draft(lean_runtime, repo_root, active)
    _submit_builder_and_run(runtime, first)
    _submit_review_and_run(runtime, first, approved=True)
    _advance_and_run(runtime, first)

    request = FlowRequest(flow_type="source_index_build", scope_id="repo:Repo", params=_params(repo_root))
    second = runtime.flow_service.start_flow(
        request,
        parent_flow_id="parent_flow",
        parent_dispatch_step_id="parent_dispatch",
        enqueue=False,
    )
    for _ in range(4):
        _advance_and_run(runtime, second)
    terminal = runtime.flow_service.get_flow(second)
    assert terminal.status is FlowStatus.COMPLETED
    assert terminal.parent_flow_id == "parent_flow"
    assert terminal.parent_dispatch_step_id == "parent_dispatch"
    assert terminal.result.outcome == "no_op"
    assert len(checkpoint.records) == 2


def test_invalid_scope_and_missing_checkpoint_adapter_have_stable_terminal_results(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    invalid = runtime.start_flow(
        "source_index_build",
        _params(repo_root, source_scope={"mode": "selected", "selectors": ["missing.md"]}),
    )
    _advance_and_run(runtime, invalid)
    _advance_and_run(runtime, invalid)
    invalid_flow = runtime.flow_service.get_flow(invalid)
    assert invalid_flow.status is FlowStatus.COMPLETED
    assert invalid_flow.result.outcome == "invalid_input"

    delattr(lean_runtime.app, "source_index_checkpoint")
    blocked = runtime.start_flow("source_index_build", _params(repo_root))
    _advance_and_run(runtime, blocked)
    blocked_flow = runtime.flow_service.get_flow(blocked)
    assert blocked_flow.status is FlowStatus.COMPLETED
    assert blocked_flow.result.outcome == "blocked"
    assert "checkpoint adapter" in blocked_flow.result.reason


def test_new_models_roundtrip_with_local_registration(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / "Repo"
    _prepare_source(lean_runtime, repo_root)
    flow_id = runtime.start_flow("source_index_build", _params(repo_root))
    _advance_and_run(runtime, flow_id)
    runtime.flow_service.assert_restorable_flows()
    reloaded = runtime.flow_service.get_flow(flow_id)
    assert isinstance(reloaded, SourceIndexBuildFlow)
    assert reloaded.state.position.phase == "resolve_scope"


@pytest.mark.parametrize("start_reason", ["initial", "continuation"])
def test_initial_and_continuation_child_inputs_roundtrip(tmp_path: Path, start_reason: str) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    repo_root = tmp_path / start_reason
    _prepare_source(lean_runtime, repo_root)
    request = FlowRequest(
        flow_type="source_index_build",
        scope_id=f"repo:{start_reason}",
        params=_params(repo_root, start_reason=start_reason),
    )
    flow_id = runtime.flow_service.start_flow(
        request,
        parent_flow_id=f"{start_reason}_parent",
        parent_dispatch_step_id=f"{start_reason}_dispatch",
        enqueue=False,
    )
    runtime.flow_service.assert_restorable_flows()
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.input.start_reason == start_reason
    assert flow.parent_flow_id == f"{start_reason}_parent"
    assert flow.parent_dispatch_step_id == f"{start_reason}_dispatch"
