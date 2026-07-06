from __future__ import annotations

from pathlib import Path

from tests.unit_services_helpers import make_runtime

from lean_constellation.services.decl_graph import DeclState
from lean_constellation.services.external_clients import ExternalCommandResult, LeanDiagnosticsResult
from lean_constellation.services.foundation import WriteMode
from lean_constellation.services.runtime import LeanRuntimeServices
from lean_constellation.services.validation_snapshot.consistency_check import ConsistencyCheckComponent


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


def _setup_formal_captures(runtime: LeanRuntimeServices, repo_root: Path) -> Path:
    assert runtime.node.node_tree.ensure_root_scope_node(repo_root).ok
    assert runtime.node.create_scope_node(repo_root, path="Main.Topic", goal="Topic goal", boundary="Topic boundary").ok
    content = runtime.node.create_content_node(
        repo_root,
        path=NODE_PATH,
        goal="Core goal.",
        boundary="Core declarations only.",
        objective="Validate formal stage consistency.",
        success_criteria="Formal captures and working files stay synchronized.",
    )
    assert content.ok, content.issues
    strategy = runtime.decl_graph.ensure_open_strategy(repo_root, node_path=NODE_PATH, objective="Formal consistency strategy.")
    assert strategy.ok and strategy.value is not None
    round_record = runtime.decl_graph.create_round_draft(
        repo_root,
        node_path=NODE_PATH,
        strategy_id=strategy.value.strategy_id,
        objective="Formal consistency round.",
    )
    assert round_record.ok and round_record.value is not None
    created = runtime.decl_graph.create_decl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        name=DECL_NAME,
        kind="theorem",
        objective="Create a trivial theorem.",
        summary="A trivial theorem for formal consistency tests.",
        public=False,
        end_after_state=DeclState.PROVED,
    )
    assert created.ok, created.issues
    assert runtime.decl_graph.start_round(repo_root, node_path=NODE_PATH, round_id=round_record.value.round_id).ok
    statement = runtime.decl_graph.write_statement_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=DECL_NAME,
        nl="The main result states True.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert statement.ok, statement.issues
    assert runtime.lean_projection.prepare_statement_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name=DECL_NAME).ok
    assert runtime.lean_projection.capture_statement_formal(repo_root, node_path=NODE_PATH, decl_name=DECL_NAME).ok
    proof_nl = runtime.decl_graph.write_proof_nl(
        repo_root,
        node_path=NODE_PATH,
        round_id=round_record.value.round_id,
        decl_name=DECL_NAME,
        nl="Use triviality.",
        origin=[{"kind": "unit_test"}],
        deps=[],
    )
    assert proof_nl.ok, proof_nl.issues
    prepared_proof = runtime.lean_projection.prepare_proof_formal_stage_file(repo_root, node_path=NODE_PATH, decl_name=DECL_NAME)
    assert prepared_proof.ok and prepared_proof.value is not None, prepared_proof.issues
    path = Path(prepared_proof.value.path)
    path.write_text(path.read_text(encoding="utf-8").replace("sorry", "trivial"), encoding="utf-8")
    assert runtime.lean_projection.capture_proof_formal(repo_root, node_path=NODE_PATH, decl_name=DECL_NAME).ok
    return path


def _current_revision(runtime: LeanRuntimeServices, repo_root: Path):
    revision = runtime.decl_graph.get_decl_revision(repo_root, node_path=NODE_PATH, name=DECL_NAME, revision=1)
    assert revision.ok and revision.value is not None
    return revision.value


def _write_revision(runtime: LeanRuntimeServices, repo_root: Path, revision) -> None:
    path = runtime.decl_graph.graph_store.revision_path(
        repo_root,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        revision=revision.revision,
    )
    assert runtime.foundation.store.write_json_atomic(path, revision, mode=WriteMode.UPDATE_EXISTING).ok


def test_formal_consistency_passes_when_decl_graph_and_snapshot_sync(tmp_path: Path) -> None:
    runtime = _runtime()
    _setup_formal_captures(runtime, tmp_path)

    statement = runtime.validation_snapshot.check_formal_stage_consistency(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="statement",
    )
    proof = runtime.validation_snapshot.check_formal_stage_consistency(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="proof",
    )

    assert statement.ok and statement.value is not None
    assert statement.value.passed is True
    assert proof.ok and proof.value is not None
    assert proof.value.passed is True


def test_formal_consistency_reports_stale_working_file(tmp_path: Path) -> None:
    runtime = _runtime()
    path = _setup_formal_captures(runtime, tmp_path)
    path.write_text(path.read_text(encoding="utf-8") + "\n-- stale edit\n", encoding="utf-8")

    gate = runtime.validation_snapshot.check_formal_stage_consistency(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="proof",
    )

    assert gate.ok and gate.value is not None
    assert gate.value.passed is False
    assert any(issue.kind == "decl_file_capture_stale" for issue in gate.value.issues)


def test_formal_consistency_reports_failed_decl_graph_lean_check(tmp_path: Path) -> None:
    runtime = _runtime()
    _setup_formal_captures(runtime, tmp_path)
    revision = _current_revision(runtime, tmp_path)
    assert revision.proof is not None
    assert revision.proof.formal is not None
    assert revision.proof.formal.check is not None
    revision.proof.formal = revision.proof.formal.model_copy(
        update={
            "check": revision.proof.formal.check.model_copy(
                update={
                    "status": "failed",
                    "contains_sorry": True,
                    "scan": revision.proof.formal.check.scan.model_copy(update={"contains_sorry": True, "sorry_count": 1}),
                }
            )
        }
    )
    _write_revision(runtime, tmp_path, revision)

    gate = runtime.validation_snapshot.check_formal_stage_consistency(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="proof",
    )

    assert gate.ok and gate.value is not None
    assert gate.value.passed is False
    assert any(issue.kind == "contains_sorry" for issue in gate.value.issues)


def test_formal_consistency_missing_provider_remains_explicit(tmp_path: Path) -> None:
    runtime = _runtime()
    component = ConsistencyCheckComponent(runtime)

    gate = component.check_formal_stage_consistency(
        tmp_path,
        node_path=NODE_PATH,
        decl_name=DECL_NAME,
        stage="statement",
    )

    assert gate.ok and gate.value is not None
    assert gate.value.passed is False
    assert gate.value.issues[0].kind == "formal_stage_provider_missing"
