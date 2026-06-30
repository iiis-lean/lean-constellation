from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import ExternalCommandResult, LeanDiagnosticsResult
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


def _runtime() -> LeanRuntimeServices:
    return make_runtime(external_overrides={"lean_mcp_toolkit": FakeToolkit(), "lake": FakeLake()})


def _ensure_content_node(runtime: LeanRuntimeServices, repo_root: Path) -> None:
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    created = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Verify DeclGraph repair integration.",
        success_criteria="Projection repair restores active Decl-owned files.",
    )
    assert created.ok, created.issues


def _create_round(runtime: LeanRuntimeServices, repo_root: Path, *, objective: str) -> str:
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Projection repair strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective=objective,
    )
    assert round_record.ok and round_record.value is not None
    return round_record.value.round_id


def _seed_committed_proved_theorem(runtime: LeanRuntimeServices, repo_root: Path) -> Path:
    _ensure_content_node(runtime, repo_root)
    round_id = _create_round(runtime, repo_root, objective="Create a proved theorem.")
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name=DECL_NAME,
        kind="theorem",
        objective="Create a trivial theorem.",
        summary="A trivial theorem used by repair integration tests.",
        public=False,
        end_after_state=DeclState.PROVED,
    )
    assert created.ok and created.value is not None, created.issues
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=DECL_NAME,
        nl="The main result states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert statement.ok, statement.issues
    prepared_statement = runtime.lean_projection.prepare_statement_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared_statement.ok, prepared_statement.issues
    captured_statement = runtime.lean_projection.capture_statement_formal(repo_root, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert captured_statement.ok, captured_statement.issues
    proof_nl = runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        decl_name=DECL_NAME,
        nl="Use triviality.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert proof_nl.ok, proof_nl.issues
    prepared_proof = runtime.lean_projection.prepare_proof_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared_proof.ok and prepared_proof.value is not None, prepared_proof.issues
    proof_path = Path(prepared_proof.value.path)
    proof_path.write_text(proof_path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    captured_proof = runtime.lean_projection.capture_proof_formal(repo_root, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert captured_proof.ok, captured_proof.issues
    committed = runtime.decl_graph.commit_decl_revision(repo_root, node_path=NODE_PATH, name=DECL_NAME, state=DeclState.PROVED)
    assert committed.ok, committed.issues
    change_summary = runtime.decl_graph.write_decl_change_summary(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        change_id=created.value.change_id,
        summary="Created and proved the theorem.",
    )
    assert change_summary.ok, change_summary.issues
    round_summary = runtime.decl_graph.write_round_summary(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        summary="Seed theorem is proved.",
    )
    assert round_summary.ok, round_summary.issues
    terminal = runtime.decl_graph.mark_round_terminal(repo_root, node_path=NODE_PATH, round_id=round_id, result_kind="success")
    assert terminal.ok, terminal.issues
    return proof_path


def _open_update_round(runtime: LeanRuntimeServices, repo_root: Path, *, start_state: DeclState) -> str:
    round_id = _create_round(runtime, repo_root, objective=f"Open update from {start_state.value}.")
    update = runtime.decl_graph.open_decl_update(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_id,
        name=DECL_NAME,
        objective=f"Reset current working revision to {start_state.value}.",
        start_before_state=start_state,
        end_after_state=DeclState.PROVED,
    )
    assert update.ok, update.issues
    started = runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_id)
    assert started.ok, started.issues
    return round_id


def _current_revision(runtime: LeanRuntimeServices, repo_root: Path):
    decl = runtime.decl_graph.get_decl(repo_root, node_path=NODE_PATH, name=DECL_NAME)
    assert decl.ok and decl.value is not None
    revision = runtime.decl_graph.get_decl_revision(
        repo_root,
        node_path=NODE_PATH,
        name=DECL_NAME,
        revision=decl.value.current_revision,
    )
    assert revision.ok and revision.value is not None
    return revision.value


def test_restore_projection_to_active_graph_uses_decl_graph_reset_revision(tmp_path: Path) -> None:
    runtime = _runtime()
    proof_path = _seed_committed_proved_theorem(runtime, tmp_path)
    _open_update_round(runtime, tmp_path, start_state=DeclState.DECLARED)
    revision = _current_revision(runtime, tmp_path)
    assert revision.revision == 2
    assert revision.statement_lean_code is not None
    assert revision.proof_lean_code is None

    proof_path.write_text("broken working projection\n", encoding="utf-8")
    restored = runtime.lean_projection.restore_projection_to_active_graph(tmp_path, node_path=NODE_PATH)

    assert restored.ok, restored.issues
    assert restored.value is not None
    assert any(action.action == "sync_decl_file" for action in restored.value.actions)
    assert proof_path.read_text(encoding="utf-8") == revision.statement_lean_code
    assert "stage: statement" in proof_path.read_text(encoding="utf-8")
    assert "trivial" not in proof_path.read_text(encoding="utf-8")


def test_sync_decl_file_after_reset_to_specified_deletes_working_file(tmp_path: Path) -> None:
    runtime = _runtime()
    proof_path = _seed_committed_proved_theorem(runtime, tmp_path)
    assert proof_path.exists()
    _open_update_round(runtime, tmp_path, start_state=DeclState.SPECIFIED)
    revision = _current_revision(runtime, tmp_path)
    assert revision.statement_nl is not None
    assert revision.statement_lean_code is None

    synced = runtime.lean_projection.sync_decl_file_after_revision_reset(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)

    assert synced.ok, synced.issues
    assert synced.value is not None
    assert synced.value.changed is True
    assert not proof_path.exists()


def test_remove_decl_file_for_delete_wrapper_removes_current_decl_file(tmp_path: Path) -> None:
    runtime = _runtime()
    proof_path = _seed_committed_proved_theorem(runtime, tmp_path)
    assert proof_path.exists()

    removed = runtime.lean_projection.remove_decl_file_for_delete(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)
    removed_again = runtime.lean_projection.remove_decl_file_for_delete(tmp_path, node_path=NODE_PATH, decl_name=DECL_NAME)

    assert removed.ok and removed.value is not None
    assert removed.value.changed is True
    assert not proof_path.exists()
    assert removed_again.ok and removed_again.value is not None
    assert removed_again.value.changed is False
