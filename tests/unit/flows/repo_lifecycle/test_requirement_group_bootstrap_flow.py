from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus, StepStatus

from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.repo_lifecycle.submissions import (
    RepoFormatAdapterChoiceSubmission,
    RepoFormatNativeChoiceSubmission,
)
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
from lean_constellation.services.foundation import FoundationService
from lean_constellation.services.validation_snapshot import RepoCheckpointKind, ValidationSnapshotService
from tests.unit_services_helpers import make_runtime


class AlwaysStableRuntimeProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ):
        del repo_root, checkpoint_kind, node_paths
        return self.foundation.ok(self.foundation.gate_passed("runtime_stability", summary="Runtime is stable."))


class FakeArkSnapshotProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root, scope_ids, label
        return self.foundation.ok("ark_snapshot")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root, leave_runtime_paused
        return self.foundation.ok(snapshot_id)


class FakeLakeClient:
    def __init__(self) -> None:
        self.updated: list[Path] = []
        self.built: list[tuple[Path, str | None]] = []
        self.checked: list[tuple[Path, str]] = []
        self.update_ok = True
        self.build_ok = True
        self.import_ok = True

    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        self.updated.append(Path(repo_root))
        return ExternalCommandResult(
            ok=self.update_ok,
            command=["lake", "update"],
            cwd=str(repo_root),
            exit_code=0 if self.update_ok else 1,
            stderr_excerpt=None if self.update_ok else "update failed",
            summary="lake update ok" if self.update_ok else "lake update failed",
        )

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        self.built.append((Path(repo_root), target))
        return ExternalCommandResult(
            ok=self.build_ok,
            command=["lake", "build"] + ([target] if target else []),
            cwd=str(repo_root),
            exit_code=0 if self.build_ok else 1,
            stderr_excerpt=None if self.build_ok else "build failed",
            summary="lake build ok" if self.build_ok else "lake build failed",
        )

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        self.checked.append((Path(repo_root), module))
        return LeanCheckSummaryView(
            ok=self.import_ok,
            module=module,
            command=["lean"],
            diagnostics_excerpt=None if self.import_ok else "unknown module",
            summary=f"import {module} ok" if self.import_ok else f"import {module} failed",
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


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, object, FakeLakeClient]:
    lake = FakeLakeClient()
    lean_runtime = make_runtime(external_overrides={"lake": lake})
    lean_runtime.app.validation_snapshot = ValidationSnapshotService(
        lean_runtime,
        runtime_stability_provider=AlwaysStableRuntimeProvider(lean_runtime.foundation),
        ark_snapshot_provider=FakeArkSnapshotProvider(lean_runtime.foundation),
    )
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    return flow_runtime, lean_runtime, lake


def _write_preparation_input(lean_runtime, repo_root: Path, *, source_mode: SourceCorpusMode = SourceCorpusMode.PREPARE) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    written = lean_runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Provide topology facts.",
            source_corpus_mode=source_mode,
            requirement_refs=[{"consumer_repo": "consumer", "requirement_name": "need_provider"}],
        ),
    )
    assert written.ok


def _start_bootstrap(runtime: FakeLeanFlowRuntime, workspace: Path, repo_root: Path) -> str:
    return runtime.start_flow(
        "requirement_group_repo_bootstrap",
        {
            "target_repo": repo_root.name,
            "repo_root": str(repo_root),
            "workspace_root": str(workspace),
            "requirement_refs": ["consumer:need_provider"],
        },
        scope_id=f"repo:{repo_root.name}",
    )


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def test_requirement_bootstrap_native_choice_initializes_native_skeleton(tmp_path: Path) -> None:
    runtime, lean_runtime, lake = _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_preparation_input(lean_runtime, repo_root)
    flow_id = _start_bootstrap(runtime, workspace, repo_root)

    validate_step_id = _advance_and_run(runtime, flow_id)
    validate_step = runtime.flow_service.get_step(validate_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert validate_step.result is not None
    assert validate_step.result.result_type == "bootstrap_input_validation"
    assert flow.state.position.phase == "format_discovery"

    agent_step_id = runtime.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    runtime.agent_service.queue_submission(
        RepoFormatNativeChoiceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="repo_format_native_choice",
            tool_name="submit_native_repo_choice",
            native_repo_name="Provider",
            source_corpus_mode="prepare",
            summary="Native route.",
        )
    )
    runtime.run_step(agent_step_id)
    assert not (repo_root / "lakefile.toml").exists()
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.state.position.phase == "apply_format_choice"

    apply_step_id = _advance_and_run(runtime, flow_id)
    apply_step = runtime.flow_service.get_step(apply_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert apply_step.result is not None
    assert apply_step.result.result_type == "apply_repo_format_choice"
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "native_bootstrap_ready"
    assert flow.result.next_preparation_flow == "native_repo_preparation"
    assert lake.built == [(repo_root, None)]
    assert runtime.flow_service.list_flows(flow_type="native_repo_preparation") == []


def test_requirement_bootstrap_adapter_choice_initializes_adapter_skeleton(tmp_path: Path) -> None:
    runtime, lean_runtime, lake = _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    repo_root = workspace / "AdapterProvider"
    _write_preparation_input(lean_runtime, repo_root)
    flow_id = _start_bootstrap(runtime, workspace, repo_root)

    _advance_and_run(runtime, flow_id)
    agent_step_id = runtime.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    runtime.agent_service.queue_submission(
        RepoFormatAdapterChoiceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="repo_format_adapter_choice",
            tool_name="submit_adapter_repo_choice",
            upstream_github_url="https://github.com/example/upstream.git",
            upstream_revision="main",
            upstream_subdir="lean",
            adapter_repo_name="AdapterProvider",
            summary="Adapter route.",
        )
    )
    runtime.run_step(agent_step_id)
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "adapter_bootstrap_ready"
    assert flow.result.next_preparation_flow == "adapter_repo_preparation"
    preparation = lean_runtime.repo_workspace.preparation.get_preparation_input(repo_root)
    assert preparation.ok and preparation.value is not None
    assert preparation.value.input.source_corpus_mode == SourceCorpusMode.NONE
    assert preparation.value.input.source_corpus_relpath is None
    assert lake.updated == [repo_root]
    assert lake.checked == [(repo_root, "upstream")]


def test_requirement_bootstrap_missing_preparation_input_needs_admin_repair(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    repo_root.mkdir(parents=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    flow_id = _start_bootstrap(runtime, workspace, repo_root)

    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "needs_admin_repair"
    assert "Preparation input" in (flow.result.reason or "")


def test_requirement_bootstrap_agent_incomplete_needs_admin_repair(tmp_path: Path) -> None:
    runtime, lean_runtime, _ = _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    _write_preparation_input(lean_runtime, repo_root)
    flow_id = _start_bootstrap(runtime, workspace, repo_root)

    _advance_and_run(runtime, flow_id)
    agent_step_id = runtime.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    runtime.agent_service.queue_incomplete_turn()
    runtime.agent_service.queue_incomplete_turn()
    runtime.agent_service.queue_incomplete_turn()
    runtime.run_step(agent_step_id)

    agent_step = runtime.flow_service.get_step(agent_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert agent_step.status is StepStatus.COMPLETED
    assert agent_step.result is not None
    assert agent_step.result.result_type == "repo_format_discovery"
    assert agent_step.result.outcome == "incomplete"
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "needs_admin_repair"


def test_pre_flow_setup_rejects_existing_target_repo(tmp_path: Path) -> None:
    _, lean_runtime, _ = _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    repo_root = workspace / "Provider"
    repo_root.mkdir(parents=True)
    preparation_input = RepoPreparationInput(
        goal="Provide dependency.",
        source_corpus_mode=SourceCorpusMode.PREPARE,
    )

    result = lean_runtime.repo_workspace.prepare_provider_repo_runtime_shell(
        workspace,
        target_repo="Provider",
        preparation_input=preparation_input,
    )

    assert not result.ok
    assert result.issues[0].kind == "target_repo_already_exists"
