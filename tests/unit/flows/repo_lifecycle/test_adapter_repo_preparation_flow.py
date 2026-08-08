from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus
from lean_constellation.app.runtime import ApplicationSnapshotRuntime

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, UpstreamDependencyInput
from lean_constellation.domain.repo import RepoCompletionMode, RepoPublicationStatus
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.repo_lifecycle.submissions import (
    AdapterCatalogBlockedSubmission,
    AdapterCatalogReadySubmission,
)
from lean_constellation.services.external_clients import (
    ExternalCommandResult,
    LeanCheckSummaryView,
    LeanMcpToolkitClient,
)
from lean_constellation.services.foundation import FoundationService
from lean_constellation.services.validation_snapshot import RepoCheckpointKind, ValidationSnapshotService
from tests.unit_services_helpers import CleanDeclarationSoundnessDispatcher, make_runtime


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


def _runtime(tmp_path: Path) -> tuple[FakeLeanFlowRuntime, object]:
    lake = FakeLakeClient()
    lean_runtime = make_runtime(
        external_overrides={
            "lake": lake,
            "lean_mcp_toolkit": LeanMcpToolkitClient(
                dispatcher=CleanDeclarationSoundnessDispatcher()
            ),
        }
    )
    lean_runtime.app.validation_snapshot = ValidationSnapshotService(lean_runtime)
    lean_runtime.app.snapshot_runtime = ApplicationSnapshotRuntime(
        lean_runtime,
        FakeArkSnapshotProvider(lean_runtime.foundation),
        runtime_stability=AlwaysStableRuntimeProvider(lean_runtime.foundation),
    )
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    return flow_runtime, lean_runtime


def _prepare_adapter_repo(
    lean_runtime,
    repo_root: Path,
    *,
    requirement_refs: list[dict[str, str]] | None = None,
) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    lean_runtime.repo_workspace.metadata.ensure_repo_model(repo_root)
    configured_as_requirement_provider = lean_runtime.repo_workspace.metadata.update_repo_config(
        repo_root,
        completion_mode=RepoCompletionMode.INTERFACE_DECLARED,
    )
    assert configured_as_requirement_provider.ok
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
            requirement_refs=requirement_refs or [],
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
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Existing upstream Lean repo.",
        visible_modules=[],
    )
    assert upstream.ok
    trusted = lean_runtime.adapter.mark_upstream_build_trusted(repo_root, summary="Lake update, build, and import check passed.")
    assert trusted.ok


def _start_adapter(runtime: FakeLeanFlowRuntime, repo_root: Path) -> str:
    return runtime.start_flow(
        "adapter_repo_preparation",
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


def _run_to_agent_catalog(runtime: FakeLeanFlowRuntime, flow_id: str) -> None:
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "ensure_main_catalog"
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "agent_catalog"


def _complete_adapter_catalog(lean_runtime, repo_root: Path) -> None:
    adapter = lean_runtime.adapter
    assert adapter.create_adapter_decl(
        repo_root,
        name="catalog_main_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.main_result",
        summary="Expose the upstream main theorem.",
    ).ok
    assert adapter.set_adapter_statement_formal(
        repo_root,
        name="catalog_main_result",
        code="theorem main_result : True := by\n  sorry",
    ).ok
    assert adapter.set_adapter_statement_nl(repo_root, name="catalog_main_result", text="Main theorem.").ok
    assert adapter.set_adapter_proof_formal(
        repo_root,
        name="catalog_main_result",
        code="theorem main_result : True := by\n  trivial",
    ).ok
    assert adapter.set_adapter_proof_nl(repo_root, name="catalog_main_result", text="Trivial proof.").ok
    assert adapter.finalize_adapter_decl(repo_root, name="catalog_main_result").ok
    assert adapter.bind_adapter_interface(
        repo_root,
        interface_name="main_result",
        decl_name="catalog_main_result",
        binding_summary="The adapter decl satisfies the required theorem interface.",
    ).ok
    assert adapter.create_adapter_decl(
        repo_root,
        name="supporting_result",
        kind="theorem",
        module="Upstream.Basic",
        lean_decl_name="Upstream.Basic.supporting_result",
        summary="Expose an additional public upstream theorem.",
    ).ok
    assert adapter.set_adapter_statement_formal(
        repo_root,
        name="supporting_result",
        code="theorem supporting_result : True := by\n  sorry",
    ).ok
    assert adapter.set_adapter_statement_nl(
        repo_root,
        name="supporting_result",
        text="Supporting theorem.",
    ).ok
    assert adapter.set_adapter_proof_formal(
        repo_root,
        name="supporting_result",
        code="theorem supporting_result : True := by\n  trivial",
    ).ok
    assert adapter.set_adapter_proof_nl(
        repo_root,
        name="supporting_result",
        text="Trivial supporting proof.",
    ).ok
    assert adapter.finalize_adapter_decl(repo_root, name="supporting_result").ok


def test_adapter_preparation_ready_marks_provider_ready(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    workspace = tmp_path / "workspace"
    consumer_root = workspace / "Consumer"
    repo_root = workspace / "AdapterProvider"
    assert lean_runtime.repo_workspace.metadata.ensure_repo_model(consumer_root).ok
    assert lean_runtime.repo_workspace.requirement.create_requirement(
        consumer_root,
        name="need_adapter_provider",
        target_repo=repo_root.name,
        reason="Use the Adapter theorem.",
    ).ok
    assert lean_runtime.repo_workspace.requirement.add_requirement_interface(
        consumer_root,
        requirement_name="need_adapter_provider",
        interface_name="main_result",
        kind=DeclKind.THEOREM,
        summary="Adapter theorem interface.",
    ).ok
    assert lean_runtime.repo_workspace.mark_requirement_waiting_for_provider(
        consumer_root,
        requirement_name="need_adapter_provider",
    ).ok
    _prepare_adapter_repo(
        lean_runtime,
        repo_root,
        requirement_refs=[
            {
                "consumer_repo": consumer_root.name,
                "requirement_name": "need_adapter_provider",
            }
        ],
    )
    flow_id = _start_adapter(runtime, repo_root)

    _run_to_agent_catalog(runtime, flow_id)
    _complete_adapter_catalog(lean_runtime, repo_root)
    transient_block = lean_runtime.adapter.submit_adapter_catalog_blocked(
        repo_root,
        reason="Visible modules have not yet been materialized.",
        missing_interfaces=[],
        evidence_summary="Catalog preflight passes but the Flow-owned projection has not run.",
        suggested_next_action="Run the deterministic adapter finalization step.",
    )
    assert not transient_block.ok
    assert transient_block.issues[0].kind == "adapter_catalog_preflight_already_passed"
    runtime.agent_service.queue_submission(
        AdapterCatalogReadySubmission(
            submission_id=new_submission_id("sub"),
            submission_type="adapter_catalog_ready",
            tool_name="submit_adapter_catalog_ready",
            summary="Adapter catalog is ready.",
        )
    )
    _advance_and_run(runtime, flow_id)
    assert runtime.flow_service.get_flow(flow_id).state.position.phase == "finalize_ready"
    assert lean_runtime.repo_workspace.metadata.get_repo_publication(
        repo_root
    ).value.publication.status == RepoPublicationStatus.DEVELOPING
    _advance_and_run(runtime, flow_id)
    assert "public import Upstream.Basic" in (repo_root / "Main" / "Interfaces.lean").read_text(encoding="utf-8")
    visible_modules = lean_runtime.adapter.list_visible_upstream_modules(repo_root)
    assert visible_modules.ok
    assert visible_modules.value is not None
    assert visible_modules.value.modules == ["Upstream.Basic"]
    exports = lean_runtime.node.export.list_scope_exports(repo_root, scope_path="Main")
    assert exports.ok and exports.value is not None
    assert [item.ref.name for item in exports.value] == [
        "catalog_main_result",
        "supporting_result",
    ]
    assert all(item.valid for item in exports.value)
    main_contract = lean_runtime.node.contract.get_current_contract(repo_root, node_path="Main")
    assert main_contract.ok and main_contract.value is not None
    bindings = {item.name: item.bound_decl for item in main_contract.value.contract.interfaces}
    assert bindings["main_result"] is not None
    assert bindings["main_result"].name == "catalog_main_result"
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "adapter_ready"
    assert flow.result.catalog_decl_count == 2
    assert flow.result.bound_interface_count == 1
    assert flow.result.imported_modules_count == 1
    assert lean_runtime.repo_workspace.metadata.get_repo_publication(
        repo_root
    ).value.publication.status == RepoPublicationStatus.STABLE
    config = lean_runtime.repo_workspace.metadata.get_repo_config(repo_root)
    publication = lean_runtime.repo_workspace.metadata.get_repo_publication(repo_root)
    assert config.ok and config.value is not None
    assert config.value.config.completion_mode == RepoCompletionMode.GRAPH_PROVED
    assert publication.ok and publication.value is not None
    assert publication.value.publication.status == RepoPublicationStatus.STABLE
    requirement = lean_runtime.repo_workspace.requirement.get_requirement(
        consumer_root,
        name="need_adapter_provider",
    )
    assert requirement.ok and requirement.value is not None
    assert requirement.value.requirement.status.value == "satisfied"
    stable_truth = lean_runtime.repo_workspace.requirement.validate_requirement_provider_truth(
        consumer_root,
        requirement_name="need_adapter_provider",
        provider_repo=repo_root.name,
        require_stable=True,
    )
    assert stable_truth.ok, stable_truth.issues
    ready_view = lean_runtime.validation_snapshot.get_repo_ready_view(repo_root)
    assert ready_view.ok, ready_view.issues
    assert ready_view.value is not None


def test_adapter_preparation_blocked_submit_finishes_blocked(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "AdapterProvider"
    _prepare_adapter_repo(lean_runtime, repo_root)
    flow_id = _start_adapter(runtime, repo_root)

    _run_to_agent_catalog(runtime, flow_id)
    runtime.agent_service.queue_submission(
        AdapterCatalogBlockedSubmission(
            submission_id=new_submission_id("sub"),
            submission_type="adapter_catalog_blocked",
            tool_name="submit_adapter_catalog_blocked",
            reason="No upstream declaration matches the required interface.",
            missing_interfaces=["main_result"],
            evidence_summary="The required interface has no matching upstream declaration.",
            suggested_next_action="Choose a different upstream repo.",
            summary="Adapter catalog blocked.",
        )
    )
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "blocked"
    assert flow.result.missing_interfaces == ["main_result"]
    assert flow.result.suggested_next_action == "Choose a different upstream repo."
