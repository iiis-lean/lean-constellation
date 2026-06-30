from __future__ import annotations

from pathlib import Path

from agent_runtime_kit.flow.models import FlowStatus

from lean_constellation.domain.interface import DeclInterface, DeclKind
from lean_constellation.domain.preparation import RepoPreparationInput, SourceCorpusMode, UpstreamDependencyInput
from lean_constellation.flows.common.submissions import new_submission_id
from lean_constellation.flows.common.testing import FakeLeanFlowRuntime, create_fake_lean_flow_runtime
from lean_constellation.flows.repo_lifecycle.submissions import (
    AdapterCatalogBlockedSubmission,
    AdapterCatalogReadySubmission,
)
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView
from tests.unit_services_helpers import make_runtime


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
    lean_runtime = make_runtime(external_overrides={"lake": lake})
    flow_runtime = create_fake_lean_flow_runtime(
        tmp_path / "ark",
        ark_services=lean_runtime.ark,
        app_services=lean_runtime.app,
    )
    return flow_runtime, lean_runtime


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
        package_name="upstream",
        dependency_name="upstream",
        evidence_summary="Existing upstream Lean repo.",
        visible_modules=["Upstream.Basic"],
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
        name="main_result",
        kind="theorem",
        module="Upstream.Basic",
        plan_summary="Expose the upstream main theorem.",
    ).ok
    assert adapter.set_adapter_statement_formal(
        repo_root,
        name="main_result",
        code="theorem main_result : True := by\n  sorry",
        upstream_decl_name="Upstream.Basic.main_result",
    ).ok
    assert adapter.set_adapter_statement_nl(repo_root, name="main_result", summary="Main theorem.").ok
    assert adapter.set_adapter_proof_formal(
        repo_root,
        name="main_result",
        code="theorem main_result : True := by\n  trivial",
        upstream_decl_name="Upstream.Basic.main_result",
    ).ok
    assert adapter.set_adapter_proof_nl(repo_root, name="main_result", summary="Trivial proof.").ok
    assert adapter.finalize_adapter_decl(repo_root, name="main_result").ok
    assert adapter.bind_adapter_interface(
        repo_root,
        interface_name="main_result",
        decl_name="main_result",
        binding_summary="The adapter decl satisfies the required theorem interface.",
    ).ok


def test_adapter_preparation_ready_marks_provider_ready(tmp_path: Path) -> None:
    runtime, lean_runtime = _runtime(tmp_path)
    repo_root = tmp_path / "workspace" / "AdapterProvider"
    _prepare_adapter_repo(lean_runtime, repo_root)
    flow_id = _start_adapter(runtime, repo_root)

    _run_to_agent_catalog(runtime, flow_id)
    _complete_adapter_catalog(lean_runtime, repo_root)
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
    _advance_and_run(runtime, flow_id)
    assert "public import Upstream.Basic" in (repo_root / "Main" / "Interfaces.lean").read_text(encoding="utf-8")
    _advance_and_run(runtime, flow_id)

    flow = runtime.flow_service.get_flow(flow_id)
    assert flow.status is FlowStatus.COMPLETED
    assert flow.result is not None
    assert flow.result.outcome == "adapter_ready"
    assert flow.result.catalog_decl_count == 1
    assert flow.result.bound_interface_count == 1
    assert flow.result.imported_modules_count == 1
    assert lean_runtime.repo_workspace.metadata.get_provider_ready(repo_root).value.ready is True


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
