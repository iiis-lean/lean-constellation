from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.unit_services_helpers import initialize_native_test_repo, make_runtime

from lean_constellation.domain.repo import DocstringProjectionConfig, WorkspaceConfig
from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import ExternalCommandResult, LeanCheckSummaryView, LeanDiagnosticsResult
from lean_constellation.services.runtime import LeanRuntimeServices


NODE_PATH = "Main.Topic.Core"
DECL_NAME = "main_result"


class FakeToolkit:
    def run_file_diagnostics(self, repo_root: Path, file_path: Path) -> LeanDiagnosticsResult:
        return LeanDiagnosticsResult(
            ok=True,
            repo_root=str(repo_root),
            file_path=str(file_path),
            diagnostics=[],
            summary="fake toolkit diagnostics",
        )


class FakeLake:
    def run_lake_env_lean(
        self,
        *,
        repo_root: Path,
        rel_file: str,
        json: bool = True,
        timeout_seconds: int | None = None,
    ) -> ExternalCommandResult:
        del json, timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "env", "lean", "--json", rel_file],
            cwd=str(repo_root),
            exit_code=0,
            summary="fake lake diagnostics",
        )

    def run_lake_build(self, repo_root: Path, target: str | None = None, targets=None, timeout_seconds=None):  # noqa: ANN001, ANN201
        del targets, timeout_seconds
        return ExternalCommandResult(
            ok=True,
            command=["lake", "build", target or ""],
            cwd=str(repo_root),
            exit_code=0,
            summary="fake module build",
        )

    def run_snippet_check(self, *, repo_root: Path, imports: list[str], code: str, timeout_seconds: int | None = None) -> LeanCheckSummaryView:
        del timeout_seconds
        return LeanCheckSummaryView(ok=True, command=["lake", "env", "lean"], summary=f"confirmed {code} from {imports[0]}")


def _runtime() -> LeanRuntimeServices:
    return make_runtime(
        external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()},
        workspace_config=WorkspaceConfig(docstring_projection=DocstringProjectionConfig.full()),
    )


def _setup_theorem_round(repo_root: Path, runtime: LeanRuntimeServices) -> str:
    initialize_native_test_repo(repo_root)
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    content = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Verify formal stage projection sync.",
        success_criteria="Statement and proof formal captures sync with DeclGraph revisions.",
    )
    assert content.ok, content.issues
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Projection sync strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Projection sync round.",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name=DECL_NAME,
        kind="theorem",
        objective="Create a trivial theorem.",
        summary="A trivial theorem used by formal stage sync tests.",
        public=True,
        target_state=DeclState.PROVED,
    )
    assert created.ok, created.issues
    started = runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_record.value.round_id)
    assert started.ok, started.issues
    statement = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=DECL_NAME,
        nl="The main result states True.",
        origin=[{"kind": "source", "source_path": "fixture.md", "start_line": 1, "end_line": 1}],
        deps=[],
    )
    assert statement.ok, statement.issues
    return round_record.value.round_id


def _write_statement_target(path: Path) -> None:
    path.write_text(
        path.read_text(encoding="utf-8") + "theorem actualResult : True := by\n  sorry\n",
        encoding="utf-8",
    )


def _current_revision(runtime: LeanRuntimeServices, repo_root: Path) -> Any:
    revision = runtime.decl_graph.get_decl_revision(repo_root, node_path=NODE_PATH, name=DECL_NAME, revision=1)
    assert revision.ok and revision.value is not None
    return revision.value


def test_statement_capture_writes_decl_graph_snapshot_and_sync_gate(tmp_path: Path) -> None:
    runtime = _runtime()
    _setup_theorem_round(tmp_path, runtime)

    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert prepared.ok, prepared.issues
    assert prepared.value is not None
    _write_statement_target(Path(prepared.value.path))
    captured = runtime.lean_projection.capture_statement_formal(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)

    assert captured.ok, captured.issues
    revision = _current_revision(runtime, tmp_path)
    assert revision.state == DeclState.PLANNED
    assert revision.statement.formal is not None
    assert revision.statement.formal.code is not None
    assert "theorem actualResult : True := by" in revision.statement.formal.code
    assert revision.lean_decl_name == "actualResult"
    assert revision.statement.formal.check is not None
    assert revision.statement.formal.check.status == "passed"
    assert revision.statement.formal.check.policy == "statement_formal"

    synced = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="statement",
    )
    assert synced.ok and synced.value is not None
    assert synced.value.passed

    path = Path(captured.value.path)
    path.write_text(path.read_text(encoding="utf-8") + "\n-- edited after capture\n", encoding="utf-8")
    stale = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="statement",
    )

    assert stale.ok and stale.value is not None
    assert not stale.value.passed
    assert stale.value.issues[0].kind == "decl_file_capture_stale"


def test_snapshot_sync_rejects_projection_policy_drift(tmp_path: Path) -> None:
    runtime = _runtime()
    _setup_theorem_round(tmp_path, runtime)
    prepared = runtime.lean_projection.prepare_statement_formal_stage_file(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
    )
    assert prepared.ok and prepared.value is not None
    _write_statement_target(Path(prepared.value.path))
    captured = runtime.lean_projection.capture_statement_formal(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert captured.ok, captured.issues

    runtime.repo_workspace.workspace_config.docstring_projection = DocstringProjectionConfig()
    stale = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="statement",
    )

    assert stale.ok and stale.value is not None
    assert not stale.value.passed
    assert stale.value.issues[0].kind == "decl_file_projection_stale"


def test_proof_restore_capture_and_strict_sync_use_decl_graph_snapshot(tmp_path: Path) -> None:
    runtime = _runtime()
    round_id = _setup_theorem_round(tmp_path, runtime)
    statement_file = runtime.lean_projection.prepare_statement_formal_stage_file(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert statement_file.ok and statement_file.value is not None
    _write_statement_target(Path(statement_file.value.path))
    assert runtime.lean_projection.capture_statement_formal(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME).ok
    proof_nl = runtime.decl_graph.write_proof_nl(
        tmp_path,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=DECL_NAME,
        nl="Use triviality.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert proof_nl.ok, proof_nl.issues

    prepared = runtime.lean_projection.prepare_proof_formal_stage_file(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared.ok, prepared.issues
    path = Path(prepared.value.path)
    text = path.read_text(encoding="utf-8")
    assert "## Proof outline" in text
    assert "Use triviality." in text
    assert "theorem actualResult : True := by" in text

    strict_failure = runtime.lean_projection.capture_proof_formal(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert not strict_failure.ok
    assert strict_failure.issues[0].kind == "proof_lean_check_failed"
    failed_revision = _current_revision(runtime, tmp_path)
    assert failed_revision.proof is not None
    assert failed_revision.proof.formal is None

    path.write_text(text.replace("sorry", "trivial"), encoding="utf-8")
    captured = runtime.lean_projection.capture_proof_formal(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)

    assert captured.ok, captured.issues
    revision = _current_revision(runtime, tmp_path)
    assert revision.state == DeclState.PLANNED
    assert revision.proof is not None
    assert revision.proof.formal is not None
    assert revision.proof.formal.code is not None
    assert "trivial" in revision.proof.formal.code
    assert revision.proof.formal.check is not None
    assert revision.proof.formal.check.status == "passed"
    assert revision.proof.formal.check.policy == "proof_formal"

    synced = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="proof",
    )
    assert synced.ok and synced.value is not None
    assert synced.value.passed

    path.write_text(path.read_text(encoding="utf-8") + "\n-- edited after proof capture\n", encoding="utf-8")
    stale = runtime.lean_projection.check_decl_file_snapshot_sync(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="proof",
    )
    assert stale.ok and stale.value is not None
    assert not stale.value.passed
    assert stale.value.issues[0].kind == "decl_file_capture_stale"
