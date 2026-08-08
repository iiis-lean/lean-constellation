from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.app.config import AutomaticCheckpointAppConfig
from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, UpstreamDependencyInput
from lean_constellation.app.runtime import ApplicationSnapshotRuntime
from lean_constellation.flows.common.flow_requests import build_content_node_task_request
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.content_node_task.flows import ContentNodeTaskResult
from lean_constellation.flows.coordinator.submissions import CoordinatorContentTasksSubmission, CoordinatorRepoRequirementSubmission
from lean_constellation.flows.repo_lifecycle.submissions import AdapterCatalogReadySubmission, RepoFormatNativeChoiceSubmission
from lean_constellation.services.external_clients import (
    ExternalCommandResult,
    LeanCheckSummaryView,
    LeanMcpToolkitClient,
)
from lean_constellation.services.foundation import FoundationService
from lean_constellation.services.validation_snapshot import RepoCheckpointKind, ValidationSnapshotService
from tests.unit_services_helpers import CleanDeclarationSoundnessDispatcher, make_runtime


class FakeLakeClient:
    def __init__(self) -> None:
        self.updated: list[Path] = []
        self.built: list[tuple[Path, str | None]] = []
        self.checked: list[tuple[Path, str]] = []

    def run_lake_update(self, repo_root: Path) -> ExternalCommandResult:
        self.updated.append(Path(repo_root))
        return ExternalCommandResult(ok=True, command=["lake", "update"], cwd=str(repo_root), exit_code=0, summary="lake update ok")

    def run_lake_build(self, repo_root: Path, target: str | None = None) -> ExternalCommandResult:
        self.built.append((Path(repo_root), target))
        return ExternalCommandResult(ok=True, command=["lake", "build"], cwd=str(repo_root), exit_code=0, summary="lake build ok")

    def run_minimal_import_check(self, repo_root: Path, module: str) -> LeanCheckSummaryView:
        self.checked.append((Path(repo_root), module))
        return LeanCheckSummaryView(ok=True, module=module, command=["lean"], summary=f"import {module} ok")

    def run_snippet_check(
        self,
        *,
        repo_root: Path,
        imports: list[str],
        code: str,
        timeout_seconds: int | None = None,
    ) -> LeanCheckSummaryView:
        del repo_root, imports, code, timeout_seconds
        return LeanCheckSummaryView(ok=True, command=["lean"], summary="registered declaration identity confirmed")

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
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.calls: list[tuple[RepoCheckpointKind, list[str]]] = []

    def check_repo_stable_point(
        self,
        repo_root: Path,
        *,
        checkpoint_kind: RepoCheckpointKind,
        node_paths: list[str] | None = None,
    ):
        del repo_root
        self.calls.append((checkpoint_kind, list(node_paths or [])))
        return self.foundation.ok(self.foundation.gate_passed("runtime_stability", summary="Runtime is stable."))


class FakeArkSnapshotProvider:
    def __init__(self, foundation: FoundationService) -> None:
        self.foundation = foundation
        self.created: list[tuple[list[str], str | None]] = []

    def create_runtime_snapshot(self, repo_root: Path, *, scope_ids: list[str], label: str | None = None):
        del repo_root
        self.created.append((list(scope_ids), label))
        return self.foundation.ok(f"ark_snapshot_{len(self.created)}")

    def restore_runtime_snapshot(self, repo_root: Path, *, snapshot_id: str, leave_runtime_paused: bool = True):
        del repo_root, leave_runtime_paused
        return self.foundation.ok(snapshot_id)


def test_requirement_bootstrap_terminal_snapshot(tmp_path: Path) -> None:
    runtime, repo_root, stability, ark_snapshot = _runtime(tmp_path)
    runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(repo_flow_boundaries_enabled=False)
    _write_preparation_input(runtime.app, repo_root)
    flow_id = _start_requirement_bootstrap(runtime, repo_root)

    _advance_and_run(runtime, flow_id)
    agent_step_id = runtime.flow_service.advance_flow(flow_id)
    assert agent_step_id is not None
    runtime.agent_service.queue_submission(
        RepoFormatNativeChoiceSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="repo_format_native_choice",
            tool_name="submit_native_repo_choice",
            summary="Native provider route.",
            searched_targets=["No reusable Lean provider was found in the fixture search."],
        )
    )
    runtime.run_step(agent_step_id)
    apply_step_id = _advance_and_run(runtime, flow_id)

    apply_step = runtime.flow_service.get_step(apply_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "native_bootstrap_ready"
    assert apply_step.result.snapshot_id is not None
    assert stability.calls == [(RepoCheckpointKind.REQUIREMENT_BOOTSTRAP_TERMINAL, [])]
    assert ark_snapshot.created == [([f"repo:{repo_root.name}"], f"requirement bootstrap terminal for {repo_root.name}")]


def test_adapter_preparation_terminal_snapshot(tmp_path: Path) -> None:
    runtime, _, stability, ark_snapshot = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "AdapterProvider"
    _prepare_adapter_repo(runtime.app, repo_root)
    flow_id = _start_adapter_preparation(runtime, repo_root)

    _run_adapter_to_agent_catalog(runtime, flow_id)
    _complete_adapter_catalog(runtime.app, repo_root)
    runtime.agent_service.queue_submission(
        AdapterCatalogReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="adapter_catalog_ready",
            tool_name="submit_adapter_catalog_ready",
            summary="Adapter catalog is ready.",
        )
    )
    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    mark_ready_step_id = _advance_and_run(runtime, flow_id)

    mark_ready_step = runtime.flow_service.get_step(mark_ready_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result.outcome == "adapter_release_prepared"
    assert mark_ready_step.result.snapshot_id is not None
    assert stability.calls == [(RepoCheckpointKind.ADAPTER_PREPARATION_TERMINAL, [])]
    assert ark_snapshot.created == [(["repo:AdapterProvider"], "adapter preparation terminal for AdapterProvider")]


def test_coordinator_requirement_waiting_snapshot(tmp_path: Path) -> None:
    runtime, repo_root, stability, ark_snapshot = _runtime(tmp_path)
    flow_id = _start_coordinator(runtime, repo_root)
    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            requirement_name="need_provider",
            target_repo="ProviderRepo",
            provider_route={"kind": "auto"},
            reason="A supporting provider repo is required.",
            summary="Need provider repo.",
        )
    )

    coordinator_step_id = _advance_and_run(runtime, flow_id)
    coordinator_step = runtime.flow_service.get_step(coordinator_step_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    assert flow.state.position.phase == "waiting_requirement"
    assert coordinator_step.result.outcome == "repo_requirement"
    assert coordinator_step.result.snapshot_id is not None
    assert stability.calls == [(RepoCheckpointKind.COORDINATOR_REQUIREMENT_WAITING, [])]
    assert ark_snapshot.created == [(["repo:Repo"], "coordinator requirement waiting for Repo")]


def test_coordinator_requirement_waiting_snapshot_is_skipped_when_repo_boundary_group_is_disabled(tmp_path: Path) -> None:
    runtime, repo_root, stability, ark_snapshot = _runtime(tmp_path)
    runtime.app.automatic_checkpoints = AutomaticCheckpointAppConfig(repo_flow_boundaries_enabled=False)
    flow_id = _start_coordinator(runtime, repo_root)
    runtime.agent_service.queue_submission(
        CoordinatorRepoRequirementSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_repo_requirement",
            tool_name="submit_repo_requirement",
            requirement_name="need_provider",
            target_repo="ProviderRepo",
            provider_route={"kind": "auto"},
            reason="A supporting provider repo is required.",
            summary="Need provider repo.",
        )
    )

    coordinator_step_id = _advance_and_run(runtime, flow_id)

    coordinator_step = runtime.flow_service.get_step(coordinator_step_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "waiting_requirement"
    assert coordinator_step.result.snapshot_id is None
    assert "checkpoint skipped" in coordinator_step.result.summary
    assert stability.calls == []
    assert ark_snapshot.created == []


def test_coordinator_requirement_waiting_checkpoint_policy() -> None:
    policy = make_runtime().validation_snapshot.snapshot_restore.checkpoint_policies()[RepoCheckpointKind.COORDINATOR_REQUIREMENT_WAITING]

    assert policy.checkpoint_kind is RepoCheckpointKind.COORDINATOR_REQUIREMENT_WAITING
    assert "requires_runtime_stable" not in policy.model_dump()
    assert "requires_ark_snapshot" not in policy.model_dump()
    assert "include_node_scopes" not in policy.model_dump()


def test_before_dispatch_snapshot(tmp_path: Path) -> None:
    runtime, repo_root, stability, ark_snapshot = _runtime(tmp_path)
    flow_id = _start_coordinator(runtime, repo_root)
    node_id = _ensure_main_core_node(runtime.app, repo_root)
    _queue_content_task_dispatch(runtime, node_id=node_id)

    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "before_content_task_dispatch_snapshot"

    snapshot_step_id = _advance_and_run(runtime, flow_id)
    snapshot_step = runtime.flow_service.get_step(snapshot_step_id)
    assert snapshot_step.step_type == "coordinator_content_batch_snapshot_step"
    assert snapshot_step.result.outcome == "snapshot_created"
    assert snapshot_step.result.checkpoint_kind == "before_content_task_dispatch"
    assert stability.calls == [(RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH, ["Main.Core"])]
    assert ark_snapshot.created == [(["repo:Repo", f"repo:Repo:node:{node_id}"], "before_content_task_dispatch for Repo")]

    dispatch_step_id = runtime.flow_service.advance_flow(flow_id)
    assert dispatch_step_id is not None
    dispatch_step = runtime.flow_service.get_step(dispatch_step_id)
    assert dispatch_step.step_type == "dispatch_step"


def test_after_child_batch_snapshot(tmp_path: Path) -> None:
    runtime, repo_root, stability, ark_snapshot = _runtime(tmp_path)
    flow_id = _start_coordinator(runtime, repo_root)
    node_id = _ensure_main_core_node(runtime.app, repo_root)
    _queue_content_task_dispatch(runtime, node_id=node_id)

    _advance_and_run(runtime, flow_id)
    _advance_and_run(runtime, flow_id)
    dispatch_step_id = _advance_and_run(runtime, flow_id)
    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.WAITING
    child_flows = runtime.flow_service.store.list_child_flows(parent_flow_id=flow_id, parent_dispatch_step_id=dispatch_step_id)
    assert len(child_flows) == 1
    _complete_child_flow(runtime, child_flows[0].flow_id)

    after_snapshot_step_id = runtime.flow_service.advance_flow(flow_id)
    assert after_snapshot_step_id is not None
    after_snapshot_step = runtime.flow_service.get_step(after_snapshot_step_id)
    assert after_snapshot_step.step_type == "coordinator_content_batch_snapshot_step"
    runtime.run_step(after_snapshot_step_id)
    after_snapshot_step = runtime.flow_service.get_step(after_snapshot_step_id)
    assert after_snapshot_step.result.outcome == "snapshot_created"
    assert after_snapshot_step.result.checkpoint_kind == "after_content_task_batch_terminal"

    callback_step_id = runtime.flow_service.advance_flow(flow_id)
    assert callback_step_id is not None
    callback_step = runtime.flow_service.get_step(callback_step_id)
    assert callback_step.step_type == "coordinator_agent_step"
    assert stability.calls == [
        (RepoCheckpointKind.BEFORE_CONTENT_TASK_DISPATCH, ["Main.Core"]),
        (RepoCheckpointKind.AFTER_CONTENT_TASK_BATCH_TERMINAL, ["Main.Core"]),
    ]
    assert ark_snapshot.created == [
        (["repo:Repo", f"repo:Repo:node:{node_id}"], "before_content_task_dispatch for Repo"),
        (["repo:Repo", f"repo:Repo:node:{node_id}"], "after_content_task_batch_terminal for Repo"),
    ]


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, Path, FakeRuntimeStabilityProvider, FakeArkSnapshotProvider]:
    lean_runtime = make_runtime(
        external_overrides={
            "lake": FakeLakeClient(),
            "lean_mcp_toolkit": LeanMcpToolkitClient(
                dispatcher=CleanDeclarationSoundnessDispatcher()
            ),
        }
    )
    stability = FakeRuntimeStabilityProvider(lean_runtime.foundation)
    ark_snapshot = FakeArkSnapshotProvider(lean_runtime.foundation)
    lean_runtime.app.validation_snapshot = ValidationSnapshotService(lean_runtime)
    lean_runtime.app.snapshot_runtime = ApplicationSnapshotRuntime(
        lean_runtime, ark_snapshot, runtime_stability=stability
    )
    runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    repo_root = tmp_path / "workspace" / "Repo"
    repo_root.mkdir(parents=True)
    return runtime, repo_root, stability, ark_snapshot


def _write_preparation_input(lean_runtime, repo_root: Path, *, source_mode: SourceCorpusMode = SourceCorpusMode.PREPARE) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    written = lean_runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Provide topology facts.",
            source_corpus_mode=source_mode,
            requirement_refs=[{"consumer_repo": "Consumer", "requirement_name": "need_provider"}],
        ),
    )
    assert written.ok


def _start_requirement_bootstrap(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    return runtime.start_flow(
        "requirement_group_repo_bootstrap",
        {
            "target_repo": repo_root.name,
            "repo_root": str(repo_root),
            "workspace_root": str(repo_root.parent),
            "requirement_refs": ["Consumer:need_provider"],
            "resolved_provider_route": {"kind": "auto"},
        },
        scope_id=f"repo:{repo_root.name}",
    )


def _start_coordinator(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    return runtime.start_flow(
        "native_repo_coordinator",
        {
            "repo_key": "Repo",
            "repo_root": str(repo_root),
            "start_mode": "admin_start",
            "start_reason": "snapshot hook test",
        },
        scope_id="repo:Repo",
    )


def _ensure_main_core_node(lean_runtime, repo_root: Path) -> str:
    repo_root.mkdir(parents=True, exist_ok=True)
    assert lean_runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    created = lean_runtime.node.create_content_node(
        repo_root,
        path="Main.Core",
        goal="Core goal",
        boundary="Core boundary",
        objective="Build core.",
        success_criteria="Core ready.",
    )
    assert created.ok and created.value is not None
    return created.value.node_id


def _prepare_adapter_repo(lean_runtime, repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    written = lean_runtime.repo_workspace.preparation.write_preparation_input(
        repo_root,
        input=RepoPreparationInput(
            goal="Expose an upstream theorem as an adapter provider.",
            source_corpus_mode=SourceCorpusMode.NONE,
            source_corpus_relpath=None,
            interface_inputs=[
                DeclInterface(
                    name="main_result",
                    kind=DeclKind.THEOREM,
                    summary="Expose the upstream main theorem.",
                )
            ],
        ),
    )
    assert written.ok
    initialized = lean_runtime.repo_workspace.initialize_repo_as_adapter(
        repo_root,
        upstream=UpstreamDependencyInput(
            git_url="https://github.com/example/upstream.git",
            package_name="upstream",
            module_name="upstream",
            evidence_summary="Existing upstream Lean repo.",
        ),
        project_name=repo_root.name,
    )
    assert initialized.ok
    upstream = lean_runtime.adapter.write_adapter_upstream_metadata(
        repo_root,
        git_url="https://github.com/example/upstream.git",
        revision="1" * 40,
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Existing upstream Lean repo.",
        visible_modules=["Upstream.Basic"],
    )
    assert upstream.ok
    trusted = lean_runtime.adapter.mark_upstream_build_trusted(repo_root, summary="Lake update, build, and import check passed.")
    assert trusted.ok


def _start_adapter_preparation(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    return runtime.start_flow(
        "adapter_repo_preparation",
        {
            "repo_key": repo_root.name,
            "repo_root": str(repo_root),
            "start_reason": "bootstrap",
        },
        scope_id=f"repo:{repo_root.name}",
    )


def _run_adapter_to_agent_catalog(runtime: FakeLeanFlowRuntime, flow_id: str) -> None:
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "ensure_main_catalog"
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "agent_catalog"


def _complete_adapter_catalog(lean_runtime, repo_root: Path) -> None:
    adapter = lean_runtime.adapter
    assert adapter.create_adapter_decl(
        repo_root,
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose the upstream main theorem.",
    ).ok
    assert adapter.set_adapter_statement_formal(
        repo_root,
        name="main_result",
        code="theorem main_result : True := by\n  sorry",
    ).ok
    assert adapter.set_adapter_statement_nl(repo_root, name="main_result", text="Main theorem.").ok
    assert adapter.set_adapter_proof_formal(
        repo_root,
        name="main_result",
        code="theorem main_result : True := by\n  trivial",
    ).ok
    assert adapter.set_adapter_proof_nl(repo_root, name="main_result", text="Trivial proof.").ok
    assert adapter.finalize_adapter_decl(repo_root, name="main_result").ok
    assert adapter.bind_adapter_interface(
        repo_root,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="The adapter decl satisfies the required theorem interface.",
    ).ok


def _queue_content_task_dispatch(runtime: FakeLeanFlowRuntime, *, node_id: str) -> None:
    runtime.agent_service.queue_submission(
        CoordinatorContentTasksSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="coordinator_content_tasks",
            tool_name="submit_content_node_tasks",
            repo_key="Repo",
            node_paths=["Main.Core"],
            requests=[build_content_node_task_request(repo_key="Repo", node_path="Main.Core", scope_id=f"repo:Repo:node:{node_id}")],
            continuation="wait_for_callback",
            summary="Run Main.Core.",
        )
    )


def _advance_and_run(runtime: FakeLeanFlowRuntime, flow_id: str) -> str:
    step_id = runtime.flow_service.advance_flow(flow_id)
    assert step_id is not None
    runtime.run_step(step_id)
    return step_id


def _complete_child_flow(runtime: FakeLeanFlowRuntime, child_flow_id: str) -> None:
    runtime.flow_service.store.update_flow_record(
        child_flow_id,
        lambda flow: (
            setattr(flow, "result", ContentNodeTaskResult(outcome="ready", repo_key="Repo", node_path="Main.Core", summary="Ready.")),
            setattr(flow, "status", FlowStatus.COMPLETED),
            setattr(flow, "current_step_id", None),
        ),
    )
